"""
air_exchange.py — Geplanter Luftwechsel (Frischluft nach Intervall).

Unabhängig von der Feuchte-Regelung wird alle air_interval_h Stunden für
air_duration_min Minuten Frischluft zugeführt. Die Regelung läuft dabei
weiter und gleicht Temperatur und Feuchte laufend aus; erzwungen werden nur
Frischluft und Umluft.

- Außenluft feuchter (absolute Feuchte) und innen nicht zu trocken:
  verkürzt lüften (air_short_pct), nächster Versuch nach 1/4 Intervall.
  Trockenere Außenluft ist kein Problem — der Befeuchter gleicht aus.
  Eine Temperaturdifferenz ist der Normalfall und kein Grund zu verkürzen.
- Ändern sich Temperatur oder Feuchte gegenüber dem Start stärker als
  air_abort_temp_k bzw. air_abort_hum_pct: Abbruch, Regelung übernimmt
  sofort, nächster Versuch nach 1/4 Intervall.

Der Zeitpunkt des letzten erfolgreichen Luftwechsels steht in der DB
(config.air_last_exchange), damit ein Neustart die Uhr nicht zurücksetzt.
"""

import logging
from typing import TYPE_CHECKING

from app.db.database import Database

if TYPE_CHECKING:
    from app.core.state import SystemState

logger = logging.getLogger(__name__)

# Rückgabewerte von update()
IDLE   = "idle"     # kein Luftwechsel — normal regeln
ACTIVE = "active"   # Luftwechsel läuft — Frischluft + Umluft erzwingen
ENDED  = "ended"    # gerade beendet/abgebrochen — Frischluft aus, normal regeln

# Erster Luftwechsel nach Aktivierung bzw. ohne gespeicherten Zeitpunkt
FIRST_RUN_DELAY_SECS = 600


class AirExchange:
    """Entscheidet pro Regelzyklus, ob gelüftet wird."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def update(self, state: "SystemState", now: float) -> str:
        s = state
        if s.air_interval_h <= 0:
            if s.air_active:
                self._finish(s, now, "abgeschaltet", next_due=None)
                return ENDED
            s.air_next_due = None
            return IDLE

        interval = s.air_interval_h * 3600

        if s.air_active:
            reason = self._abort_reason(s)
            if reason:
                logger.warning(f"Luftwechsel abgebrochen: {reason}")
                self._finish(s, now, f"abgebrochen ({reason})", next_due=now + interval / 4)
                return ENDED
            if now >= s.air_end:
                if s.air_short:
                    self._finish(s, now, "verkürzt", next_due=now + interval / 4, success=True)
                else:
                    self._finish(s, now, "vollständig", next_due=now + interval, success=True)
                return ENDED
            return ACTIVE

        self.schedule(s, now)

        if now < s.air_next_due or s.temp_intern is None or s.hum_intern is None:
            return IDLE

        short_reason = self._unfavorable_reason(s)
        duration = s.air_duration_min * 60
        if short_reason:
            duration = duration * s.air_short_pct / 100

        s.air_active     = True
        s.air_short      = bool(short_reason)
        s.air_started    = now
        s.air_end        = now + duration
        s.air_start_temp = s.temp_intern
        s.air_start_hum  = s.hum_intern
        logger.info(
            f"Luftwechsel gestartet für {int(duration)} s"
            + (f" (verkürzt: {short_reason})" if short_reason else "")
        )
        return ACTIVE

    def schedule(self, state: "SystemState", now: float) -> None:
        """
        Berechnet den nächsten Termin, falls keiner feststeht — ab dem
        letzten erfolgreichen Luftwechsel bzw. kurz nach der Aktivierung.
        Startet nichts; darf deshalb auch aus dem API-Thread laufen.
        """
        s = state
        if s.air_interval_h <= 0:
            s.air_next_due = None
        elif s.air_next_due is None and not s.air_active:
            s.air_next_due = (
                s.air_last_exchange + s.air_interval_h * 3600 if s.air_last_exchange
                else now + FIRST_RUN_DELAY_SECS
            )

    def cancel(self, state: "SystemState", now: float) -> None:
        """Bricht einen laufenden Luftwechsel ab (Modus-Wechsel, Safe-Mode)."""
        if state.air_active:
            interval = max(state.air_interval_h, 0) * 3600
            self._finish(state, now, "abgebrochen (Moduswechsel)",
                         next_due=now + interval / 4 if interval else None)

    # ── intern ────────────────────────────────────────────────────────────────

    def _abort_reason(self, s: "SystemState") -> str:
        if s.temp_intern is None or s.hum_intern is None:
            return "keine Messwerte"
        dt = abs(s.temp_intern - s.air_start_temp)
        if dt > s.air_abort_temp_k:
            return f"Temperatur {dt:.1f} K verändert"
        dh = abs(s.hum_intern - s.air_start_hum)
        if dh > s.air_abort_hum_pct:
            return f"Feuchte {dh:.1f} %rF verändert"
        return ""

    def _unfavorable_reason(self, s: "SystemState") -> str:
        """
        Leer = Außenluft passt (oder kein Außensensor).

        Nur feuchtere Außenluft ist ungünstig, weil sich ausschließlich mit
        Frischluft trocknen lässt — außer innen ist es ohnehin zu trocken.
        """
        ah_in, ah_out = s.abs_hum_intern, s.abs_hum_extern
        if ah_in is None or ah_out is None:
            return ""
        if ah_out - ah_in > s.abs_hum_threshold and s.hum_intern >= s.target_hum:
            return "Außenluft feuchter"
        return ""

    def _finish(self, s: "SystemState", now: float, result: str,
                next_due: float | None, success: bool = False) -> None:
        s.air_active      = False
        s.air_short       = False
        s.air_last_result = result
        s.air_next_due    = next_due
        if success:
            s.air_last_exchange = now
            try:
                self._db.execute(
                    "UPDATE config SET air_last_exchange = ? WHERE id = 1", (now,)
                )
            except Exception as e:
                logger.warning(f"Zeitpunkt Luftwechsel nicht gespeichert: {e}")
        logger.info(f"Luftwechsel beendet: {result}")
