"""Tests für die Funktionen aus dem Masterplan: Tarife, Rate-Limits, API-Protokoll, Massenausgabe,
E-Mail, tägliche Wartung, Zertifikatsanfrage, Exporte, Ankündigungen, Einbett-Button."""

import datetime
import io
import zipfile
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app import bulk, maintenance, mailer
from app.config import Settings
from app.models import (ApiRequestLog, BulkIssue, Certificate, Job, Pass, Template, Tenant, UsageDaily)
from app.security import Vault
from app.worker import Worker
from creatwallet.build import inspect_pkpass

from .test_web import WebCase


class FakePusher:
    def send(self, cert, token):
        return "ok", ""


class FeatureCase(WebCase):
    approval = False

    def setUp(self):
        super().setUp()
        self.kino = self.company()
        self.admin = self.activate()
        self.tenant_id = self.tenant().id
        with self.Session() as s:
            s.get(Tenant, self.tenant_id).plan = "business"
            s.commit()
        self.post(self.kino, "/portal/templates", {"name": "Karte", "base": "generic"})
        with self.Session() as s:
            self.tid = s.query(Template).one().id
        data = self.kino.get(f"/portal/templates/{self.tid}/editor/data").json()
        data["pass"]["generic"]["primaryFields"][0]["value"] = "{{name}}"
        r = self.kino.post(f"/portal/templates/{self.tid}/editor/save",
                           json={"pass": data["pass"], "images": data["images"]},
                           headers={"X-CSRF-Token": self.csrf(self.kino)})
        assert r.status_code == 200, r.text

    def set(self, **changes):
        self.app.state.settings = Settings(**{**self.app.state.settings.__dict__, **changes})

    def worker(self):
        return Worker(self.Session, self.app.state.settings, Vault(self.settings.secret_key), FakePusher(),
                      signers=self.app.state.signers)

    def api_key(self):
        import re
        r = self.post(self.kino, "/portal/keys", {"name": "Test"})
        return re.search(r"wk_[A-Za-z0-9]+_[A-Za-z0-9_-]+", r.text).group(0)


class PlanTests(FeatureCase):
    def test_free_plan_limits(self):
        self.set(enforce_plan_limits=True)
        with self.Session() as s:
            s.get(Tenant, self.tenant_id).plan = "free"
            s.commit()
        key = self.api_key()
        r = self.kino.get("/api/v1/account", headers={"Authorization": f"Bearer {key}"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("API ist im Tarif Kostenlos nicht enthalten", r.json()["error"])
        # Nur eine Vorlage
        r = self.post(self.kino, "/portal/templates", {"name": "Zweite", "base": "generic"})
        self.assertIn("1 Vorlage(n) möglich", r.text)
        # Hartes Kontingent: 25 aktive Pässe
        with self.Session() as s:
            t = s.get(Tenant, self.tenant_id)
            for i in range(25):
                s.add(Pass(tenant_id=t.id, template_id=self.tid, serial_number=f"s{i}", data={"name": "x"},
                           download_token=f"tok{i}", auth_token_enc="x"))
            s.commit()
        r = self.post(self.kino, "/portal/passes", {"template_id": self.tid, "f_name": "A"})
        self.assertIn("Kontingent erreicht", r.text)
        page = self.kino.get("/portal/billing").text
        self.assertIn("100 %", page)
        self.assertIn("Kontingent erreicht", page)

    def test_business_has_webhooks_starter_not(self):
        self.set(enforce_plan_limits=True)
        with self.Session() as s:
            s.get(Tenant, self.tenant_id).plan = "starter"
            s.commit()
        r = self.post(self.kino, "/portal/webhooks", {"url": "http://hooks.test/x", "events": ["pass.updated"]})
        self.assertIn("Webhooks ist im Tarif Starter nicht enthalten", r.text)
        r = self.post(self.kino, "/portal/certificate/request")
        self.assertIn("Eigenes Zertifikat ist im Tarif Starter nicht enthalten", r.text)

    def test_billing_page(self):
        page = self.kino.get("/portal/billing").text
        self.assertIn("Business", page)
        self.assertIn("dein Tarif", page)


class ApiTests(FeatureCase):
    def test_rate_limit_and_log(self):
        self.set(rate_limit_per_minute=2)
        key = self.api_key()
        h = {"Authorization": f"Bearer {key}"}
        codes = [self.kino.get("/api/v1/account", headers=h).status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])
        r = self.kino.get("/api/v1/account", headers=h)
        self.assertIn("Retry-After", r.headers)
        with self.Session() as s:
            self.assertGreaterEqual(s.query(ApiRequestLog).count(), 3)
        page = self.kino.get("/portal/developers").text
        self.assertIn("GET /api/v1/account", page)
        self.assertIn(">429<", page)


class BulkTests(FeatureCase):
    CSV = "﻿Name;email;seriennummer\nAnna;anna@kunde.test;B-1\nBert;;\n;;\n".encode()

    def test_read_csv(self):
        cols, rows = bulk.read_csv(self.CSV)
        self.assertEqual(cols, ["Name", "email", "seriennummer"])
        self.assertEqual(len(rows), 2)
        cols, rows = bulk.read_csv("name,email\nÄrger,x@y.de\n".encode("cp1252"))
        self.assertEqual(rows[0]["name"], "Ärger")
        with self.assertRaises(bulk.CsvError):
            bulk.read_csv(b"")

    def test_full_bulk_run_with_emails(self):
        self.set(smtp_url="smtp://mail.test:25?starttls=0")
        r = self.post(self.kino, "/portal/bulk/preview", {"template_id": self.tid, "send_emails": "true"},
                      files={"file": ("kunden.csv", self.CSV, "text/csv")})
        self.assertIn("Vorschau: 2 Zeilen", r.text)
        with self.Session() as s:
            run_id = s.query(BulkIssue).one().id
        r = self.post(self.kino, f"/portal/bulk/{run_id}/start")
        self.assertIn("2 Pässe werden ausgegeben", r.text)
        sent = []
        with mock.patch("smtplib.SMTP") as smtp:
            smtp.return_value.send_message.side_effect = lambda m: sent.append(m["To"])
            self.worker().run_until_empty()
        with self.Session() as s:
            run = s.get(BulkIssue, run_id)
            self.assertEqual((run.status, run.done, run.failed), ("done", 2, 0))
            self.assertEqual(s.query(Pass).count(), 2)
            self.assertEqual(s.query(Pass).filter_by(serial_number="B-1").count(), 1)
        self.assertEqual(sent, ["anna@kunde.test"])
        csv_text = self.kino.get(f"/portal/bulk/{run_id}/ergebnis.csv").text
        self.assertIn("B-1", csv_text)
        self.assertIn("/p/", csv_text)
        # Wiederholung desselben Jobs erzeugt keine doppelten Pässe
        with self.Session() as s:
            bulk.process(s, run_id, self.app.state.settings, self.app.state.signers, Vault(self.settings.secret_key))
            self.assertEqual(s.query(Pass).count(), 2)

    def test_missing_columns(self):
        r = self.post(self.kino, "/portal/bulk/preview", {"template_id": self.tid},
                      files={"file": ("x.csv", b"vorname\nAnna\n", "text/csv")})
        self.assertIn("Spalten fehlen in der CSV: name", r.text)

    def test_sample_csv(self):
        r = self.kino.get(f"/portal/bulk/vorlage.csv?template_id={self.tid}")
        self.assertIn("name;email", r.text)


class MailAndDailyTests(FeatureCase):
    def test_invitation_mail(self):
        self.set(smtp_url="smtp://mail.test:25?starttls=0")
        r = self.post(self.kino, "/portal/team/invite", {"email": "ben@kino.test", "role": "issuer"})
        self.assertIn("per E-Mail an ben@kino.test verschickt", r.text)
        with self.Session() as s:
            job = s.query(Job).filter_by(kind="email").one()
            self.assertIn("/invite/", job.payload["text"])

    def test_mailer_without_smtp_only_logs(self):
        self.assertFalse(mailer.Mailer("", "x@y.de").send("a@b.de", "Test", "Text"))

    def test_daily_maintenance(self):
        self.set(enforce_plan_limits=True)
        with self.Session() as s:
            t = s.get(Tenant, self.tenant_id)
            t.plan = "free"
            for i in range(20):
                s.add(Pass(tenant_id=t.id, template_id=self.tid, serial_number=f"d{i}", data={"name": "x"},
                           download_token=f"dt{i}", auth_token_enc="x"))
            s.commit()
            now = datetime.datetime.now(datetime.timezone.utc)
            summary = maintenance.run_daily(s, self.app.state.settings, now)
            self.assertEqual(summary["usage"], 1)
            self.assertEqual(s.query(UsageDaily).one().active, 20)
            self.assertGreaterEqual(summary["quota_mails"], 1)  # 20 von 25 = 80 %
            # Test-Zertifikat läuft in < 30 Tagen ab -> Mail an Plattform-Admins
            self.assertGreaterEqual(summary["certificate_mails"], 1)
            self.assertEqual(maintenance.run_daily(s, self.app.state.settings, now), {})  # einmal pro Tag
            self.assertEqual(maintenance.run_daily(s, self.app.state.settings, now, force=True)["quota_mails"], 0)


class CsrTests(FeatureCase):
    def test_csr_flow_creates_own_certificate(self):
        self.set(enforce_plan_limits=False)
        self.post(self.kino, "/portal/certificate/request")
        page = self.kino.get("/portal/certificate").text
        self.assertIn("Offene Zertifikatsanfrage", page)
        from app.models import CertificateRequest
        with self.Session() as s:
            req = s.query(CertificateRequest).one()
            req_id, csr_pem = req.id, req.csr_pem
        csr = x509.load_pem_x509_csr(self.kino.get(f"/portal/certificate/request/{req_id}.certSigningRequest").content)
        self.assertEqual(csr.public_bytes(serialization.Encoding.PEM).decode(), csr_pem)
        # "Apple" stellt das Zertifikat aus
        ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        now = datetime.datetime.now(datetime.timezone.utc)
        cer = (x509.CertificateBuilder()
               .subject_name(x509.Name([x509.NameAttribute(NameOID.USER_ID, "pass.de.kino.csr"),
                                        x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, "KINO123456")]))
               .issuer_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Apple")]))
               .public_key(csr.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=365))
               .sign(ca_key, hashes.SHA256()))
        r = self.post(self.kino, f"/portal/certificate/request/{req_id}/complete", {"activate": "true"},
                      files={"cer": ("pass.cer", cer.public_bytes(serialization.Encoding.DER), "application/x-x509-ca-cert")})
        self.assertIn("pass.de.kino.csr eingerichtet", r.text)
        with self.Session() as s:
            c = s.query(Certificate).filter_by(pass_type_identifier="pass.de.kino.csr").one()
            self.assertEqual(c.owner_tenant_id, self.tenant_id)
            self.assertEqual(s.get(Tenant, self.tenant_id).certificate_id, c.id)
            self.assertEqual(s.get(CertificateRequest, req_id).key_enc, "")
        r = self.post(self.kino, "/portal/passes", {"template_id": self.tid, "f_name": "A"})
        with self.Session() as s:
            p = s.query(Pass).one()
        pj = inspect_pkpass(self.kino.get(f"/p/{p.download_token}/pass.pkpass").content)["pass"]
        self.assertEqual(pj["passTypeIdentifier"], "pass.de.kino.csr")

    def test_wrong_cer_rejected(self):
        self.post(self.kino, "/portal/certificate/request")
        from app.models import CertificateRequest
        from .helpers import make_credentials
        with self.Session() as s:
            req_id = s.query(CertificateRequest).one().id
        other = make_credentials()["cert"]
        r = self.post(self.kino, f"/portal/certificate/request/{req_id}/complete", {},
                      files={"cer": ("pass.cer", other, "application/x-x509-ca-cert")})
        self.assertIn("gehört zu einer anderen Anfrage", r.text)


class ExportAndExtrasTests(FeatureCase):
    def test_exports(self):
        self.post(self.kino, "/portal/passes", {"template_id": self.tid, "f_name": "Anna"})
        r = self.kino.get("/portal/stats.csv?days=7")
        self.assertTrue(r.text.startswith("﻿tag;ausgegeben;installiert"))
        self.assertEqual(len(r.text.strip().splitlines()), 8)
        z = zipfile.ZipFile(io.BytesIO(self.kino.get("/portal/settings/export.zip").content))
        names = z.namelist()
        self.assertIn("firma.json", names)
        self.assertIn("paesse.csv", names)
        self.assertTrue(any(n.endswith("pass.json") for n in names))
        self.assertIn("Anna", z.read("paesse.csv").decode())

    def test_announcements(self):
        self.post(self.admin, "/admin/announcements", {"text": "Wartung am Sonntag", "level": "warn"}, page="/admin")
        self.assertIn("Wartung am Sonntag", self.kino.get("/portal").text)
        from app.models import Announcement
        with self.Session() as s:
            ann_id = s.query(Announcement).one().id
        self.post(self.admin, f"/admin/announcements/{ann_id}/toggle", page="/admin")
        self.assertNotIn("Wartung am Sonntag", self.kino.get("/portal").text)

    def test_embed(self):
        self.post(self.kino, "/portal/passes", {"template_id": self.tid, "f_name": "Anna"})
        with self.Session() as s:
            token = s.query(Pass).one().download_token
        js = self.kino.get("/embed.js")
        self.assertIn("data-wallet-pass", js.text)
        self.assertEqual(js.headers["access-control-allow-origin"], "*")
        self.assertIn("<svg", self.kino.get(f"/p/{token}/qr.svg").text)
        self.assertEqual(self.kino.get("/p/unbekannt/qr.svg").status_code, 404)

    def test_admin_pages(self):
        for url in ("/admin/announcements", "/admin/certificates", f"/admin/tenants/{self.tenant_id}"):
            self.assertEqual(self.admin.get(url).status_code, 200, url)
        for url in ("/portal/bulk", "/portal/billing", "/portal/certificate", "/portal/developers", "/portal"):
            self.assertEqual(self.kino.get(url).status_code, 200, url)
