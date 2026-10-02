"""Small local web editor (stdlib only): ``creatwallet serve``."""

import base64
import hmac
import json
import signal
import uuid
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from urllib.parse import quote

from . import __version__
from . import validation as v
from .build import BuildError, build_pkpass, inspect_pkpass
from .templates import TEMPLATES, new_pass, placeholder_images

MAX_BODY = 25 * 1024 * 1024


def _decode_images(images):
    files = {}
    for name, b64 in (images or {}).items():
        if "/" in name.replace(".lproj/", "") or name.startswith(".") or ".." in name:
            raise ValueError(f"Ungültiger Dateiname: {name}")
        files[name] = base64.b64decode(b64)
    return files


def make_handler(signer, auth=None):
    """``auth`` is ``"user:password"`` to require HTTP Basic auth, or None."""
    expected = ("Basic " + base64.b64encode(auth.encode()).decode()).encode() if auth else None
    index_html = resources.files("creatwallet").joinpath("web/index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = f"creatwallet/{__version__}"

        def log_message(self, fmt, *args):  # quieter default logging
            print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

        def _send(self, status, body, content_type="application/json; charset=utf-8", headers=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, val in (headers or {}).items():
                self.send_header(k, val)
            self.end_headers()
            self.wfile.write(body)

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ValueError("Anfrage zu groß (max. 25 MB).")
            return self.rfile.read(length)

        def _authorized(self):
            if expected is None:
                return True
            given = (self.headers.get("Authorization") or "").encode()
            if hmac.compare_digest(given, expected):
                return True
            self._send(HTTPStatus.UNAUTHORIZED, {"error": "Anmeldung erforderlich"},
                       headers={"WWW-Authenticate": 'Basic realm="creatwallet", charset="UTF-8"'})
            return False

        def do_GET(self):
            if self.path == "/healthz":  # for Docker / load balancer health checks
                return self._send(HTTPStatus.OK, {"status": "ok"})
            if not self._authorized():
                return None
            if self.path in ("/", "/index.html"):
                return self._send(HTTPStatus.OK, index_html, "text/html; charset=utf-8")
            if self.path == "/api/status":
                return self._send(HTTPStatus.OK, {
                    "version": __version__,
                    "canSign": signer is not None,
                    "passTypeIdentifier": signer.pass_type_identifier if signer else None,
                    "teamIdentifier": signer.team_identifier if signer else None,
                })
            if self.path == "/api/templates":
                return self._send(HTTPStatus.OK, [
                    {"name": n, "title": t["title"], "minIOS": t["min_ios"]} for n, t in TEMPLATES.items()])
            if self.path.startswith("/api/template/"):
                name = self.path.rsplit("/", 1)[-1]
                if name not in TEMPLATES:
                    return self._send(HTTPStatus.NOT_FOUND, {"error": "Unbekannte Vorlage"})
                data = new_pass(name)
                if signer:
                    data["passTypeIdentifier"] = signer.pass_type_identifier or data["passTypeIdentifier"]
                    data["teamIdentifier"] = signer.team_identifier or data["teamIdentifier"]
                images = {k: base64.b64encode(b).decode() for k, b in placeholder_images(name, (2,)).items()}
                return self._send(HTTPStatus.OK, {"pass": data, "images": images})
            return self._send(HTTPStatus.NOT_FOUND, {"error": "Nicht gefunden"})

        def do_POST(self):
            if not self._authorized():
                return None
            try:
                if self.path == "/api/inspect":
                    info = inspect_pkpass(self._body())
                    images = {k: base64.b64encode(b).decode() for k, b in info["images"].items()}
                    return self._send(HTTPStatus.OK, {
                        "pass": info["pass"], "files": info["files"], "problems": info["problems"],
                        "certificates": info["certificates"], "images": images,
                        "issues": [asdict(i) for i in info["issues"]]})
                payload = json.loads(self._body() or b"{}")
                pass_data = payload.get("pass")
                files = _decode_images(payload.get("images"))
                if self.path == "/api/validate":
                    return self._send(HTTPStatus.OK, {"issues": [asdict(i) for i in v.validate(pass_data, files)]})
                if self.path == "/api/build":
                    if signer is None:
                        return self._send(HTTPStatus.CONFLICT, {"error": "Kein Signatur-Zertifikat geladen. "
                                          "Server mit --p12 (oder --cert/--key) und --wwdr starten."})
                    if not isinstance(pass_data, dict):
                        return self._send(HTTPStatus.BAD_REQUEST, {"error": "pass fehlt"})
                    overrides = {"serialNumber": uuid.uuid4().hex} if payload.get("newSerial") else None
                    try:
                        data, _ = build_pkpass(pass_data, files, signer, overrides=overrides,
                                               strict=not payload.get("force"))
                    except BuildError as exc:
                        return self._send(HTTPStatus.UNPROCESSABLE_ENTITY, {
                            "error": str(exc), "issues": [asdict(i) for i in exc.issues]})
                    filename = (pass_data.get("description") or "pass").replace('"', "")[:60] + ".pkpass"
                    return self._send(HTTPStatus.OK, data, "application/vnd.apple.pkpass", {
                        "Content-Disposition": f"attachment; filename=\"{_ascii(filename)}\"; "
                                               f"filename*=UTF-8''{quote(filename)}"})
                return self._send(HTTPStatus.NOT_FOUND, {"error": "Nicht gefunden"})
            except (ValueError, BuildError) as exc:
                return self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    return Handler


def _ascii(text):
    return text.encode("ascii", "replace").decode().replace("?", "_")


def serve(host, port, signer, auth=None):
    httpd = ThreadingHTTPServer((host, port), make_handler(signer, auth))
    # Docker stops containers with SIGTERM - shut down cleanly like on Ctrl+C.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    print(f"creatwallet Web-Editor läuft auf http://{host}:{port}  (Strg+C zum Beenden)")
    if auth:
        print("Anmeldung per Benutzername/Passwort ist aktiv.")
    elif host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNUNG: Der Editor ist ohne Passwort im Netzwerk erreichbar. "
              "Jeder könnte mit deinem Zertifikat Pässe signieren - --auth / CREATWALLET_AUTH setzen!")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")
    finally:
        httpd.server_close()
