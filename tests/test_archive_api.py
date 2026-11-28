"""
test_archive_api.py — Messdaten für die Diagramme.

Prüft Zeitraum, Ausdünnen in SQLite (erster und letzter Punkt bleiben,
höchstens limit Punkte), unverändertes Ergebnis bei kleinem Archiv und
die Laufzeit bei 30 Tagen Archiv.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask

import app.api.archive as archive
from tests.test_phase3 import make_db

TICK = 10


def make(rows, end=None):
    db  = make_db()
    end = end or time.time()
    db.executemany(
        "INSERT INTO state_archive (timestamp, mode, temp_intern, hum_intern, "
        "relay_cool, target_temp, target_hum) VALUES (?, 1, ?, 78.0, ?, 14.0, 78.0)",
        [(end - (rows - 1 - i) * TICK, 14.0 + i % 7 * 0.1, i % 2) for i in range(rows)],
    )
    flask_app = Flask(__name__)
    flask_app.db = db
    flask_app.register_blueprint(archive.bp)
    return db, flask_app.test_client()


def test_small_archive_unchanged():
    print("── Kleines Archiv: alle Punkte ──────────────────")
    db, client = make(100)
    rows = client.get("/api/archive?hours=24&limit=600").get_json()
    assert len(rows) == 100
    assert [r["ts"] for r in rows] == sorted(r["ts"] for r in rows)
    assert set(rows[0]) >= {"ts", "temp", "hum", "relay_cool", "target_temp"}
    print("  100 von 100 ✓")
    db.close()


def test_hours_filter():
    print("── Zeitraum ─────────────────────────────────────")
    db, client = make(2 * 360)                            # 2 h
    rows = client.get("/api/archive?hours=1").get_json()
    assert 359 <= len(rows) <= 361
    print(f"  1 h → {len(rows)} Punkte ✓")
    db.close()


def test_downsample_keeps_ends():
    print("── Ausdünnen: Grenzen und Anzahl ────────────────")
    end = time.time()
    db, client = make(10_000, end)
    for limit in (3, 4, 600, 9_999):
        rows = client.get(f"/api/archive?hours=48&limit={limit}").get_json()
        assert 2 <= len(rows) <= limit, (limit, len(rows))
        assert rows[0]["ts"] == end - 9_999 * TICK and rows[-1]["ts"] == end
    assert len(client.get("/api/archive?hours=48&limit=600").get_json()) >= 550
    assert client.get("/api/archive?limit=2").status_code == 400
    print("  erster/letzter Punkt bleiben, höchstens limit ✓")
    db.close()


def test_month_archive_fast():
    print("── 30 Tage Archiv ───────────────────────────────")
    db, client = make(259_200)
    t = time.perf_counter()
    rows = client.get("/api/archive?hours=720&limit=600").get_json()
    secs = time.perf_counter() - t
    assert 550 <= len(rows) <= 600
    assert secs < 2.0, f"{secs:.2f} s"
    print(f"  {len(rows)} Punkte in {secs:.2f} s ✓")
    db.close()


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    try:
        for t in tests:
            t()
        print("\n✅  Alle Tests bestanden.\n")
    except AssertionError as e:
        print(f"\n❌  Test fehlgeschlagen: {e}\n")
        sys.exit(1)
