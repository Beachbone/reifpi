"""
program_runner.py — Reifeprogramm-Ablauf und Schritt-Steuerung.

Verantwortlich für:
- Programm starten / stoppen
- Aktuellen Schritt prüfen und bei Ablauf weiterschalten
- Sollwerte (Temp/Hum/Modus) aus dem aktiven Schritt in den State schreiben
- Programm-Status für die API liefern
"""

import logging
import time
from typing import TYPE_CHECKING

from app.db.database import Database

if TYPE_CHECKING:
    from app.core.state import SystemState

logger = logging.getLogger(__name__)


class ProgramRunner:
    """
    Verwaltet den Ablauf eines Reifeprogramms.

    Wird vom Controller einmal pro Zyklus über check_and_advance()
    aufgerufen. Schreibt Sollwerte direkt in den SystemState.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    # ── Programm starten ─────────────────────────────────────────────────────

    def activate_program(self, program_id: int, state: "SystemState") -> bool:
        """
        Startet ein Reifeprogramm ab Schritt 1.

        - Löscht laufende Einträge im riping_archive
        - Legt alle Schritte mit berechneten End-Zeiten an
        - Setzt Sollwerte des ersten Schritts in den State
        - Gibt True zurück bei Erfolg
        """
        pgm = self._db.fetchone(
            "SELECT id, name FROM riping_pgms WHERE id = ?", (program_id,)
        )
        if not pgm:
            logger.error(f"Programm {program_id} nicht gefunden.")
            return False

        steps = self._db.fetchall(
            "SELECT * FROM riping_steps WHERE riping_pgms_id = ? ORDER BY step ASC",
            (program_id,),
        )
        if not steps:
            logger.error(f"Programm {program_id} hat keine Schritte.")
            return False

        # Laufende Archive-Einträge beenden
        self._db.execute(
            "UPDATE riping_archive SET time_step_ended = ? WHERE time_step_ended IS NULL",
            (time.time(),),
        )

        # Alle Schritte mit Zeitplan ins Archive schreiben
        now = time.time()
        cursor_time = now
        for step in steps:
            duration_secs = int(step["duration_h"]) * 3600
            end_time = cursor_time + duration_secs
            self._db.execute(
                """
                INSERT INTO riping_archive (riping_pgm, riping_step, start_time, end_time)
                VALUES (?, ?, ?, ?)
                """,
                (program_id, step["step"], cursor_time, end_time),
            )
            cursor_time = end_time

        # Ersten Schritt aktivieren
        self._apply_step(steps[0], state)
        state.active_program_id    = program_id
        state.active_program_name  = pgm["name"]
        state.active_program_step  = 1
        state.program_step_started = now
        state.program_step_end     = now + steps[0]["duration_h"] * 3600

        logger.info(
            f"Reifeprogramm '{pgm['name']}' gestartet — "
            f"{len(steps)} Schritte, erster Schritt: "
            f"{steps[0]['temp']}°C / {steps[0]['moist']}%rF für {steps[0]['duration_h']}h"
        )
        return True

    # ── Programm stoppen ─────────────────────────────────────────────────────

    def stop_program(self, state: "SystemState") -> None:
        """Beendet das laufende Programm sofort."""
        if not state.active_program_id:
            return

        self._db.execute(
            "UPDATE riping_archive SET time_step_ended = ? WHERE time_step_ended IS NULL",
            (time.time(),),
        )
        name = state.active_program_name
        state.active_program_id    = None
        state.active_program_name  = ""
        state.active_program_step  = 0
        state.program_step_started = None
        state.program_step_end     = None
        logger.info(f"Reifeprogramm '{name}' manuell gestoppt.")

    # ── Zyklus-Check ─────────────────────────────────────────────────────────

    def check_and_advance(self, state: "SystemState") -> bool:
        """
        Prüft ob der aktuelle Schritt abgelaufen ist und schaltet ggf. weiter.
        Wird vom Controller einmal pro Zyklus aufgerufen.

        Returns:
            True wenn eine Änderung stattgefunden hat (neuer Schritt oder Ende).
        """
        if not state.active_program_id:
            return False

        now = time.time()

        # Schritt noch aktiv?
        if state.program_step_end and now < state.program_step_end:
            return False

        # Aktuellen Archiv-Eintrag abschließen
        current_entry = self._db.fetchone(
            """
            SELECT id, riping_pgm, riping_step
            FROM   riping_archive
            WHERE  riping_pgm = ? AND riping_step = ? AND time_step_ended IS NULL
            """,
            (state.active_program_id, state.active_program_step),
        )
        if current_entry:
            self._db.execute(
                "UPDATE riping_archive SET time_step_ended = ? WHERE id = ?",
                (now, current_entry["id"]),
            )

        # Nächsten Schritt suchen
        next_step_num = state.active_program_step + 1
        next_step = self._db.fetchone(
            """
            SELECT rs.*, ra.start_time, ra.end_time
            FROM   riping_steps rs
            JOIN   riping_archive ra
                   ON  ra.riping_pgm  = rs.riping_pgms_id
                   AND ra.riping_step = rs.step
            WHERE  rs.riping_pgms_id = ? AND rs.step = ?
            """,
            (state.active_program_id, next_step_num),
        )

        if next_step:
            # Nächsten Schritt aktivieren
            self._apply_step(next_step, state)
            state.active_program_step  = next_step_num
            state.program_step_started = next_step["start_time"]
            state.program_step_end     = next_step["end_time"]
            logger.info(
                f"Reifeprogramm '{state.active_program_name}': "
                f"Schritt {next_step_num} → {next_step['temp']}°C / "
                f"{next_step['moist']}%rF für {next_step['duration_h']}h"
            )
            return True
        else:
            # Alle Schritte abgeschlossen
            pgm_name = state.active_program_name
            state.active_program_id    = None
            state.active_program_name  = ""
            state.active_program_step  = 0
            state.program_step_started = None
            state.program_step_end     = None
            logger.info(f"Reifeprogramm '{pgm_name}' erfolgreich abgeschlossen.")
            return True

    # ── Status für API ────────────────────────────────────────────────────────

    def get_status(self, state: "SystemState") -> dict:
        """Liefert den aktuellen Programmstatus für die API."""
        if not state.active_program_id:
            return {"active": False}

        now = time.time()
        remaining_secs = (
            int(max(0, state.program_step_end - now))
            if state.program_step_end else 0
        )

        # Gesamtfortschritt berechnen
        total_steps = self._db.fetchscalar(
            "SELECT COUNT(*) FROM riping_steps WHERE riping_pgms_id = ?",
            (state.active_program_id,),
            default=0,
        )

        # Schritt-Beschreibung
        step_info = self._db.fetchone(
            "SELECT * FROM riping_steps WHERE riping_pgms_id = ? AND step = ?",
            (state.active_program_id, state.active_program_step),
        )

        return {
            "active":           True,
            "program_id":       state.active_program_id,
            "program_name":     state.active_program_name,
            "current_step":     state.active_program_step,
            "total_steps":      total_steps,
            "step_description": step_info["description"] if step_info else "",
            "step_target_temp": step_info["temp"]  if step_info else None,
            "step_target_hum":  step_info["moist"] if step_info else None,
            "step_duration_h":  step_info["duration_h"] if step_info else None,
            "remaining_secs":   remaining_secs,
            "remaining_h":      round(remaining_secs / 3600, 1),
            "step_started_at":  state.program_step_started,
            "step_ends_at":     state.program_step_end,
        }

    # ── Hilfsfunktionen ───────────────────────────────────────────────────────

    def _apply_step(self, step: dict, state: "SystemState") -> None:
        """Schreibt die Sollwerte eines Schritts in den State."""
        state.target_temp = float(step["temp"])
        state.target_hum  = float(step["moist"])
        if step.get("modes_id") is not None:
            state.mode = int(step["modes_id"])
        logger.debug(
            f"Schritt {step['step']} angewendet: "
            f"{state.target_temp}°C / {state.target_hum}%rF, Modus {state.mode}"
        )
