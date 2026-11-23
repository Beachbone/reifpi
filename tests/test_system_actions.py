"""
test_system_actions.py — Ausschalten / Neustart aus dem Dashboard.

Prüft die systemctl-Aufrufe (statt sudo, das an NoNewPrivileges scheitert),
Relais-Abschaltung vor Poweroff/Reboot, Fehlerweitergabe an die Oberfläche
und dass der Dev-Modus nichts auslöst.
"""

import os
import subprocess
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from flask import Flask

import app.api.control as control


class FakeController:
    def __init__(self):
        self.off_calls = 0

    def all_off_immediate(self):
        self.off_calls += 1


def make(dummy=False, returncode=0, stderr=""):
    flask_app = Flask(__name__)
    flask_app.config["GPIO_DUMMY"] = dummy
    flask_app.controller = FakeController()
    flask_app.register_blueprint(control.bp)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)

    control.subprocess.run = fake_run
    return flask_app.test_client(), flask_app.controller, calls


def teardown_function(_):
    control.subprocess.run = subprocess.run


def test_shutdown_and_reboot_switch_relays_off():
    print("── Herunterfahren / Neustart Pi ─────────────────")
    for action, verb in (("shutdown", "poweroff"), ("reboot", "reboot")):
        client, ctrl, calls = make()
        r = client.post(f"/api/system/{action}")
        assert r.status_code == 200 and r.get_json()["ok"]
        assert calls == [["systemctl", "--no-ask-password", verb]]
        assert ctrl.off_calls == 1, "Relais müssen vorher aus"
    teardown_function(None)
    print("  systemctl poweroff/reboot, Relais aus ✓")


def test_service_restart_only():
    print("── Nur Steuerung neu starten ────────────────────")
    client, ctrl, calls = make()
    r = client.post("/api/system/restart")
    assert r.status_code == 200
    assert calls == [["systemctl", "--no-ask-password", "--no-block", "restart", "reifpi.service"]]
    assert ctrl.off_calls == 0
    teardown_function(None)
    print("  nur reifpi.service ✓")


def test_failure_reported():
    print("── Fehler wird gemeldet ─────────────────────────")
    client, _, _ = make(returncode=1, stderr="Access denied")
    r = client.post("/api/system/reboot")
    assert r.status_code == 500 and r.get_json()["error"] == "Access denied"
    teardown_function(None)
    print("  500 + Text ✓")


def test_restart_killed_by_systemd_is_ok():
    print("── Dienst-Neustart: SIGTERM = angenommen ────────")
    client, _, _ = make(returncode=-15)
    assert client.post("/api/system/restart").status_code == 200
    client, _, _ = make(returncode=-15)
    assert client.post("/api/system/reboot").status_code == 500, "nur beim Dienst-Neustart"
    teardown_function(None)
    print("  restart 200, reboot weiterhin Fehler ✓")


def test_unknown_action_and_dev_mode():
    print("── Unbekannte Aktion / Dev-Modus ────────────────")
    client, _, calls = make()
    assert client.post("/api/system/format").status_code == 404
    client, ctrl, calls = make(dummy=True)
    r = client.post("/api/system/shutdown")
    assert r.get_json().get("simulated") and not calls and ctrl.off_calls == 0
    teardown_function(None)
    print("  404, Dev-Modus löst nichts aus ✓")


if __name__ == "__main__":
    try:
        for t in (test_shutdown_and_reboot_switch_relays_off, test_service_restart_only,
                  test_failure_reported, test_restart_killed_by_systemd_is_ok,
                  test_unknown_action_and_dev_mode):
            t()
        print("\n✅  Alle Tests bestanden.\n")
    except AssertionError as e:
        print(f"\n❌  Test fehlgeschlagen: {e}\n")
        sys.exit(1)
