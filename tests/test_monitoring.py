"""Tests für /metrics, /readyz und die Messwerte."""

from app.config import Settings

from .test_api import ApiTestCase


class MonitoringTests(ApiTestCase):
    def test_metrics_need_internal_network_or_token(self):
        # Der Testclient hat keine IP-Adresse - gilt als extern
        self.assertEqual(self.client.get("/metrics").status_code, 403)
        self.client.app.state.settings = Settings(**{**self.settings.__dict__, "metrics_token": "geheim"})
        self.assertEqual(self.client.get("/metrics", headers={"Authorization": "Bearer falsch"}).status_code, 403)

    def test_metrics_content(self):
        tid = self.make_template()
        self.issue(tid)
        app = self.client.app
        app.state.settings = Settings(**{**self.settings.__dict__, "metrics_token": "geheim"})
        r = self.client.get("/metrics", headers={"Authorization": "Bearer geheim"})
        self.assertEqual(r.status_code, 200)
        body = r.text
        self.assertIn('wallet_passes{status="active"} 1.0', body)
        self.assertIn("wallet_passes_issued_total", body)
        self.assertIn('wallet_certificate_expiry_seconds{kind="standard",pass_type_identifier="pass.com.example.test"}', body)
        self.assertIn('wallet_tenants{status="active"} 2.0', body)
        self.assertIn('route="/api/v1/passes"', body)
        self.assertIn("wallet_signing_seconds_bucket", body)

    def test_readyz(self):
        self.assertEqual(self.client.get("/readyz").json(), {"ok": True})
