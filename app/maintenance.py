"""Tägliche Wartung (läuft im Worker, einmal pro Tag; manuell: ``python -m app daily``).

- Verbrauch je Firma festhalten (Grundlage für Abrechnung und Kontingent)
- Kontingent-Warnungen bei 80 % und 100 % (einmal pro Monat und Stufe)
- Warnungen vor ablaufenden Zertifikaten (30 und 7 Tage vorher)
- Aufräumen: alte API-Protokolle, Rate-Limit-Zähler, abgelaufene Einladungen, alte Massenausgaben
"""

import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select

from . import mailer, plans
from .models import (ApiRequestLog, BulkIssue, Certificate, Invitation, Pass, RateCounter, Registration, SystemState,
                     Tenant, UsageDaily, User)

log = logging.getLogger("wallet.daily")
KEEP_API_LOG = timedelta(days=30)
KEEP_BULK = timedelta(days=30)


def _aware(dt):
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def _state(session, key):
    row = session.get(SystemState, key)
    return row.value if row else ""


def _set_state(session, key, value):
    row = session.get(SystemState, key)
    if row is None:
        session.add(SystemState(key=key, value=value))
    else:
        row.value = value


def due(session, now=None):
    now = now or datetime.now(timezone.utc)
    return _state(session, "daily_last_run") != now.strftime("%Y-%m-%d")


def run_daily(session, settings, now=None, force=False):
    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    if not force and _state(session, "daily_last_run") == today:
        return {}
    summary = {"usage": _snapshot_usage(session, now), "quota_mails": _quota_warnings(session, settings, now),
               "certificate_mails": _certificate_warnings(session, settings, now)}
    summary.update(_cleanup(session, now))
    _set_state(session, "daily_last_run", today)
    session.commit()
    log.info("Tägliche Wartung: %s", summary)
    return summary


def _snapshot_usage(session, now):
    day = now.strftime("%Y-%m-%d")
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    n = 0
    for t in session.scalars(select(Tenant)):
        active = session.scalar(select(func.count(Pass.id)).where(Pass.tenant_id == t.id, Pass.status == "active"))
        issued = session.scalar(select(func.count(Pass.id)).where(Pass.tenant_id == t.id, Pass.created_at >= start))
        installed = session.scalar(select(func.count(Registration.id)).join(Pass).where(Pass.tenant_id == t.id))
        row = session.scalars(select(UsageDaily).where(UsageDaily.tenant_id == t.id, UsageDaily.day == day)).first()
        if row is None:
            session.add(UsageDaily(tenant_id=t.id, day=day, active=active, issued=issued, installed=installed))
        else:
            row.active, row.issued, row.installed = max(row.active, active), issued, installed
        n += 1
    return n


def _recipients(session, tenant):
    users = session.scalars(select(User).where(User.tenant_id == tenant.id, User.role.in_(("owner", "admin")))).all()
    emails = {u.email for u in users if u.email}
    if not emails and tenant.contact_email:
        emails.add(tenant.contact_email)
    return sorted(emails)


def _quota_warnings(session, settings, now):
    month, sent = now.strftime("%Y-%m"), 0
    for t in session.scalars(select(Tenant).where(Tenant.status == "active")):
        u = plans.usage(session, t, now)
        if not u["plan"].included:
            continue
        level = 100 if u["percent"] >= 100 else 80 if u["percent"] >= 80 else 0
        notice = f"{month}:{level}"
        if not level or (t.quota_notice.startswith(month) and int(t.quota_notice.split(":")[1]) >= level):
            continue
        for to in _recipients(session, t):
            mailer.queue_mail(session, to, f"Kontingent zu {u['percent']} % genutzt",
                              mailer.quota_text(t.name, u["plan"].label, u["active"], u["plan"].included,
                                                u["percent"]), tenant_id=t.id)
            sent += 1
        t.quota_notice = notice
    return sent


def _certificate_warnings(session, settings, now):
    sent = 0
    for c in session.scalars(select(Certificate)):
        days = (_aware(c.expires_at) - now).days
        level = 7 if days <= 7 else 30 if days <= 30 else 0
        key = f"cert:{c.id}:{level}"
        if not level or _state(session, key):
            continue
        if c.owner_tenant_id:
            t = session.get(Tenant, c.owner_tenant_id)
            to_list, name = _recipients(session, t), t.name
        else:
            to_list = sorted({u.email for u in session.scalars(select(User).where(User.is_admin.is_(True)))
                              if u.email})
            name = "Standard-Zertifikat der Plattform"
        for to in to_list:
            mailer.queue_mail(session, to, f"Wallet-Zertifikat läuft in {days} Tagen ab",
                              mailer.certificate_text(name, c.pass_type_identifier, _aware(c.expires_at), days,
                                                      bool(c.owner_tenant_id)), tenant_id=c.owner_tenant_id)
            sent += 1
        _set_state(session, key, now.isoformat(timespec="seconds"))
    return sent


def _cleanup(session, now):
    window = int(time.time() // 60) - 10
    return {
        "api_log": session.execute(delete(ApiRequestLog).where(ApiRequestLog.created_at < now - KEEP_API_LOG)).rowcount,
        "rate_counters": session.execute(delete(RateCounter).where(RateCounter.window < window)).rowcount,
        "invitations": session.execute(delete(Invitation).where(Invitation.accepted_at.is_(None),
                                                                Invitation.expires_at < now - timedelta(days=30))).rowcount,
        "bulk": session.execute(delete(BulkIssue).where(BulkIssue.status == "done",
                                                        BulkIssue.finished_at < now - KEEP_BULK)).rowcount,
    }
