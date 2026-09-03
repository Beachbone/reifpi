"""
database.py — Persistente SQLite-Verbindung mit Thread-Safety.

Eine einzige Verbindung für die gesamte Laufzeit des Prozesses.
Kein connect/close pro Funktion mehr.
"""

import sqlite3
import threading
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class Database:
    """
    Persistente SQLite-Verbindung.

    Thread-sicher durch einen Lock. WAL-Modus für bessere Parallelität
    (mehrere Leser gleichzeitig möglich, ein Schreiber).
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def connect(self) -> None:
        """Öffnet die Datenbankverbindung. Einmalig beim Start aufrufen."""
        self._conn = sqlite3.connect(
            self._db_path,
            check_same_thread=False,   # Wir sichern Thread-Safety selbst per Lock
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self._conn.row_factory = sqlite3.Row   # Ergebnisse als dict-ähnliche Objekte
        self._conn.execute("PRAGMA journal_mode = WAL")   # Write-Ahead-Log
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA synchronous = NORMAL") # Gut genug, spart SD-Writes
        logger.info(f"Datenbankverbindung geöffnet: {self._db_path}")

    def close(self) -> None:
        """Schließt die Datenbankverbindung. Beim Shutdown aufrufen."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("Datenbankverbindung geschlossen.")

    def execute(
        self, sql: str, params: tuple | list = ()
    ) -> sqlite3.Cursor:
        """Führt ein einzelnes SQL-Statement aus (INSERT/UPDATE/DELETE/CREATE)."""
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor

    def executemany(
        self, sql: str, params_list: list[tuple | list]
    ) -> None:
        """Führt ein SQL-Statement für mehrere Parametersätze aus (Batch-Insert)."""
        with self._lock:
            self._conn.executemany(sql, params_list)
            self._conn.commit()

    def execute_script(self, sql: str) -> None:
        """Führt mehrere SQL-Statements aus (für Schema-Initialisierung)."""
        with self._lock:
            self._conn.executescript(sql)

    def fetchone(
        self, sql: str, params: tuple | list = ()
    ) -> dict | None:
        """Gibt eine einzelne Zeile als dict zurück, oder None."""
        with self._lock:
            cursor = self._conn.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None

    def fetchall(
        self, sql: str, params: tuple | list = ()
    ) -> list[dict]:
        """Gibt alle Zeilen als Liste von dicts zurück."""
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def fetchscalar(
        self, sql: str, params: tuple | list = (), default: Any = None
    ) -> Any:
        """Gibt den ersten Wert der ersten Zeile zurück."""
        row = self.fetchone(sql, params)
        if row is None:
            return default
        return next(iter(row.values()), default)

    @property
    def is_connected(self) -> bool:
        return self._conn is not None
