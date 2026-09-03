"""
web/views.py — HTML-Seitenrouten (Flask Templates).

Alle Seiten liefern nur das HTML-Gerüst;
Daten werden clientseitig per Fetch-API nachgeladen.
"""

from flask import Blueprint, render_template

bp = Blueprint("web", __name__)


@bp.get("/")
def index():
    """Dashboard — Istwerte, Sollwerte, Relais-Status."""
    return render_template("index.html")


@bp.get("/programs")
def programs():
    """Reifeprogramme — Liste, Erstellen, Bearbeiten, Aktivieren."""
    return render_template("programs.html")


@bp.get("/archive")
def archive():
    """Historische Charts — Temperatur, Luftfeuchtigkeit, Gewicht."""
    return render_template("archive.html")


@bp.get("/settings")
def settings():
    """Einstellungen — Regelparameter, GPIO, E-Mail, ntfy."""
    return render_template("settings.html")


@bp.get("/hardware_setup")
def hardware_setup():
    """Hardware-Setup — Sensor-Auswahl und I2C-Konfiguration."""
    return render_template("hardware_setup.html")
