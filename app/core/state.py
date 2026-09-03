"""
state.py — Systemzustand als Dataclass + StateManager.

SystemState ist die einzige Quelle der Wahrheit für den RAM-Zustand.
Keine globalen Variablen. Der Controller liest und schreibt ausschließlich
über dieses Objekt.

Die state.json unter /run/reifeschrank/ (tmpfs) ist die Brücke zur UI:
- Controller schreibt sie nach jedem Zyklus
- Flask-API liest sie für GET /api/status
- Kein DB-Zugriff für Live-Daten mehr
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Any

from app.db.database import Database

logger = logging.getLogger(__name__)


@dataclass
class SystemState:
    """Vollständiger Systemzustand im RAM."""

    # ── Konfiguration (aus DB geladen, bei Änderung neu gelesen) ─────────────
    power:                  bool  = True
    mode:                   int   = 1
    target_temp:            float = 14.0
    target_hum:             float = 80.0
    hum_hyst_mois:          float = 5.0
    temp_hyst_cool:         float = 0.5
    hum_hyst_fana:          float = 2.0
    temp_hyst_heat:         float = 1.0
    abs_hum_threshold:      float = 0.5
    aht21_hyst_humidify:    float = 4.0
    aht21_hyst_protect:     float = 2.0
    refreshrate:            int   = 30

    # ── GPIO-Pins (aus Config, Neustart erforderlich bei Änderung) ───────────
    gpio_pin_fanu:          int   = 27
    gpio_pin_cool:          int   = 23
    gpio_pin_fana:          int   = 17
    gpio_pin_mois:          int   = 22
    gpio_pin_heat:          int   = 24
    relay_active_high:      bool  = False

    # ── Sensordaten (aktuellste Messung) ─────────────────────────────────────
    temp_intern:            float | None = None
    hum_intern:             float | None = None
    temp_extern:            float | None = None
    hum_extern:             float | None = None
    abs_hum_intern:         float | None = None
    abs_hum_extern:         float | None = None
    weight:                 float | None = None   # Waage (optional)

    # ── Relais-Zustände ───────────────────────────────────────────────────────
    relay_heat:             bool = False
    relay_cool:             bool = False
    relay_mois:             bool = False
    relay_fanu:             bool = False
    relay_fana:             bool = False

    # ── Kompressor-Schutz ─────────────────────────────────────────────────────
    compressor_last_off:    float = 0.0
    # Unix-Timestamp: wann der Kompressor zuletzt ausgeschaltet wurde
    compressor_protected:   bool  = False
    # True wenn Kompressor gerade wegen Schutzzeit gesperrt ist

    # ── Safe-Mode ─────────────────────────────────────────────────────────────
    safe_mode:              bool  = False
    safe_mode_reason:       str   = ""

    # ── Reifeprogramm ─────────────────────────────────────────────────────────
    active_program_id:      int | None = None
    active_program_name:    str        = ""
    active_program_step:    int        = 0
    program_step_started:   float | None = None
    program_step_end:       float | None = None
    # Geplantes Ende des aktuellen Schritts (Unix-Timestamp)

    # ── Diagnose ──────────────────────────────────────────────────────────────
    sensor_fail_count:      int   = 0
    cycle_count:            int   = 0
    last_error:             str   = ""
    timestamp:              float = field(default_factory=time.time)
    uptime_start:           float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Serialisiert den State für JSON-Export."""
        d = asdict(self)
        # Berechnete Felder ergänzen
        d["uptime_secs"] = int(time.time() - self.uptime_start)
        if self.program_step_end and self.program_step_started:
            d["program_step_remaining_secs"] = max(
                0, int(self.program_step_end - time.time())
            )
        else:
            d["program_step_remaining_secs"] = None
        return d


class StateManager:
    """
    Lädt den initialen State aus der DB und schreibt ihn nach jedem
    Zyklus als JSON in die RAM-Datei.
    """

    def __init__(self, state_file: str, db: Database) -> None:
        self._state_file = state_file
        self._db = db
        self._ensure_state_dir()

    def load_from_db(self) -> SystemState:
        """
        Lädt Konfiguration aus DB und gibt einen initialen SystemState zurück.
        Wird einmalig beim Start aufgerufen.
        """
        row = self._db.fetchone("SELECT * FROM config WHERE id = 1")
        if not row:
            logger.warning("Keine Konfiguration in DB — verwende Defaults.")
            return SystemState()

        state = SystemState(
            power               = bool(row["power"]),
            mode                = int(row["mode"]),
            target_temp         = float(row["target_temp"]),
            target_hum          = float(row["target_hum"]),
            hum_hyst_mois       = float(row["hum_hyst_mois"]),
            temp_hyst_cool      = float(row["temp_hyst_cool"]),
            hum_hyst_fana       = float(row["hum_hyst_fana"]),
            temp_hyst_heat      = float(row["temp_hyst_heat"]),
            abs_hum_threshold   = float(row["abs_hum_threshold"]),
            aht21_hyst_humidify = float(row["aht21_hyst_humidify"]),
            aht21_hyst_protect  = float(row["aht21_hyst_protect"]),
            refreshrate         = int(row["refreshrate"]),
            gpio_pin_fanu       = int(row["gpio_pin_fanu"]),
            gpio_pin_cool       = int(row["gpio_pin_cool"]),
            gpio_pin_fana       = int(row["gpio_pin_fana"]),
            gpio_pin_mois       = int(row["gpio_pin_mois"]),
            gpio_pin_heat       = int(row["gpio_pin_heat"]),
            relay_active_high   = bool(row["relay_active_high"]),
        )
        logger.info(
            f"State aus DB geladen — Soll: {state.target_temp}°C / "
            f"{state.target_hum}%rF, Modus: {state.mode}"
        )
        return state

    def reload_config(self, state: SystemState) -> bool:
        """
        Liest Konfiguration neu aus DB und aktualisiert state in-place.
        Gibt True zurück wenn sich etwas geändert hat.
        Wird vom Controller nach jedem Zyklus aufgerufen.
        """
        row = self._db.fetchone(
            "SELECT * FROM config WHERE id = 1"
        )
        if not row:
            return False

        changed = False
        fields = [
            ("power",               bool,  "power"),
            ("mode",                int,   "mode"),
            ("target_temp",         float, "target_temp"),
            ("target_hum",          float, "target_hum"),
            ("hum_hyst_mois",       float, "hum_hyst_mois"),
            ("temp_hyst_cool",      float, "temp_hyst_cool"),
            ("hum_hyst_fana",       float, "hum_hyst_fana"),
            ("temp_hyst_heat",      float, "temp_hyst_heat"),
            ("abs_hum_threshold",   float, "abs_hum_threshold"),
            ("aht21_hyst_humidify", float, "aht21_hyst_humidify"),
            ("aht21_hyst_protect",  float, "aht21_hyst_protect"),
            ("refreshrate",         int,   "refreshrate"),
        ]
        for attr, cast, col in fields:
            new_val = cast(row[col])
            if getattr(state, attr) != new_val:
                setattr(state, attr, new_val)
                changed = True

        if changed:
            logger.info("Konfiguration aus DB neu geladen.")
        return changed

    def restore_after_poweroff(self, state: SystemState) -> None:
        """
        Prüft beim Start ob ein Reifeprogramm noch laufen sollte.
        Setzt den Kompressor-Last-Off auf jetzt (Schutzzeit nach Neustart).

        Strategie:
        - Wenn der letzte Schritt in riping_archive noch nicht beendet ist
          (time_step_ended IS NULL) und das geplante Ende noch in der Zukunft
          liegt → Schritt fortsetzen.
        - Bei langem Ausfall (end_time bereits überschritten) → Schritt als
          abgelaufen markieren, ProgramRunner entscheidet ob nächster Schritt.
        """
        # Kompressor-Schutzzeit nach jedem Neustart einhalten
        state.compressor_last_off = time.time()
        logger.info("Kompressor-Schutzzeit nach Neustart gesetzt.")

        # Laufendes Programm wiederherstellen
        row = self._db.fetchone(
            """
            SELECT ra.id, ra.riping_pgm, ra.riping_step,
                   ra.start_time, ra.end_time,
                   rp.name as program_name
            FROM   riping_archive ra
            JOIN   riping_pgms    rp ON rp.id = ra.riping_pgm
            WHERE  ra.time_step_ended IS NULL
            ORDER  BY ra.id DESC
            LIMIT  1
            """
        )
        if not row:
            logger.info("Kein laufendes Reifeprogramm gefunden.")
            return

        now = time.time()
        if row["end_time"] > now:
            # Schritt läuft noch
            state.active_program_id   = row["riping_pgm"]
            state.active_program_name = row["program_name"]
            state.active_program_step = row["riping_step"]
            state.program_step_started = row["start_time"]
            state.program_step_end     = row["end_time"]
            logger.info(
                f"Reifeprogramm '{row['program_name']}' "
                f"Schritt {row['riping_step']} wiederhergestellt. "
                f"Verbleibend: {int((row['end_time'] - now) / 3600)}h"
            )
        else:
            # Schritt ist abgelaufen während Pi aus war
            state.active_program_id   = row["riping_pgm"]
            state.active_program_name = row["program_name"]
            state.active_program_step = row["riping_step"]
            state.program_step_started = row["start_time"]
            state.program_step_end     = row["end_time"]
            logger.info(
                f"Reifeprogramm '{row['program_name']}' Schritt "
                f"{row['riping_step']} ist während Stromausfall abgelaufen — "
                "ProgramRunner wird beim nächsten Zyklus weiterschalten."
            )

    def write_state_file(self, state: SystemState) -> None:
        """
        Schreibt den aktuellen State als JSON nach /run/reifeschrank/state.json.
        Diese Datei liegt auf tmpfs (RAM) — kein SD-Zugriff.
        """
        state.timestamp = time.time()
        try:
            tmp = self._state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(state.to_dict(), f, ensure_ascii=False)
            os.replace(tmp, self._state_file)   # atomares Überschreiben
        except OSError as e:
            logger.error(f"State-Datei konnte nicht geschrieben werden: {e}")

    def read_state_file(self) -> dict | None:
        """Liest die aktuelle State-Datei (für die API)."""
        try:
            with open(self._state_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"State-Datei konnte nicht gelesen werden: {e}")
            return None

    def persist_config(self, state: SystemState) -> None:
        """
        Schreibt die Konfigurationsfelder des State zurück in die DB.
        Wird aufgerufen wenn die API Konfigurationsänderungen empfängt.
        """
        self._db.execute(
            """
            UPDATE config SET
                power               = :power,
                mode                = :mode,
                target_temp         = :target_temp,
                target_hum          = :target_hum,
                hum_hyst_mois       = :hum_hyst_mois,
                temp_hyst_cool      = :temp_hyst_cool,
                hum_hyst_fana       = :hum_hyst_fana,
                temp_hyst_heat      = :temp_hyst_heat,
                abs_hum_threshold   = :abs_hum_threshold,
                aht21_hyst_humidify = :aht21_hyst_humidify,
                aht21_hyst_protect  = :aht21_hyst_protect,
                refreshrate         = :refreshrate,
                updated_at          = :now
            WHERE id = 1
            """,
            {
                "power":               int(state.power),
                "mode":                state.mode,
                "target_temp":         state.target_temp,
                "target_hum":          state.target_hum,
                "hum_hyst_mois":       state.hum_hyst_mois,
                "temp_hyst_cool":      state.temp_hyst_cool,
                "hum_hyst_fana":       state.hum_hyst_fana,
                "temp_hyst_heat":      state.temp_hyst_heat,
                "abs_hum_threshold":   state.abs_hum_threshold,
                "aht21_hyst_humidify": state.aht21_hyst_humidify,
                "aht21_hyst_protect":  state.aht21_hyst_protect,
                "refreshrate":         state.refreshrate,
                "now":                 time.time(),
            },
        )

    def _ensure_state_dir(self) -> None:
        """Erstellt das Verzeichnis für die State-Datei falls nötig."""
        state_dir = os.path.dirname(self._state_file)
        if state_dir and not os.path.exists(state_dir):
            try:
                os.makedirs(state_dir, exist_ok=True)
                logger.info(f"State-Verzeichnis erstellt: {state_dir}")
            except OSError as e:
                logger.warning(f"State-Verzeichnis konnte nicht erstellt werden: {e}")
