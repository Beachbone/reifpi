"""
dispatcher.py — Zentrale Benachrichtigung aus dem Regelbetrieb.

Liest bei jedem Versand die aktuellen Einstellungen aus der DB (Änderungen
auf der Einstellungsseite gelten sofort) und verschickt über alle
aktivierten Kanäle: ntfy und E-Mail. Der Versand läuft in einem eigenen
Thread, damit eine langsame Verbindung nie den Regelzyklus aufhält.

E-Mails respektieren den Mindestabstand email_sleep (Minuten); ntfy wird
immer zugestellt.
"""

import logging
import threading
import time

from app.db.database import Database
from app.notifications.email_notifier import EmailNotifier
from app.notifications.ntfy_notifier import NtfyNotifier

logger = logging.getLogger(__name__)


class NotificationDispatcher:

    def __init__(self, db: Database) -> None:
        self._db        = db
        self._last_mail = 0.0
        self._lock      = threading.Lock()

    def send(self, subject: str, body: str,
             priority: str = "high", tags: list[str] | None = None) -> None:
        """Verschickt im Hintergrund; kehrt sofort zurück."""
        try:
            row = self._db.fetchone("SELECT * FROM config WHERE id = 1")
        except Exception as e:
            logger.error(f"Benachrichtigung: Konfiguration nicht lesbar: {e}")
            return
        if not row:
            return
        cfg = dict(row)
        threading.Thread(
            target=self._deliver,
            args=(cfg, subject, body, priority, tags or []),
            name="reifpi-notify",
            daemon=True,
        ).start()

    def _deliver(self, cfg: dict, subject: str, body: str,
                 priority: str, tags: list[str]) -> None:
        if cfg.get("ntfy_enabled"):
            NtfyNotifier.from_config(cfg).send(
                title=f"Reifeschrank: {subject}", message=body,
                priority=priority, tags=tags,
            )
        if cfg.get("mail_enabled"):
            with self._lock:
                wait = int(cfg.get("email_sleep", 60)) * 60
                if time.time() - self._last_mail < wait:
                    logger.info(f"E-Mail '{subject}' unterdrückt (Mindestabstand)")
                    return
                mailer = EmailNotifier.from_config({**cfg, "email_sleep": 0})
                if mailer.send(subject, body):
                    self._last_mail = time.time()
