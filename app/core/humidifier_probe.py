"""
humidifier_probe.py — Aktiver Befeuchtertest (Wassertank leer?).

Der passive Trockenlauf-Schutz greift in der Praxis kaum: Der Befeuchter
läuft fast nur während und direkt nach dem Kühlen, und dann steigt die
Feuchte auch ohne Wasser (Kondensat am Verdampfer verdunstet wieder,
Frischluft hilft mit). Deshalb wird alle mois_probe_interval_h Stunden in
einer ruhigen Phase ein kurzer Testpuls gegeben und der Anstieg der
absoluten Feuchte gemessen — die ist unabhängig von der Temperatur.

- Ruhig: Kühlung und Heizung seit QUIET_SECS aus, Frischluft seit
  FANA_QUIET_SECS aus, Befeuchter aus, kein Luftwechsel, Feuchte höchstens
  MAX_ABOVE_TARGET über Soll (der Puls soll nicht gleich das Trocknen
  auslösen).
- Puls: mois_probe_secs Befeuchter an, danach SETTLE_SECS Nachmessen.
  Frischluft bleibt die ganze Zeit aus, Umluft an.
- Anstieg unter mois_probe_min_rise [g/m³]: nach RETRY_SECS erneut. Erst
  zwei Fehlschläge hintereinander gelten als leerer Tank (FAILED).
- Springt währenddessen Kühlung/Heizung an oder startet der Luftwechsel,
  wird abgebrochen und beim nächsten ruhigen Moment wiederholt.

Messung 22./23.11.2026: Natürliche Schwankung in ruhigen Phasen über 2 min
höchstens +0,25 g/m³ (99 % unter +0,16), Befeuchter mit Wasser +0,6–1,0.
"""

import logging
from typing import TYPE_CHECKING

from app.db.database import Database

if TYPE_CHECKING:
    from app.core.state import SystemState

logger = logging.getLogger(__name__)

# Rückgabewerte von update()
IDLE   = "idle"     # kein Test — normal regeln
PULSE  = "pulse"    # Befeuchter an, Frischluft aus erzwingen
SETTLE = "settle"   # Befeuchter und Frischluft aus erzwingen (Nachmessen)
PASSED = "passed"   # Test bestanden — normal regeln
FAILED = "failed"   # zweimal kein Anstieg — Befeuchter sperren

QUIET_SECS        = 1200   # Kühlung/Heizung so lange aus
FANA_QUIET_SECS   = 180    # Frischluft so lange aus
MAX_ABOVE_TARGET  = 1.0    # %rF über Soll
SETTLE_SECS       = 60     # Nachmessen nach dem Puls
RETRY_SECS        = 1800   # Wiederholung nach erstem Fehlschlag
FIRST_DELAY_SECS  = 1800   # erster Test nach Start ohne gespeicherten Termin
AFTER_RESET_SECS  = 600    # Test kurz nach Freigabe (Nachfüllen bestätigen)
FAILS_FOR_FAULT   = 2


class HumidifierProbe:
    """Entscheidet pro Regelzyklus, ob ein Befeuchtertest läuft."""

    def __init__(self, db: Database) -> None:
        self._db           = db
        self._fana_last_on = 0.0

    def update(self, state: "SystemState", now: float) -> str:
        s = state
        if s.relay_fana:
            self._fana_last_on = now

        if s.mois_probe_interval_h <= 0 or s.mois_fault:
            if s.mois_probe_active:
                self._finish(s, "abgebrochen")
            s.mois_probe_next_due = None
            return IDLE

        if s.mois_probe_active:
            return self._running(s, now)

        self.schedule(s, now)
        if now < s.mois_probe_next_due or not self._quiet(s, now):
            return IDLE

        s.mois_probe_active    = True
        s.mois_probe_started   = now
        s.mois_probe_pulse_end = now + s.mois_probe_secs
        s.mois_probe_end       = now + s.mois_probe_secs + SETTLE_SECS
        s.mois_probe_ah_start  = s.abs_hum_intern
        s.mois_probe_ah_max    = s.abs_hum_intern
        logger.info(
            f"Befeuchtertest gestartet ({s.mois_probe_secs} s, "
            f"AH {s.abs_hum_intern:.2f} g/m³)"
        )
        return PULSE

    def schedule(self, state: "SystemState", now: float) -> None:
        """Nächsten Termin festlegen, falls keiner feststeht."""
        s = state
        if s.mois_probe_interval_h <= 0:
            s.mois_probe_next_due = None
        elif s.mois_probe_next_due is None and not s.mois_probe_active:
            s.mois_probe_next_due = (
                s.mois_probe_last + s.mois_probe_interval_h * 3600 if s.mois_probe_last
                else now + FIRST_DELAY_SECS
            )

    def cancel(self, state: "SystemState", now: float) -> None:
        """Bricht einen laufenden Test ab (Moduswechsel, Safe-Mode)."""
        if state.mois_probe_active:
            self._finish(state, "abgebrochen (Moduswechsel)")
            state.mois_probe_next_due = now + RETRY_SECS

    def after_reset(self, state: "SystemState", now: float) -> None:
        """Nach der Freigabe bald testen — bestätigt das Nachfüllen."""
        state.mois_probe_fails = 0
        if state.mois_probe_interval_h > 0:
            state.mois_probe_next_due = now + AFTER_RESET_SECS

    # ── intern ────────────────────────────────────────────────────────────────

    def _quiet(self, s: "SystemState", now: float) -> bool:
        return (
            not (s.relay_cool or s.relay_heat or s.relay_mois or s.relay_fana)
            and not s.air_active
            and now - s.cool_last_off >= QUIET_SECS
            and now - s.heat_last_off >= QUIET_SECS
            and now - self._fana_last_on >= FANA_QUIET_SECS
            and s.hum_intern is not None and s.abs_hum_intern is not None
            and s.hum_intern <= s.target_hum + MAX_ABOVE_TARGET
        )

    def _running(self, s: "SystemState", now: float) -> str:
        disturbed = (
            "Kühlung" if s.relay_cool else
            "Heizung" if s.relay_heat else
            "Luftwechsel" if s.air_active else
            "keine Messwerte" if s.abs_hum_intern is None else ""
        )
        if disturbed:
            logger.info(f"Befeuchtertest abgebrochen: {disturbed}")
            self._finish(s, f"abgebrochen ({disturbed})")
            s.mois_probe_next_due = now + 300
            return IDLE

        s.mois_probe_ah_max = max(s.mois_probe_ah_max, s.abs_hum_intern)
        if now < s.mois_probe_pulse_end:
            return PULSE
        if now < s.mois_probe_end:
            return SETTLE

        rise = s.mois_probe_ah_max - s.mois_probe_ah_start
        de   = f"{rise:+.2f}".replace(".", ",")
        if rise >= s.mois_probe_min_rise:
            s.mois_probe_fails    = 0
            s.mois_probe_last     = now
            s.mois_probe_next_due = now + s.mois_probe_interval_h * 3600
            self._finish(s, f"OK, {de} g/m³")
            try:
                self._db.execute(
                    "UPDATE config SET mois_probe_last = ? WHERE id = 1", (now,)
                )
            except Exception as e:
                logger.warning(f"Zeitpunkt Befeuchtertest nicht gespeichert: {e}")
            return PASSED

        s.mois_probe_fails += 1
        logger.warning(
            f"Befeuchtertest ohne Wirkung ({s.mois_probe_fails}/{FAILS_FOR_FAULT}): "
            f"{de} g/m³ < {s.mois_probe_min_rise} g/m³"
        )
        if s.mois_probe_fails >= FAILS_FOR_FAULT:
            s.mois_probe_next_due = None
            self._finish(s, f"kein Anstieg ({de} g/m³)")
            return FAILED
        s.mois_probe_next_due = now + RETRY_SECS
        self._finish(s, f"kein Anstieg ({de} g/m³), Wiederholung")
        return IDLE

    def _finish(self, s: "SystemState", result: str) -> None:
        s.mois_probe_active = False
        s.mois_probe_result = result
        logger.info(f"Befeuchtertest beendet: {result}")
