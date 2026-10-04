"""Tarife und Kontingente (Werte aus dem Masterplan, Abschnitt "Abrechnung und Abo-Modell").

Durchgesetzt werden die Grenzen nur mit ``WALLET_ENFORCE_PLAN_LIMITS=true`` (Standard im Betrieb).
"Aktive Pässe" = ausgegeben, nicht gesperrt.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select

from .models import Invitation, Pass, Template, UsageDaily, User


@dataclass(frozen=True)
class Plan:
    key: str
    label: str
    price_month: float | None  # None = nach Angebot
    included: int | None  # aktive Pässe inklusive (None = individuell)
    overage: float | None  # Preis je weiterem aktiven Pass
    hard_limit: bool = False  # mehr als 'included' geht gar nicht (Kostenlos)
    max_templates: int | None = None
    max_users: int | None = None
    features: frozenset = field(default_factory=frozenset)


ALL = frozenset({"api", "push_updates", "webhooks", "bulk", "own_certificate"})
PLANS = {
    "free": Plan("free", "Kostenlos", 0, 25, None, hard_limit=True, max_templates=1, max_users=1,
                 features=frozenset()),
    "starter": Plan("starter", "Starter", 29, 500, 0.05, max_templates=3, max_users=2,
                    features=frozenset({"api"})),
    "business": Plan("business", "Business", 99, 5000, 0.03, max_users=10,
                     features=frozenset({"api", "push_updates", "webhooks", "bulk"})),
    "pro": Plan("pro", "Pro", 299, 25000, 0.02, max_users=50, features=ALL),
    "enterprise": Plan("enterprise", "Enterprise", None, None, None, features=ALL),
}
FEATURE_LABELS = {"api": "API", "push_updates": "Updates per Push", "webhooks": "Webhooks",
                  "bulk": "Massenausgabe", "own_certificate": "Eigenes Zertifikat"}


def plan_of(tenant):
    return PLANS.get(tenant.plan, PLANS["free"])


def cheapest_with(feature):
    for p in PLANS.values():
        if feature in p.features:
            return p
    return PLANS["enterprise"]


def allows(tenant, feature, settings):
    return not settings.enforce_plan_limits or feature in plan_of(tenant).features


def require(tenant, feature, settings):
    from .services import ServiceError

    if not allows(tenant, feature, settings):
        plan, need = plan_of(tenant), cheapest_with(feature)
        raise ServiceError(403, f"{FEATURE_LABELS[feature]} ist im Tarif {plan.label} nicht enthalten "
                                f"(ab {need.label}).")


def active_passes(session, tenant):
    return session.scalar(select(func.count(Pass.id)).where(Pass.tenant_id == tenant.id, Pass.status == "active"))


def check_new_pass(session, tenant, settings, count=1):
    from .services import ServiceError

    plan = plan_of(tenant)
    if settings.enforce_plan_limits and plan.hard_limit and plan.included is not None:
        if active_passes(session, tenant) + count > plan.included:
            raise ServiceError(403, f"Kontingent erreicht: Im Tarif {plan.label} sind {plan.included} aktive Pässe "
                                    "enthalten. Bitte Tarif wechseln oder alte Pässe sperren.")


def check_new_template(session, tenant, settings):
    from .services import ServiceError

    plan = plan_of(tenant)
    if settings.enforce_plan_limits and plan.max_templates is not None:
        n = session.scalar(select(func.count(Template.id)).where(Template.tenant_id == tenant.id))
        if n >= plan.max_templates:
            raise ServiceError(403, f"Im Tarif {plan.label} sind {plan.max_templates} Vorlage(n) möglich.")


def check_new_user(session, tenant, settings):
    from .services import ServiceError

    plan = plan_of(tenant)
    if settings.enforce_plan_limits and plan.max_users is not None:
        members = session.scalar(select(func.count(User.id)).where(User.tenant_id == tenant.id))
        open_invites = session.scalar(select(func.count(Invitation.id)).where(
            Invitation.tenant_id == tenant.id, Invitation.accepted_at.is_(None)))
        if members + open_invites >= plan.max_users:
            raise ServiceError(403, f"Im Tarif {plan.label} sind {plan.max_users} Zugang/Zugänge möglich.")


def usage(session, tenant, now=None):
    """Verbrauch im laufenden Monat: aktuell aktiv, Höchststand, Kontingent, voraussichtliche Kosten."""
    now = now or datetime.now(timezone.utc)
    plan = plan_of(tenant)
    active = active_passes(session, tenant)
    month = now.strftime("%Y-%m")
    peak = session.scalar(select(func.max(UsageDaily.active)).where(UsageDaily.tenant_id == tenant.id,
                                                                    UsageDaily.day.like(f"{month}-%"))) or 0
    peak = max(peak, active)
    percent = round(active / plan.included * 100) if plan.included else 0
    extra = max(0, peak - plan.included) if plan.included is not None else 0
    cost = None
    if plan.price_month is not None:
        cost = plan.price_month + extra * (plan.overage or 0)
    months = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
              "November", "Dezember")
    return {"plan": plan, "active": active, "peak": peak, "percent": percent, "extra": extra, "cost": cost,
            "month": month, "month_label": f"{months[now.month - 1]} {now.year}"}
