"""Tests der Plattform-API (Phase 1) mit SQLite im Speicher und Test-Zertifikaten."""

import base64
import json
import os
import tempfile
import unittest
import zipfile
from io import BytesIO

import httpx
from fastapi.testclient import TestClient

from app import placeholders
from app.api import create_app
from app.certs import FileCertStore, OpenBaoCertStore, SignerProvider, import_certificate
from app.config import Settings
from app.db import Base, make_engine, make_sessionmaker
from app.models import ApiKey, Tenant, utcnow
from app.security import Vault, generate_api_key
from app.services import approve_version
from creatwallet.build import inspect_pkpass
from creatwallet.templates import new_pass

from .helpers import PTI, TEAM, make_credentials


def generic_template():
    data = new_pass("generic")
    data["generic"]["primaryFields"] = [{"key": "member", "label": "MITGLIED", "value": "{{name}}"}]
    data["generic"]["secondaryFields"] = [{"key": "number", "label": "NUMMER", "value": "{{nummer}}"}]
    data["generic"]["auxiliaryFields"] = [{"key": "since", "label": "SEIT", "value": "{{seit}}",
                                           "dateStyle": "PKDateStyleMedium"}]
    data["barcodes"] = [{"format": "PKBarcodeFormatQR", "message": "M-{{nummer}}", "messageEncoding": "iso-8859-1"}]
    return data


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        creds = make_credentials()
        wwdr = os.path.join(self.tmp.name, "wwdr.pem")
        with open(wwdr, "wb") as fh:
            fh.write(creds["wwdr"])
        self.settings = Settings(database_url="sqlite://", public_base_url="https://wallet.test",
                                 secret_key=Vault.generate_key(), wwdr_path=wwdr,
                                 cert_dir=os.path.join(self.tmp.name, "store"), require_template_approval=True)
        self.engine = make_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.Session = make_sessionmaker(self.engine)
        store = FileCertStore(self.settings.cert_dir, Vault(self.settings.secret_key))
        self.signers = SignerProvider(store, wwdr)
        with self.Session() as s:
            cert = import_certificate(s, store, creds["p12"], creds["password"], wwdr)
            self.tenant_id, self.key = self._tenant(s, "Museum GmbH", cert)
            self.other_id, self.other_key = self._tenant(s, "Andere AG", cert)
            s.commit()
        app = create_app(self.settings, engine=self.engine, signers=self.signers)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def _tenant(self, s, name, cert):
        t = Tenant(name=name, organization_name=name, certificate=cert)
        s.add(t)
        s.flush()
        key, prefix, h = generate_api_key()
        s.add(ApiKey(tenant_id=t.id, prefix=prefix, secret_hash=h))
        return t.id, key

    def h(self, key=None, **extra):
        return {"Authorization": f"Bearer {key or self.key}", **extra}

    def make_template(self, approve=True, key=None):
        r = self.client.post("/api/v1/templates", headers=self.h(key),
                             json={"name": "Mitgliedskarte", "pass": generic_template(), "base_template": "generic"})
        self.assertEqual(r.status_code, 201, r.text)
        tid = r.json()["id"]
        if approve:
            self.approve(tid)
        return tid

    def approve(self, tid):
        from app.models import Template
        with self.Session() as s:
            approve_version(s.get(Template, tid).latest_version)
            s.commit()

    def issue(self, tid, **data):
        payload = {"name": "Anna Beispiel", "nummer": "4711", "seit": "2026-01-15T00:00+01:00", **data}
        return self.client.post("/api/v1/passes", headers=self.h(), json={"template_id": tid, "data": payload})


class AuthTests(ApiTestCase):
    def test_missing_and_wrong_key(self):
        self.assertEqual(self.client.get("/api/v1/templates").status_code, 401)
        r = self.client.get("/api/v1/templates", headers={"Authorization": "Bearer wk_abc_falsch"})
        self.assertEqual(r.status_code, 401)
        self.assertIn("error", r.json())

    def test_revoked_key(self):
        with self.Session() as s:
            for k in s.query(ApiKey).filter_by(tenant_id=self.tenant_id):
                k.revoked_at = utcnow()
            s.commit()
        self.assertEqual(self.client.get("/api/v1/account", headers=self.h()).status_code, 401)

    def test_account(self):
        r = self.client.get("/api/v1/account", headers=self.h())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["pass_type_identifier"], PTI)


class TemplateTests(ApiTestCase):
    def test_create_lists_placeholders_and_strips_platform_keys(self):
        r = self.client.post("/api/v1/templates", headers=self.h(),
                             json={"name": "Karte", "pass": generic_template(), "base_template": "generic"})
        body = r.json()
        self.assertEqual(body["placeholders"], ["name", "nummer", "seit"])
        self.assertEqual(body["status"], "pending")
        self.assertNotIn("serialNumber", body["pass"])
        self.assertIn("icon@2x.png", body["images"])
        # Fehler durch Beispieltexte in Platzhalter-Feldern werden nur als Hinweis gemeldet.
        self.assertFalse([i for i in body["issues"] if i["level"] == "error"], body["issues"])

    def test_base_template_only(self):
        r = self.client.post("/api/v1/templates", headers=self.h(),
                             json={"name": "Konzert", "base_template": "posterEventTicket"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["placeholders"], [])

    def test_bad_image(self):
        r = self.client.post("/api/v1/templates", headers=self.h(), json={
            "name": "X", "pass": generic_template(), "images": {"icon@2x.png": base64.b64encode(b"kein png").decode()}})
        self.assertEqual(r.status_code, 422)
        r = self.client.post("/api/v1/templates", headers=self.h(), json={
            "name": "X", "pass": generic_template(), "images": {"../evil.png": base64.b64encode(b"x").decode()}})
        self.assertEqual(r.status_code, 422)

    def test_update_creates_version_and_keeps_approved(self):
        tid = self.make_template()
        changed = generic_template()
        changed["description"] = "Neue Karte"
        r = self.client.patch(f"/api/v1/templates/{tid}", headers=self.h(), json={"pass": changed})
        body = r.json()
        self.assertEqual(body["latest_version"]["number"], 2)
        self.assertEqual(body["latest_version"]["status"], "pending")
        self.assertEqual(body["approved_version"], 1)
        self.assertIn("icon@2x.png", body["images"])  # Bilder der Vorversion übernommen

    def test_test_pass_before_approval(self):
        tid = self.make_template(approve=False)
        r = self.client.post(f"/api/v1/templates/{tid}/test-pass", headers=self.h(),
                             json={"data": {"seit": "2026-02-01T00:00+01:00"}})
        self.assertEqual(r.status_code, 200, r.text)
        info = inspect_pkpass(r.content)
        self.assertEqual(info["problems"], [])
        self.assertIn("expirationDate", info["pass"])
        self.assertTrue(info["pass"]["serialNumber"].startswith("test-"))

    def test_other_tenant_cannot_see(self):
        tid = self.make_template()
        self.assertEqual(self.client.get(f"/api/v1/templates/{tid}", headers=self.h(self.other_key)).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/templates", headers=self.h(self.other_key)).json(), [])


class PassTests(ApiTestCase):
    def test_pending_template_cannot_issue(self):
        tid = self.make_template(approve=False)
        r = self.issue(tid)
        self.assertEqual(r.status_code, 409)

    def test_issue_and_download(self):
        tid = self.make_template()
        r = self.issue(tid)
        self.assertEqual(r.status_code, 201, r.text)
        p = r.json()
        self.assertTrue(p["page_url"].startswith("https://wallet.test/p/"))
        path = p["download_url"].replace("https://wallet.test", "")
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/vnd.apple.pkpass")
        info = inspect_pkpass(r.content)
        self.assertEqual(info["problems"], [])
        pj = info["pass"]
        self.assertEqual(pj["generic"]["primaryFields"][0]["value"], "Anna Beispiel")
        self.assertEqual(pj["barcodes"][0]["message"], "M-4711")
        self.assertEqual(pj["serialNumber"], p["serial_number"])
        self.assertEqual((pj["passTypeIdentifier"], pj["teamIdentifier"]), (PTI, TEAM))
        self.assertEqual(pj["organizationName"], "Beispiel GmbH")  # aus der Vorlage
        self.assertEqual(pj["webServiceURL"], "https://wallet.test/")
        self.assertGreaterEqual(len(pj["authenticationToken"]), 16)
        with zipfile.ZipFile(BytesIO(r.content)) as zf:
            self.assertIn("signature", zf.namelist())

    def test_data_checks(self):
        tid = self.make_template()
        r = self.client.post("/api/v1/passes", headers=self.h(), json={"template_id": tid, "data": {"name": "A"}})
        self.assertEqual(r.status_code, 422)
        self.assertIn("nummer", r.json()["error"])
        r = self.issue(tid, extra="x")
        self.assertEqual(r.status_code, 422)
        self.assertIn("extra", r.json()["error"])
        r = self.issue(tid, seit="kein Datum")
        self.assertEqual(r.status_code, 422)
        self.assertTrue(r.json()["issues"])

    def test_idempotency_and_serial(self):
        tid = self.make_template()
        body = {"template_id": tid, "serial_number": "M-1",
                "data": {"name": "A", "nummer": "1", "seit": "2026-01-15T00:00+01:00"}}
        r1 = self.client.post("/api/v1/passes", headers=self.h(**{"Idempotency-Key": "abc"}), json=body)
        r2 = self.client.post("/api/v1/passes", headers=self.h(**{"Idempotency-Key": "abc"}), json=body)
        self.assertEqual((r1.status_code, r2.status_code), (201, 200))
        self.assertEqual(r1.json()["id"], r2.json()["id"])
        r3 = self.client.post("/api/v1/passes", headers=self.h(), json=body)
        self.assertEqual(r3.status_code, 409)
        body["serial_number"] = "mit leerzeichen"
        self.assertEqual(self.client.post("/api/v1/passes", headers=self.h(), json=body).status_code, 422)

    def test_update_and_void(self):
        tid = self.make_template()
        p = self.issue(tid).json()
        r = self.client.patch(f"/api/v1/passes/{p['id']}", headers=self.h(), json={"data": {"name": "Bert"}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["version"], 2)
        self.assertEqual(r.json()["data"]["nummer"], "4711")
        r = self.client.get(f"/api/v1/passes/{p['id']}/pkpass", headers=self.h())
        self.assertEqual(inspect_pkpass(r.content)["pass"]["generic"]["primaryFields"][0]["value"], "Bert")
        # ungültige Änderung lässt den Pass unverändert
        r = self.client.patch(f"/api/v1/passes/{p['id']}", headers=self.h(), json={"data": {"seit": "nö"}})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.client.get(f"/api/v1/passes/{p['id']}", headers=self.h()).json()["version"], 2)

        r = self.client.post(f"/api/v1/passes/{p['id']}/void", headers=self.h())
        self.assertEqual(r.json()["status"], "voided")
        r = self.client.get(f"/api/v1/passes/{p['id']}/pkpass", headers=self.h())
        self.assertTrue(inspect_pkpass(r.content)["pass"]["voided"])
        page = self.client.get(p["page_url"].replace("https://wallet.test", ""))
        self.assertIn("nicht mehr gültig", page.text)
        r = self.client.patch(f"/api/v1/passes/{p['id']}", headers=self.h(), json={"data": {"name": "C"}})
        self.assertEqual(r.status_code, 409)

    def test_list_and_isolation(self):
        tid = self.make_template()
        p = self.issue(tid).json()
        self.issue(tid, nummer="2")
        listed = self.client.get(f"/api/v1/passes?template_id={tid}&limit=1", headers=self.h()).json()
        self.assertEqual(len(listed["items"]), 1)
        self.assertEqual(self.client.get(f"/api/v1/passes/{p['id']}", headers=self.h(self.other_key)).status_code, 404)
        r = self.client.post("/api/v1/passes", headers=self.h(self.other_key),
                             json={"template_id": tid, "data": {}})
        self.assertEqual(r.status_code, 404)

    def test_suspended_tenant(self):
        tid = self.make_template()
        with self.Session() as s:
            s.get(Tenant, self.tenant_id).status = "suspended"
            s.commit()
        self.assertEqual(self.issue(tid).status_code, 403)

    def test_landing_page(self):
        tid = self.make_template()
        p = self.issue(tid).json()
        path = p["page_url"].replace("https://wallet.test", "")
        r = self.client.get(path)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Mitgliedsausweis", r.text)
        self.assertIn("<svg", r.text)
        self.assertIn("pass.pkpass", r.text)
        self.assertIn("default-src 'none'", r.headers["content-security-policy"])
        self.assertEqual(self.client.get(path + "/logo.png").headers["content-type"], "image/png")
        self.assertEqual(self.client.get("/p/unbekannt").status_code, 404)

    def test_openapi(self):
        spec = self.client.get("/openapi.json").json()
        self.assertIn("/api/v1/passes", spec["paths"])
        self.assertNotIn("/p/{token}", spec["paths"])


class PlaceholderTests(unittest.TestCase):
    def test_render(self):
        tpl = {"a": "{{preis}}", "b": "Hallo {{ name }}!", "c": ["{{tags}}"], "d": 3}
        out = placeholders.render(tpl, {"preis": 12.5, "name": "Anna", "tags": ["x", "y"]})
        self.assertEqual(out, {"a": 12.5, "b": "Hallo Anna!", "c": [["x", "y"]], "d": 3})
        self.assertEqual(placeholders.find(tpl), ["name", "preis", "tags"])

    def test_check(self):
        self.assertEqual(placeholders.check(["a"], {"a": 1}), [])
        self.assertTrue(placeholders.check(["a"], {"a": {"x": 1}}))


class CertStoreTests(unittest.TestCase):
    def test_file_store_encrypts(self):
        with tempfile.TemporaryDirectory() as d:
            store = FileCertStore(d, Vault(Vault.generate_key()))
            store.save("pass.a.b", b"P12DATA", "pw")
            raw = open(os.path.join(d, "pass.a.b.cert")).read()
            self.assertNotIn(base64.b64encode(b"P12DATA").decode(), raw)
            self.assertEqual(store.load("pass.a.b"), (b"P12DATA", "pw"))
            with self.assertRaises(Exception):
                store.save("../x", b"", "")

    def test_openbao_store(self):
        saved = {}

        def handler(request):
            self.assertEqual(request.headers["X-Vault-Token"], "tok")
            if request.method == "POST":
                saved[request.url.path] = json.loads(request.content)["data"]
                return httpx.Response(200, json={})
            if request.url.path in saved:
                return httpx.Response(200, json={"data": {"data": saved[request.url.path]}})
            return httpx.Response(404, json={})

        client = httpx.Client(transport=httpx.MockTransport(handler), headers={"X-Vault-Token": "tok"})
        store = OpenBaoCertStore("http://bao", "tok", client=client)
        store.save("pass.a.b", b"P12", "pw")
        self.assertIn("/v1/secret/data/wallet/certs/pass.a.b", saved)
        self.assertEqual(store.load("pass.a.b"), (b"P12", "pw"))


if __name__ == "__main__":
    unittest.main()
