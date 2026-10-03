"""Tests für Phase 2: Apple-Web-Service, Push-Worker, Webhooks."""

import json
from datetime import timedelta
from email.utils import format_datetime

import httpx

from app import apns, webhooks
from app.models import Device, Job, Pass, Registration, Template, WebhookEndpoint, utcnow
from app.security import Vault
from app.services import approve_version, update_template
from app.worker import Worker
from creatwallet.build import inspect_pkpass

from .helpers import PTI
from .test_api import ApiTestCase, generic_template


class FakePusher:
    def __init__(self, result=apns.OK):
        self.result = result
        self.sent = []

    def send(self, certificate, token):
        self.sent.append((certificate.pass_type_identifier, token))
        return self.result, "test"


class Phase2Case(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.tid = self.make_template()
        self.p = self.issue(self.tid).json()
        r = self.client.get(f"/api/v1/passes/{self.p['id']}/pkpass", headers=self.h())
        self.token = inspect_pkpass(r.content)["pass"]["authenticationToken"]
        self.serial = self.p["serial_number"]
        self.pusher = FakePusher()
        self.hooks = []
        self.hook_status = 200

        def hook_handler(request):
            self.hooks.append(request)
            return httpx.Response(self.hook_status)

        self.worker = Worker(self.Session, self.app_settings(), Vault(self.settings.secret_key), self.pusher,
                             httpx.Client(transport=httpx.MockTransport(hook_handler)))

    def app_settings(self):
        return self.client.app.state.settings

    def apple(self, token=None):
        return {"Authorization": f"ApplePass {token or self.token}"}

    def register(self, device="dev1", push="push1", token=None):
        return self.client.post(f"/v1/devices/{device}/registrations/{PTI}/{self.serial}",
                                headers=self.apple(token), json={"pushToken": push})

    def jobs(self, kind=None, status="pending"):
        with self.Session() as s:
            q = s.query(Job).filter_by(status=status)
            if kind:
                q = q.filter_by(kind=kind)
            return q.all()


class AppleWebServiceTests(Phase2Case):
    def test_register_and_unregister(self):
        self.assertEqual(self.register(token="falsch" * 4).status_code, 401)
        self.assertEqual(self.register().status_code, 201)
        self.assertEqual(self.register().status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/passes/{self.p['id']}", headers=self.h()).json()
                         ["installed_devices"], 1)
        r = self.client.delete(f"/v1/devices/dev1/registrations/{PTI}/{self.serial}", headers=self.apple())
        self.assertEqual(r.status_code, 200)
        with self.Session() as s:
            self.assertEqual(s.query(Registration).count(), 0)
            self.assertEqual(s.query(Device).count(), 0)

    def test_updated_serials(self):
        self.register()
        r = self.client.get(f"/v1/devices/dev1/registrations/{PTI}")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["serialNumbers"], [self.serial])
        tag = body["lastUpdated"]
        self.assertEqual(self.client.get(f"/v1/devices/dev1/registrations/{PTI}?passesUpdatedSince={tag}")
                         .status_code, 204)
        self.client.patch(f"/api/v1/passes/{self.p['id']}", headers=self.h(), json={"data": {"name": "Neu"}})
        r = self.client.get(f"/v1/devices/dev1/registrations/{PTI}?passesUpdatedSince={tag}")
        self.assertEqual(r.json()["serialNumbers"], [self.serial])
        self.assertEqual(self.client.get(f"/v1/devices/unbekannt/registrations/{PTI}").status_code, 204)

    def test_latest_pass_and_not_modified(self):
        self.assertEqual(self.client.get(f"/v1/passes/{PTI}/{self.serial}").status_code, 401)
        r = self.client.get(f"/v1/passes/{PTI}/{self.serial}", headers=self.apple())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/vnd.apple.pkpass")
        lm = r.headers["last-modified"]
        r = self.client.get(f"/v1/passes/{PTI}/{self.serial}", headers={**self.apple(), "If-Modified-Since": lm})
        self.assertEqual(r.status_code, 304)
        earlier = format_datetime(utcnow() - timedelta(days=1), usegmt=True)
        r = self.client.get(f"/v1/passes/{PTI}/{self.serial}", headers={**self.apple(), "If-Modified-Since": earlier})
        self.assertEqual(r.status_code, 200)

    def test_log(self):
        r = self.client.post("/v1/log", json={"logs": ["Fehler beim Laden"]})
        self.assertEqual(r.status_code, 200)

    def test_serial_unique_per_certificate(self):
        # Beide Test-Firmen nutzen dasselbe Zertifikat: Apple könnte die Pässe sonst nicht unterscheiden.
        other_tid = self.make_template(key=self.other_key)
        r = self.client.post("/api/v1/passes", headers=self.h(self.other_key), json={
            "template_id": other_tid, "serial_number": self.serial,
            "data": {"name": "X", "nummer": "1", "seit": "2026-01-15T00:00+01:00"}})
        self.assertEqual(r.status_code, 409)


class PushTests(Phase2Case):
    def test_update_pushes_registered_devices(self):
        r = self.client.patch(f"/api/v1/passes/{self.p['id']}", headers=self.h(), json={"data": {"name": "B"}})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.jobs("push"), [])  # noch kein Gerät -> kein Push
        self.register(push="tokenA")
        self.client.patch(f"/api/v1/passes/{self.p['id']}", headers=self.h(), json={"data": {"name": "C"}})
        self.assertEqual(len(self.jobs("push")), 1)
        self.worker.run_until_empty()
        self.assertEqual(self.pusher.sent, [(PTI, "tokenA")])
        self.assertEqual(len(self.jobs("push", status="done")), 1)

    def test_void_pushes(self):
        self.register()
        self.client.post(f"/api/v1/passes/{self.p['id']}/void", headers=self.h())
        self.worker.run_until_empty()
        self.assertEqual(len(self.pusher.sent), 1)

    def test_gone_device_is_removed(self):
        self.register()
        self.pusher.result = apns.GONE
        self.client.patch(f"/api/v1/passes/{self.p['id']}", headers=self.h(), json={"data": {"name": "C"}})
        self.worker.run_until_empty()
        with self.Session() as s:
            self.assertEqual(s.query(Registration).count(), 0)
            self.assertEqual(s.query(Device).count(), 0)

    def test_retry_only_failed_devices(self):
        self.register("dev1", "t1")
        self.register("dev2", "t2")
        self.pusher.result = apns.RETRY
        self.client.patch(f"/api/v1/passes/{self.p['id']}", headers=self.h(), json={"data": {"name": "C"}})
        self.worker.run_until_empty()
        retry = self.jobs("push")
        self.assertEqual(len(retry), 1)
        self.assertEqual(len(retry[0].payload["device_ids"]), 2)
        self.assertGreater(retry[0].run_after.replace(tzinfo=None), utcnow().replace(tzinfo=None))

    def test_template_approval_pushes_all_passes(self):
        self.register()
        with self.Session() as s:
            t = s.get(Template, self.tid)
            changed = generic_template()
            changed["description"] = "Neues Design"
            _, version = update_template(s, t, None, changed, None, self.app_settings())
            approve_version(version)
            s.commit()
            self.assertEqual(s.get(Pass, self.p["id"]).version, 2)
        self.worker.run_until_empty()
        self.assertEqual(len(self.pusher.sent), 1)
        r = self.client.get(f"/v1/passes/{PTI}/{self.serial}", headers=self.apple())
        self.assertEqual(inspect_pkpass(r.content)["pass"]["description"], "Neues Design")


class WebhookTests(Phase2Case):
    def setUp(self):
        super().setUp()
        self.client.app.state.settings = self.settings.__class__(
            **{**self.settings.__dict__, "allow_insecure_webhooks": True})
        self.worker.settings = self.client.app.state.settings

    def create_hook(self, events=("pass.installed", "pass.removed", "pass.updated")):
        r = self.client.post("/api/v1/webhooks", headers=self.h(),
                             json={"url": "http://hooks.test/wallet", "events": list(events)})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def test_create_list_delete(self):
        hook = self.create_hook()
        self.assertTrue(hook["secret"].startswith("whsec_"))
        listed = self.client.get("/api/v1/webhooks", headers=self.h()).json()
        self.assertNotIn("secret", listed[0])
        self.assertEqual(self.client.get("/api/v1/webhooks", headers=self.h(self.other_key)).json(), [])
        r = self.client.post("/api/v1/webhooks", headers=self.h(), json={"url": "http://x.test", "events": ["nope"]})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.client.delete(f"/api/v1/webhooks/{hook['id']}", headers=self.h()).status_code, 204)

    def test_installed_event_signed(self):
        hook = self.create_hook()
        self.register()
        self.worker.run_until_empty()
        self.assertEqual(len(self.hooks), 1)
        req = self.hooks[0]
        body = json.loads(req.content)
        self.assertEqual(body["type"], "pass.installed")
        self.assertEqual(body["data"]["serial_number"], self.serial)
        self.assertTrue(webhooks.verify(hook["secret"], req.content, req.headers["Wallet-Signature"]))
        self.assertFalse(webhooks.verify("whsec_falsch", req.content, req.headers["Wallet-Signature"]))

    def test_only_subscribed_events(self):
        self.create_hook(events=["pass.voided"])
        self.register()
        self.client.patch(f"/api/v1/passes/{self.p['id']}", headers=self.h(), json={"data": {"name": "C"}})
        self.worker.run_until_empty()
        self.assertEqual(self.hooks, [])

    def test_failed_delivery_is_retried(self):
        self.create_hook()
        self.hook_status = 500
        self.register()
        self.worker.run_until_empty()
        pending = self.jobs("webhook")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].attempts, 1)
        self.assertIn("500", pending[0].last_error)

    def test_test_endpoint(self):
        hook = self.create_hook()
        self.assertEqual(self.client.post(f"/api/v1/webhooks/{hook['id']}/test", headers=self.h()).status_code, 202)
        self.worker.run_until_empty()
        self.assertEqual(json.loads(self.hooks[0].content)["type"], "webhook.test")

    def test_ssrf_protection(self):
        for url in ("https://127.0.0.1/x", "https://10.0.0.5/x", "http://example.com/x", "https://[::1]/x"):
            with self.assertRaises(webhooks.UnsafeUrl, msg=url):
                webhooks.check_url(url)
        with self.Session() as s:
            self.assertEqual(s.query(WebhookEndpoint).count(), 0)


class ApnsClassifyTests(Phase2Case):
    def test_classify(self):
        self.assertEqual(apns.classify(200)[0], apns.OK)
        self.assertEqual(apns.classify(410, "Unregistered")[0], apns.GONE)
        self.assertEqual(apns.classify(400, "BadDeviceToken")[0], apns.GONE)
        self.assertEqual(apns.classify(503)[0], apns.RETRY)
        self.assertEqual(apns.classify(403, "BadCertificate")[0], apns.ERROR)

    def test_ssl_context_from_store(self):
        pusher = apns.ApnsPusher(self.signers.store)
        ctx = pusher._ssl_context(PTI)
        self.assertIsNotNone(ctx)
