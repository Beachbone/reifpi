"""
test_humidifier_guard.py — Trockenlauf-Schutz Befeuchter + Benachrichtigung.

Prüft Erkennung (Befeuchter läuft, Feuchte steigt nicht), keine Fehlalarme
bei funktionierendem Befeuchter oder kurzem Einbruch, Sperre über Neustart,
Freigabe, Abschalten des Schutzes, Migration v9 und den Melder.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app.notifications.dispatcher as dispatcher_mod
from app.db.archive_writer import ArchiveWriter
from app.db.database import Database
from app.db.migrations import apply_migrations, CURRENT_SCHEMA_VERSION
from app.db.schema import ALL_TABLES, TABLE_CONFIG
from app.core.controller import ReifpiController, MODE_AUTO
from app.core.gpio_manager import GpioManager
from app.core.program_runner import ProgramRunner
from app.core.sensors import SensorManager, SimulatedSensor, NoneSensor
from app.core.state import StateManager
from app.config import DevConfig
from app.notifications.dispatcher import NotificationDispatcher
from tests.test_phase1 import get_default_config
from tests.test_phase3 import make_db

T0  = 1_800_000_000.0
MIN = 60


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, subject, body, priority="high", tags=None):
        self.sent.append((subject, body, priority, tags))


def make():
    db    = make_db()
    sm    = StateManager("/tmp/test_guard_state.json", db)
    state = sm.load_from_db()
    note  = FakeNotifier()
    ctrl  = ReifpiController(
        state=state, state_manager=sm,
        sensor_manager=SensorManager(SimulatedSensor(15.0, 70.0), NoneSensor()),
        gpio=GpioManager({"fanu": 27, "cool": 23, "fana": 17, "mois": 22, "heat": 24}, dummy=True),
        archive_writer=ArchiveWriter(db, interval_secs=9999),
        program_runner=ProgramRunner(db),
        notifier=note,
    )
    s = state
    s.mode, s.target_temp, s.target_hum, s.hum_hyst_mois = MODE_AUTO, 15.0, 77.0, 2.0
    s.temp_intern, s.hum_intern = 15.0, 70.0
    s.abs_hum_intern = s.abs_hum_extern = None
    return db, ctrl, s, note, sm


def run_minutes(ctrl, s, hums, start=T0):
    """Regelt einmal pro Minute mit der jeweiligen Feuchte."""
    for i, h in enumerate(hums):
        s.hum_intern = h
        ctrl._regulate_humidity()
        ctrl._check_humidifier(start + i * MIN)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_defaults():
    print("── Standardwerte ────────────────────────────────")
    db, ctrl, s, note, sm = make()
    assert (s.mois_dry_check_min, s.mois_dry_min_rise, s.mois_fault) == (15, 1.0, False)
    print("  15 min / 1 %rF, frei ✓")
    db.close()


def test_working_humidifier_no_alarm():
    print("── Befeuchter arbeitet → kein Alarm ─────────────")
    db, ctrl, s, note, sm = make()
    run_minutes(ctrl, s, [70.0 + 0.15 * i for i in range(20)])   # +3 %rF in 20 min
    assert not s.mois_fault and not note.sent
    print("  kein Alarm ✓")
    db.close()


def test_dry_run_detected():
    print("── Trockenlauf erkannt ──────────────────────────")
    db, ctrl, s, note, sm = make()
    run_minutes(ctrl, s, [70.0 + 0.02 * i for i in range(17)])   # +0,3 %rF
    assert s.mois_fault, "Sperre erwartet"
    assert not s.relay_mois, "Befeuchter muss aus sein"
    assert db.fetchscalar("SELECT mois_fault_since FROM config") > 0
    assert len(note.sent) == 1
    subject, body, prio, tags = note.sent[0]
    assert subject == "Befeuchter abgeschaltet" and prio == "high"
    assert "Wassertank" in body and "15 min" in body
    print(f"  gesperrt, gemeldet: {body.splitlines()[0]} ✓")
    db.close()


def test_dip_then_rise_no_alarm():
    print("── Erst Einbruch, dann Anstieg → kein Alarm ─────")
    db, ctrl, s, note, sm = make()
    hums = [72.0, 71.0, 70.0, 69.0] + [69.0 + 0.12 * i for i in range(14)]
    run_minutes(ctrl, s, hums)
    assert not s.mois_fault, "Anstieg ab Tiefstwert reicht"
    print("  Tiefstwert als Bezug ✓")
    db.close()


def test_interrupted_run_resets_timer():
    print("── Unterbrochene Laufzeit zählt neu ─────────────")
    db, ctrl, s, note, sm = make()
    run_minutes(ctrl, s, [70.0] * 10)
    s.hum_intern = 77.5                                # Soll erreicht → aus
    ctrl._regulate_humidity(); ctrl._check_humidifier(T0 + 10 * MIN)
    assert not s.relay_mois and s.mois_on_since is None
    run_minutes(ctrl, s, [70.0] * 10, start=T0 + 11 * MIN)
    assert not s.mois_fault
    print("  2 × 10 min ≠ 15 min am Stück ✓")
    db.close()


def test_fault_blocks_and_survives_restart():
    print("── Sperre hält, auch nach Neustart ──────────────")
    db, ctrl, s, note, sm = make()
    run_minutes(ctrl, s, [70.0] * 17)
    assert s.mois_fault
    s.hum_intern = 60.0
    ctrl._regulate_humidity()
    assert not s.relay_mois, "gesperrt trotz sehr trockener Luft"

    fresh = sm.load_from_db()
    assert fresh.mois_fault, "Sperre muss Neustart überstehen"
    print("  bleibt aus, auch nach Neustart ✓")
    db.close()


def test_reset_releases():
    print("── Freigabe im Dashboard ────────────────────────")
    db, ctrl, s, note, sm = make()
    run_minutes(ctrl, s, [70.0] * 17)
    ctrl.reset_humidifier_fault()
    assert not s.mois_fault
    assert db.fetchscalar("SELECT mois_fault_since FROM config") == 0
    s.hum_intern = 70.0
    ctrl._regulate_humidity()
    assert s.relay_mois, "Befeuchter darf wieder"
    assert not sm.load_from_db().mois_fault
    print("  frei, schaltet wieder ✓")
    db.close()


def test_guard_disabled():
    print("── Schutz abgeschaltet ──────────────────────────")
    db, ctrl, s, note, sm = make()
    s.mois_dry_check_min = 0
    run_minutes(ctrl, s, [70.0] * 30)
    assert not s.mois_fault and s.relay_mois
    print("  kein Eingriff ✓")
    db.close()


def test_full_cycle_with_gpio():
    print("── Kompletter Regelzyklus ───────────────────────")
    db, ctrl, s, note, sm = make()
    db.execute("UPDATE config SET mode = 1, target_hum = 77, hum_hyst_mois = 2 WHERE id = 1")
    s.mois_on_since, s.mois_on_min_hum = time.time() - 16 * MIN, 70.0   # läuft schon 16 min
    s.relay_mois = True
    ctrl._run_cycle()                                  # Simulator liefert 70 %rF
    assert s.mois_fault and not ctrl._gpio.get_state("mois")
    assert note.sent
    print("  GPIO aus, Meldung raus ✓")
    db.close()


def test_migration_v8_to_v9():
    print("── Migration v8 → v9 ────────────────────────────")
    v9 = {"mois_dry_check_min", "mois_dry_min_rise", "mois_fault_since"}
    db = Database(":memory:")
    db.connect()
    old = "\n".join(l for l in TABLE_CONFIG.splitlines()
                    if not any(l.strip().startswith(c) for c in v9))
    for st in ALL_TABLES:
        db.execute(old if st is TABLE_CONFIG else st)
    db.execute("INSERT INTO schema_version (version, applied_at) VALUES (8, 0)")
    db.execute("INSERT INTO config (id) VALUES (1)")
    apply_migrations(db, get_default_config(DevConfig))
    row = db.fetchone("SELECT * FROM config")
    assert row["mois_dry_check_min"] == 15 and row["mois_fault_since"] == 0
    assert db.fetchscalar("SELECT MAX(version) FROM schema_version") == CURRENT_SCHEMA_VERSION >= 9
    print("  3 Spalten, Schutz an ✓")
    db.close()


def test_dispatcher_channels_and_mail_throttle():
    print("── Melder: ntfy + E-Mail, Mindestabstand ────────")
    db = make_db()
    db.execute(
        "UPDATE config SET ntfy_enabled = 1, ntfy_topic = 't', mail_enabled = 1, "
        "mail_smtp = 'smtp', mail_user = 'u', mail_receiver = 'r', email_sleep = 60 WHERE id = 1"
    )
    ntfy, mail = [], []
    orig_n, orig_m = dispatcher_mod.NtfyNotifier.send, dispatcher_mod.EmailNotifier.send
    dispatcher_mod.NtfyNotifier.send  = lambda self, title, message, priority="default", tags=None: ntfy.append(title) or True
    dispatcher_mod.EmailNotifier.send = lambda self, subject, body: mail.append(subject) or True
    try:
        d = NotificationDispatcher(db)
        d.send("Befeuchter abgeschaltet", "Text")
        d.send("Safe-Mode aktiviert", "Text")
        deadline = time.time() + 3
        while len(ntfy) < 2 and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.2)
    finally:
        dispatcher_mod.NtfyNotifier.send, dispatcher_mod.EmailNotifier.send = orig_n, orig_m
    assert sorted(ntfy) == ["Reifeschrank: Befeuchter abgeschaltet", "Reifeschrank: Safe-Mode aktiviert"]
    assert len(mail) == 1, f"E-Mail-Mindestabstand: {mail}"
    print("  2 × ntfy, 1 × E-Mail ✓")
    db.close()


if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    try:
        for t in tests:
            t()
        print("\n✅  Alle Tests bestanden.\n")
    except AssertionError as e:
        print(f"\n❌  Test fehlgeschlagen: {e}\n")
        sys.exit(1)
