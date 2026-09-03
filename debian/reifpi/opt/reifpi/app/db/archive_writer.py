"""
archive_writer.py — Gepufferte Archiv-Schreibungen für SD-Schonung.

Messwerte werden im RAM gesammelt und alle INTERVAL_SECS als Batch
in die DB geschrieben. Typisch: alle 5 Minuten ein INSERT statt
jeden Regelzyklus (30s).
"""

import logging
import time
from typing import TYPE_CHECKING

from app.db.database import Database

if TYPE_CHECKING:
    from app.core.state import SystemState

logger = logging.getLogger(__name__)


class ArchiveWriter:
    """
    Puffert Messwerte im RAM und schreibt sie gebatcht in die DB.
    """

    def __init__(self, db: Database, interval_secs: int = 300) -> None:
        self._db = db
        self._interval = interval_secs
        self._buffer: list[dict] = []
        self._last_flush: float = time.time()

    def add(self, state: "SystemState") -> None:
        """
        Nimmt den aktuellen State auf und fügt ihn dem Buffer hinzu.
        Wird vom Controller nach jedem Zyklus aufgerufen.
        """
        if state.temp_intern is None:
            return   # Keine Messung → nicht archivieren

        self._buffer.append({
            "timestamp":    state.timestamp,
            "mode":         state.mode,
            "riping_pgm":   state.active_program_id or 0,
            "riping_step":  state.active_program_step,
            "temp_intern":  state.temp_intern,
            "hum_intern":   state.hum_intern,
            "temp_extern":  state.temp_extern,
            "hum_extern":   state.hum_extern,
            "abs_hum_intern": state.abs_hum_intern,
            "abs_hum_extern": state.abs_hum_extern,
            "weight":       state.weight,
            "relay_heat":   int(state.relay_heat),
            "relay_cool":   int(state.relay_cool),
            "relay_mois":   int(state.relay_mois),
            "relay_fanu":   int(state.relay_fanu),
            "relay_fana":   int(state.relay_fana),
            "target_temp":  state.target_temp,
            "target_hum":   state.target_hum,
        })

    def maybe_flush(self) -> None:
        """
        Schreibt den Buffer in die DB wenn das Intervall abgelaufen ist.
        Wird vom Controller nach jedem Zyklus aufgerufen.
        """
        if not self._buffer:
            return
        if time.time() - self._last_flush < self._interval:
            return
        self.flush()

    def flush(self) -> None:
        """Schreibt alle gepufferten Einträge sofort in die DB."""
        if not self._buffer:
            return

        entries = self._buffer.copy()
        self._buffer.clear()
        self._last_flush = time.time()

        try:
            self._db.executemany(
                """
                INSERT INTO state_archive (
                    timestamp, mode, riping_pgm, riping_step,
                    temp_intern, hum_intern,
                    temp_extern, hum_extern,
                    abs_hum_intern, abs_hum_extern,
                    weight,
                    relay_heat, relay_cool, relay_mois, relay_fanu, relay_fana,
                    target_temp, target_hum
                ) VALUES (
                    :timestamp, :mode, :riping_pgm, :riping_step,
                    :temp_intern, :hum_intern,
                    :temp_extern, :hum_extern,
                    :abs_hum_intern, :abs_hum_extern,
                    :weight,
                    :relay_heat, :relay_cool, :relay_mois, :relay_fanu, :relay_fana,
                    :target_temp, :target_hum
                )
                """,
                entries,
            )
            logger.debug(f"Archiv: {len(entries)} Einträge geschrieben.")
            self._trim_archive()
        except Exception as e:
            logger.error(f"Archiv-Flush fehlgeschlagen: {e}")
            # Buffer nicht verloren — beim nächsten flush() erneut versuchen
            self._buffer = entries + self._buffer

    def _trim_archive(self) -> None:
        """Löscht älteste Einträge wenn max_entries überschritten."""
        try:
            max_entries = self._db.fetchscalar(
                "SELECT arch_handling FROM config WHERE id = 1",
                default=12000,
            )
            if not max_entries or max_entries <= 0:
                return

            count = self._db.fetchscalar(
                "SELECT COUNT(*) FROM state_archive",
                default=0,
            )
            if count <= max_entries:
                return

            to_delete = count - max_entries
            self._db.execute(
                """
                DELETE FROM state_archive
                WHERE id IN (
                    SELECT id FROM state_archive
                    ORDER BY timestamp ASC
                    LIMIT ?
                )
                """,
                (to_delete,),
            )
            logger.info(f"Archiv: {to_delete} alte Einträge gelöscht.")
        except Exception as e:
            logger.warning(f"Archiv-Trim fehlgeschlagen: {e}")

    @property
    def buffer_size(self) -> int:
        return len(self._buffer)
