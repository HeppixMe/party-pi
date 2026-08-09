#!/usr/bin/env python3
"""
LAN-Party-Leaderboard - Anzeige- und Steuerserver.

Ein einziger Prozess, ausschliesslich Python-Standardbibliothek. Kein
Passwort, keine Anmeldung - /admin ist offen wie jede andere Seite, damit
der Einstieg so einfach wie moeglich ist.

Seiten:

  /                Leaderboard fuer den Fernseher
  /seiten          Uebersicht aller Seiten
  /qr              grosser QR-Code zum Mitmachen
  /mitmachen       Spieler-Anmeldung (Name + Team), fuers Handy
  /teams           Kader: wer ist in welchem Team
  /system          Systemstatus des Pi
  /info            Info-Tafel (Text im Admin bearbeitbar)
  /screen/<name>   gesteuerter Bildschirm - zeigt, was der Admin zuweist
  /admin           Steuerung

Schnittstelle:

  GET  /api/state    aktueller Zustand als JSON
  GET  /api/events   Server-Sent-Events: jede Aenderung sofort an alle Seiten
  GET  /api/fan      Temperatur und Luefterstufe
  GET  /api/system   Laufzeit, Last, Adresse, verbundene Seiten
  GET  /api/qr.svg   QR-Code der Beitritts-Adresse
  POST /api/...      alle Aenderungen, ohne Anmeldung

Zustand liegt in <data>/state.json, das Cookie-Signierschluessel (fuer die
Spieler-Wiedererkennung) in <data>/config.json.

Start fuer einen schnellen Test auf dem Laptop:

    python3 leaderboard.py --port 8000 --data ./data

Sicherheitshinweis: reines HTTP ohne TLS und ohne Anmeldung, gedacht fuer das
vertrauenswuerdige Heim-/Party-Netz. Jeder im selben Netz kann Punkte
verstellen - genau wie am Spieltisch nebenan. Nichts davon gehoert ungeschuetzt
ins Internet.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import html
import json
import mimetypes
import os
import queue
import secrets
import socket
import sys
import tempfile
import threading
import time
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

import qr

APP_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(APP_DIR, "static")

STATE_VERSION = 2
PLAYER_COOKIE = "lanparty_player"
PLAYER_TTL = 48 * 3600

MAX_PLAYERS = 100
MAX_SCREENS = 20
MAX_BOARDS = 16
MAX_SHORTCUTS = 30
MAX_STEPS_PER_SHORTCUT = 40
MAX_SPLITS = 16
SCREEN_OFFLINE_AFTER = 120_000  # ms ohne Lebenszeichen -> als offline anzeigen

DEFAULT_INFO_TEXT = (
    "WLAN: <Netzname>\n"
    "Passwort: <Passwort>\n"
    "\n"
    "Ablauf:\n"
    "  20:00  SOS, Team gegen Team\n"
    "  22:00  Smash-Turnier\n"
)

# Ansichten, die der Admin einem Bildschirm zuweisen kann.
VIEWS = (
    ("/", "Leaderboard"),
    ("/qr", "Mitmachen (QR)"),
    ("/teams", "Kader"),
    ("/system", "Systemstatus"),
    ("/info", "Info-Tafel"),
    ("/vibe", "Vibe (Deko)"),
)
VIEW_PATHS = {path for path, _label in VIEWS}
SSE_HEARTBEAT = 15.0


def _valid_view(state: dict, view) -> bool:
    """Eine Ansicht ist gueltig, wenn sie fest verdrahtet ist oder ein
    bestehendes eigenes Leaderboard bzw. einen geteilten Bildschirm meint."""
    if view in VIEW_PATHS:
        return True
    if isinstance(view, str) and view.startswith("/board/"):
        return find_board(state, view[len("/board/"):]) is not None
    if isinstance(view, str) and view.startswith("/split/"):
        return find_split(state, view[len("/split/"):]) is not None
    return False


def _valid_split_side(state: dict, view) -> bool:
    """Eine Haelfte eines geteilten Bildschirms darf alles zeigen, was auch
    ein normaler Bildschirm zeigen kann - nur keinen weiteren geteilten
    Bildschirm, sonst koennten sich Splits gegenseitig oder sich selbst
    einbetten."""
    if isinstance(view, str) and view.startswith("/split/"):
        return False
    return _valid_view(state, view)


def _valid_display_active(state: dict, active) -> bool:
    """Gueltig ist 'all', 'sos', 'smash' oder 'board:<id>' eines bestehenden
    eigenen Leaderboards."""
    if active in ("all", "sos", "smash"):
        return True
    if isinstance(active, str) and active.startswith("board:"):
        return find_board(state, active[len("board:"):]) is not None
    return False


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


def local_ip_address() -> str:
    """
    Die LAN-Adresse dieses Rechners. Der Trick mit dem UDP-Socket verschickt
    nichts - er laesst nur das Betriebssystem die passende Netzwerkkarte
    auswaehlen und fragt danach deren Adresse ab.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(0.2)
            probe.connect(("192.0.2.1", 9))  # Adresse aus dem Testbereich, kein Verkehr
            return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def clamp_screen_id(value) -> str:
    """
    Bildschirm-Name aus der URL saeubern: nur Kleinbuchstaben, Ziffern und
    Bindestrich. Der Name landet in Pfaden und Dateien - alles andere waere
    eine Einladung fuer Ueberraschungen.
    """
    if not isinstance(value, str):
        return ""
    cleaned = "".join(
        char for char in value.strip().lower() if char.isalnum() or char in "-_"
    )
    return cleaned[:24]


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
        # Eine Personenliste fuer beides: Kader-Zugehoerigkeit und Smash-Wertung.
        "players": [],
        "info": {"text": DEFAULT_INFO_TEXT},
        # display.active bestimmt, welches Leaderboard das Haupt-Leaderboard
        # (/) und das Admin-Panel anzeigen: "all" (S.O.S. + Smash, wie
        # bisher), "sos"/"smash" (nur eines der beiden eingebauten) oder
        # "board:<id>" fuer ein eigenes Leaderboard.
        "display": {"fx": "full", "active": "all"},
        "screens": [],
        "fan": {"force": False},
        # Zusaetzliche, frei anlegbare Leaderboards fuer weitere Spiele -
        # das eingebaute S.O.S.-Rennen und die Smash-Rangliste oben bleiben
        # davon unberuehrt und sind immer da.
        "boards": [],
        # Kurzbefehle: gespeicherte Bausteinfolgen, jeweils per GET /run/<id>
        # ausloesbar (Lesezeichen, Handy-Homescreen, Stream Deck, ...).
        "shortcuts": [],
        # Geteilte Bildschirme: eine Ansicht links, eine rechts, als eigene
        # waehlbare Ansicht "/split/<id>" in der Bildschirm-Palette.
        "splits": [],
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


def new_board(name, kind: str, mode: str) -> dict:
    """
    Ein zusaetzliches Leaderboard fuer ein weiteres Spiel.

    kind='race'  - Zeitrennen wie S.O.S.: immer zwei Teams (a/b), Level mit
                   Uhren. Ein Rennen mit nur einer Bahn ergibt keinen Sinn,
                   deshalb erzwingt 'race' den Modus 'teams'.
    kind='score' - Sieg/Niederlage-Rangliste wie Smash Bros: entweder mit
                   Team-Zuordnung oder als reine Einzelspieler-Liste.
    """
    if kind not in ("race", "score"):
        raise ValueError("Typ muss 'race' oder 'score' sein")
    if mode not in ("teams", "solo"):
        raise ValueError("Modus muss 'teams' oder 'solo' sein")
    if kind == "race":
        mode = "teams"
    board = {
        "id": new_id(),
        "name": clamp_name(name, "Leaderboard", limit=60),
        "kind": kind,
        "mode": mode,
        "createdAt": 0,
        "teams": (
            [{"id": "a", "name": "Team 1"}, {"id": "b", "name": "Team 2"}]
            if mode == "teams" else []
        ),
    }
    if kind == "race":
        board["levels"] = []
    else:
        board["entrants"] = []
    return board


def find_board(state: dict, board_id) -> dict | None:
    for board in state["boards"]:
        if board["id"] == board_id:
            return board
    return None


def board_find_level(board: dict, level_id) -> dict | None:
    for level in board.get("levels", []):
        if level["id"] == level_id:
            return level
    return None


def board_find_entrant(board: dict, entrant_id) -> dict | None:
    for entrant in board.get("entrants", []):
        if entrant["id"] == entrant_id:
            return entrant
    return None


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


def freeze_levels(levels: list, now: int) -> None:
    for level in levels:
        for timer in level["timers"].values():
            freeze_timer(timer, now)


def freeze_all(state: dict, now: int) -> None:
    freeze_levels(state["sos"]["levels"], now)


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

    # Spieler lagen frueher unter smash.players; seit Version 2 stehen sie
    # oben, weil sie auch die Kader-Zugehoerigkeit tragen. Beide Orte lesen,
    # damit ein Spielstand von vorher nichts verliert.
    smash = raw.get("smash") if isinstance(raw.get("smash"), dict) else {}
    players = raw.get("players")
    if not isinstance(players, list):
        players = smash.get("players")
    if isinstance(players, list):
        for stored in players:
            if not isinstance(stored, dict):
                continue
            name = clamp_name(stored.get("name"))
            if not name:
                continue
            team_id = stored.get("teamId")
            state["players"].append(
                {
                    "id": stored.get("id") or new_id(),
                    "name": name,
                    "teamId": team_id if team_id in TEAM_IDS else None,
                    "wins": max(0, as_int(stored.get("wins"), 0)),
                    "losses": max(0, as_int(stored.get("losses"), 0)),
                }
            )

    info = raw.get("info")
    if isinstance(info, dict) and isinstance(info.get("text"), str):
        state["info"]["text"] = info["text"][:4000]

    display = raw.get("display")
    if isinstance(display, dict) and display.get("fx") in ("full", "plain"):
        state["display"]["fx"] = display["fx"]

    fan = raw.get("fan")
    if isinstance(fan, dict):
        state["fan"]["force"] = bool(fan.get("force"))

    boards = raw.get("boards")
    if isinstance(boards, list):
        for stored in boards:
            if not isinstance(stored, dict):
                continue
            kind = stored.get("kind")
            if kind not in ("race", "score"):
                continue
            mode = stored.get("mode") if stored.get("mode") in ("teams", "solo") else "teams"
            board = new_board(stored.get("name"), kind, mode)
            board["id"] = stored.get("id") or board["id"]
            board["createdAt"] = as_int(stored.get("createdAt"), 0)

            stored_teams = stored.get("teams")
            if board["mode"] == "teams" and isinstance(stored_teams, list):
                for index, team_id in enumerate(TEAM_IDS):
                    found = next(
                        (t for t in stored_teams if isinstance(t, dict) and t.get("id") == team_id),
                        None,
                    )
                    if found:
                        board["teams"][index]["name"] = clamp_name(
                            found.get("name"), board["teams"][index]["name"]
                        )

            if board["kind"] == "race":
                stored_levels = stored.get("levels")
                if isinstance(stored_levels, list):
                    for stored_level in stored_levels:
                        if not isinstance(stored_level, dict):
                            continue
                        level = {
                            "id": stored_level.get("id") or new_id(),
                            "name": clamp_name(stored_level.get("name"), "Level"),
                            "active": bool(stored_level.get("active")),
                            "timers": {},
                        }
                        stored_timers = (
                            stored_level.get("timers")
                            if isinstance(stored_level.get("timers"), dict) else {}
                        )
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
                        board["levels"].append(level)
            else:
                stored_entrants = stored.get("entrants")
                if isinstance(stored_entrants, list):
                    for stored_entrant in stored_entrants:
                        if not isinstance(stored_entrant, dict):
                            continue
                        name = clamp_name(stored_entrant.get("name"), limit=40)
                        if not name:
                            continue
                        team_id = stored_entrant.get("teamId")
                        board["entrants"].append(
                            {
                                "id": stored_entrant.get("id") or new_id(),
                                "name": name,
                                "teamId": (
                                    team_id if board["mode"] == "teams" and team_id in TEAM_IDS
                                    else None
                                ),
                                "wins": max(0, as_int(stored_entrant.get("wins"), 0)),
                                "losses": max(0, as_int(stored_entrant.get("losses"), 0)),
                            }
                        )

            state["boards"].append(board)

    # Erst nach den Boards pruefbar, da "board:<id>" ein bestehendes eigenes
    # Leaderboard voraussetzt.
    if isinstance(display, dict) and _valid_display_active(state, display.get("active")):
        state["display"]["active"] = display["active"]

    # Ebenfalls erst nach den Boards, aber vor den Bildschirmen (die pruefen
    # "/split/<id>" gegen state["splits"]).
    splits = raw.get("splits")
    if isinstance(splits, list):
        for stored in splits:
            if not isinstance(stored, dict):
                continue
            name = clamp_name(stored.get("name"), "Geteilter Bildschirm", limit=60)
            left = stored.get("left")
            right = stored.get("right")
            state["splits"].append(
                {
                    "id": stored.get("id") or new_id(),
                    "name": name,
                    "left": left if _valid_split_side(state, left) else "/",
                    "right": right if _valid_split_side(state, right) else "/",
                }
            )

    screens = raw.get("screens")
    if isinstance(screens, list):
        for stored in screens:
            if not isinstance(stored, dict):
                continue
            screen_id = clamp_screen_id(stored.get("id"))
            if not screen_id:
                continue
            view = stored.get("view")
            state["screens"].append(
                {
                    "id": screen_id,
                    "name": clamp_name(stored.get("name"), screen_id, limit=40),
                    "view": view if _valid_view(state, view) else "/",
                    "seenAt": as_int(stored.get("seenAt"), 0),
                }
            )

    shortcuts = raw.get("shortcuts")
    if isinstance(shortcuts, list):
        for stored in shortcuts:
            if not isinstance(stored, dict):
                continue
            name = clamp_name(stored.get("name"))
            if not name:
                continue
            state["shortcuts"].append(
                {
                    "id": stored.get("id") or new_id(),
                    "name": name[:60],
                    "createdAt": as_int(stored.get("createdAt"), 0),
                    "steps": clamp_steps(stored.get("steps")),
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
        views = [{"path": path, "label": label} for path, label in VIEWS]
        # Eigene Leaderboards tauchen automatisch in der Bildschirm-Palette
        # auf - niemand soll eine Ansicht von Hand eintragen muessen.
        for board in self.state["boards"]:
            icon = "\U0001F3C1" if board["kind"] == "race" else "\U0001F3C6"
            views.append({"path": f"/board/{board['id']}", "label": f"{icon} {board['name']}"})
        # Dasselbe fuer geteilte Bildschirme.
        for split in self.state["splits"]:
            views.append({"path": f"/split/{split['id']}", "label": f"◧ {split['name']}"})
        body = {
            "state": self.state,
            "standings": compute_standings(self.state, now),
            "views": views,
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

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

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


def op_start_active_timers(state: dict, now: int) -> None:
    """Startet beide Uhren des zuletzt gestarteten Levels auf einmal - der
    Startknopf fuer den Fall, dass ein Level ohne Autostart angelegt wurde
    und beide Teams gleichzeitig losgehen sollen."""
    level = next((l for l in state["sos"]["levels"] if l["active"]), None)
    if level is None:
        raise KeyError("Kein Level gestartet")
    for team_id in TEAM_IDS:
        timer = level["timers"][team_id]
        if timer["runningSince"] is None:
            timer["runningSince"] = now
        timer["finished"] = False


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


def op_add_player(state: dict, name, team_id=None) -> dict:
    label = clamp_name(name, limit=40)
    if not label:
        raise ValueError("Name fehlt")
    if len(state["players"]) >= MAX_PLAYERS:
        raise ValueError(f"Es sind schon {MAX_PLAYERS} Spieler eingetragen")
    player = {
        "id": new_id(),
        "name": label,
        "teamId": team_id if team_id in TEAM_IDS else None,
        "wins": 0,
        "losses": 0,
    }
    state["players"].append(player)
    return player


def find_player(state: dict, player_id) -> dict | None:
    for player in state["players"]:
        if player["id"] == player_id:
            return player
    return None


def op_join(state: dict, player_id, name, team_id) -> dict:
    """
    Anmeldung eines Gastes ueber den QR-Code.

    Bringt er das Cookie einer frueheren Anmeldung mit, wird sein Eintrag
    geaendert statt ein zweiter angelegt - sonst haette jeder, der die Seite
    zweimal oeffnet, einen Doppelgaenger im Kader.
    """
    label = clamp_name(name, limit=40)
    if not label:
        raise ValueError("Bitte einen Namen eingeben")
    if team_id not in TEAM_IDS:
        raise ValueError("Bitte ein Team waehlen")

    player = find_player(state, player_id) if player_id else None
    if player is None:
        return op_add_player(state, label, team_id)

    player["name"] = label
    player["teamId"] = team_id
    return player


def op_set_player_team(state: dict, player_id, team_id) -> None:
    player = find_player(state, player_id)
    if player is None:
        raise KeyError("Spieler unbekannt")
    if team_id is not None and team_id not in TEAM_IDS:
        raise ValueError("Unbekanntes Team")
    player["teamId"] = team_id


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
    """
    Zuruecksetzen. 'smash' loescht nur die Siege, nicht die Spieler - die
    Personenliste ist auch der Kader und soll nicht mit der Wertung
    verschwinden. Bildschirme, Info-Text und Schalter bleiben immer stehen.
    """
    if scope in ("all", "sos"):
        state["sos"]["levels"] = []
    if scope in ("all", "smash"):
        for player in state["players"]:
            player["wins"] = 0
            player["losses"] = 0
    if scope in ("all", "players"):
        state["players"] = []
    if scope == "all":
        state["sos"]["teams"] = default_state()["sos"]["teams"]


def op_set_info(state: dict, text) -> None:
    state["info"]["text"] = (text if isinstance(text, str) else "")[:4000]


def op_set_fx(state: dict, fx) -> None:
    if fx not in ("full", "plain"):
        raise ValueError("fx muss 'full' oder 'plain' sein")
    state["display"]["fx"] = fx


def op_set_display_active(state: dict, active) -> None:
    if not _valid_display_active(state, active):
        raise ValueError("active muss 'all', 'sos', 'smash' oder 'board:<id>' sein")
    state["display"]["active"] = active


def op_set_fan_force(state: dict, force) -> bool:
    state["fan"]["force"] = bool(force)
    return state["fan"]["force"]


# -- Bildschirme -------------------------------------------------------------
def find_screen(state: dict, screen_id) -> dict | None:
    for screen in state["screens"]:
        if screen["id"] == screen_id:
            return screen
    return None


def op_touch_screen(state: dict, screen_id: str, now: int) -> dict:
    """
    Ein Bildschirm meldet sich. Neue melden sich damit selbst an, bekannte
    halten nur ihren Zeitstempel frisch - daran haengt die Offline-Anzeige.
    """
    screen = find_screen(state, screen_id)
    if screen is None:
        if len(state["screens"]) >= MAX_SCREENS:
            raise ValueError(f"Es sind schon {MAX_SCREENS} Bildschirme angemeldet")
        screen = {"id": screen_id, "name": screen_id, "view": "/", "seenAt": now}
        state["screens"].append(screen)
    else:
        screen["seenAt"] = now
    return screen


def op_assign_screen(state: dict, screen_id, view) -> dict:
    screen = find_screen(state, screen_id)
    if screen is None:
        raise KeyError("Bildschirm unbekannt")
    if not _valid_view(state, view):
        raise ValueError("Unbekannte Ansicht")
    screen["view"] = view
    return screen


def op_rename_screen(state: dict, screen_id, name) -> None:
    screen = find_screen(state, screen_id)
    if screen is None:
        raise KeyError("Bildschirm unbekannt")
    screen["name"] = clamp_name(name, screen["id"], limit=40)


def op_delete_screen(state: dict, screen_id) -> None:
    screens = state["screens"]
    remaining = [s for s in screens if s["id"] != screen_id]
    if len(remaining) == len(screens):
        raise KeyError("Bildschirm unbekannt")
    state["screens"] = remaining


# -- Eigene Leaderboards ------------------------------------------------------
def op_create_board(state: dict, name, kind, mode, now: int) -> dict:
    if len(state["boards"]) >= MAX_BOARDS:
        raise ValueError(f"Es sind schon {MAX_BOARDS} Leaderboards angelegt")
    board = new_board(name, kind, mode)
    board["createdAt"] = now
    # Neuestes zuerst - im Admin sofort sichtbar, ohne dass man scrollen muss.
    state["boards"].insert(0, board)
    return board


def op_rename_board(state: dict, board_id, name) -> None:
    board = find_board(state, board_id)
    if board is None:
        raise KeyError("Leaderboard unbekannt")
    board["name"] = clamp_name(name, board["name"], limit=60)


def op_delete_board(state: dict, board_id) -> None:
    boards = state["boards"]
    remaining = [b for b in boards if b["id"] != board_id]
    if len(remaining) == len(boards):
        raise KeyError("Leaderboard unbekannt")
    state["boards"] = remaining
    # Bildschirme, die dieses Leaderboard zeigten, fallen auf das
    # Haupt-Leaderboard zurueck - sonst zeigen sie fuer immer eine Ansicht,
    # die es nicht mehr gibt.
    view = f"/board/{board_id}"
    for screen in state["screens"]:
        if screen["view"] == view:
            screen["view"] = "/"
    # Dasselbe fuer die Anzeige-Auswahl: zeigte sie genau dieses Leaderboard,
    # faellt sie auf "alle" zurueck statt fuer immer ins Leere zu zeigen.
    if state["display"]["active"] == f"board:{board_id}":
        state["display"]["active"] = "all"
    # ... und fuer geteilte Bildschirme: eine Haelfte, die dieses Leaderboard
    # zeigte, faellt auf das Haupt-Leaderboard zurueck.
    for split in state["splits"]:
        if split["left"] == view:
            split["left"] = "/"
        if split["right"] == view:
            split["right"] = "/"


def op_set_board_team_names(state: dict, board_id, names: dict) -> None:
    board = find_board(state, board_id)
    if board is None:
        raise KeyError("Leaderboard unbekannt")
    if board["mode"] != "teams":
        raise ValueError("Dieses Leaderboard hat keine Teams")
    for team in board["teams"]:
        if team["id"] in names:
            team["name"] = clamp_name(names[team["id"]], team["name"])


def op_board_reset(state: dict, board_id) -> None:
    board = find_board(state, board_id)
    if board is None:
        raise KeyError("Leaderboard unbekannt")
    if board["kind"] == "race":
        board["levels"] = []
    else:
        for entrant in board["entrants"]:
            entrant["wins"] = 0
            entrant["losses"] = 0


# -- Leaderboards vom Typ "Rennen" (wie S.O.S., Zeit-Uhren pro Team) ---------
def op_board_start_level(state: dict, board_id, name, autostart: bool, now: int) -> dict:
    board = find_board(state, board_id)
    if board is None or board["kind"] != "race":
        raise KeyError("Renn-Leaderboard unbekannt")
    freeze_levels(board["levels"], now)
    for level in board["levels"]:
        level["active"] = False
    label = clamp_name(name, f"Level {len(board['levels']) + 1}")
    level = new_level(label, running_since=now if autostart else None)
    board["levels"].append(level)
    return level


def op_board_rename_level(state: dict, board_id, level_id, name) -> None:
    board = find_board(state, board_id)
    level = board_find_level(board, level_id) if board else None
    if level is None:
        raise KeyError("Level unbekannt")
    level["name"] = clamp_name(name, level["name"])


def op_board_delete_level(state: dict, board_id, level_id) -> None:
    board = find_board(state, board_id)
    if board is None:
        raise KeyError("Leaderboard unbekannt")
    levels = board["levels"]
    remaining = [lv for lv in levels if lv["id"] != level_id]
    if len(remaining) == len(levels):
        raise KeyError("Level unbekannt")
    board["levels"] = remaining
    if remaining and not any(lv.get("active") for lv in remaining):
        remaining[-1]["active"] = True


def op_board_timer(state: dict, board_id, level_id, team_id: str, action: str, now: int) -> None:
    board = find_board(state, board_id)
    level = board_find_level(board, level_id) if board else None
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


def op_board_start_active_timers(state: dict, board_id, now: int) -> None:
    """Dasselbe wie op_start_active_timers, nur fuer ein eigenes Leaderboard."""
    board = find_board(state, board_id)
    if board is None or board.get("kind") != "race":
        raise KeyError("Renn-Leaderboard unbekannt")
    level = next((l for l in board["levels"] if l["active"]), None)
    if level is None:
        raise KeyError("Kein Level gestartet")
    for team_id in TEAM_IDS:
        timer = level["timers"][team_id]
        if timer["runningSince"] is None:
            timer["runningSince"] = now
        timer["finished"] = False


def op_board_adjust_timer(
    state: dict, board_id, level_id, team_id: str, value_ms: int, now: int
) -> None:
    board = find_board(state, board_id)
    level = board_find_level(board, level_id) if board else None
    if level is None or team_id not in TEAM_IDS:
        raise KeyError("Level oder Team unbekannt")
    timer = level["timers"][team_id]
    timer["elapsedMs"] = max(0, as_int(value_ms, 0))
    if timer["runningSince"] is not None:
        timer["runningSince"] = now


# -- Leaderboards vom Typ "Rangliste" (wie Smash, Sieg/Niederlage) ----------
def op_board_add_entrant(state: dict, board_id, name, team_id=None) -> dict:
    board = find_board(state, board_id)
    if board is None or board["kind"] != "score":
        raise KeyError("Ranglisten-Leaderboard unbekannt")
    label = clamp_name(name, limit=40)
    if not label:
        raise ValueError("Name fehlt")
    if len(board["entrants"]) >= MAX_PLAYERS:
        raise ValueError(f"Es sind schon {MAX_PLAYERS} Teilnehmer eingetragen")
    entrant = {
        "id": new_id(),
        "name": label,
        "teamId": team_id if (board["mode"] == "teams" and team_id in TEAM_IDS) else None,
        "wins": 0,
        "losses": 0,
    }
    board["entrants"].append(entrant)
    return entrant


def op_board_rename_entrant(state: dict, board_id, entrant_id, name) -> None:
    board = find_board(state, board_id)
    entrant = board_find_entrant(board, entrant_id) if board else None
    if entrant is None:
        raise KeyError("Teilnehmer unbekannt")
    entrant["name"] = clamp_name(name, entrant["name"], limit=40)


def op_board_delete_entrant(state: dict, board_id, entrant_id) -> None:
    board = find_board(state, board_id)
    if board is None:
        raise KeyError("Leaderboard unbekannt")
    entrants = board["entrants"]
    remaining = [e for e in entrants if e["id"] != entrant_id]
    if len(remaining) == len(entrants):
        raise KeyError("Teilnehmer unbekannt")
    board["entrants"] = remaining


def op_board_entrant_score(state: dict, board_id, entrant_id, field: str, delta: int) -> None:
    if field not in ("wins", "losses"):
        raise ValueError("Feld muss 'wins' oder 'losses' sein")
    board = find_board(state, board_id)
    entrant = board_find_entrant(board, entrant_id) if board else None
    if entrant is None:
        raise KeyError("Teilnehmer unbekannt")
    entrant[field] = max(0, as_int(entrant[field], 0) + as_int(delta, 0))


def op_board_set_entrant_team(state: dict, board_id, entrant_id, team_id) -> None:
    board = find_board(state, board_id)
    entrant = board_find_entrant(board, entrant_id) if board else None
    if entrant is None:
        raise KeyError("Teilnehmer unbekannt")
    if board["mode"] != "teams":
        raise ValueError("Dieses Leaderboard hat keine Teams")
    if team_id is not None and team_id not in TEAM_IDS:
        raise ValueError("Unbekanntes Team")
    entrant["teamId"] = team_id


# -- Geteilte Bildschirme -----------------------------------------------------
# Ein Bildschirm, der in der Mitte geteilt eine Ansicht links und eine
# andere rechts zeigt - z.B. Leaderboard links, Info-Tafel rechts. Wie bei
# den eigenen Leaderboards bekommt jeder Split eine Id und taucht als eigene
# waehlbare Ansicht "/split/<id>" in der Bildschirm-Palette auf.
def new_split(name) -> dict:
    return {
        "id": new_id(),
        "name": clamp_name(name, "Geteilter Bildschirm", limit=60),
        "left": "/",
        "right": "/",
    }


def find_split(state: dict, split_id) -> dict | None:
    for split in state["splits"]:
        if split["id"] == split_id:
            return split
    return None


def op_create_split(state: dict, name, left, right) -> dict:
    if len(state["splits"]) >= MAX_SPLITS:
        raise ValueError(f"Es sind schon {MAX_SPLITS} geteilte Bildschirme angelegt")
    if not _valid_split_side(state, left) or not _valid_split_side(state, right):
        raise ValueError("Ungueltige Ansicht fuer eine Haelfte")
    split = new_split(name)
    split["left"] = left
    split["right"] = right
    # Neuester zuerst - im Admin sofort sichtbar, wie bei den Leaderboards.
    state["splits"].insert(0, split)
    return split


def op_rename_split(state: dict, split_id, name) -> None:
    split = find_split(state, split_id)
    if split is None:
        raise KeyError("Geteilter Bildschirm unbekannt")
    split["name"] = clamp_name(name, split["name"], limit=60)


def op_set_split_sides(state: dict, split_id, left, right) -> None:
    split = find_split(state, split_id)
    if split is None:
        raise KeyError("Geteilter Bildschirm unbekannt")
    if not _valid_split_side(state, left) or not _valid_split_side(state, right):
        raise ValueError("Ungueltige Ansicht fuer eine Haelfte")
    split["left"] = left
    split["right"] = right


def op_delete_split(state: dict, split_id) -> None:
    splits = state["splits"]
    remaining = [s for s in splits if s["id"] != split_id]
    if len(remaining) == len(splits):
        raise KeyError("Geteilter Bildschirm unbekannt")
    state["splits"] = remaining
    # Bildschirme, die diesen Split zeigten, fallen auf das Haupt-Leaderboard
    # zurueck - sonst zeigen sie fuer immer eine Ansicht, die es nicht mehr gibt.
    view = f"/split/{split_id}"
    for screen in state["screens"]:
        if screen["view"] == view:
            screen["view"] = "/"


# -- Kurzbefehle --------------------------------------------------------------
# Gespeicherte Bausteinfolgen (wie bei Scratch zusammengesteckt), jede ueber
# eine eigene Adresse GET /run/<id> ausloesbar - als Lesezeichen, Kachel auf
# dem Handy-Homescreen oder Knopf auf einem Stream Deck. Jeder Baustein ist
# ein kleines dict mit "type" plus seinen eigenen Feldern; SHORTCUT_STEP_TYPES
# haelt fest, welche Typen es gibt, damit ein kaputter/fremder Typ beim
# Laden oder Speichern einfach verworfen wird statt den Server zu verwirren.
SHORTCUT_STEP_TYPES = (
    "assign_screen", "create_board", "start_level", "board_start_level",
    "start_timer", "board_start_timer",
    "set_fx", "set_info", "reset", "board_reset",
)


def new_shortcut(name) -> dict:
    return {
        "id": new_id(),
        "name": clamp_name(name, "Kurzbefehl", limit=60),
        "createdAt": 0,
        "steps": [],
    }


def find_shortcut(state: dict, shortcut_id) -> dict | None:
    for shortcut in state["shortcuts"]:
        if shortcut["id"] == shortcut_id:
            return shortcut
    return None


def find_shortcut_by_id_or_name(state: dict, token) -> dict | None:
    """/run/<...> soll sowohl per Id als auch per Namen erreichbar sein - so
    liest sich die Adresse selbst erklaerend (/run/Party-Start statt
    /run/3f9a2b1c). Ids sind eindeutig und haben Vorrang; bei mehreren
    Kurzbefehlen mit demselben Namen zaehlt einfach der erste Treffer."""
    found = find_shortcut(state, token)
    if found is not None:
        return found
    for shortcut in state["shortcuts"]:
        if shortcut["name"] == token:
            return shortcut
    return None


def clamp_step(raw) -> dict | None:
    """Einen einzelnen Baustein aus JSON saeubern. Unbekannte, kaputte oder
    unvollstaendige Bausteine werden beim Speichern stillschweigend
    verworfen - ein Kurzbefehl soll nie an einem einzelnen Baustein
    haengen bleiben, weder beim Speichern noch beim Ausfuehren."""
    if not isinstance(raw, dict):
        return None
    step_type = raw.get("type")
    if step_type not in SHORTCUT_STEP_TYPES:
        return None

    if step_type == "assign_screen":
        screen_id = clamp_screen_id(raw.get("screenId"))
        view = raw.get("view")
        if not screen_id or not isinstance(view, str) or not view:
            return None
        return {"type": step_type, "screenId": screen_id, "view": view}

    if step_type == "create_board":
        kind = raw.get("kind")
        mode = raw.get("mode")
        if kind not in ("race", "score") or mode not in ("teams", "solo"):
            return None
        return {
            "type": step_type,
            "name": clamp_name(raw.get("name"), "Leaderboard", limit=60),
            "kind": kind,
            "mode": mode,
        }

    if step_type == "start_level":
        return {
            "type": step_type,
            "name": clamp_name(raw.get("name"), "", limit=60),
            "autostart": bool(raw.get("autostart", False)),
            # Statt des festen Namens beim Ausfuehren erst danach fragen -
            # siehe shortcut_needs_name_prompt()/op_run_shortcut().
            "promptName": bool(raw.get("promptName", False)),
        }

    if step_type == "board_start_level":
        board_id = raw.get("boardId")
        if not isinstance(board_id, str) or not board_id:
            return None
        return {
            "type": step_type,
            "boardId": board_id,
            "name": clamp_name(raw.get("name"), "", limit=60),
            "autostart": bool(raw.get("autostart", False)),
            "promptName": bool(raw.get("promptName", False)),
        }

    if step_type == "start_timer":
        return {"type": step_type}

    if step_type == "board_start_timer":
        board_id = raw.get("boardId")
        if not isinstance(board_id, str) or not board_id:
            return None
        return {"type": step_type, "boardId": board_id}

    if step_type == "set_fx":
        fx = raw.get("fx")
        if fx not in ("full", "plain"):
            return None
        return {"type": step_type, "fx": fx}

    if step_type == "set_info":
        text = raw.get("text")
        if not isinstance(text, str):
            return None
        return {"type": step_type, "text": text[:4000]}

    if step_type == "reset":
        scope = raw.get("scope")
        if scope not in ("all", "sos", "smash", "players"):
            return None
        return {"type": step_type, "scope": scope}

    if step_type == "board_reset":
        board_id = raw.get("boardId")
        if not isinstance(board_id, str) or not board_id:
            return None
        return {"type": step_type, "boardId": board_id}

    return None


def clamp_steps(raw) -> list:
    if not isinstance(raw, list):
        return []
    cleaned = []
    for item in raw[:MAX_STEPS_PER_SHORTCUT]:
        step = clamp_step(item)
        if step is not None:
            cleaned.append(step)
    return cleaned


def op_create_shortcut(state: dict, name, steps, now: int) -> dict:
    if len(state["shortcuts"]) >= MAX_SHORTCUTS:
        raise ValueError(f"Es sind schon {MAX_SHORTCUTS} Kurzbefehle angelegt")
    shortcut = new_shortcut(name)
    shortcut["createdAt"] = now
    shortcut["steps"] = clamp_steps(steps)
    # Neuester zuerst - im Admin sofort sichtbar, wie bei den Leaderboards.
    state["shortcuts"].insert(0, shortcut)
    return shortcut


def op_rename_shortcut(state: dict, shortcut_id, name) -> None:
    shortcut = find_shortcut(state, shortcut_id)
    if shortcut is None:
        raise KeyError("Kurzbefehl unbekannt")
    shortcut["name"] = clamp_name(name, shortcut["name"], limit=60)


def op_update_shortcut_steps(state: dict, shortcut_id, steps) -> dict:
    shortcut = find_shortcut(state, shortcut_id)
    if shortcut is None:
        raise KeyError("Kurzbefehl unbekannt")
    shortcut["steps"] = clamp_steps(steps)
    return shortcut


def op_delete_shortcut(state: dict, shortcut_id) -> None:
    shortcuts = state["shortcuts"]
    remaining = [s for s in shortcuts if s["id"] != shortcut_id]
    if len(remaining) == len(shortcuts):
        raise KeyError("Kurzbefehl unbekannt")
    state["shortcuts"] = remaining


def run_shortcut_step(state: dict, step: dict, now: int) -> None:
    """Fuehrt einen einzelnen Baustein aus. Fehler (z.B. ein inzwischen
    geloeschter Bildschirm oder ein geloeschtes Leaderboard) werden
    abgefangen, statt den ganzen Kurzbefehl abzubrechen - die uebrigen
    Bausteine sollen trotzdem laufen."""
    step_type = step.get("type")
    try:
        if step_type == "assign_screen":
            op_assign_screen(state, step["screenId"], step["view"])
        elif step_type == "create_board":
            op_create_board(state, step.get("name"), step["kind"], step["mode"], now)
        elif step_type == "start_level":
            op_start_level(state, step.get("name") or None, step.get("autostart", False), now)
        elif step_type == "board_start_level":
            op_board_start_level(
                state, step["boardId"], step.get("name") or None, step.get("autostart", False), now
            )
        elif step_type == "start_timer":
            op_start_active_timers(state, now)
        elif step_type == "board_start_timer":
            op_board_start_active_timers(state, step["boardId"], now)
        elif step_type == "set_fx":
            op_set_fx(state, step["fx"])
        elif step_type == "set_info":
            op_set_info(state, step.get("text", ""))
        elif step_type == "reset":
            op_reset(state, step["scope"])
        elif step_type == "board_reset":
            op_board_reset(state, step["boardId"])
    except (KeyError, ValueError):
        pass


def shortcut_needs_name_prompt(shortcut: dict) -> bool:
    """Wahr, wenn mindestens ein Level-Baustein vor dem Start erst nach
    einem Namen fragen soll - dann zeigt GET /run/<...> zuerst eine kleine
    Eingabeseite, statt sofort auszufuehren."""
    return any(
        step.get("promptName") and step.get("type") in ("start_level", "board_start_level")
        for step in shortcut["steps"]
    )


def op_run_shortcut(state: dict, shortcut_id_or_name, now: int, prompt_name=None) -> dict:
    shortcut = find_shortcut_by_id_or_name(state, shortcut_id_or_name)
    if shortcut is None:
        raise KeyError("Kurzbefehl unbekannt")
    for step in shortcut["steps"]:
        if (
            prompt_name is not None
            and step.get("promptName")
            and step.get("type") in ("start_level", "board_start_level")
        ):
            step = {**step, "name": prompt_name}
        run_shortcut_step(state, step, now)
    return shortcut


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

    # -- Anmeldeversuche bremsen ------------------------------------------
    # Nur noch fuer die Spieler-Anmeldung (/api/join) gedacht: bremst, wer
    # dort mit leerem Namen o.ae. die Liste vollzuspammen versucht.
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

    # -- Signiertes Cookie fuer die Spieler-Wiedererkennung -----------------
    def _secret_bytes(self) -> bytes:
        return self.config["secret"].encode()

    def make_value_token(self, value: str, ttl: int) -> str:
        """Signiertes Cookie, das einen Wert traegt - fuer die Spieler-Kennung."""
        expires = int(time.time()) + ttl
        payload = f"{expires}:{value}"
        signature = hmac.new(self._secret_bytes(), payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}:{signature}"

    def check_value_token(self, token):
        """Gibt den Wert zurueck, wenn Signatur und Frist stimmen, sonst None."""
        if not token or token.count(":") != 2:
            return None
        expires, value, signature = token.split(":")
        expected = hmac.new(
            self._secret_bytes(), f"{expires}:{value}".encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        return value if as_int(expires, 0) > time.time() else None


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
    public_url: str = ""

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

    def _run_name_prompt_page(self, shortcut: dict, token: str) -> None:
        """Kleine Zwischenseite fuer /run/<...>, wenn der Kurzbefehl vor dem
        Levelstart erst nach einem Namen fragen soll (siehe
        shortcut_needs_name_prompt). Das Formular schickt denselben Aufruf
        noch einmal ab, jetzt mit dem eingegebenen Namen im Query-String -
        dann laeuft der Kurzbefehl ganz normal weiter."""
        title = html.escape(shortcut["name"])
        action = "/run/" + quote(token, safe="")
        body = f"""<!DOCTYPE html>
<html lang="de" data-fx="plain">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Levelname – {title}</title>
<link rel="icon" href="data:,">
<link rel="stylesheet" href="/static/theme.css">
<link rel="stylesheet" href="/static/pages.css">
</head>
<body>
<section class="panel join">
<h1>Levelname</h1>
<p class="subtle">Kurzbefehl „{title}“</p>
<form method="get" action="{action}">
<div class="field">
<label for="name">Name des neuen Levels</label>
<input type="text" id="name" name="name" maxlength="60" autofocus
       placeholder="leer lassen fuer automatische Nummerierung">
</div>
<button class="primary big" style="width: 100%" type="submit">Level starten</button>
</form>
</section>
</body>
</html>"""
        self._send(HTTPStatus.OK, body.encode("utf-8"), "text/html; charset=utf-8")

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

    # Oeffentliche Seiten: Pfad -> Datei in static/
    PAGES = {
        "/": "display.html",
        "/seiten": "seiten.html",
        "/qr": "qr.html",
        "/mitmachen": "join.html",
        "/teams": "teams.html",
        "/system": "system.html",
        "/info": "info.html",
        "/vibe": "vibe.html",
        "/kurzbefehle": "shortcuts.html",
        "/fernbedienung": "fernbedienung.html",
    }

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"

        if path in self.PAGES:
            return self._static(self.PAGES[path])
        if path == "/admin":
            return self._static("admin.html")
        if path.startswith("/mitmachen/"):
            # Dieselbe Anmeldeseite wie /mitmachen, aber mit fest vorgegebenem
            # Team - join.js liest das Team aus dem Pfad und blendet die
            # Teamwahl aus. /mitmachen selbst bleibt unveraendert bestehen.
            team_id = path[len("/mitmachen/"):]
            return self._static("join.html") if team_id in TEAM_IDS \
                else self._error(HTTPStatus.NOT_FOUND, "Unbekanntes Team")
        if path.startswith("/screen/"):
            # Der Name steckt im Pfad; die Seite meldet sich selbst per API an.
            return self._static("screen.html") if clamp_screen_id(path[len("/screen/"):]) \
                else self._error(HTTPStatus.NOT_FOUND, "Ungueltiger Bildschirmname")
        if path.startswith("/board/"):
            # Die Kennung steckt im Pfad; board.js sucht sie sich selbst aus
            # dem Live-Zustand - unbekannte IDs zeigen dort einen Hinweis.
            return self._static("board.html")
        if path.startswith("/split/"):
            # Dieselbe Idee wie bei /board/<id>: split.js sucht sich die
            # Kennung selbst aus dem Live-Zustand.
            return self._static("split.html")
        if path.startswith("/run/"):
            # Aufrufen fuehrt den Kurzbefehl aus und leitet dann weiter -
            # kein eigener Seiteninhalt, absichtlich per einfachem GET
            # ausloesbar (Lesezeichen, Handy-Homescreen, Stream Deck ...).
            # Ein unbekannter oder inzwischen geloeschter Kurzbefehl macht
            # einfach nichts, statt einen Fehler anzuzeigen.
            #
            # Ausnahme: enthaelt der Kurzbefehl einen Level-Baustein mit
            # "erst nach dem Namen fragen" und wurde noch kein Name
            # mitgegeben, zeigt diese Adresse zuerst eine kleine
            # Eingabeseite statt sofort auszufuehren - siehe
            # shortcut_needs_name_prompt().
            shortcut_token = unquote(path[len("/run/"):])
            query = parse_qs(urlparse(self.path).query)
            prompt_name = query.get("name", [None])[0]
            shortcut = find_shortcut_by_id_or_name(self.store.snapshot(), shortcut_token)
            if shortcut is not None and prompt_name is None and shortcut_needs_name_prompt(shortcut):
                return self._run_name_prompt_page(shortcut, shortcut_token)
            try:
                self.store.mutate(
                    lambda s: op_run_shortcut(s, shortcut_token, now_ms(), prompt_name)
                )
            except KeyError:
                pass
            return self._send(HTTPStatus.FOUND, b"", "text/plain; charset=utf-8", {"Location": "/admin"})

        if path == "/api/state":
            payload = json.loads(self.store.payload())
            payload["me"] = self._current_player()
            payload["joinUrl"] = self._join_url()
            return self._json(payload)
        if path == "/api/fan":
            return self._json(self._fan_status())
        if path == "/api/system":
            return self._json(self._system_status())
        if path == "/api/qr.svg":
            body = qr.svg(self._join_url()).encode("utf-8")
            return self._send(HTTPStatus.OK, body, "image/svg+xml; charset=utf-8")
        if path == "/api/events":
            return self._events()
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])

        self._error(HTTPStatus.NOT_FOUND, "Unbekannter Pfad")

    # -- Adresse, die im QR-Code landet -------------------------------------
    def _join_url(self) -> str:
        """
        Die Adresse, unter der Gaeste die Anmeldeseite erreichen.

        Reihenfolge: fest eingestellt -> der Host, ueber den diese Anfrage kam
        -> die LAN-Adresse des Pi. Der Umweg ueber den Host ist wichtig, weil
        der Kiosk-Browser auf dem Pi selbst ueber localhost kommt - dieser
        Adresse kann ein Handy nicht folgen.
        """
        if self.public_url:
            return self.public_url.rstrip("/") + "/mitmachen"
        host = (self.headers.get("Host") or "").strip()
        hostname = host.split(":")[0]
        if host and hostname not in ("localhost", "127.0.0.1", "::1", ""):
            return f"http://{host}/mitmachen"
        port = self.server.server_address[1]
        suffix = "" if port == 80 else f":{port}"
        return f"http://{local_ip_address()}{suffix}/mitmachen"

    def _current_player(self) -> dict | None:
        """Der Spieler, dessen Cookie diese Anfrage mitbringt (falls vorhanden)."""
        player_id = self._player_id()
        if not player_id:
            return None
        state = self.store.snapshot()
        return find_player(state, player_id)

    def _player_id(self):
        header = self.headers.get("Cookie")
        if not header:
            return None
        try:
            morsel = SimpleCookie(header).get(PLAYER_COOKIE)
        except Exception:
            return None
        if not morsel:
            return None
        return self.auth.check_value_token(morsel.value)

    def _system_status(self) -> dict:
        """Werte fuer die Systemseite - alles direkt aus /proc und /sys."""
        status = {"fan": self._fan_status()}
        try:
            with open("/proc/uptime", "r", encoding="utf-8") as fh:
                status["uptimeSeconds"] = int(float(fh.read().split()[0]))
        except (OSError, ValueError, IndexError):
            status["uptimeSeconds"] = None
        try:
            with open("/proc/loadavg", "r", encoding="utf-8") as fh:
                status["load"] = [float(value) for value in fh.read().split()[:3]]
        except (OSError, ValueError):
            status["load"] = None
        status["address"] = local_ip_address()
        status["connections"] = self.store.subscriber_count()
        status["serverNow"] = now_ms()
        return status

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

        if path == "/api/join":
            return self._join(body)
        if path == "/api/screens/hello":
            return self._screen_hello(body)

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
                        s, body.get("name"), body.get("autostart", False), now
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
            "/api/sos/timer/start-all": lambda body, now: store.mutate(
                lambda s: op_start_active_timers(s, now)
            ),
            "/api/smash/player": lambda body, now: self._smash_player(body),
            "/api/smash/result": lambda body, now: store.mutate(
                lambda s: op_match(s, body.get("winnerId"), body.get("loserId"))
            ),
            "/api/reset": lambda body, now: store.mutate(
                lambda s: op_reset(s, body.get("scope", "all"))
            ),
            # -- Bildschirme, Anzeige, Luefter, Info-Tafel ------------------
            "/api/screens/assign": lambda body, now: {
                "screen": store.mutate(
                    lambda s: op_assign_screen(s, body.get("screenId"), body.get("view"))
                )
            },
            "/api/screens/rename": lambda body, now: store.mutate(
                lambda s: op_rename_screen(s, body.get("screenId"), body.get("name"))
            ),
            "/api/screens/delete": lambda body, now: store.mutate(
                lambda s: op_delete_screen(s, body.get("screenId"))
            ),
            "/api/display": lambda body, now: store.mutate(
                lambda s: op_set_fx(s, body.get("fx"))
            ),
            "/api/display/active": lambda body, now: store.mutate(
                lambda s: op_set_display_active(s, body.get("active"))
            ),
            "/api/info": lambda body, now: store.mutate(
                lambda s: op_set_info(s, body.get("text"))
            ),
            "/api/fan": lambda body, now: self._set_fan(body),
            # -- Eigene Leaderboards -----------------------------------------
            "/api/boards/create": lambda body, now: {
                "board": store.mutate(
                    lambda s: op_create_board(
                        s, body.get("name"), body.get("kind"), body.get("mode"), now
                    )
                )
            },
            "/api/boards/rename": lambda body, now: store.mutate(
                lambda s: op_rename_board(s, body.get("boardId"), body.get("name"))
            ),
            "/api/boards/delete": lambda body, now: store.mutate(
                lambda s: op_delete_board(s, body.get("boardId"))
            ),
            "/api/boards/teams": lambda body, now: store.mutate(
                lambda s: op_set_board_team_names(s, body.get("boardId"), body.get("names") or {})
            ),
            "/api/boards/reset": lambda body, now: store.mutate(
                lambda s: op_board_reset(s, body.get("boardId"))
            ),
            "/api/boards/level": lambda body, now: {
                "level": store.mutate(
                    lambda s: op_board_start_level(
                        s, body.get("boardId"), body.get("name"), body.get("autostart", False), now
                    )
                )
            },
            "/api/boards/level/rename": lambda body, now: store.mutate(
                lambda s: op_board_rename_level(s, body.get("boardId"), body.get("levelId"), body.get("name"))
            ),
            "/api/boards/level/delete": lambda body, now: store.mutate(
                lambda s: op_board_delete_level(s, body.get("boardId"), body.get("levelId"))
            ),
            "/api/boards/timer": lambda body, now: store.mutate(
                lambda s: op_board_timer(
                    s, body.get("boardId"), body.get("levelId"), body.get("team"),
                    body.get("action", ""), now,
                )
            ),
            "/api/boards/timer/adjust": lambda body, now: store.mutate(
                lambda s: op_board_adjust_timer(
                    s, body.get("boardId"), body.get("levelId"), body.get("team"),
                    body.get("valueMs"), now,
                )
            ),
            "/api/boards/timer/start-all": lambda body, now: store.mutate(
                lambda s: op_board_start_active_timers(s, body.get("boardId"), now)
            ),
            "/api/boards/entrant": lambda body, now: self._board_entrant(body),
            # -- Geteilte Bildschirme --------------------------------------------
            "/api/splits/create": lambda body, now: {
                "split": store.mutate(
                    lambda s: op_create_split(
                        s, body.get("name"), body.get("left"), body.get("right")
                    )
                )
            },
            "/api/splits/rename": lambda body, now: store.mutate(
                lambda s: op_rename_split(s, body.get("splitId"), body.get("name"))
            ),
            "/api/splits/sides": lambda body, now: store.mutate(
                lambda s: op_set_split_sides(
                    s, body.get("splitId"), body.get("left"), body.get("right")
                )
            ),
            "/api/splits/delete": lambda body, now: store.mutate(
                lambda s: op_delete_split(s, body.get("splitId"))
            ),
            # -- Kurzbefehle ---------------------------------------------------
            "/api/shortcuts/create": lambda body, now: {
                "shortcut": store.mutate(
                    lambda s: op_create_shortcut(s, body.get("name"), body.get("steps"), now)
                )
            },
            "/api/shortcuts/rename": lambda body, now: store.mutate(
                lambda s: op_rename_shortcut(s, body.get("shortcutId"), body.get("name"))
            ),
            "/api/shortcuts/steps": lambda body, now: {
                "shortcut": store.mutate(
                    lambda s: op_update_shortcut_steps(s, body.get("shortcutId"), body.get("steps"))
                )
            },
            "/api/shortcuts/delete": lambda body, now: store.mutate(
                lambda s: op_delete_shortcut(s, body.get("shortcutId"))
            ),
        }

    def _set_fan(self, body: dict):
        """
        Luefter auf Volllast oder zurueck auf die Kurve.

        Der Luefter-Dienst ist ein eigener Prozess unter einem anderen
        Benutzer; die Absprache laeuft ueber eine kleine Datei, die er in
        jedem Durchlauf liest.
        """
        force = self.store.mutate(lambda s: op_set_fan_force(s, body.get("force")))
        path = os.path.join(self.data_dir, "fan-override.json")
        try:
            write_json_atomic(path, {"force": force, "updated": int(time.time())})
        except OSError as err:
            raise ValueError(f"Konnte den Luefter-Schalter nicht schreiben: {err}") from err
        return {"fanForce": force}

    def _smash_player(self, body: dict):
        action = body.get("action", "add")
        store = self.store
        if action == "add":
            return {
                "player": store.mutate(
                    lambda s: op_add_player(s, body.get("name"), body.get("teamId"))
                )
            }
        if action == "rename":
            def rename(state):
                player = find_player(state, body.get("id"))
                if player is None:
                    raise KeyError("Spieler unbekannt")
                player["name"] = clamp_name(body.get("name"), player["name"], limit=40)
            return store.mutate(rename)
        if action == "delete":
            def delete(state):
                players = state["players"]
                remaining = [p for p in players if p["id"] != body.get("id")]
                if len(remaining) == len(players):
                    raise KeyError("Spieler unbekannt")
                state["players"] = remaining
            return store.mutate(delete)
        if action == "score":
            return store.mutate(
                lambda s: op_player_score(
                    s, body.get("id"), body.get("field", ""), body.get("delta", 0)
                )
            )
        if action == "team":
            return store.mutate(
                lambda s: op_set_player_team(s, body.get("id"), body.get("teamId"))
            )
        raise ValueError(f"Unbekannte Aktion '{action}'")

    def _board_entrant(self, body: dict):
        action = body.get("action", "add")
        store = self.store
        board_id = body.get("boardId")
        if action == "add":
            return {
                "entrant": store.mutate(
                    lambda s: op_board_add_entrant(s, board_id, body.get("name"), body.get("teamId"))
                )
            }
        if action == "rename":
            return store.mutate(
                lambda s: op_board_rename_entrant(s, board_id, body.get("id"), body.get("name"))
            )
        if action == "delete":
            return store.mutate(
                lambda s: op_board_delete_entrant(s, board_id, body.get("id"))
            )
        if action == "score":
            return store.mutate(
                lambda s: op_board_entrant_score(
                    s, board_id, body.get("id"), body.get("field", ""), body.get("delta", 0)
                )
            )
        if action == "team":
            return store.mutate(
                lambda s: op_board_set_entrant_team(s, board_id, body.get("id"), body.get("teamId"))
            )
        raise ValueError(f"Unbekannte Aktion '{action}'")

    def _join(self, body: dict):
        """Anmeldung eines Gastes ueber den QR-Code."""
        client = self.address_string()
        if self.auth.is_blocked(client):
            return self._error(HTTPStatus.TOO_MANY_REQUESTS, "Zu viele Versuche - kurz warten")
        try:
            player = self.store.mutate(
                lambda s: op_join(s, self._player_id(), body.get("name"), body.get("teamId"))
            )
        except ValueError as err:
            # Fehlversuche mitzaehlen, damit niemand die Liste vollschreibt.
            time.sleep(self.auth.note_failure(client))
            return self._error(HTTPStatus.BAD_REQUEST, str(err))
        self.auth.note_success(client)

        token = self.auth.make_value_token(player["id"], PLAYER_TTL)
        cookie = f"{PLAYER_COOKIE}={token}; Path=/; Max-Age={PLAYER_TTL}; HttpOnly; SameSite=Lax"
        self._json({"ok": True, "me": player}, extra={"Set-Cookie": cookie})

    def _screen_hello(self, body: dict):
        """Ein Bildschirm meldet sich (beim Oeffnen und danach regelmaessig)."""
        screen_id = clamp_screen_id(body.get("id"))
        if not screen_id:
            return self._error(HTTPStatus.BAD_REQUEST, "Ungueltiger Bildschirmname")
        try:
            screen = self.store.mutate(lambda s: op_touch_screen(s, screen_id, now_ms()))
        except ValueError as err:
            return self._error(HTTPStatus.BAD_REQUEST, str(err))
        return self._json({"ok": True, "screen": screen})


# ---------------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------------
def build_server(host: str, port: int, data_dir: str,
                 public_url: str = "") -> ThreadingHTTPServer:
    os.makedirs(data_dir, exist_ok=True)
    store = Store(os.path.join(data_dir, "state.json"))
    auth = Auth(os.path.join(data_dir, "config.json"))

    class BoundHandler(Handler):
        pass

    BoundHandler.store = store
    BoundHandler.auth = auth
    BoundHandler.data_dir = data_dir
    BoundHandler.public_url = public_url

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
        "--public-url",
        default=os.environ.get("LAN_PUBLIC_URL", ""),
        help="Adresse, die im QR-Code steht (z.B. http://192.168.178.42). "
             "Ohne Angabe wird sie selbst ermittelt.",
    )
    args = parser.parse_args(argv)

    data_dir = os.path.abspath(args.data)
    os.makedirs(data_dir, exist_ok=True)

    httpd = build_server(args.host, args.port, data_dir, args.public_url)

    address = local_ip_address() if args.host in ("0.0.0.0", "") else args.host
    suffix = "" if args.port == 80 else f":{args.port}"
    print(f"Anzeige   : http://{address}{suffix}/")
    print(f"Admin     : http://{address}{suffix}/admin")
    print(f"Mitmachen : http://{address}{suffix}/mitmachen")
    print(f"Uebersicht: http://{address}{suffix}/seiten")
    print(f"Daten     : {data_dir}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nBeendet.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
