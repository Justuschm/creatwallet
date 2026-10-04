"""Admin-Bereich für den Plattform-Betreiber: Firmen, Prüfungen, Zertifikate, Jobs, Audit-Log."""

import base64
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import placeholders, services
from ..api.deps import ctx, get_session
from ..certs import CertStoreError, import_certificate
from ..models import AuditLog, Certificate, Job, Pass, Registration, TemplateVersion, Tenant, User, utcnow
from ..services import ServiceError
from .core import admin_user, audit, check_csrf, flash
from .core import render as _render

router = APIRouter(prefix="/admin", include_in_schema=False, dependencies=[Depends(check_csrf)])
PLANS = ("free", "starter", "business", "pro", "enterprise")


def render(request, name, user, **ctx):
    """Wie core.render, zusätzlich die Zahl offener Prüfungen für die Navigation."""
    session = request.app.state.sessionmaker()
    try:
        pending = session.scalar(select(func.count(Tenant.id)).where(Tenant.status == "pending"))
        if request.app.state.settings.require_template_approval:
            pending += session.scalar(select(func.count(TemplateVersion.id)).where(TemplateVersion.status == "pending"))
    finally:
        session.close()
    return _render(request, name, user, nav_pending=pending, **ctx)


def back(url, request=None, message=None, kind="ok"):
    if request is not None and message:
        flash(request, message, kind)
    return RedirectResponse(url, status_code=303)


def _aware(dt):
    from datetime import timezone
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


@router.get("")
def cockpit(request: Request, user: User = Depends(admin_user), session: Session = Depends(get_session)):
    by_status = dict(session.execute(select(Tenant.status, func.count()).group_by(Tenant.status)).all())
    jobs = dict(session.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    soon = utcnow() + timedelta(days=60)
    expiring = [c for c in session.scalars(select(Certificate).order_by(Certificate.expires_at)) if
                _aware(c.expires_at) < soon]
    stats = {
        "passes": session.scalar(select(func.count(Pass.id)).where(Pass.status == "active")),
        "devices": session.scalar(select(func.count(Registration.id))),
        "pending_templates": session.scalar(select(func.count(TemplateVersion.id))
                                            .where(TemplateVersion.status == "pending")),
    }
    return render(request, "admin/cockpit.html", user, by_status=by_status, jobs=jobs, expiring=expiring,
                  stats=stats, now=utcnow())


# ---------------------------------------------------------------- Firmen

@router.get("/tenants")
def tenants(request: Request, status: str = "", user: User = Depends(admin_user),
            session: Session = Depends(get_session)):
    q = select(Tenant).order_by(Tenant.created_at.desc())
    if status:
        q = q.where(Tenant.status == status)
    rows = session.scalars(q).all()
    counts = dict(session.execute(select(Pass.tenant_id, func.count(Pass.id)).where(Pass.status == "active")
                                  .group_by(Pass.tenant_id)).all())
    return render(request, "admin/tenants.html", user, tenants=rows, counts=counts, status=status)


@router.post("/tenants/{tenant_id}/status")
def tenant_status(tenant_id: str, request: Request, status: str = Form(...), note: str = Form(""),
                  user: User = Depends(admin_user), session: Session = Depends(get_session)):
    t = services.tenant_by_id(session, tenant_id)
    if status not in ("active", "suspended", "pending"):
        raise ServiceError(422, "Unbekannter Status.")
    t.status, t.review_note = status, note.strip()[:2000]
    audit(session, user, f"admin.tenant.{status}", t.id, t.id, note=t.review_note)
    session.commit()
    return back(f"/admin/tenants/{t.id}", request, f"{t.name}: {status}")


@router.post("/tenants/{tenant_id}/plan")
def tenant_plan(tenant_id: str, request: Request, plan: str = Form(...), user: User = Depends(admin_user),
                session: Session = Depends(get_session)):
    t = services.tenant_by_id(session, tenant_id)
    if plan not in PLANS:
        raise ServiceError(422, "Unbekannter Tarif.")
    t.plan = plan
    audit(session, user, "admin.tenant.plan", t.id, t.id, plan=plan)
    session.commit()
    return back(f"/admin/tenants/{t.id}", request, f"Tarif: {plan}")


@router.post("/tenants/{tenant_id}/certificate")
def tenant_certificate(tenant_id: str, request: Request, certificate_id: str = Form(""),
                       user: User = Depends(admin_user), session: Session = Depends(get_session)):
    t = services.tenant_by_id(session, tenant_id)
    cert = session.get(Certificate, certificate_id) if certificate_id else None
    try:
        if services.assign_certificate(session, t, cert):
            flash(request, "Hinweis: " + services.SWITCH_HINT, "warn")
    except ServiceError as exc:
        return back(f"/admin/tenants/{t.id}/certificate", request, exc.message, "error")
    audit(session, user, "admin.tenant.certificate", t.id, t.id,
          pass_type_identifier=cert.pass_type_identifier if cert else None)
    session.commit()
    return back(f"/admin/tenants/{t.id}/certificate", request,
                "Zertifikat zugeordnet." if cert else "Zertifikat entfernt.")


# ---------------------------------------------------------------- Prüfungen

@router.get("/reviews")
def reviews(request: Request, user: User = Depends(admin_user), session: Session = Depends(get_session)):
    versions = session.scalars(select(TemplateVersion).where(TemplateVersion.status == "pending")
                               .order_by(TemplateVersion.created_at)).all()
    new_tenants = session.scalars(select(Tenant).where(Tenant.status == "pending").order_by(Tenant.created_at)).all()
    names = dict(session.execute(select(Tenant.id, Tenant.name)).all())
    return render(request, "admin/reviews.html", user, versions=versions, new_tenants=new_tenants, names=names)


@router.get("/reviews/{version_id}")
def review_detail(version_id: str, request: Request, user: User = Depends(admin_user),
                  session: Session = Depends(get_session)):
    version = session.get(TemplateVersion, version_id)
    if version is None:
        raise ServiceError(404, "Version nicht gefunden.")
    tenant = session.get(Tenant, version.template.tenant_id)
    issues = services.preview_issues(tenant, version.pass_json, version.file_map())
    previous = version.template.approved_version
    images = {f.name: base64.b64encode(f.data).decode() for f in version.files}
    return render(request, "admin/review_detail.html", user, version=version, tenant=tenant, issues=issues,
                  pass_json=json.dumps(version.pass_json, indent=2, ensure_ascii=False), images=images,
                  previous=previous, placeholders=placeholders.find(version.pass_json))


@router.post("/reviews/{version_id}/{decision}")
def review_decide(version_id: str, decision: str, request: Request, note: str = Form(""),
                  user: User = Depends(admin_user), session: Session = Depends(get_session)):
    version = session.get(TemplateVersion, version_id)
    if version is None or version.status != "pending":
        return back("/admin/reviews", request, "Version nicht (mehr) offen.", "error")
    tenant_id = version.template.tenant_id
    if decision == "approve":
        services.approve_version(version, note.strip())
        audit(session, user, "admin.template.approve", tenant_id, version.template_id, version=version.number)
        message = f"Freigegeben: {version.template.name} v{version.number}"
    elif decision == "reject":
        if not note.strip():
            return back(f"/admin/reviews/{version_id}", request, "Bitte eine Begründung angeben.", "error")
        services.reject_version(version, note.strip())
        audit(session, user, "admin.template.reject", tenant_id, version.template_id, version=version.number,
              note=note.strip())
        message = f"Abgelehnt: {version.template.name} v{version.number}"
    else:
        raise ServiceError(404, "Unbekannte Entscheidung.")
    session.commit()
    return back("/admin/reviews", request, message)


@router.post("/reviews/{version_id}/test-pass/download")
def review_test_pass(version_id: str, request: Request, user: User = Depends(admin_user),
                     session: Session = Depends(get_session)):
    version = session.get(TemplateVersion, version_id)
    if version is None:
        raise ServiceError(404, "Version nicht gefunden.")
    tenant = session.get(Tenant, version.template.tenant_id)
    try:
        data = services.build_test_pass(tenant, version, None, ctx(request)[1])
    except ServiceError as exc:
        return back(f"/admin/reviews/{version_id}", request, exc.message, "error")
    return Response(data, media_type="application/vnd.apple.pkpass",
                    headers={"Content-Disposition": 'attachment; filename="pruefung.pkpass"'})


# ---------------------------------------------------------------- Zertifikate

@router.get("/certificates")
def certificates(request: Request, user: User = Depends(admin_user), session: Session = Depends(get_session)):
    certs = session.scalars(select(Certificate).order_by(Certificate.expires_at)).all()
    usage = dict(session.execute(select(Tenant.certificate_id, func.count(Tenant.id))
                                 .group_by(Tenant.certificate_id)).all())
    names = dict(session.execute(select(Tenant.id, Tenant.name)).all())
    return render(request, "admin/certificates.html", user, certs=certs, usage=usage, names=names, now=utcnow())


@router.post("/certificates")
async def certificate_upload(request: Request, p12: UploadFile = File(...), password: str = Form(""),
                             user: User = Depends(admin_user), session: Session = Depends(get_session)):
    settings, signers, _ = ctx(request)
    data = await p12.read()
    if len(data) > 100_000:
        return back("/admin/certificates", request, "Datei zu groß für ein .p12-Zertifikat.", "error")
    try:
        cert = import_certificate(session, signers.store, data, password, settings.wwdr_path)
    except CertStoreError as exc:
        session.rollback()
        return back("/admin/certificates", request, str(exc), "error")
    signers.forget(cert.pass_type_identifier)
    audit(session, user, "admin.certificate.import", None, cert.id, pass_type_identifier=cert.pass_type_identifier)
    session.commit()
    return back("/admin/certificates", request, f"Gespeichert: {cert.pass_type_identifier}, gültig bis "
                                                f"{cert.expires_at:%d.%m.%Y}")


# ---------------------------------------------------------------- Jobs und Audit-Log

@router.get("/jobs")
def jobs_page(request: Request, status: str = "failed", user: User = Depends(admin_user),
              session: Session = Depends(get_session)):
    counts = dict(session.execute(select(Job.status, func.count()).group_by(Job.status)).all())
    rows = session.scalars(select(Job).where(Job.status == status).order_by(Job.updated_at.desc()).limit(100)).all()
    names = dict(session.execute(select(Tenant.id, Tenant.name)).all())
    return render(request, "admin/jobs.html", user, rows=rows, counts=counts, status=status, names=names)


@router.post("/jobs/{job_id}/retry")
def job_retry(job_id: str, request: Request, user: User = Depends(admin_user), session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if job is None:
        raise ServiceError(404, "Job nicht gefunden.")
    job.status, job.attempts, job.run_after = "pending", 0, utcnow()
    audit(session, user, "admin.job.retry", None, job.id, kind=job.kind)
    session.commit()
    return back("/admin/jobs", request, "Job wird erneut ausgeführt.")


@router.get("/audit")
def audit_page(request: Request, page: int = 1, user: User = Depends(admin_user),
               session: Session = Depends(get_session)):
    rows = session.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(101)
                           .offset((max(page, 1) - 1) * 100)).all()
    names = dict(session.execute(select(Tenant.id, Tenant.name)).all())
    return render(request, "admin/audit.html", user, rows=rows[:100], more=len(rows) > 100, page=max(page, 1),
                  names=names)

