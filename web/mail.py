"""
Outgoing email: alerts to the administrator (failed runs, server errors).

Sent over SMTP (PDD_SMTP_HOST and the other PDD_SMTP_* settings), so any provider works: Amazon SES,
Gmail, Resend, Mailgun... Without PDD_SMTP_HOST, each email is written to the server log instead, which is
enough for development. Emails are sent in a background thread, so a
slow mail server never slows down the page that triggered it.
"""
import logging
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage

log = logging.getLogger("pdd.mail")


class Mailer:
    def __init__(self, settings):
        self.settings = settings

    @property
    def configured(self) -> bool:
        return bool(self.settings.smtp_host)

    def send(self, to: str, subject: str, body: str) -> None:
        threading.Thread(target=self._send, args=(to, subject, body), name="pdd-mail", daemon=True).start()

    def _send(self, to: str, subject: str, body: str) -> None:
        s = self.settings
        if not self.configured:
            log.warning("email not sent (PDD_SMTP_HOST is not set) to %s: %s\n%s", to, subject, body)
            return
        message = EmailMessage()
        message["From"] = s.mail_from or s.smtp_user
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        try:
            context = ssl.create_default_context()
            if s.smtp_port == 465:
                with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, context=context, timeout=30) as smtp:
                    self._deliver(smtp, message)
            else:
                with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as smtp:
                    smtp.starttls(context=context)
                    self._deliver(smtp, message)
            log.info("email sent to %s: %s", to, subject)
        except Exception:
            log.exception("email to %s failed: %s", to, subject)

    def _deliver(self, smtp, message: EmailMessage) -> None:
        if self.settings.smtp_user:
            smtp.login(self.settings.smtp_user, self.settings.smtp_password)
        smtp.send_message(message)


class Alerts:
    """
    Emails the administrator (PDD_ALERT_EMAIL) when a run fails or the server hits an error. The same kind
    of problem is reported at most once every `quiet_minutes`, so a broken dependency doesn't flood the inbox.
    """

    def __init__(self, mailer: Mailer, to: str, quiet_minutes: int = 15):
        self.mailer, self.to, self.quiet = mailer, to, quiet_minutes * 60
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def report(self, kind: str, subject: str, body: str) -> None:
        if not self.to:
            return
        with self._lock:
            now = time.monotonic()
            if now - self._last.get(kind, -self.quiet) < self.quiet:
                return
            self._last[kind] = now
        self.mailer.send(self.to, f"[Pidi] {subject}", body)
