"""
ntfy_notifier.py — Push-Benachrichtigungen via ntfy.sh.

Kein requests-Package nötig — nutzt urllib aus der Standardbibliothek.
"""

import base64
import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)

def _ascii_header(value: str) -> str:
    """
    Bereinigt einen String für HTTP-Header (urllib codiert als latin-1).
    Ersetzt häufige Unicode-Zeichen durch ASCII-Äquivalente.
    Alle verbleibenden non-ASCII-Zeichen werden entfernt.
    """
    replacements = {
        "\u2013": "-",   # en-dash –
        "\u2014": "-",   # em-dash —
        "\u2018": "'",   # linkes einfaches Anführungszeichen
        "\u2019": "'",   # rechtes einfaches Anführungszeichen
        "\u201c": '"',   # linkes doppeltes Anführungszeichen
        "\u201d": '"',   # rechtes doppeltes Anführungszeichen
        "\u00b0": " Grad",  # °
        "\u00e4": "ae",  # ä
        "\u00f6": "oe",  # ö
        "\u00fc": "ue",  # ü
        "\u00c4": "Ae",  # Ä
        "\u00d6": "Oe",  # Ö
        "\u00dc": "Ue",  # Ü
        "\u00df": "ss",  # ß
    }
    for char, replacement in replacements.items():
        value = value.replace(char, replacement)
    return value.encode("ascii", "ignore").decode("ascii")


PRIORITY_MAP = {
    "low":     "low",
    "default": "default",
    "high":    "high",
    "urgent":  "urgent",
}


class NtfyNotifier:

    def __init__(
        self,
        topic:   str,
        server:  str  = "https://ntfy.sh",
        enabled: bool = True,
        token:   str  = "",
        user:    str  = "",
    ) -> None:
        self._topic   = topic.strip("/")
        self._server  = server.rstrip("/")
        self._enabled = enabled
        self._token   = token.strip()
        self._user    = user.strip()

    def send(
        self,
        title:    str,
        message:  str,
        priority: str = "default",
        tags:     list[str] | None = None,
    ) -> bool:
        """Sendet eine Push-Benachrichtigung. Gibt True zurück wenn erfolgreich."""
        if not self._enabled or not self._topic:
            return False

        url = f"{self._server}/{self._topic}"

        # Header-basiertes Format: plain text Body, Metadaten als HTTP-Header.
        # Python's urllib codiert Header-Werte als latin-1 → non-ASCII muss entfernt werden.
        headers = {
            "Content-Type": "text/plain; charset=utf-8",
            "X-Title":      _ascii_header(title),
            "X-Priority":   PRIORITY_MAP.get(priority, "default"),
        }
        if tags:
            headers["X-Tags"] = ",".join(tags)
        if self._user and self._token:
            # Basic-Auth: Benutzername + Passwort
            creds = base64.b64encode(f"{self._user}:{self._token}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
        elif self._token:
            # Bearer-Token
            headers["Authorization"] = f"Bearer {self._token}"

        try:
            data = message.encode("utf-8")
            req  = urllib.request.Request(
                url,
                data=data,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    logger.info(f"ntfy gesendet: {title}")
                    return True
                logger.warning(f"ntfy HTTP {resp.status}")
                return False
        except urllib.error.HTTPError as e:
            logger.warning(f"ntfy HTTP {e.code}: {e.reason}")
            return False
        except urllib.error.URLError as e:
            logger.warning(f"ntfy nicht erreichbar: {e}")
            return False
        except Exception as e:
            logger.error(f"ntfy Fehler: {e}")
            return False

    @classmethod
    def from_config(cls, config: dict) -> "NtfyNotifier":
        return cls(
            topic   = config.get("ntfy_topic",  ""),
            server  = config.get("ntfy_server", "https://ntfy.sh"),
            enabled = bool(config.get("ntfy_enabled", False)),
            token   = config.get("ntfy_token",  ""),
            user    = config.get("ntfy_user",   ""),
        )
