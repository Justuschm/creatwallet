"""Messwerte für Prometheus und Fehlerberichte an GlitchTip (Sentry-kompatibel).

Zähler (Anfragen, ausgegebene Pässe, Pushes …) entstehen dort, wo etwas passiert. Zustände
(Jobs in der Warteschlange, Zertifikats-Laufzeiten, Firmen) werden bei jedem Abruf von
``/metrics`` aus der Datenbank gelesen, damit sie über alle Prozesse stimmen.

Die API läuft mit mehreren Prozessen: Dafür nutzt prometheus_client den Multiprozess-Modus,
sobald ``PROMETHEUS_MULTIPROC_DIR`` gesetzt ist (im Docker-Image der Fall).
"""

import os
import time

from prometheus_client import (CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest,
                               start_http_server)
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import func, select

HTTP_REQUESTS = Counter("wallet_http_requests_total", "HTTP-Anfragen", ["method", "route", "status"])
HTTP_SECONDS = Histogram("wallet_http_request_seconds", "Dauer von HTTP-Anfragen", ["route"],
                         buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
PASSES_ISSUED = Counter("wallet_passes_issued_total", "Ausgegebene Pässe")
PASS_UPDATES = Counter("wallet_pass_updates_total", "Geänderte oder gesperrte Pässe", ["kind"])
PKPASS_BUILDS = Counter("wallet_pkpass_builds_total", "Erzeugte .pkpass-Dateien", ["source"])
SIGNING_SECONDS = Histogram("wallet_signing_seconds", "Dauer für Bauen und Signieren eines Passes",
                            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1))
APNS_PUSHES = Counter("wallet_apns_push_total", "Push-Nachrichten an Apple", ["result"])
WEBHOOK_DELIVERIES = Counter("wallet_webhook_deliveries_total", "Webhook-Zustellungen", ["result"])
DEVICE_REGISTRATIONS = Counter("wallet_device_registrations_total", "An- und Abmeldungen von Geräten", ["action"])


class DatabaseCollector:
    """Zustände aus der Datenbank, gelesen bei jedem Abruf."""

    def __init__(self, sessionmaker):
        self.Session = sessionmaker

    def collect(self):
        from .models import Certificate, Job, Pass, Registration, Tenant, utcnow

        with self.Session() as s:
            jobs = GaugeMetricFamily("wallet_jobs", "Jobs nach Status", labels=["status", "kind"])
            for status, kind, n in s.execute(select(Job.status, Job.kind, func.count()).group_by(Job.status, Job.kind)):
                jobs.add_metric([status, kind], n)
            yield jobs

            oldest = s.scalar(select(func.min(Job.run_after)).where(Job.status == "pending"))
            age = GaugeMetricFamily("wallet_job_oldest_pending_seconds", "Wartezeit des ältesten fälligen Jobs")
            age.add_metric([], max(0.0, (utcnow() - _aware(oldest)).total_seconds()) if oldest else 0.0)
            yield age

            certs = GaugeMetricFamily("wallet_certificate_expiry_seconds", "Restlaufzeit der Zertifikate",
                                      labels=["pass_type_identifier", "kind"])
            for c in s.scalars(select(Certificate)):
                certs.add_metric([c.pass_type_identifier, "own" if c.owner_tenant_id else "standard"],
                                 (_aware(c.expires_at) - utcnow()).total_seconds())
            yield certs

            tenants = GaugeMetricFamily("wallet_tenants", "Firmen nach Status", labels=["status"])
            for status, n in s.execute(select(Tenant.status, func.count()).group_by(Tenant.status)):
                tenants.add_metric([status], n)
            yield tenants

            passes = GaugeMetricFamily("wallet_passes", "Pässe nach Status", labels=["status"])
            for status, n in s.execute(select(Pass.status, func.count()).group_by(Pass.status)):
                passes.add_metric([status], n)
            yield passes

            installs = GaugeMetricFamily("wallet_installations", "Pässe, die in Wallets liegen (Registrierungen)")
            installs.add_metric([], s.scalar(select(func.count(Registration.id))) or 0)
            yield installs


def _aware(dt):
    from datetime import timezone
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def render_metrics(sessionmaker):
    """Inhalt für /metrics: Zähler (aller Prozesse) plus Zustände aus der Datenbank."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        from prometheus_client import multiprocess

        counters = CollectorRegistry()
        multiprocess.MultiProcessCollector(counters)
    else:
        from prometheus_client import REGISTRY as counters
    state = CollectorRegistry()
    state.register(DatabaseCollector(sessionmaker))
    return generate_latest(counters) + generate_latest(state), CONTENT_TYPE_LATEST


def start_worker_metrics(port):
    """Der Worker ist ein einzelner Prozess mit eigenem kleinen Metrik-Server."""
    if port:
        start_http_server(port)


class Timer:
    def __init__(self, histogram):
        self.histogram = histogram

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.histogram.observe(time.perf_counter() - self.start)


def init_error_reporting(dsn, environment="production", component="api"):
    """Fehler an GlitchTip melden (Sentry-Protokoll). Ohne DSN passiert nichts."""
    if not dsn:
        return
    import sentry_sdk

    sentry_sdk.init(dsn=dsn, environment=environment, send_default_pii=False, traces_sample_rate=0.0,
                    server_name=component)
