import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from creatwallet import (BuildError, SigningError, TEMPLATES, build_pkpass, inspect_pkpass,
                         load_bundle, load_signer, new_pass, placeholder_images, write_project)

from .helpers import PTI, TEAM, make_credentials

CREDS = make_credentials()


class SignerTests(unittest.TestCase):
    def test_p12(self):
        signer = load_signer(CREDS["wwdr_der"], p12=CREDS["p12"], password=CREDS["password"])
        self.assertEqual(signer.pass_type_identifier, PTI)
        self.assertEqual(signer.team_identifier, TEAM)

    def test_pem(self):
        signer = load_signer(CREDS["wwdr"], cert=CREDS["cert"], key=CREDS["key"], password=CREDS["password"])
        self.assertEqual(signer.pass_type_identifier, PTI)

    def test_wrong_password(self):
        with self.assertRaises(SigningError):
            load_signer(CREDS["wwdr"], p12=CREDS["p12"], password="falsch")

    def test_mismatched_key(self):
        other = make_credentials()
        with self.assertRaises(SigningError):
            load_signer(CREDS["wwdr"], cert=CREDS["cert"], key=other["key"], password=other["password"])

    def test_missing_wwdr(self):
        with self.assertRaises(SigningError):
            load_signer(None, p12=CREDS["p12"], password=CREDS["password"])


class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signer = load_signer(CREDS["wwdr"], p12=CREDS["p12"], password=CREDS["password"])
        cls.tmp = Path(tempfile.mkdtemp())
        (cls.tmp / "wwdr.pem").write_bytes(CREDS["wwdr"])

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def _build(self, name):
        p = new_pass(name)
        del p["passTypeIdentifier"], p["teamIdentifier"]  # filled from the certificate
        return build_pkpass(p, placeholder_images(name), self.signer)[0]

    def test_all_templates_build_and_verify(self):
        for name in TEMPLATES:
            with self.subTest(template=name):
                data = self._build(name)
                zf = zipfile.ZipFile(io.BytesIO(data))
                names = set(zf.namelist())
                self.assertTrue({"pass.json", "manifest.json", "signature", "icon.png"} <= names)
                manifest = json.loads(zf.read("manifest.json"))
                self.assertEqual(set(manifest), names - {"manifest.json", "signature"})
                for fname, digest in manifest.items():
                    self.assertEqual(hashlib.sha1(zf.read(fname)).hexdigest(), digest)
                pass_json = json.loads(zf.read("pass.json"))
                self.assertEqual(pass_json["passTypeIdentifier"], PTI)
                self.assertEqual(pass_json["teamIdentifier"], TEAM)
                info = inspect_pkpass(data)
                self.assertEqual(info["problems"], [])
                self.assertEqual(len(info["certificates"]), 2)  # signer + WWDR
                self._verify_with_openssl(zf)

    def _verify_with_openssl(self, zf):
        if not shutil.which("openssl"):
            self.skipTest("openssl nicht installiert")
        (self.tmp / "manifest.json").write_bytes(zf.read("manifest.json"))
        (self.tmp / "signature").write_bytes(zf.read("signature"))
        result = subprocess.run(
            ["openssl", "cms", "-verify", "-binary", "-inform", "DER", "-in", str(self.tmp / "signature"),
             "-content", str(self.tmp / "manifest.json"), "-CAfile", str(self.tmp / "wwdr.pem"),
             "-purpose", "any", "-out", "/dev/null"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_pass_type_mismatch_fails(self):
        p = new_pass("generic")
        p["passTypeIdentifier"] = "pass.com.other"
        with self.assertRaises(BuildError) as ctx:
            build_pkpass(p, placeholder_images("generic"), self.signer)
        self.assertIn("passTypeIdentifier", {i.path for i in ctx.exception.issues})

    def test_errors_block_build_unless_not_strict(self):
        p = new_pass("generic")
        p["passTypeIdentifier"], p["teamIdentifier"] = PTI, TEAM
        p["backgroundColor"] = "rot"
        with self.assertRaises(BuildError):
            build_pkpass(p, placeholder_images("generic"), self.signer)
        data, issues = build_pkpass(p, placeholder_images("generic"), self.signer, strict=False)
        self.assertTrue(data.startswith(b"PK"))

    def test_tampered_pass_detected(self):
        data = self._build("coupon")
        src = zipfile.ZipFile(io.BytesIO(data))
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as dst:
            for n in src.namelist():
                dst.writestr(n, b"{}" if n == "pass.json" else src.read(n))
        self.assertTrue(any("pass.json" in p for p in inspect_pkpass(out.getvalue())["problems"]))

    def test_project_roundtrip_with_localization(self):
        project = write_project("eventTicket", self.tmp / "ticket.pass", PTI, TEAM)
        (project / "de.lproj").mkdir()
        (project / "de.lproj" / "pass.strings").write_text('"Info" = "Info";\n', encoding="utf-16")
        (project / ".DS_Store").write_bytes(b"x")
        pass_data, files = load_bundle(project)
        self.assertIn("de.lproj/pass.strings", files)
        self.assertNotIn(".DS_Store", files)
        data, _ = build_pkpass(pass_data, files, self.signer)
        self.assertIn("de.lproj/pass.strings", zipfile.ZipFile(io.BytesIO(data)).namelist())


if __name__ == "__main__":
    unittest.main()
