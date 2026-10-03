"""Öffentliche Seiten für Endkunden: Download-Seite mit Button und QR-Code, Pass-Datei, Logo."""

import html

import segno
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from .. import placeholders, services
from .deps import ctx, get_session

router = APIRouter(include_in_schema=False)
PKPASS = "application/vnd.apple.pkpass"
LOGO_CANDIDATES = ("logo@2x.png", "logo@3x.png", "primaryLogo@2x.png", "logo.png", "icon@2x.png", "icon.png")
PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
                               "script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex",
}


def _find(session, token):
    p = services.pass_by_token(session, token)
    if p is None:
        raise services.ServiceError(404, "Dieser Link ist ungültig.")
    return p


def _logo(p):
    version = p.template.approved_version
    if version is None:
        return None
    files = version.file_map()
    return next((n for n in LOGO_CANDIDATES if n in files), None)


@router.get("/p/{token}", response_class=HTMLResponse)
def pass_page(token: str, request: Request, session: Session = Depends(get_session)):
    settings, _, _ = ctx(request)
    p = _find(session, token)
    version = p.template.approved_version
    rendered = placeholders.render(version.pass_json, p.data) if version else {}
    org = rendered.get("organizationName") or p.tenant.organization_name
    title = rendered.get("description") or "Dein Pass"
    page_url = f"{settings.public_base_url}/p/{token}"
    qr = segno.make(page_url, error="m").svg_inline(scale=5, border=2, dark="#000", light="#fff")
    logo = f'<img class="logo" src="/p/{html.escape(token)}/logo.png" alt="">' if _logo(p) else ""
    voided = p.status == "voided"
    body = (f'<p class="note">Dieser Pass ist nicht mehr gültig.</p>' if voided else
            f'''<a class="wallet" href="/p/{html.escape(token)}/pass.pkpass">
      <small>Hinzufügen zu</small><br>Apple Wallet</a>
    <div class="qr"><p>Am Computer? Code mit der iPhone-Kamera scannen:</p>{qr}</div>''')
    page = f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)} – {html.escape(org)}</title>
<style>
  :root {{ --bg: #f2f2f7; --card: #fff; --text: #1c1c1e; --muted: #6e6e73; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #000; --card: #1c1c1e; --text: #f2f2f7; --muted: #98989d; }} }}
  body {{ margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; background: var(--bg);
         color: var(--text); font: 16px/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
  main {{ background: var(--card); border-radius: 20px; padding: 32px 24px; margin: 16px; max-width: 360px; width: 100%;
          text-align: center; box-shadow: 0 8px 30px #0002; }}
  .logo {{ max-width: 160px; max-height: 60px; margin-bottom: 12px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }} .org {{ color: var(--muted); margin: 0 0 24px; }}
  /* Für den Livebetrieb durch Apples offizielles „Add to Apple Wallet“-Badge ersetzen (Apple-Richtlinien). */
  .wallet {{ display: inline-block; background: #000; color: #fff; text-decoration: none;
             border-radius: 10px; padding: 10px 22px; text-align: left; font-size: 19px; font-weight: 600; line-height: 1.1;
             border: 1px solid #a6a6a6; }}
  .wallet small {{ font-size: 11px; font-weight: 400; }}
  .qr {{ margin-top: 28px; color: var(--muted); font-size: 13px; }} .qr svg {{ width: 168px; height: 168px; border-radius: 8px; }}
  .note {{ color: #c62828; font-weight: 600; }}
  @media (hover: none) and (pointer: coarse) {{ .qr {{ display: none; }} }}
</style></head><body><main>{logo}<h1>{html.escape(title)}</h1><p class="org">{html.escape(org)}</p>{body}</main></body></html>"""
    return HTMLResponse(page, headers=PAGE_HEADERS)


@router.get("/p/{token}/pass.pkpass")
def pass_file(token: str, request: Request, session: Session = Depends(get_session)):
    settings, signers, vault = ctx(request)
    p = _find(session, token)
    data = services.build_pass(p, settings, signers, vault)
    return Response(data, media_type=PKPASS, headers={
        "Content-Disposition": f'attachment; filename="{p.serial_number}.pkpass"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/p/{token}/logo.png")
def pass_logo(token: str, session: Session = Depends(get_session)):
    p = _find(session, token)
    name = _logo(p)
    if name is None:
        raise services.ServiceError(404, "Kein Logo.")
    return Response(p.template.approved_version.file_map()[name], media_type="image/png",
                    headers={"Cache-Control": "public, max-age=3600"})
