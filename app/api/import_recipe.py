"""
api/import_recipe.py — Reifeprogramm aus JSON-LD importieren.

POST /api/import_recipe
Body: {"url": "https://reifeschrank.b50.de/pages/reifebuch.php?recipe_id=3"}
  oder direkt das JSON-LD als Body ({"@context": ..., "name": ..., "recipeInstructions": [...]})

Portiert von api_import.php (PHP → Python).
"""

import json
import logging
import time
import urllib.error
import urllib.request
from flask import Blueprint, current_app, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint("import_recipe", __name__)

_FETCH_TIMEOUT = 10   # Sekunden
_MAX_STEPS     = 20


@bp.post("/api/import_recipe")
def import_recipe():
    """
    Importiert ein Reifeprogramm aus einer JSON-LD-URL oder direkt als JSON.

    Unterstützte Formate:
    1. {"url": "https://..."}  → Fetch + Parse
    2. Direkt das JSON-LD-Objekt im Body

    Legt das Programm als neues Programm an (keine Duplikat-Prüfung — der Name
    kann im Frontend vor dem Speichern angepasst werden).
    """
    data = request.get_json(silent=True) or {}

    # ── Daten beschaffen ──────────────────────────────────────────────────────
    if "url" in data:
        recipe_data, err = _fetch_url(data["url"])
        if err:
            return jsonify({"error": err}), 502
    elif "@context" in data or "recipeInstructions" in data or "name" in data:
        recipe_data = data
    else:
        return jsonify({"error": "Entweder 'url' oder JSON-LD-Body erforderlich"}), 400

    # ── Parsen ────────────────────────────────────────────────────────────────
    program, errors = _parse_recipe(recipe_data)
    if errors:
        return jsonify({"error": "Import-Fehler", "details": errors}), 422

    # ── In DB speichern ───────────────────────────────────────────────────────
    db = current_app.db
    try:
        cursor = db.execute(
            "INSERT INTO riping_pgms (name, description, created_at) VALUES (?, ?, ?)",
            (program["name"], program["description"], time.time()),
        )
        pgm_id = cursor.lastrowid

        for i, step in enumerate(program["steps"], 1):
            db.execute(
                """
                INSERT INTO riping_steps
                    (riping_pgms_id, step, duration_h, temp, moist,
                     description, modes_id, target_weight_loss_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pgm_id,
                    i,
                    step["duration_h"],
                    step["temp"],
                    step["moist"],
                    step.get("description", ""),
                    step.get("modes_id", 1),
                    step.get("target_weight_loss_pct"),
                ),
            )

        # Vollständiges Programm zurückgeben
        steps = db.fetchall(
            "SELECT * FROM riping_steps WHERE riping_pgms_id = ? ORDER BY step ASC",
            (pgm_id,),
        )
        pgm = db.fetchone("SELECT * FROM riping_pgms WHERE id = ?", (pgm_id,))
        return jsonify({**dict(pgm), "steps": [dict(s) for s in steps]}), 201

    except Exception as e:
        if "UNIQUE" in str(e):
            return jsonify({"error": f"Programm '{program['name']}' bereits vorhanden"}), 409
        logger.error(f"Import DB-Fehler: {e}")
        return jsonify({"error": str(e)}), 500


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _ensure_json_ld_param(url: str) -> str:
    """Hängt &format=json-ld an die URL, falls noch nicht vorhanden."""
    if "format=json-ld" in url:
        return url
    sep = "&" if "?" in url else "?"
    return url + sep + "format=json-ld"


def _fetch_url(url: str) -> tuple[dict | None, str | None]:
    """Lädt JSON von einer URL. Gibt (data, None) oder (None, Fehlermeldung) zurück."""
    url = _ensure_json_ld_param(url)
    try:
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json, application/ld+json, */*"},
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw), None
    except urllib.error.URLError as e:
        return None, f"URL nicht erreichbar: {e}"
    except json.JSONDecodeError as e:
        return None, f"Ungültiges JSON: {e}"
    except Exception as e:
        return None, f"Fetch-Fehler: {e}"


def _parse_recipe(data: dict) -> tuple[dict | None, list[str]]:
    """
    Parst ein JSON-LD HowTo/Recipe-Objekt in ein internes Program-Dict.

    Erwartete Felder (JSON-LD Schema.org):
    - name                  → Programmname
    - description           → optional
    - recipeInstructions    → Liste von Schritten

    Jeder Schritt:
    - name / text           → Beschreibung
    - duration / duration_h → Dauer in Stunden (PT48H oder Integer)
    - temperature / temp    → Temperatur °C
    - humidity / moist      → Luftfeuchtigkeit %
    - modes_id              → optional, Default 1
    - target_weight_loss_pct → optional
    """
    errors = []

    name = (data.get("name") or "").strip()
    if not name:
        errors.append("'name' fehlt oder ist leer")

    description = (data.get("description") or "").strip()

    instructions = (data.get("recipeInstructions")
                    or data.get("ripeningInstructions")
                    or data.get("instructions")
                    or [])
    if not instructions:
        errors.append("'recipeInstructions' fehlt oder ist leer")

    if errors:
        return None, errors

    if len(instructions) > _MAX_STEPS:
        errors.append(f"Maximal {_MAX_STEPS} Schritte erlaubt (gefunden: {len(instructions)})")
        return None, errors

    steps = []
    for i, raw in enumerate(instructions, 1):
        step, step_errors = _parse_step(i, raw)
        errors.extend(step_errors)
        if step:
            steps.append(step)

    if errors:
        return None, errors

    return {"name": name, "description": description, "steps": steps}, []


def _parse_step(index: int, raw: dict | str) -> tuple[dict | None, list[str]]:
    """Parst einen einzelnen Schritt."""
    errors = []

    if isinstance(raw, str):
        # Nur Text, keine strukturierten Daten → nicht importierbar
        errors.append(f"Schritt {index}: nur Text vorhanden, keine strukturierten Daten")
        return None, errors

    # Beschreibung
    desc = (raw.get("name") or raw.get("text") or raw.get("description") or "").strip()

    # Dauer
    duration_raw = raw.get("duration") or raw.get("duration_h")
    duration_h   = _parse_duration(duration_raw)
    if duration_h is None:
        errors.append(f"Schritt {index}: 'duration' fehlt oder ungültig ('{duration_raw}')")
    elif not (1 <= duration_h <= 9999):
        errors.append(f"Schritt {index}: duration_h={duration_h} außerhalb 1–9999")

    # Temperatur
    temp_raw = raw.get("target_temp") or raw.get("temperature") or raw.get("temp")
    try:
        temp = float(temp_raw)
        if not (-50 <= temp <= 60):
            errors.append(f"Schritt {index}: temp={temp} außerhalb -50–60°C")
    except (TypeError, ValueError):
        errors.append(f"Schritt {index}: 'temperature'/'temp' fehlt oder ungültig")
        temp = None

    # Luftfeuchtigkeit
    moist_raw = raw.get("target_humidity") or raw.get("humidity") or raw.get("moist")
    try:
        moist = float(moist_raw)
        if not (0 <= moist <= 100):
            errors.append(f"Schritt {index}: moist={moist} außerhalb 0–100%")
    except (TypeError, ValueError):
        errors.append(f"Schritt {index}: 'humidity'/'moist' fehlt oder ungültig")
        moist = None

    if errors:
        return None, errors

    step = {
        "duration_h":            duration_h,
        "temp":                  temp,
        "moist":                 moist,
        "description":           desc,
        "modes_id":              int(raw.get("modes_id") or raw.get("mode_id") or 1),
        "target_weight_loss_pct": raw.get("target_weight_loss_pct"),
    }
    return step, []


def _parse_duration(value) -> int | None:
    """
    Parst ISO 8601 Dauer (PT48H, P2D) oder einen Integer/Float-Wert.
    Gibt immer einen Integer (Stunden) zurück oder None bei Fehler.
    """
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return int(value)

    s = str(value).strip().upper()

    # ISO 8601: PT48H, PT12H30M, P2D, P1DT12H, ...
    if s.startswith("P"):
        hours = 0
        s = s.lstrip("P")
        if "T" in s:
            date_part, time_part = s.split("T", 1)
        else:
            date_part, time_part = s, ""

        # Tage
        if "D" in date_part:
            try:
                hours += int(date_part.split("D")[0]) * 24
            except ValueError:
                return None

        # Stunden
        if "H" in time_part:
            try:
                hours += int(time_part.split("H")[0])
            except ValueError:
                return None

        return hours if hours > 0 else None

    # Zahl als String
    try:
        return int(float(s))
    except ValueError:
        return None
