"""Tests für Kunden-Portal und Admin-Bereich (Login über den Entwickler-Login)."""

import os
import re
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.api import create_app
from app.certs import FileCertStore, SignerProvider, import_certificate
from app.config import Settings
from app.db import Base, make_engine, make_sessionmaker
from app.models import AuditLog, Certificate, Pass, Template, TemplateVersion, Tenant, User
from app.security import Vault
from creatwallet.build import inspect_pkpass

from .helpers import make_credentials

CSRF_RE = re.compile(r'name="csrf" (?:value|content)="([^"]+)"')


class WebCase(unittest.TestCase):
    approval = True  # die meisten Tests prüfen den Ablauf mit Freigabe; Standard der Plattform ist ohne

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        creds = make_credentials()
        wwdr = os.path.join(self.tmp.name, "wwdr.pem")
        with open(wwdr, "wb") as fh:
            fh.write(creds["wwdr"])
        self.settings = Settings(database_url="sqlite://", public_base_url="http://testserver",
                                 secret_key=Vault.generate_key(), wwdr_path=wwdr, dev_login=True,
                                 cert_dir=os.path.join(self.tmp.name, "store"), allow_insecure_webhooks=True,
                                 require_template_approval=self.approval)
        self.engine = make_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.Session = make_sessionmaker(self.engine)
        store = FileCertStore(self.settings.cert_dir, Vault(self.settings.secret_key))
        with self.Session() as s:
            self.cert_id = import_certificate(s, store, creds["p12"], creds["password"], wwdr).id
            s.commit()
        self.app = create_app(self.settings, engine=self.engine, signers=SignerProvider(store, wwdr))
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            c.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def client(self):
        c = TestClient(self.app)
        self.clients.append(c)
        return c

    @staticmethod
    def csrf(c, url="/portal"):
        m = CSRF_RE.search(c.get(url).text)
        return m.group(1) if m else ""

    def login(self, email, admin=False):
        c = self.client()
        token = self.csrf(c, "/auth/dev-login")
        r = c.post("/auth/dev-login", data={"csrf": token, "email": email, **({"admin": "true"} if admin else {})})
        self.assertEqual(r.status_code, 200, r.text[:300])
        return c

    def post(self, c, url, data=None, page=None, **kw):
        token = self.csrf(c, page or "/portal/settings")
        return c.post(url, data={"csrf": token, **(data or {})}, **kw)

    def company(self, email="anna@kino.test", name="Kino am Markt"):
        c = self.login(email)
        r = self.post(c, "/portal/onboarding", {"name": name, "terms": "true"}, page="/portal/onboarding")
        self.assertIn("Übersicht", r.text)
        return c

    def tenant(self, name="Kino am Markt"):
        with self.Session() as s:
            return s.query(Tenant).filter_by(name=name).one()

    def activate(self, name="Kino am Markt"):
        admin = self.login("chef@plattform.test", admin=True)
        t = self.tenant(name)
        self.post(admin, f"/admin/tenants/{t.id}/status", {"status": "active"}, page="/admin")
        self.post(admin, f"/admin/tenants/{t.id}/certificate", {"certificate_id": self.cert_id}, page="/admin")
        return admin


class LoginTests(WebCase):
    def test_redirects_to_login(self):
        c = self.client()
        r = c.get("/portal", follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertTrue(r.headers["location"].startswith("/auth/login"))

    def test_onboarding_creates_pending_tenant(self):
        c = self.company()
        t = self.tenant()
        self.assertEqual(t.status, "pending")
        self.assertIn("wird gerade geprüft", c.get("/portal").text)
        with self.Session() as s:
            self.assertEqual(s.query(User).filter_by(email="anna@kino.test").one().role, "owner")

    def test_csrf_required(self):
        c = self.login("anna@kino.test")
        r = c.post("/portal/onboarding", data={"name": "X", "terms": "true"})
        self.assertEqual(r.status_code, 403)

    def test_dev_login_disabled_by_default(self):
        app = create_app(Settings(database_url="sqlite://", secret_key=Vault.generate_key()), engine=self.engine,
                         signers=self.app.state.signers)
        with TestClient(app) as c:
            self.assertEqual(c.get("/auth/dev-login").status_code, 403)
            self.assertEqual(c.get("/auth/login").status_code, 503)

    def test_admin_requires_group(self):
        c = self.company()
        self.assertEqual(c.get("/admin").status_code, 403)
        admin = self.login("chef@plattform.test", admin=True)
        self.assertEqual(admin.get("/admin").status_code, 200)


class PortalFlowTests(WebCase):
    def test_full_flow(self):
        c = self.company()
        # Vorlage anlegen und im Editor speichern (mit Platzhalter)
        r = self.post(c, "/portal/templates", {"name": "Mitgliedskarte", "base": "generic"})
        self.assertIn("CW_PLATFORM", r.text)
        with self.Session() as s:
            tid = s.query(Template).one().id
        data = c.get(f"/portal/templates/{tid}/editor/data").json()
        data["pass"]["generic"]["primaryFields"][0]["value"] = "{{name}}"
        token = self.csrf(c)
        r = c.post(f"/portal/templates/{tid}/editor/save", json={"pass": data["pass"], "images": data["images"]},
                   headers={"X-CSRF-Token": token})
        self.assertEqual(r.json(), {"version": 2, "status": "pending"})
        r = c.post(f"/portal/templates/{tid}/editor/save", json={"pass": data["pass"]})
        self.assertEqual(r.status_code, 403)  # ohne CSRF-Header
        v = c.post(f"/portal/templates/{tid}/editor/validate", json={"pass": data["pass"], "images": data["images"]},
                   headers={"X-CSRF-Token": token}).json()
        self.assertEqual(v["placeholders"], ["name"])
        self.assertIn("name", c.get(f"/portal/templates/{tid}").text)

        # Vor Freigabe kein Test-Pass ohne Zertifikat, keine Ausgabe
        self.assertIn("kein Zertifikat", self.post(c, f"/portal/templates/{tid}/test-pass").text)
        admin = self.activate()

        # Admin prüft und gibt frei
        self.assertIn("Mitgliedskarte", admin.get("/admin/reviews").text)
        with self.Session() as s:
            vid = s.query(TemplateVersion).filter_by(status="pending").order_by(TemplateVersion.number.desc()).first().id
        page = admin.get(f"/admin/reviews/{vid}")
        self.assertIn("pass.json", page.text)
        r = self.post(admin, f"/admin/reviews/{vid}/test-pass/download", page="/admin")
        self.assertEqual(r.headers["content-type"], "application/vnd.apple.pkpass")
        r = self.post(admin, f"/admin/reviews/{vid}/approve", page="/admin")
        self.assertIn("Freigegeben", r.text)

        # Test-Pass aus dem Editor (ungespeicherter Stand)
        r = c.post(f"/portal/templates/{tid}/editor/test-pass", json={"pass": data["pass"], "images": data["images"]},
                   headers={"X-CSRF-Token": token})
        self.assertEqual(inspect_pkpass(r.content)["problems"], [])

        # Pass im Portal ausgeben, ändern, sperren
        self.assertIn('name="f_name"', c.get(f"/portal/passes/new?template_id={tid}").text)
        r = self.post(c, "/portal/passes", {"template_id": tid, "f_name": "Anna Beispiel"})
        self.assertIn("Pass ausgegeben", r.text)
        with self.Session() as s:
            p = s.query(Pass).one()
            pid, token_dl = p.id, p.download_token
        self.assertIn(f"/p/{token_dl}", c.get(f"/portal/passes/{pid}").text)
        self.assertIn("Anna Beispiel", c.get("/portal/passes?q=Anna").text)
        self.assertNotIn("Anna Beispiel", c.get("/portal/passes?q=Zzz").text)
        r = self.post(c, f"/portal/passes/{pid}", {"f_name": "Bert"})
        self.assertIn("Version 2", r.text)
        r = self.post(c, f"/portal/passes/{pid}/void")
        self.assertIn("gesperrt", r.text)

        # Einbinden: Schlüssel und Webhook
        r = self.post(c, "/portal/keys", {"name": "Shop"})
        key = re.search(r"wk_[A-Za-z0-9]+_[A-Za-z0-9_-]+", r.text).group(0)
        self.assertEqual(c.get("/api/v1/account", headers={"Authorization": f"Bearer {key}"}).status_code, 200)
        self.assertNotIn(key, c.get("/portal/developers").text)  # nur einmal angezeigt
        r = self.post(c, "/portal/webhooks", {"url": "http://hooks.test/x", "events": ["pass.updated"]})
        self.assertIn("whsec_", r.text)

        for url in ("/portal", "/portal/templates", "/portal/stats", "/portal/stats?days=90", "/portal/team",
                    "/portal/settings", "/portal/developers"):
            self.assertEqual(c.get(url).status_code, 200, url)
        for url in ("/admin", "/admin/tenants", "/admin/tenants?status=active", f"/admin/tenants/{self.tenant().id}",
                    "/admin/certificates", "/admin/jobs", "/admin/jobs?status=done", "/admin/audit"):
            self.assertEqual(admin.get(url).status_code, 200, url)
        with self.Session() as s:
            actions = {a.action for a in s.query(AuditLog)}
        self.assertTrue({"tenant.signup", "admin.template.approve", "pass.void", "apikey.create"} <= actions)

    def test_reject_with_note(self):
        c = self.company()
        self.post(c, "/portal/templates", {"name": "Karte", "base": "generic"})
        admin = self.login("chef@plattform.test", admin=True)
        with self.Session() as s:
            vid = s.query(TemplateVersion).one().id
        self.assertIn("Begründung", self.post(admin, f"/admin/reviews/{vid}/reject", {"note": ""}, page="/admin").text)
        self.post(admin, f"/admin/reviews/{vid}/reject", {"note": "Fremdes Logo"}, page="/admin")
        self.assertIn("Fremdes Logo", c.get(f"/portal/templates/{self.first_template_id()}").text)

    def first_template_id(self):
        with self.Session() as s:
            return s.query(Template).first().id


class NoApprovalTests(WebCase):
    approval = False

    def test_saved_template_is_usable_at_once(self):
        c = self.company()
        self.activate()
        self.post(c, "/portal/templates", {"name": "Karte", "base": "generic"})
        with self.Session() as s:
            t = s.query(Template).one()
            self.assertIsNotNone(t.approved_version_id)
            tid = t.id
        data = c.get(f"/portal/templates/{tid}/editor/data").json()
        data["pass"]["description"] = "Neu"
        r = c.post(f"/portal/templates/{tid}/editor/save", json={"pass": data["pass"], "images": data["images"]},
                   headers={"X-CSRF-Token": self.csrf(c)})
        self.assertEqual(r.json(), {"version": 2, "status": "approved"})
        r = self.post(c, "/portal/passes", {"template_id": tid})
        self.assertIn("Pass ausgegeben", r.text)
        admin = self.login("chef@plattform.test", admin=True)
        page = admin.get("/admin/reviews").text
        self.assertIn("Neue Firmen", page)
        self.assertNotIn("Keine offenen Vorlagen", page)


class TeamAndPermissionTests(WebCase):
    def test_invite_roles_and_isolation(self):
        owner = self.company()
        r = self.post(owner, "/portal/team/invite", {"email": "ben@kino.test", "role": "viewer"})
        link = re.search(r"http://testserver(/invite/[A-Za-z0-9_-]+)", r.text).group(1)
        ben = self.login("ben@kino.test")
        r = ben.get(link)
        self.assertIn("Willkommen bei Kino am Markt", r.text)
        # Nur lesen: keine Vorlage anlegen
        r = self.post(ben, "/portal/templates", {"name": "X", "base": "generic"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(ben.get(link).status_code, 200)  # zweiter Aufruf: Einladung verbraucht, kein Fehler
        # Rolle ändern, letzten Inhaber schützen
        with self.Session() as s:
            ben_id = s.query(User).filter_by(email="ben@kino.test").one().id
            anna_id = s.query(User).filter_by(email="anna@kino.test").one().id
        self.post(owner, f"/portal/team/{ben_id}/role", {"role": "designer"})
        self.assertIn("CW_PLATFORM", self.post(ben, "/portal/templates", {"name": "Y", "base": "generic"}).text)
        r = self.post(owner, f"/portal/team/{anna_id}/remove")
        self.assertIn("letzte Inhaber", r.text)

        # Andere Firma sieht nichts
        other = self.company("eva@zoo.test", "Zoo GmbH")
        with self.Session() as s:
            tid = s.query(Template).first().id
        self.assertEqual(other.get(f"/portal/templates/{tid}").status_code, 404)
        self.assertEqual(other.get(f"/portal/templates/{tid}/editor/data").status_code, 404)


class AdminCertificateTests(WebCase):
    def test_upload_certificate(self):
        admin = self.login("chef@plattform.test", admin=True)
        creds = make_credentials(b"pw2")
        # Zertifikat mit derselben Pass Type ID: ersetzt das alte
        r = self.post(admin, "/admin/certificates", {"password": "pw2"}, page="/admin",
                      files={"p12": ("pass.p12", creds["p12"], "application/x-pkcs12")})
        self.assertIn("Gespeichert", r.text)
        r = self.post(admin, "/admin/certificates", {"password": "falsch"}, page="/admin",
                      files={"p12": ("pass.p12", creds["p12"], "application/x-pkcs12")})
        self.assertIn("Passwort falsch", r.text)
        with self.Session() as s:
            self.assertEqual(s.query(Certificate).count(), 1)

    def test_admin_network_restriction(self):
        admin = self.login("chef@plattform.test", admin=True)
        self.app.state.settings = Settings(**{**self.settings.__dict__, "admin_networks": ("10.0.0.0/8",)})
        r = admin.get("/admin")
        self.assertEqual(r.status_code, 403)
        self.assertIn("nicht erreichbar", r.text)


if __name__ == "__main__":
    unittest.main()

