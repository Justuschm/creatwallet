"""E-Mail-Versand über SMTP. Mails laufen als Job über die Warteschlange - so blockiert ein
langsamer Mailserver keine Anfrage, und fehlgeschlagene Mails werden wiederholt.

Konfiguration: ``WALLET_SMTP_URL`` z. B. ``smtp://benutzer:passwort@mail.example.de:587`` (STARTTLS)
oder ``smtps://…:465`` (TLS), ``WALLET_MAIL_FROM`` als Absender. Ohne SMTP-URL werden Mails nur
protokolliert - praktisch für Entwicklung und Tests.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from urllib.parse import unquote, urlsplit

log = logging.getLogger("wallet.mail")
EMAIL = "email"


class MailError(Exception):
    pass


class Mailer:
    def __init__(self, smtp_url, sender):
        self.url = smtp_url or ""
        self.sender = sender or "Wallet-Pass-Plattform <noreply@localhost>"

    @property
    def enabled(self):
        return bool(self.url)

    def send(self, to, subject, text):
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = to
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain=self.sender.rsplit("@", 1)[-1].strip(">") or "localhost")
        msg.set_content(text)
        if not self.enabled:
            log.info("E-Mail (nicht verschickt, WALLET_SMTP_URL fehlt) an %s: %s", to, subject)
            return False
        parts = urlsplit(self.url)
        host, port = parts.hostname, parts.port
        try:
            if parts.scheme == "smtps":
                server = smtplib.SMTP_SSL(host, port or 465, context=ssl.create_default_context(), timeout=20)
            elif parts.scheme == "smtp":
                server = smtplib.SMTP(host, port or 587, timeout=20)
                if "starttls=0" not in (parts.query or ""):
                    server.starttls(context=ssl.create_default_context())
            else:
                raise MailError(f"Unbekanntes Schema in WALLET_SMTP_URL: {parts.scheme}")
            with server:
                if parts.username:
                    server.login(unquote(parts.username), unquote(parts.password or ""))
                server.send_message(msg)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailError(f"Versand an {to} fehlgeschlagen: {exc}") from exc
        return True


def queue_mail(session, to, subject, text, tenant_id=None):
    """Mail über die Warteschlange verschicken (Worker)."""
    from . import jobs

    if not to or "@" not in to:
        return None
    return jobs.enqueue(session, EMAIL, {"to": to, "subject": subject, "text": text}, tenant_id=tenant_id)


# ---------------------------------------------------------------- Texte

FOOTER = "\n\n--\nDiese Nachricht wurde automatisch von der Wallet-Pass-Plattform verschickt."


def invite_text(tenant_name, inviter, role_label, link):
    return (f"Hallo,\n\n{inviter} lädt dich ein, im Portal von {tenant_name} mitzuarbeiten "
            f"(Rolle: {role_label}).\n\nEinladung annehmen (7 Tage gültig):\n{link}" + FOOTER)


def pass_text(organization, description, page_url):
    return (f"Hallo,\n\ndein Pass „{description}“ von {organization} ist bereit.\n\n"
            f"Auf dem iPhone öffnen und „Zu Apple Wallet hinzufügen“ tippen:\n{page_url}" + FOOTER)


def quota_text(tenant_name, plan_label, active, included, percent):
    return (f"Hallo,\n\n{tenant_name} nutzt {active} von {included} aktiven Pässen im Tarif {plan_label} "
            f"({percent} %).\n\nIm Portal unter „Abrechnung“ siehst du den Verbrauch und kannst den Tarif wechseln."
            + FOOTER)


def certificate_text(name, pti, expires, days, own):
    who = ("Bitte im eigenen Apple-Developer-Account ein neues Zertifikat mit derselben Pass Type ID erzeugen "
           "und im Portal unter „Zertifikat“ hochladen." if own else
           "Bitte im Apple-Developer-Account ein neues Zertifikat erzeugen und im Admin-Bereich hochladen.")
    return (f"Hallo,\n\ndas Wallet-Zertifikat {pti} ({name}) läuft am {expires:%d.%m.%Y} ab – in {days} Tagen. "
            f"Danach lassen sich keine Pässe mehr ausgeben oder aktualisieren.\n\n{who}" + FOOTER)


def sender(name, address):
    return formataddr((name, address))
