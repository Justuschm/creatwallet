import base64
import contextlib
import io
import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

from creatwallet import load_signer
from creatwallet.cli import main
from creatwallet.server import make_handler

from .helpers import PTI, TEAM, make_credentials

CREDS = make_credentials()


def run_cli(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = main(list(args))
    return code, out.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for name in ("p12", "wwdr"):
            (self.tmp / name).write_bytes(CREDS[name])

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_init_validate_build_inspect(self):
        project = self.tmp / "konzert.pass"
        code, out = run_cli("init", "posterEventTicket", str(project), "--pass-type-id", PTI, "--team-id", TEAM)
        self.assertEqual(code, 0, out)
        code, out = run_cli("validate", str(project))
        self.assertEqual(code, 0, out)
        target = self.tmp / "konzert.pkpass"
        code, out = run_cli("build", str(project), "-o", str(target), "--p12", str(self.tmp / "p12"),
                            "--password", CREDS["password"], "--wwdr", str(self.tmp / "wwdr"),
                            "--set", "serialNumber=A-1", "--set", "semantics.attendeeName=Anna Beispiel")
        self.assertEqual(code, 0, out)
        pass_json = json.loads(zipfile.ZipFile(target).read("pass.json"))
        self.assertEqual(pass_json["serialNumber"], "A-1")
        self.assertEqual(pass_json["semantics"]["attendeeName"], "Anna Beispiel")
        self.assertEqual(pass_json["semantics"]["venueName"], "Arena Hamburg")  # deep merge keeps siblings
        code, out = run_cli("inspect", str(target))
        self.assertEqual(code, 0, out)

    def test_build_fails_on_errors(self):
        project = self.tmp / "p.pass"
        run_cli("init", "generic", str(project), "--pass-type-id", PTI, "--team-id", TEAM)
        code, out = run_cli("build", str(project), "-o", str(self.tmp / "x.pkpass"), "--p12", str(self.tmp / "p12"),
                            "--password", CREDS["password"], "--wwdr", str(self.tmp / "wwdr"),
                            "--set", "backgroundColor=blau")
        self.assertEqual(code, 1)
        self.assertIn("backgroundColor", out)
        self.assertFalse((self.tmp / "x.pkpass").exists())

    def test_init_refuses_non_empty_dir(self):
        project = self.tmp / "p.pass"
        run_cli("init", "generic", str(project))
        code, out = run_cli("init", "generic", str(project))
        self.assertEqual(code, 1)

    def test_templates(self):
        code, out = run_cli("templates")
        self.assertIn("posterGeneric", out)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        signer = load_signer(CREDS["wwdr"], p12=CREDS["p12"], password=CREDS["password"])
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(signer))
        cls.httpd.RequestHandlerClass.log_message = lambda *a: None
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def req(self, path, body=None):
        data = json.dumps(body).encode() if isinstance(body, dict) else body
        with urllib.request.urlopen(urllib.request.Request(self.base + path, data=data)) as r:
            return r.status, r.headers, r.read()

    def test_index_and_status(self):
        status, _, body = self.req("/")
        self.assertIn(b"creatwallet", body)
        status = json.loads(self.req("/api/status")[2])
        self.assertTrue(status["canSign"])
        self.assertEqual(status["passTypeIdentifier"], PTI)

    def test_template_validate_build_inspect(self):
        tpl = json.loads(self.req("/api/template/semanticBoardingPass")[2])
        self.assertEqual(tpl["pass"]["passTypeIdentifier"], PTI)
        issues = json.loads(self.req("/api/validate", tpl)[2])["issues"]
        self.assertFalse([i for i in issues if i["level"] == "error"])
        status, headers, data = self.req("/api/build", tpl)
        self.assertEqual(headers["Content-Type"], "application/vnd.apple.pkpass")
        self.assertTrue(data.startswith(b"PK"))
        info = json.loads(self.req("/api/inspect", data)[2])
        self.assertEqual(info["problems"], [])
        self.assertIn("icon@2x.png", info["images"])

    def test_build_with_errors_returns_issues(self):
        tpl = json.loads(self.req("/api/template/coupon")[2])
        tpl["pass"]["expirationDate"] = "bald"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.req("/api/build", tpl)
        self.assertEqual(ctx.exception.code, 422)
        body = json.loads(ctx.exception.read())
        self.assertIn("expirationDate", {i["path"] for i in body["issues"]})

    def test_rejects_path_traversal(self):
        body = {"pass": {}, "images": {"../evil.png": base64.b64encode(b"x").decode()}}
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.req("/api/validate", body)
        self.assertEqual(ctx.exception.code, 400)


if __name__ == "__main__":
    unittest.main()


class AuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(None, auth="admin:geheim"))
        cls.httpd.RequestHandlerClass.log_message = lambda *a: None
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path, credentials=None, data=None):
        req = urllib.request.Request(self.base + path, data=data)
        if credentials:
            req.add_header("Authorization", "Basic " + base64.b64encode(credentials.encode()).decode())
        with urllib.request.urlopen(req) as r:
            return r.status

    def test_requires_login(self):
        for path, data in (("/", None), ("/api/status", None), ("/api/validate", b"{}")):
            with self.subTest(path=path), self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get(path, data=data)
            self.assertEqual(ctx.exception.code, 401)
            self.assertIn("Basic", ctx.exception.headers["WWW-Authenticate"])

    def test_wrong_password(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/", "admin:falsch")
        self.assertEqual(ctx.exception.code, 401)

    def test_correct_password(self):
        self.assertEqual(self.get("/", "admin:geheim"), 200)

    def test_healthcheck_is_public(self):
        self.assertEqual(self.get("/healthz"), 200)
