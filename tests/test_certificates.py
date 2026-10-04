"""Standard-Zertifikate der Plattform und eigene Zertifikate von Firmen."""

from app.models import Certificate, Pass, Tenant
from creatwallet.build import inspect_pkpass

from .helpers import PTI, make_credentials
from .test_web import WebCase

OWN_PTI = "pass.de.kino.eigen"


class CertificateTests(WebCase):
    approval = False

    def setUp(self):
        super().setUp()
        self.kino = self.company()
        self.admin = self.activate()
        self.kino_id = self.tenant().id
        self.post(self.kino, "/portal/templates", {"name": "Karte", "base": "generic"})
        with self.Session() as s:
            from app.models import Template
            self.tid = s.query(Template).one().id

    def upload_own(self, tenant_id, pti=OWN_PTI, activate=True):
        creds = make_credentials(b"pw", pti=pti, team="KINO123456")
        data = {"password": "pw", **({"activate": "true"} if activate else {})}
        return self.post(self.admin, f"/admin/tenants/{tenant_id}/certificate/upload", data, page="/admin",
                         files={"p12": ("eigen.p12", creds["p12"], "application/x-pkcs12")})

    def issue(self):
        r = self.post(self.kino, "/portal/passes", {"template_id": self.tid})
        self.assertIn("Pass ausgegeben", r.text)
        with self.Session() as s:
            return s.query(Pass).order_by(Pass.created_at.desc()).first()

    def test_tab_shows_standard_certificate(self):
        page = self.admin.get(f"/admin/tenants/{self.kino_id}/certificate").text
        self.assertIn("Standard (Plattform)", page)
        self.assertIn(PTI, page)

    def test_own_certificate_upload_and_switch_keeps_old_passes(self):
        old_pass = self.issue()
        r = self.upload_own(self.kino_id)
        self.assertIn("Eigenes Zertifikat", r.text)
        self.assertIn(OWN_PTI, r.text)
        with self.Session() as s:
            cert = s.query(Certificate).filter_by(pass_type_identifier=OWN_PTI).one()
            self.assertEqual(cert.owner_tenant_id, self.kino_id)
            self.assertEqual(s.get(Tenant, self.kino_id).certificate_id, cert.id)
        new_pass = self.issue()
        # Neuer Pass: eigenes Zertifikat. Alter Pass: bleibt beim Standard-Zertifikat (Apple kennt ihn so).
        dl = lambda p: inspect_pkpass(self.kino.get(f"/p/{p.download_token}/pass.pkpass").content)["pass"]
        self.assertEqual(dl(new_pass)["passTypeIdentifier"], OWN_PTI)
        self.assertEqual(dl(old_pass)["passTypeIdentifier"], PTI)
        # Apple-Web-Service findet den alten Pass weiterhin über die alte Pass Type ID
        token = dl(old_pass)["authenticationToken"]
        r = self.kino.get(f"/v1/passes/{PTI}/{old_pass.serial_number}", headers={"Authorization": f"ApplePass {token}"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(inspect_pkpass(r.content)["pass"]["passTypeIdentifier"], PTI)
        # Übersicht im Firmenbereich und Zertifikatsliste zeigen die Herkunft
        self.assertIn("Eigenes Zertifikat der Firma", self.admin.get(f"/admin/tenants/{self.kino_id}").text)
        page = self.admin.get("/admin/certificates").text
        self.assertIn("Standard (Plattform)", page)
        self.assertIn("Eigenes Zertifikat der Firma", page)
        self.assertIn("Eigenes Zertifikat der Firma", self.kino.get("/portal/settings").text)

    def test_own_certificate_cannot_be_used_by_other_company(self):
        self.upload_own(self.kino_id)
        self.company("eva@zoo.test", "Zoo GmbH")
        zoo_id = self.tenant("Zoo GmbH").id
        with self.Session() as s:
            own_id = s.query(Certificate).filter_by(pass_type_identifier=OWN_PTI).one().id
        r = self.post(self.admin, f"/admin/tenants/{zoo_id}/certificate", {"certificate_id": own_id}, page="/admin")
        self.assertIn("gehört einer anderen Firma", r.text)
        with self.Session() as s:
            self.assertIsNone(s.get(Tenant, zoo_id).certificate_id)
        # Dieselbe Pass Type ID als eigenes Zertifikat einer anderen Firma: abgelehnt
        r = self.upload_own(zoo_id)
        self.assertIn("gehört bereits einer anderen Firma", r.text)
        # Und kein Standard-Zertifikat kann zum eigenen einer Firma werden
        r = self.upload_own(zoo_id, pti=PTI)
        self.assertIn("bereits ein Standard-Zertifikat", r.text)

    def test_upload_without_activation(self):
        self.upload_own(self.kino_id, activate=False)
        with self.Session() as s:
            t = s.get(Tenant, self.kino_id)
            self.assertEqual(t.certificate.pass_type_identifier, PTI)
        page = self.admin.get(f"/admin/tenants/{self.kino_id}/certificate").text
        self.assertIn("Aktivieren", page)
