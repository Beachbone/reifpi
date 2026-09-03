#!/usr/bin/env python3
"""
redirect.py — Leichtgewichtiger Weiterleitungsdienst auf Port 80.

Leitet jede Anfrage per HTTP 302 auf die eigentliche BOF (Port 5000) weiter,
damit die reine IP im Browser reicht. Reine Python-Standardbibliothek,
keine Abhängigkeit vom venv/den App-Requirements.

Bindet Port 80 nur, wenn er frei ist. Ist er belegt — z.B. von einem
anderen Webserver, der auf demselben Gerät parallel laufen soll — wartet
dieser Dienst einfach und versucht es periodisch erneut, statt den Port
zu erzwingen. Kein Ersatz für einen echten Webserver, reine Komfortfunktion.

Läuft als eigener systemd-Dienst (reifpi-redirect.service), separat vom
eigentlichen reifpi.service, damit ein Fehler hier nie die Steuerung selbst
beeinträchtigt.
"""

import http.server
import sys
import time

BIND_HOST = "0.0.0.0"
BIND_PORT = 80
TARGET_PORT = 5000
RETRY_INTERVAL_SECS = 30


class RedirectHandler(http.server.BaseHTTPRequestHandler):
    def _redirect(self) -> None:
        hostname = self.headers.get("Host", self.server.server_address[0]).split(":")[0]
        self.send_response(302)
        self.send_header("Location", f"http://{hostname}:{TARGET_PORT}{self.path}")
        self.end_headers()

    def do_GET(self) -> None:
        self._redirect()

    def do_HEAD(self) -> None:
        self._redirect()

    def log_message(self, fmt: str, *args) -> None:
        # Kein eigenes Access-Log — das übernimmt gunicorn auf Port 5000.
        pass


def main() -> int:
    while True:
        try:
            server = http.server.HTTPServer((BIND_HOST, BIND_PORT), RedirectHandler)
        except OSError as exc:
            print(
                f"reifpi-redirect: Port {BIND_PORT} ist belegt ({exc}) — "
                f"vermutlich läuft dort schon ein anderer Webserver. "
                f"Neuer Versuch in {RETRY_INTERVAL_SECS}s.",
                file=sys.stderr, flush=True,
            )
            time.sleep(RETRY_INTERVAL_SECS)
            continue

        print(
            f"reifpi-redirect: Port {BIND_PORT} ist frei, leite jetzt auf "
            f":{TARGET_PORT} weiter.",
            file=sys.stderr, flush=True,
        )
        server.serve_forever()  # kehrt nur bei explizitem shutdown() zurück


if __name__ == "__main__":
    sys.exit(main())
