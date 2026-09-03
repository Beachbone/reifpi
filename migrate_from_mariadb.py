"""
migrate_from_mariadb.py — Einmalige Migration der alten MariaDB → neue SQLite.

Migriert:
  1. state_archive   (14.530 Messwerte)
  2. riping_pgms     (Reifeprogramme)
  3. riping_steps    (Reifeschritte)

Aufruf auf dem Pi:
    cd /opt/reifpi
    venv/bin/python migrate_from_mariadb.py

Voraussetzung: Die SQLite-DB muss bereits initialisiert sein (einmal run.py
gestartet haben), damit die Tabellen existieren.
"""

import sqlite3
import sys
import time
from datetime import datetime

try:
    import pymysql
except ImportError:
    print("FEHLER: pymysql nicht installiert.")
    print("       venv/bin/pip install pymysql")
    sys.exit(1)

# ── Konfiguration ─────────────────────────────────────────────────────────────

MARIADB = {
    "host":     "localhost",
    "user":     "backy",
    "password": "OpaJochen",
    "database": "oj_cab",
    "charset":  "utf8mb4",
}

# Entspricht dem REIFPI_DB_PATH-Default aus debian/reifpi.service (FHS-Pfad).
SQLITE_PATH = "/var/lib/reifpi/reifeschrank.db"

BATCH_SIZE = 500   # Anzahl Zeilen pro INSERT-Batch

# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def progress(label, done, total):
    pct = int(done / total * 100) if total else 0
    bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
    print(f"\r  {label}: [{bar}] {done}/{total} ({pct}%)", end="", flush=True)


def ts_to_unix(ts) -> float:
    """MariaDB timestamp → Unix-Float."""
    if ts is None:
        return 0.0
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, datetime):
        return ts.timestamp()
    return float(ts)


# ── Verbindungen ──────────────────────────────────────────────────────────────

def connect_mariadb():
    try:
        conn = pymysql.connect(**MARIADB, cursorclass=pymysql.cursors.DictCursor)
        print(f"  MariaDB verbunden: {MARIADB['database']}@{MARIADB['host']}")
        return conn
    except Exception as e:
        print(f"FEHLER MariaDB-Verbindung: {e}")
        sys.exit(1)


def connect_sqlite():
    try:
        conn = sqlite3.connect(SQLITE_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=OFF")   # Während Migration deaktivieren
        print(f"  SQLite verbunden: {SQLITE_PATH}")
        return conn
    except Exception as e:
        print(f"FEHLER SQLite-Verbindung: {e}")
        sys.exit(1)


# ── Migration: state_archive ──────────────────────────────────────────────────

def migrate_state_archive(my_conn, sq_conn):
    print("\n[1/3] state_archive migrieren...")

    # Vorhandene Einträge in SQLite prüfen
    existing = sq_conn.execute("SELECT COUNT(*) FROM state_archive").fetchone()[0]
    if existing > 0:
        answer = input(f"  SQLite enthält bereits {existing} Einträge. Überschreiben? (j/N): ")
        if answer.lower() != "j":
            print("  Übersprungen.")
            return
        sq_conn.execute("DELETE FROM state_archive")
        sq_conn.commit()

    with my_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM state_archive")
        total = cur.fetchone()["n"]

        cur.execute("""
            SELECT timestamp, mode, riping_pgm, riping_step,
                   temp, hum,
                   heat, cool, moist, fan_a, fan_u,
                   target_temp, target_hum
            FROM   state_archive
            ORDER  BY timestamp ASC
        """)

        batch = []
        done  = 0

        for row in cur:
            batch.append((
                ts_to_unix(row["timestamp"]),
                int(row["mode"]        or 0),
                int(row["riping_pgm"]  or 0),
                int(row["riping_step"] or 0),
                float(row["temp"]      or 0),
                float(row["hum"]       or 0),
                None,   # temp_extern
                None,   # hum_extern
                None,   # abs_hum_intern
                None,   # abs_hum_extern
                None,   # weight
                int(row["heat"]        or 0),
                int(row["cool"]        or 0),
                int(row["moist"]       or 0),
                int(row["fan_u"]       or 0),
                int(row["fan_a"]       or 0),
                float(row["target_temp"] or 0),
                float(row["target_hum"]  or 0),
            ))
            done += 1

            if len(batch) >= BATCH_SIZE:
                sq_conn.executemany("""
                    INSERT INTO state_archive
                        (timestamp, mode, riping_pgm, riping_step,
                         temp_intern, hum_intern, temp_extern, hum_extern,
                         abs_hum_intern, abs_hum_extern, weight,
                         relay_heat, relay_cool, relay_mois, relay_fanu, relay_fana,
                         target_temp, target_hum)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, batch)
                sq_conn.commit()
                batch.clear()
                progress("state_archive", done, total)

        if batch:
            sq_conn.executemany("""
                INSERT INTO state_archive
                    (timestamp, mode, riping_pgm, riping_step,
                     temp_intern, hum_intern, temp_extern, hum_extern,
                     abs_hum_intern, abs_hum_extern, weight,
                     relay_heat, relay_cool, relay_mois, relay_fanu, relay_fana,
                     target_temp, target_hum)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, batch)
            sq_conn.commit()
            progress("state_archive", done, total)

    print(f"\n  ✓ {done} Einträge migriert.")


# ── Migration: riping_pgms ────────────────────────────────────────────────────

def migrate_programs(my_conn, sq_conn):
    print("\n[2/3] riping_pgms migrieren...")

    existing = sq_conn.execute("SELECT COUNT(*) FROM riping_pgms").fetchone()[0]
    if existing > 0:
        answer = input(f"  SQLite enthält bereits {existing} Programme. Überschreiben? (j/N): ")
        if answer.lower() != "j":
            print("  Übersprungen.")
            return False
        sq_conn.execute("DELETE FROM riping_steps")
        sq_conn.execute("DELETE FROM riping_pgms")
        sq_conn.commit()

    with my_conn.cursor() as cur:
        cur.execute("SELECT id, name, description FROM riping_pgms ORDER BY id ASC")
        rows = cur.fetchall()

    id_map = {}   # alte MariaDB-ID → neue SQLite-ID
    now    = time.time()

    for row in rows:
        cursor = sq_conn.execute(
            "INSERT INTO riping_pgms (name, description, created_at) VALUES (?, ?, ?)",
            (row["name"], row["description"] or "", now),
        )
        id_map[row["id"]] = cursor.lastrowid
        print(f"  Programm: '{row['name']}' (alt ID {row['id']} → neu ID {cursor.lastrowid})")

    sq_conn.commit()
    print(f"  ✓ {len(rows)} Programme migriert.")
    return id_map


# ── Migration: riping_steps ───────────────────────────────────────────────────

def migrate_steps(my_conn, sq_conn, id_map):
    print("\n[3/3] riping_steps migrieren...")

    with my_conn.cursor() as cur:
        cur.execute("""
            SELECT riping_pgms_id, step, duration_h, temp, moist,
                   description, modes_id
            FROM   riping_steps
            ORDER  BY riping_pgms_id ASC, step ASC
        """)
        rows = cur.fetchall()

    count = 0
    for row in rows:
        new_pgm_id = id_map.get(row["riping_pgms_id"])
        if new_pgm_id is None:
            print(f"  WARNUNG: Programm-ID {row['riping_pgms_id']} nicht in id_map — Schritt übersprungen.")
            continue
        sq_conn.execute("""
            INSERT INTO riping_steps
                (riping_pgms_id, step, duration_h, temp, moist,
                 description, modes_id, target_weight_loss_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
        """, (
            new_pgm_id,
            int(row["step"]),
            int(row["duration_h"]),
            float(row["temp"]),
            float(row["moist"]),
            row["description"] or "",
            int(row["modes_id"] or 1),
        ))
        count += 1

    sq_conn.commit()
    print(f"  ✓ {count} Schritte migriert.")


# ── Abschluss-Check ───────────────────────────────────────────────────────────

def print_summary(sq_conn):
    archive = sq_conn.execute("SELECT COUNT(*) FROM state_archive").fetchone()[0]
    pgms    = sq_conn.execute("SELECT COUNT(*) FROM riping_pgms").fetchone()[0]
    steps   = sq_conn.execute("SELECT COUNT(*) FROM riping_steps").fetchone()[0]

    print("\n=== Migrations-Zusammenfassung ===")
    print(f"  state_archive : {archive:>6} Einträge")
    print(f"  riping_pgms   : {pgms:>6} Programme")
    print(f"  riping_steps  : {steps:>6} Schritte")
    print("==================================")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=== Reifeschrank: MariaDB → SQLite Migration ===\n")

    my_conn = connect_mariadb()
    sq_conn = connect_sqlite()

    try:
        migrate_state_archive(my_conn, sq_conn)
        id_map = migrate_programs(my_conn, sq_conn)
        if id_map:
            migrate_steps(my_conn, sq_conn, id_map)
        print_summary(sq_conn)
    finally:
        sq_conn.execute("PRAGMA foreign_keys=ON")
        my_conn.close()
        sq_conn.close()

    print("\nMigration abgeschlossen.")


if __name__ == "__main__":
    main()
