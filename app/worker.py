"""Worker für Hintergrundaufgaben: ``python -m app worker``.

Holt fällige Jobs aus der Datenbank (bei PostgreSQL mit ``FOR UPDATE SKIP LOCKED``, damit
mehrere Worker parallel laufen können), führt sie aus und plant Wiederholungen mit
wachsendem Abstand.
"""

import logging
import time
from datetime import timedelta

import httpx
from sqlalchemy import delete, select, update

from . import apns, jobs, metrics, webhooks
from .models import Device, Job, Pass, WebhookEndpoint, utcnow

log = logging.getLogger("wallet.worker")

BACKOFF = (60, 300, 1800, 7200, 21600, 43200)  # Sekunden: 1 min ... 12 h, danach aufgeben
STALE_AFTER = timedelta(minutes=10)
KEEP_DONE = timedelta(days=7)


class Retry(Exception):
    pass


class Worker:
    def __init__(self, sessionmaker, settings, vault, pusher, http_client=None):
        self.Session = sessionmaker
        self.settings = settings
        self.vault = vault
        self.pusher = pusher
        self.http = http_client or httpx.Client(timeout=10)
        self._last_maintenance = 0.0

    # ------------------------------------------------------------ Schleife

    def run_forever(self, poll_seconds=1.0):
        log.info("Worker gestartet")
        while True:
            try:
                if time.monotonic() - self._last_maintenance > 300:
                    self.maintenance()
                if not self.run_once():
                    time.sleep(poll_seconds)
            except KeyboardInterrupt:
                log.info("Worker beendet")
                return
            except Exception:  # noqa: BLE001 - der Worker darf nicht sterben
                log.exception("Unerwarteter Fehler im Worker")
                time.sleep(5)

    def run_until_empty(self, limit=1000):
        """Alle fälligen Jobs abarbeiten (für Tests und Kommandozeile)."""
        n = 0
        while n < limit and self.run_once():
            n += 1
        return n

    def maintenance(self):
        self._last_maintenance = time.monotonic()
        now = utcnow()
        with self.Session() as s:
            # Jobs eines abgestürzten Workers wieder freigeben, alte erledigte löschen
            s.execute(update(Job).where(Job.status == "running", Job.updated_at < now - STALE_AFTER)
                      .values(status="pending"))
            s.execute(delete(Job).where(Job.status == "done", Job.updated_at < now - KEEP_DONE))
            s.commit()

    def _claim(self, s):
        q = (select(Job).where(Job.status == "pending", Job.run_after <= utcnow())
             .order_by(Job.run_after).limit(1))
        if s.bind.dialect.name == "postgresql":
            q = q.with_for_update(skip_locked=True)
        job = s.scalars(q).first()
        if job is None:
            return None
        job.status = "running"
        job.attempts += 1
        job.updated_at = utcnow()
        s.commit()
        return job

    def run_once(self):
        with self.Session() as s:
            job = self._claim(s)
            if job is None:
                return False
            try:
                handler = {jobs.PUSH: self._push, jobs.WEBHOOK: self._webhook}[job.kind]
                handler(s, job.payload)
            except Retry as exc:
                s.rollback()
                self._reschedule(s, job.id, str(exc))
            except Exception as exc:  # noqa: BLE001
                s.rollback()
                log.exception("Job %s (%s) fehlgeschlagen", job.id, job.kind)
                self._reschedule(s, job.id, f"{type(exc).__name__}: {exc}")
            else:
                job.status = "done"
                job.last_error = ""
                job.updated_at = utcnow()
                s.commit()
            return True

    def _reschedule(self, s, job_id, error):
        job = s.get(Job, job_id)
        job.last_error = error[:2000]
        job.updated_at = utcnow()
        if job.attempts > len(BACKOFF):
            job.status = "failed"
            log.warning("Job %s endgültig fehlgeschlagen: %s", job.id, error)
        else:
            job.status = "pending"
            job.run_after = utcnow() + timedelta(seconds=BACKOFF[job.attempts - 1])
        s.commit()

    # ------------------------------------------------------------ Push

    def _push(self, s, payload):
        p = s.get(Pass, payload["pass_id"])
        cert = (p.certificate or p.tenant.certificate) if p is not None else None
        if cert is None:
            return
        regs = list(p.registrations)
        if payload.get("device_ids") is not None:
            regs = [r for r in regs if r.device_id in payload["device_ids"]]
        failed, last_reason = [], ""
        for reg in regs:
            result, reason = self.pusher.send(cert, reg.device.push_token)
            metrics.APNS_PUSHES.labels(result).inc()
            if result == apns.GONE:
                log.info("Gerät abgemeldet (%s) - Registrierung entfernt", reason)
                jobs.emit(s, p.tenant_id, "pass.removed", jobs.pass_event_data(p))
                s.delete(reg)
            elif result == apns.RETRY:
                log.warning("Push für Pass %s wird wiederholt: %s", p.id, reason)
                failed.append(reg.device_id)
                last_reason = reason
            elif result == apns.ERROR:
                log.warning("APNs-Fehler für Pass %s: %s", p.id, reason)
        s.flush()
        _remove_orphan_devices(s)
        if failed:
            # Nur die fehlgeschlagenen Geräte später erneut versuchen.
            retry = jobs.enqueue(s, jobs.PUSH, {"pass_id": p.id, "device_ids": failed,
                                                "round": payload.get("round", 0) + 1},
                                 delay_seconds=BACKOFF[min(payload.get("round", 0), len(BACKOFF) - 1)],
                                 tenant_id=p.tenant_id)
            retry.last_error = last_reason[:2000]
            if payload.get("round", 0) + 1 > len(BACKOFF):
                retry.status = "failed"

    # ------------------------------------------------------------ Webhook

    def _webhook(self, s, payload):
        ep = s.get(WebhookEndpoint, payload["endpoint_id"])
        if ep is None or not ep.active:
            return
        ok, error = webhooks.deliver(self.http, ep.url, self.vault.decrypt(ep.secret_enc), payload["body"],
                                     allow_insecure=self.settings.allow_insecure_webhooks)
        metrics.WEBHOOK_DELIVERIES.labels("ok" if ok else "error").inc()
        if not ok:
            raise Retry(error)


def _remove_orphan_devices(s):
    s.execute(delete(Device).where(~Device.registrations.any()))
