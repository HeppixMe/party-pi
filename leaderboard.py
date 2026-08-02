#!/usr/bin/env python3
"""
LAN-Party-Leaderboard - Anzeige- und Steuerserver.

Ein einziger Prozess, ausschliesslich Python-Standardbibliothek:

  GET  /            Anzeige-Seite fuer den Beamer/Fernseher (ohne Passwort)
  GET  /admin       Steuerseite (Passwort noetig)
  GET  /api/state   aktueller Zustand als JSON
  GET  /api/events  Server-Sent-Events: schiebt jede Aenderung sofort an alle Seiten
  GET  /api/fan     Temperatur/Luefterstufe (aus data/fan-status.json, siehe fan/fan_control.py)
  POST /api/...     Aenderungen (nur mit gueltiger Anmeldung)

Zustand liegt in <data>/state.json, Konfiguration (Passwort-Hash, Cookie-Secret)
in <data>/config.json.

Start fuer einen schnellen Test auf dem Laptop:

    python3 leaderboard.py --port 8000 --data ./data

Passwort setzen (macht auch das Installationsskript):

    python3 leaderboard.py --data ./data --set-password

Sicherheitshinweis: reines HTTP ohne TLS, gedacht fuer das vertrauenswuerdige
Heim-/Party-Netz. Das Admin-Passwort haelt mitspielende Gaeste davon ab, Punkte
zu verstellen - es ist kein Schutz gegen einen Angreifer im selben Netz.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import secrets
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")

STATE_VERSION = 1
SESSION_TTL = 12 * 3600  # Ein Party-Abend ist damit sicher abgedeckt.
COOKIE_NAME = "lanparty_session"
SSE_HEARTBEAT = 15.0
PBKDF2_ITERATIONS = 200_000

TEAM_IDS = ("a", "b")


# ---------------------------------------------------------------------------
# Kleine Helfer
# ---------------------------------------------------------------------------
def now_ms() -> int:
    """Wanduhrzeit in Millisekunden - dieselbe Basis, mit der der Browser rechnet."""
    return int(time.time() * 1000)


def new_id() -> str:
    return secrets.token_hex(4)


def write_json_atomic(path: str, data, mode: int = 0o644) -> None:
    """JSON so schreiben, dass ein Stromausfall keine halbe Datei hinterlaesst."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def clamp_name(value, fallback: str = "", limit: int = 60) -> str:
    if not isinstance(value, str):
        return fallback
    cleaned = " ".join(value.split())
    return cleaned[:limit] if cleaned else fallback


def as_int(value, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------------------
# Zustand: Datenmodell und reine Rechenfunktionen (ohne I/O, damit testbar)
# ---------------------------------------------------------------------------
def default_state() -> dict:
    return {
        "version": STATE_VERSION,
        "sos": {
            "teams": [
                {"id": "a", "name": "Team 1"},
                {"id": "b", "name": "Team 2"},
            ],
            "levels": [],
        },
        "smash": {"players": []},
    }


def new_timer() -> dict:
    return {"elapsedMs": 0, "runningSince": None, "finished": False}


def new_level(name: str, running_since=None) -> dict:
    timers = {}
    for team_id in TEAM_IDS:
        timer = new_timer()
        timer["runningSince"] = running_since
        timers[team_id] = timer
    return {"id": new_id(), "name": name, "active": True, "timers": timers}


def timer_value_ms(timer: dict, now: int) -> int:
    """Angezeigte Zeit einer Uhr: eingefrorener Anteil plus laufender Anteil."""
    value = as_int(timer.get("elapsedMs"), 0)
    started = timer.get("runningSince")
    # Ausdruecklich gegen None pruefen: der Zeitstempel 0 ist ein gueltiger
    # Startzeitpunkt, waere als Wahrheitswert aber "laeuft nicht".
    if started is not None:
        value += max(0, now - as_int(started, now))
    return value


def freeze_timer(timer: dict, now: int) -> None:
    """Laufende Uhr anhalten - der bisherige Stand bleibt exakt erhalten."""
    if timer.get("runningSince") is not None:
        timer["elapsedMs"] = timer_value_ms(timer, now)
        timer["runningSince"] = None


def freeze_all(state: dict, now: int) -> None:
    for level in state["sos"]["levels"]:
        for timer in level["timers"].values():
            freeze_timer(timer, now)


def compute_standings(state: dict, now: int) -> dict:
    """
    Gesamtwertung SOS.

    - totals:    Summe aller Level-Zeiten je Team (laufende Uhren zaehlen mit)
    - levelWins: gewonnene Level je Team - ein Level zaehlt erst, wenn beide
                 Teams es abgeschlossen haben ('finished'), sonst waere das
                 gerade schnellere Team schon vor dem Ziel Sieger
    - leader:    mehr gewonnene Level fuehrt; bei Gleichstand die kleinere
                 Gesamtzeit; sonst None (echter Gleichstand)
    """
    totals = {team_id: 0 for team_id in TEAM_IDS}
    wins = {team_id: 0 for team_id in TEAM_IDS}
    finished_levels = 0

    for level in state["sos"]["levels"]:
        values = {}
        for team_id in TEAM_IDS:
            timer = level["timers"].get(team_id) or new_timer()
            values[team_id] = timer_value_ms(timer, now)
            totals[team_id] += values[team_id]

        if all(level["timers"].get(t, {}).get("finished") for t in TEAM_IDS):
            finished_levels += 1
            best = min(values.values())
            winners = [t for t in TEAM_IDS if values[t] == best]
            if len(winners) == 1:
                wins[winners[0]] += 1

    leader = None
    a, b = TEAM_IDS
    if wins[a] != wins[b]:
        leader = a if wins[a] > wins[b] else b
    elif totals[a] != totals[b] and (totals[a] or totals[b]):
        leader = a if totals[a] < totals[b] else b

    return {
        "totals": totals,
        "levelWins": wins,
        "finishedLevels": finished_levels,
        "leader": leader,
    }


def sort_players(players: list) -> list:
    """Smash-Rangliste: meiste Siege, dann bessere Quote, dann weniger Niederlagen."""

    def key(player):
        wins = as_int(player.get("wins"), 0)
        losses = as_int(player.get("losses"), 0)
        played = wins + losses
        rate = wins / played if played else 0.0
        return (-wins, -rate, losses, player.get("name", "").lower())

    return sorted(players, key=key)


def migrate_state(raw) -> dict:
    """Gespeicherten Zustand einlesen und fehlende Felder ergaenzen."""
    state = default_state()
    if not isinstance(raw, dict):
        return state

    sos = raw.get("sos") if isinstance(raw.get("sos"), dict) else {}
    teams = sos.get("teams")
    if isinstance(teams, list):
        for index, team_id in enumerate(TEAM_IDS):
            stored = next(
                (t for t in teams if isinstance(t, dict) and t.get("id") == team_id), None
            )
            if stored:
                state["sos"]["teams"][index]["name"] = clamp_name(
                    stored.get("name"), state["sos"]["teams"][index]["name"]
                )

    levels = sos.get("levels")
    if isinstance(levels, list):
        for stored in levels:
            if not isinstance(stored, dict):
                continue
            level = {
                "id": stored.get("id") or new_id(),
                "name": clamp_name(stored.get("name"), "Level"),
                "active": bool(stored.get("active")),
                "timers": {},
            }
            stored_timers = stored.get("timers") if isinstance(stored.get("timers"), dict) else {}
            for team_id in TEAM_IDS:
                timer = stored_timers.get(team_id)
                if not isinstance(timer, dict):
                    timer = new_timer()
                running = timer.get("runningSince")
                level["timers"][team_id] = {
                    "elapsedMs": max(0, as_int(timer.get("elapsedMs"), 0)),
                    "runningSince": as_int(running) if running is not None else None,
                    "finished": bool(timer.get("finished")),
                }
            state["sos"]["levels"].append(level)

    smash = raw.get("smash") if isinstance(raw.get("smash"), dict) else {}
    players = smash.get("players")
    if isinstance(players, list):
        for stored in players:
            if not isinstance(stored, dict):
                continue
            name = clamp_name(stored.get("name"))
            if not name:
                continue
            state["smash"]["players"].append(
                {
                    "id": stored.get("id") or new_id(),
                    "name": name,
                    "wins": max(0, as_int(stored.get("wins"), 0)),
                    "losses": max(0, as_int(stored.get("losses"), 0)),
                }
            )

    return state


# ---------------------------------------------------------------------------
# Store: Zustand halten, speichern, Aenderungen an alle Seiten schicken
# ---------------------------------------------------------------------------
class Store:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._subscribers = set()
        self.state = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                return migrate_state(json.load(fh))
        except FileNotFoundError:
            return default_state()
        except (json.JSONDecodeError, OSError) as err:
            # Lieber mit leerem Board weitermachen als gar nicht starten - die
            # kaputte Datei wird zur Sicherheit beiseitegelegt.
            sys.stderr.write(f"[!] state.json unlesbar ({err}) - starte mit leerem Board\n")
            try:
                os.replace(self.path, self.path + ".broken")
            except OSError:
                pass
            return default_state()

    def snapshot(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.state))

    def mutate(self, fn):
        """Zustand unter Sperre aendern, speichern und alle Clients benachrichtigen."""
        with self._lock:
            result = fn(self.state)
            try:
                write_json_atomic(self.path, self.state)
            except OSError as err:
                sys.stderr.write(f"[!] Konnte Zustand nicht speichern: {err}\n")
            payload = self._payload_locked()
        self._broadcast(payload)
        return result

    def _payload_locked(self) -> str:
        now = now_ms()
        body = {
            "state": self.state,
            "standings": compute_standings(self.state, now),
            "serverNow": now,
        }
        return json.dumps(body, ensure_ascii=False)

    def payload(self) -> str:
        with self._lock:
            return self._payload_locked()

    # -- SSE-Abonnenten ----------------------------------------------------
    def subscribe(self) -> "queue.Queue":
        client = queue.Queue(maxsize=32)
        with self._lock:
            self._subscribers.add(client)
        return client

    def unsubscribe(self, client) -> None:
        with self._lock:
            self._subscribers.discard(client)

    def _broadcast(self, payload: str) -> None:
        with self._lock:
            clients = list(self._subscribers)
        for client in clients:
            try:
                client.put_nowait(payload)
            except queue.Full:
                # Client kommt nicht hinterher. Jede Nachricht enthaelt den
                # vollen Zustand, also sind die aelteren wertlos: Puffer leeren
                # und nur den neuesten Stand hineinlegen. Den Client dagegen
                # abzuhaengen wuerde die Seite stumm veralten lassen.
                try:
                    while True:
                        client.get_nowait()
                except queue.Empty:
                    pass
                try:
                    client.put_nowait(payload)
                except queue.Full:
                    pass


# ---------------------------------------------------------------------------
# Zustands-Operationen (die eigentliche Fachlogik der Admin-Knoepfe)
# ---------------------------------------------------------------------------
def find_level(state: dict, level_id) -> dict | None:
    for level in state["sos"]["levels"]:
        if level["id"] == level_id:
            return level
    return None


def active_level(state: dict) -> dict | None:
    for level in reversed(state["sos"]["levels"]):
        if level.get("active"):
            return level
    return state["sos"]["levels"][-1] if state["sos"]["levels"] else None


def op_set_team_names(state: dict, names: dict) -> None:
    for team in state["sos"]["teams"]:
        if team["id"] in names:
            team["name"] = clamp_name(names[team["id"]], team["name"])


def op_start_level(state: dict, name, autostart: bool, now: int) -> dict:
    """
    Neues Level beginnen: alles Bisherige friert genau so ein, wie es steht,
    und darunter beginnt eine neue Zeile mit zwei frischen Uhren.
    """
    freeze_all(state, now)
    for level in state["sos"]["levels"]:
        level["active"] = False
    label = clamp_name(name, f"Level {len(state['sos']['levels']) + 1}")
    level = new_level(label, running_since=now if autostart else None)
    state["sos"]["levels"].append(level)
    return level


def op_timer(state: dict, level_id, team_id: str, action: str, now: int) -> None:
    level = find_level(state, level_id)
    if level is None or team_id not in TEAM_IDS:
        raise KeyError("Level oder Team unbekannt")
    timer = level["timers"][team_id]

    if action == "start":
        if timer["runningSince"] is None:
            timer["runningSince"] = now
        timer["finished"] = False
    elif action == "stop":
        freeze_timer(timer, now)
    elif action == "finish":
        freeze_timer(timer, now)
        timer["finished"] = True
    elif action == "unfinish":
        timer["finished"] = False
    elif action == "reset":
        level["timers"][team_id] = new_timer()
    else:
        raise ValueError(f"Unbekannte Aktion '{action}'")


def op_adjust_timer(state: dict, level_id, team_id: str, value_ms: int, now: int) -> None:
    """Zeit von Hand korrigieren - eine laufende Uhr laeuft ab dem neuen Wert weiter."""
    level = find_level(state, level_id)
    if level is None or team_id not in TEAM_IDS:
        raise KeyError("Level oder Team unbekannt")
    timer = level["timers"][team_id]
    timer["elapsedMs"] = max(0, as_int(value_ms, 0))
    if timer["runningSince"] is not None:
        timer["runningSince"] = now


def op_rename_level(state: dict, level_id, name) -> None:
    level = find_level(state, level_id)
    if level is None:
        raise KeyError("Level unbekannt")
    level["name"] = clamp_name(name, level["name"])


def op_delete_level(state: dict, level_id) -> None:
    levels = state["sos"]["levels"]
    remaining = [lv for lv in levels if lv["id"] != level_id]
    if len(remaining) == len(levels):
        raise KeyError("Level unbekannt")
    state["sos"]["levels"] = remaining
    if remaining and not any(lv.get("active") for lv in remaining):
        remaining[-1]["active"] = True


def op_add_player(state: dict, name) -> dict:
    label = clamp_name(name)
    if not label:
        raise ValueError("Name fehlt")
    player = {"id": new_id(), "name": label, "wins": 0, "losses": 0}
    state["smash"]["players"].append(player)
    return player


def find_player(state: dict, player_id) -> dict | None:
    for player in state["smash"]["players"]:
        if player["id"] == player_id:
            return player
    return None


def op_player_score(state: dict, player_id, field: str, delta: int) -> None:
    if field not in ("wins", "losses"):
        raise ValueError("Feld muss 'wins' oder 'losses' sein")
    player = find_player(state, player_id)
    if player is None:
        raise KeyError("Spieler unbekannt")
    player[field] = max(0, as_int(player[field], 0) + as_int(delta, 0))


def op_match(state: dict, winner_id, loser_id) -> None:
    winner = find_player(state, winner_id)
    loser = find_player(state, loser_id)
    if winner is None or loser is None:
        raise KeyError("Spieler unbekannt")
    if winner is loser:
        raise ValueError("Sieger und Verlierer sind derselbe Spieler")
    winner["wins"] = as_int(winner["wins"], 0) + 1
    loser["losses"] = as_int(loser["losses"], 0) + 1


def op_reset(state: dict, scope: str) -> None:
    if scope in ("all", "sos"):
        state["sos"]["levels"] = []
    if scope in ("all", "smash"):
        state["smash"]["players"] = []
    if scope == "all":
        fresh = default_state()
        state["sos"]["teams"] = fresh["sos"]["teams"]


# ---------------------------------------------------------------------------
# Konfiguration und Anmeldung
# ---------------------------------------------------------------------------
class Auth:
    """Passwort-Hash und Cookie-Secret in <data>/config.json."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.RLock()
        self._failures: dict[str, list] = {}
        self.config = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                config = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            config = {}
        if not isinstance(config, dict):
            config = {}
        if not config.get("secret"):
            config["secret"] = secrets.token_hex(32)
            self._save(config)
        return config

    def _save(self, config: dict) -> None:
        write_json_atomic(self.path, config, mode=0o600)

    @property
    def has_password(self) -> bool:
        return bool(self.config.get("password"))

    def set_password(self, password: str) -> None:
        with self._lock:
            self.config["password"] = hash_password(password)
            self._save(self.config)

    def verify(self, password: str) -> bool:
        record = self.config.get("password")
        if not record:
            return False
        return verify_password(record, password)

    # -- Anmeldeversuche bremsen ------------------------------------------
    def note_failure(self, client: str) -> float:
        """Fehlversuche zaehlen und die passende Wartezeit zurueckgeben."""
        with self._lock:
            entry = self._failures.setdefault(client, [0, 0.0])
            entry[0] += 1
            entry[1] = time.time()
            return min(2.0, 0.25 * entry[0])

    def note_success(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)

    def is_blocked(self, client: str) -> bool:
        with self._lock:
            entry = self._failures.get(client)
            if not entry:
                return False
            count, last = entry
            if count >= 20 and time.time() - last < 60:
                return True
            if time.time() - last > 600:
                self._failures.pop(client, None)
            return False

    # -- Sitzungs-Cookie ---------------------------------------------------
    def _secret_bytes(self) -> bytes:
        return self.config["secret"].encode()

    def make_token(self) -> str:
        expires = int(time.time()) + SESSION_TTL
        payload = f"{expires}.{secrets.token_hex(8)}"
        signature = hmac.new(self._secret_bytes(), payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{signature}"

    def check_token(self, token) -> bool:
        if not token or token.count(".") != 2:
            return False
        expires, nonce, signature = token.split(".")
        expected = hmac.new(
            self._secret_bytes(), f"{expires}.{nonce}".encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
        return as_int(expires, 0) > time.time()


def hash_password(password: str, salt: bytes | None = None) -> dict:
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return {
        "algo": "pbkdf2_sha256",
        "iterations": PBKDF2_ITERATIONS,
        "salt": salt.hex(),
        "hash": derived.hex(),
    }


def verify_password(record: dict, password: str) -> bool:
    try:
        salt = bytes.fromhex(record["salt"])
        iterations = as_int(record.get("iterations"), PBKDF2_ITERATIONS)
        derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    except (KeyError, ValueError, TypeError):
        return False
    return hmac.compare_digest(derived.hex(), record.get("hash", ""))


def random_password(length: int = 8) -> str:
    # Ohne leicht verwechselbare Zeichen - das Passwort wird oft abgetippt.
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "LanPartyLeaderboard/1.0"
    protocol_version = "HTTP/1.1"

    # von der Server-Instanz gesetzt
    store: Store
    auth: Auth
    data_dir: str

    def log_message(self, fmt, *args):
        # Ruhigeres Log: der Live-Stream und die Temperaturabfrage der Anzeige
        # wuerden journalctl sonst zumuellen.
        if self.path.startswith("/api/events") or self.path.startswith("/api/fan"):
            return
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- Antworten ---------------------------------------------------------
    def _send(self, status, body: bytes, content_type: str, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status=HTTPStatus.OK, extra=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8", extra)

    def _error(self, status, message: str):
        self._json({"ok": False, "error": message}, status=status)

    def _static(self, name: str, status=HTTPStatus.OK):
        path = os.path.normpath(os.path.join(STATIC_DIR, name))
        if not path.startswith(STATIC_DIR + os.sep) or not os.path.isfile(path):
            self._error(HTTPStatus.NOT_FOUND, "Datei nicht gefunden")
            return
        content_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in (
            "application/javascript",
            "application/json",
        ):
            content_type += "; charset=utf-8"
        with open(path, "rb") as fh:
            self._send(status, fh.read(), content_type)

    # -- Anmeldung ---------------------------------------------------------
    def _is_authed(self) -> bool:
        header = self.headers.get("Cookie")
        if not header:
            return False
        try:
            cookies = SimpleCookie(header)
        except Exception:
            return False
        morsel = cookies.get(COOKIE_NAME)
        return bool(morsel) and self.auth.check_token(morsel.value)

    def _same_origin(self) -> bool:
        """
        CSRF-Schutz: schickt der Browser einen Origin-Header, muss er zu diesem
        Server passen. Werkzeuge wie curl schicken keinen - die duerfen durch,
        weil sie ohnehin kein fremdes Cookie mitbringen koennen.
        """
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = self.headers.get("Host", "")
        return urlparse(origin).netloc == host

    def _require_auth(self) -> bool:
        if self._is_authed():
            return True
        self._error(HTTPStatus.UNAUTHORIZED, "Nicht angemeldet")
        return False

    def _body(self) -> dict:
        """
        Rumpf der Anfrage lesen - und zwar immer, auch wenn wir gleich ablehnen.
        Bleibt er im Puffer liegen, haelt der Browser die Verbindung offen und
        der Server versucht, den Rest als naechste Anfrage zu lesen.
        """
        length = as_int(self.headers.get("Content-Length"), 0)
        if length <= 0:
            return {}
        if length > 1_000_000:
            self.close_connection = True  # so viel lesen wir gar nicht erst
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    # -- GET ---------------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/":
            return self._static("display.html")
        if path in ("/admin", "/admin/"):
            if not self._is_authed():
                # 401 mit der Anmeldeseite als Inhalt: der Browser zeigt sie an,
                # Skripte sehen trotzdem einen sauberen Fehlercode.
                return self._static("login.html", status=HTTPStatus.UNAUTHORIZED)
            return self._static("admin.html")
        if path == "/api/state":
            payload = json.loads(self.store.payload())
            payload["authed"] = self._is_authed()
            payload["passwordSet"] = self.auth.has_password
            return self._json(payload)
        if path == "/api/fan":
            return self._json(self._fan_status())
        if path == "/api/events":
            return self._events()
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])

        self._error(HTTPStatus.NOT_FOUND, "Unbekannter Pfad")

    def _fan_status(self) -> dict:
        """Status des Luefter-Dienstes; faellt auf direktes Auslesen der CPU-Temperatur zurueck."""
        try:
            with open(os.path.join(self.data_dir, "fan-status.json"), "r", encoding="utf-8") as fh:
                status = json.load(fh)
            if isinstance(status, dict):
                return status
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        try:
            with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding="utf-8") as fh:
                return {"tempC": round(int(fh.read().strip()) / 1000.0, 1), "backend": "unbekannt"}
        except (OSError, ValueError):
            return {}

    def _events(self):
        """Server-Sent-Events: einmal den vollen Zustand, danach jede Aenderung."""
        client = self.store.subscribe()
        self.close_connection = True
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self._sse_write(self.store.payload())
            while True:
                try:
                    payload = client.get(timeout=SSE_HEARTBEAT)
                except queue.Empty:
                    # Heartbeat: haelt Zwischenstationen davon ab, die Leitung zu kappen.
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self._sse_write(payload)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.store.unsubscribe(client)

    def _sse_write(self, payload: str) -> None:
        self.wfile.write(b"data: " + payload.encode("utf-8") + b"\n\n")
        self.wfile.flush()

    # -- POST --------------------------------------------------------------
    def do_POST(self):
        path = urlparse(self.path).path
        # Zuerst lesen, dann pruefen - siehe Kommentar in _body().
        body = self._body()

        if not self._same_origin():
            return self._error(HTTPStatus.FORBIDDEN, "Fremde Herkunft abgelehnt")

        if path == "/api/login":
            return self._login(body)
        if path == "/api/logout":
            return self._json(
                {"ok": True},
                extra={
                    "Set-Cookie": f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict"
                },
            )

        if not self._require_auth():
            return

        now = now_ms()
        try:
            handler = self._post_routes().get(path)
            if handler is None:
                return self._error(HTTPStatus.NOT_FOUND, "Unbekannter Pfad")
            result = handler(body, now)
        except KeyError as err:
            return self._error(HTTPStatus.NOT_FOUND, str(err.args[0] if err.args else err))
        except ValueError as err:
            return self._error(HTTPStatus.BAD_REQUEST, str(err))

        payload = json.loads(self.store.payload())
        if isinstance(result, dict):
            payload.update(result)
        payload["ok"] = True
        self._json(payload)

    def _post_routes(self):
        store = self.store
        return {
            "/api/sos/teams": lambda body, now: store.mutate(
                lambda s: op_set_team_names(s, body.get("names") or {})
            ),
            "/api/sos/level": lambda body, now: {
                "level": store.mutate(
                    lambda s: op_start_level(
                        s, body.get("name"), body.get("autostart", True), now
                    )
                )
            },
            "/api/sos/level/rename": lambda body, now: store.mutate(
                lambda s: op_rename_level(s, body.get("levelId"), body.get("name"))
            ),
            "/api/sos/level/delete": lambda body, now: store.mutate(
                lambda s: op_delete_level(s, body.get("levelId"))
            ),
            "/api/sos/timer": lambda body, now: store.mutate(
                lambda s: op_timer(
                    s,
                    body.get("levelId") or (active_level(s) or {}).get("id"),
                    body.get("team"),
                    body.get("action", ""),
                    now,
                )
            ),
            "/api/sos/timer/adjust": lambda body, now: store.mutate(
                lambda s: op_adjust_timer(
                    s, body.get("levelId"), body.get("team"), body.get("valueMs"), now
                )
            ),
            "/api/smash/player": lambda body, now: self._smash_player(body),
            "/api/smash/result": lambda body, now: store.mutate(
                lambda s: op_match(s, body.get("winnerId"), body.get("loserId"))
            ),
            "/api/reset": lambda body, now: store.mutate(
                lambda s: op_reset(s, body.get("scope", "all"))
            ),
        }

    def _smash_player(self, body: dict):
        action = body.get("action", "add")
        store = self.store
        if action == "add":
            return {"player": store.mutate(lambda s: op_add_player(s, body.get("name")))}
        if action == "rename":
            def rename(state):
                player = find_player(state, body.get("id"))
                if player is None:
                    raise KeyError("Spieler unbekannt")
                player["name"] = clamp_name(body.get("name"), player["name"])
            return store.mutate(rename)
        if action == "delete":
            def delete(state):
                players = state["smash"]["players"]
                remaining = [p for p in players if p["id"] != body.get("id")]
                if len(remaining) == len(players):
                    raise KeyError("Spieler unbekannt")
                state["smash"]["players"] = remaining
            return store.mutate(delete)
        if action == "score":
            return store.mutate(
                lambda s: op_player_score(
                    s, body.get("id"), body.get("field", ""), body.get("delta", 0)
                )
            )
        raise ValueError(f"Unbekannte Aktion '{action}'")

    def _login(self, body: dict):
        client = self.address_string()
        if self.auth.is_blocked(client):
            return self._error(HTTPStatus.TOO_MANY_REQUESTS, "Zu viele Fehlversuche - kurz warten")
        password = body.get("password", "")
        if not isinstance(password, str) or not self.auth.verify(password):
            time.sleep(self.auth.note_failure(client))
            return self._error(HTTPStatus.UNAUTHORIZED, "Falsches Passwort")
        self.auth.note_success(client)
        token = self.auth.make_token()
        cookie = (
            f"{COOKIE_NAME}={token}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Strict"
        )
        self._json({"ok": True}, extra={"Set-Cookie": cookie})


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
def build_server(host: str, port: int, data_dir: str) -> ThreadingHTTPServer:
    os.makedirs(data_dir, exist_ok=True)
    store = Store(os.path.join(data_dir, "state.json"))
    auth = Auth(os.path.join(data_dir, "config.json"))

    class BoundHandler(Handler):
        pass

    BoundHandler.store = store
    BoundHandler.auth = auth
    BoundHandler.data_dir = data_dir

    httpd = ThreadingHTTPServer((host, port), BoundHandler)
    httpd.daemon_threads = True
    httpd.store = store
    httpd.auth = auth
    return httpd


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="LAN-Party-Leaderboard")
    parser.add_argument("--host", default="0.0.0.0", help="Adresse (Standard 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port (Standard 8000)")
    parser.add_argument(
        "--data",
        default=os.path.join(APP_DIR, "data"),
        help="Verzeichnis fuer state.json/config.json",
    )
    parser.add_argument(
        "--set-password",
        action="store_true",
        help="Nur das Admin-Passwort setzen und beenden",
    )
    args = parser.parse_args(argv)

    data_dir = os.path.abspath(args.data)
    os.makedirs(data_dir, exist_ok=True)

    if args.set_password:
        auth = Auth(os.path.join(data_dir, "config.json"))
        password = os.environ.get("LAN_ADMIN_PASSWORD") or getpass.getpass("Neues Admin-Passwort: ")
        if not password:
            sys.stderr.write("Kein Passwort eingegeben - abgebrochen.\n")
            return 1
        auth.set_password(password)
        print("Admin-Passwort gesetzt.")
        return 0

    httpd = build_server(args.host, args.port, data_dir)

    if not httpd.auth.has_password:
        generated = random_password()
        httpd.auth.set_password(generated)
        print("=" * 60)
        print("  Noch kein Admin-Passwort gesetzt - eines erzeugt:")
        print(f"      {generated}")
        print("  Aendern mit:  python3 leaderboard.py --data <dir> --set-password")
        print("=" * 60)

    print(f"Anzeige : http://{args.host}:{args.port}/")
    print(f"Admin   : http://{args.host}:{args.port}/admin")
    print(f"Daten   : {data_dir}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
