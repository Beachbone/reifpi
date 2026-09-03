"""
run.py — Einstiegspunkt für Entwicklung.

Produktion startet via gunicorn (siehe systemd-Service):
    gunicorn -w 1 -b 0.0.0.0:5000 "run:app"

Entwicklung:
    python run.py
    REIFE_ENV=dev python run.py
"""

import os
from app import create_app

env = os.environ.get("REIFE_ENV", "prod")
app = create_app(env)

if __name__ == "__main__":
    # Nur für Entwicklung — Produktion nutzt gunicorn
    app.run(
        host  = "0.0.0.0",
        port  = int(os.environ.get("PORT", 5000)),
        debug = (env == "dev"),
        use_reloader = False,   # Reloader würde Controller-Thread doppelt starten
    )
