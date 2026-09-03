"""
email_notifier.py — E-Mail Benachrichtigungen.

Throttling: Mindestabstand zwischen E-Mails konfigurierbar (email_sleep in Minuten).
Wird vom Controller bei Safe-Mode und Sensor-Fehlern aufgerufen.
"""

import logging
import smtplib
import ssl
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


class EmailNotifier:

    def __init__(
        self,
        smtp_server:  str,
        port:         int,
        user:         str,
        password:     str,
        receiver:     str,
        throttle_min: int = 60,
        enabled:      bool = True,
    ) -> None:
        self._smtp       = smtp_server
        self._port       = port
        self._user       = user
        self._password   = password
        self._receiver   = receiver
        self._throttle   = throttle_min * 60   # → Sekunden
        self._enabled    = enabled
        self._last_sent: float = 0.0

    def send(self, subject: str, body: str) -> bool:
        """
        Sendet eine E-Mail. Gibt True zurück wenn erfolgreich.
        Ignoriert die Nachricht wenn Throttle-Interval noch nicht abgelaufen.
        """
        if not self._enabled:
            logger.debug("E-Mail deaktiviert — nicht gesendet.")
            return False

        if not self._smtp or not self._user or not self._receiver:
            logger.warning("E-Mail nicht konfiguriert.")
            return False

        elapsed = time.time() - self._last_sent
        if elapsed < self._throttle:
            remaining = int((self._throttle - elapsed) / 60)
            logger.info(f"E-Mail Throttle aktiv — nächste E-Mail in {remaining} Min.")
            return False

        try:
            msg = MIMEMultipart()
            msg["From"]    = self._user
            msg["To"]      = self._receiver
            msg["Subject"] = f"[Reifeschrank] {subject}"
            msg.attach(MIMEText(body, "plain", "utf-8"))

            context = ssl.create_default_context()
            with smtplib.SMTP(self._smtp, self._port) as server:
                server.starttls(context=context)
                server.login(self._user, self._password)
                server.sendmail(self._user, self._receiver, msg.as_string())

            self._last_sent = time.time()
            logger.info(f"E-Mail gesendet: {subject}")
            return True

        except Exception as e:
            logger.error(f"E-Mail Fehler: {e}")
            return False

    @classmethod
    def from_config(cls, config: dict) -> "EmailNotifier":
        """Erstellt einen EmailNotifier aus einem Config-Dict (aus DB)."""
        return cls(
            smtp_server  = config.get("mail_smtp", ""),
            port         = int(config.get("mail_port", 587)),
            user         = config.get("mail_user", ""),
            password     = config.get("mail_password", ""),
            receiver     = config.get("mail_receiver", ""),
            throttle_min = int(config.get("email_sleep", 60)),
            enabled      = bool(config.get("mail_enabled", False)),
        )
