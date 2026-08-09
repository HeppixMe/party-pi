"""
Tests fuer die Seiten, die Spieler-Anmeldung und die Bildschirmsteuerung.

Alles ueber echte HTTP-Aufrufe gegen einen laufenden Server - damit ist auch
geprueft, dass Cookies und Routen wirklich zusammenpassen. Es gibt kein
Admin-Passwort: /admin und alle schreibenden Endpunkte sind offen, das ist
mit Absicht so einfach wie moeglich gehalten.
"""

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import leaderboard as lb  # noqa: E402


class ServerTestCase(unittest.TestCase):
    """Gemeinsamer Unterbau: ein Server pro Testklasse, sauberer Zustand je Test."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.httpd = lb.build_server("127.0.0.1", 0, cls.dir)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        self.cookies = {}
        self.httpd.store.mutate(lambda s: s.update(lb.default_state()))

    # -- kleine HTTP-Hilfe ---------------------------------------------------
    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        payload = json.dumps(body or {}) if method == "POST" else None
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        set_cookie = response.getheader("Set-Cookie")
        content_type = response.getheader("Content-Type", "")
        connection.close()

        if set_cookie:
            name, _, rest = set_cookie.partition("=")
            self.cookies[name] = rest.split(";", 1)[0]
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            parsed = raw
        return response.status, parsed, content_type


class PublicPagesTest(ServerTestCase):
    PUBLIC = [
        "/", "/seiten", "/qr", "/mitmachen", "/teams", "/system", "/info", "/vibe",
        "/admin", "/kurzbefehle", "/fernbedienung", "/mitmachen/a", "/mitmachen/b",
        "/qr/a", "/qr/b",
    ]

    def test_every_public_page_is_reachable(self):
        for path in self.PUBLIC:
            status, body, content_type = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn("text/html", content_type, path)
            self.assertIn(b"</html>", body, path)

    def test_trailing_slash_works_too(self):
        # Wer die Adresse abtippt, haengt gern einen Schraegstrich an.
        for path in ("/qr/", "/teams/", "/mitmachen/"):
            status, _body, _type = self.request("GET", path)
            self.assertEqual(status, 200, path)

    def test_qr_and_mitmachen_reject_an_unknown_team(self):
        for path in ("/mitmachen/c", "/qr/c"):
            status, _body, _type = self.request("GET", path)
            self.assertEqual(status, 404, path)

    def test_screen_page_needs_a_usable_name(self):
        self.assertEqual(self.request("GET", "/screen/tv")[0], 200)
        self.assertEqual(self.request("GET", "/screen/wohnzimmer-2")[0], 200)
        # Bleibt nach dem Saeubern nichts uebrig, gibt es die Seite nicht.
        self.assertEqual(self.request("GET", "/screen/...")[0], 404)
        self.assertEqual(self.request("GET", "/screen/")[0], 404)

    def test_screen_name_can_never_escape_into_the_filesystem(self):
        # Der Name kommt aus der URL. Ausgeliefert wird immer dieselbe Datei,
        # und der gesaeuberte Name enthaelt weder Schraegstrich noch Punkt.
        self.assertEqual(self.request("GET", "/screen/..%2F..%2Fetc%2Fpasswd")[0], 200)
        cleaned = lb.clamp_screen_id("../../etc/passwd")
        self.assertNotIn("/", cleaned)
        self.assertNotIn(".", cleaned)

    def test_admin_is_open_without_any_login(self):
        # Kein Passwort - die Steuerung ist genauso offen wie jede andere Seite.
        status, body, content_type = self.request("GET", "/admin")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)

    def test_unknown_path_is_a_clean_404(self):
        status, body, _type = self.request("GET", "/gibtsnicht")
        self.assertEqual(status, 404)
        self.assertFalse(body["ok"])


class QrEndpointTest(ServerTestCase):
    def test_returns_an_svg(self):
        status, body, content_type = self.request("GET", "/api/qr.svg")
        self.assertEqual(status, 200)
        self.assertIn("image/svg+xml", content_type)
        self.assertTrue(body.startswith(b"<svg "))
        self.assertIn(b"</svg>", body)

    def test_code_contains_the_join_address(self):
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertTrue(payload["joinUrl"].endswith("/mitmachen"))
        # Dieselbe Adresse muss sich aus dem SVG wieder herstellen lassen:
        # der Encoder wird an anderer Stelle gegen einen echten Decoder
        # geprueft, hier reicht der Abgleich der Modulanzahl.
        import qr

        expected = qr.make_matrix(payload["joinUrl"])
        _status, svg, _type = self.request("GET", "/api/qr.svg")
        span = expected.size + 8
        self.assertIn(f'viewBox="0 0 {span} {span}"'.encode(), svg)

    def test_host_header_decides_the_address(self):
        # Wichtig fuer den Kiosk: kommt die Anfrage ueber localhost, darf das
        # nicht im QR-Code landen - da kaeme kein Handy hin.
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", "/api/state", headers={"Host": "192.168.178.42"})
        payload = json.loads(connection.getresponse().read().decode())
        connection.close()
        self.assertEqual(payload["joinUrl"], "http://192.168.178.42/mitmachen")

    def test_localhost_falls_back_to_the_lan_address(self):
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertNotIn("localhost", payload["joinUrl"])
        self.assertNotIn("127.0.0.1", payload["joinUrl"])

    def test_team_qr_code_encodes_the_team_specific_address(self):
        # /qr/a soll auf /mitmachen/a fuehren, nicht auf die allgemeine
        # Anmeldung - derselbe Modulanzahl-Abgleich wie beim Grund-Code.
        _status, payload, _type = self.request("GET", "/api/state")
        team_url = payload["joinUrl"] + "/a"
        self.assertTrue(team_url.endswith("/mitmachen/a"))

        import qr

        expected = qr.make_matrix(team_url)
        _status, svg, _type = self.request("GET", "/api/qr.svg?team=a")
        span = expected.size + 8
        self.assertIn(f'viewBox="0 0 {span} {span}"'.encode(), svg)

    def test_qr_svg_ignores_an_unknown_team_query(self):
        # Ein unbekanntes Team im Query-String faellt auf die allgemeine
        # Anmeldung zurueck statt einen Fehler zu werfen.
        status, body, _type = self.request("GET", "/api/qr.svg?team=c")
        self.assertEqual(status, 200)
        self.assertTrue(body.startswith(b"<svg "))

    def test_team_join_views_include_qr_variants(self):
        _status, payload, _type = self.request("GET", "/api/state")
        paths = [v["path"] for v in payload["views"]]
        self.assertIn("/qr/a", paths)
        self.assertIn("/qr/b", paths)


class JoinTest(ServerTestCase):
    def test_guest_can_join_without_password(self):
        status, body, _type = self.request("POST", "/api/join", {"name": "Mia", "teamId": "a"})
        self.assertEqual(status, 200)
        self.assertEqual(body["me"]["name"], "Mia")
        self.assertEqual(body["me"]["teamId"], "a")
        self.assertIn(lb.PLAYER_COOKIE, self.cookies)

    def test_second_visit_changes_instead_of_duplicating(self):
        self.request("POST", "/api/join", {"name": "Mia", "teamId": "a"})
        self.request("POST", "/api/join", {"name": "Mia B.", "teamId": "b"})

        _status, payload, _type = self.request("GET", "/api/state")
        players = payload["state"]["players"]
        self.assertEqual(len(players), 1)
        self.assertEqual(players[0]["name"], "Mia B.")
        self.assertEqual(players[0]["teamId"], "b")

    def test_without_cookie_a_second_player_is_created(self):
        self.request("POST", "/api/join", {"name": "Mia", "teamId": "a"})
        self.cookies.clear()  # anderes Handy
        self.request("POST", "/api/join", {"name": "Tom", "teamId": "b"})

        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(len(payload["state"]["players"]), 2)

    def test_state_tells_the_guest_who_they_are(self):
        self.request("POST", "/api/join", {"name": "Mia", "teamId": "a"})
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["me"]["name"], "Mia")

    def test_name_and_team_are_checked(self):
        status, body, _type = self.request("POST", "/api/join", {"name": "  ", "teamId": "a"})
        self.assertEqual(status, 400)
        self.assertIn("Namen", body["error"])

        status, body, _type = self.request("POST", "/api/join", {"name": "Mia", "teamId": "x"})
        self.assertEqual(status, 400)
        self.assertIn("Team", body["error"])

    def test_joined_player_shows_up_for_smash(self):
        self.request("POST", "/api/join", {"name": "Mia", "teamId": "a"})
        _status, payload, _type = self.request("GET", "/api/state")
        player = payload["state"]["players"][0]
        self.assertEqual((player["wins"], player["losses"]), (0, 0))


class ScreenTest(ServerTestCase):
    def test_screen_registers_itself(self):
        status, body, _type = self.request("POST", "/api/screens/hello", {"id": "tv"})
        self.assertEqual(status, 200)
        self.assertEqual(body["screen"]["id"], "tv")
        self.assertEqual(body["screen"]["view"], "/")  # Voreinstellung

    def test_saying_hello_twice_does_not_duplicate(self):
        self.request("POST", "/api/screens/hello", {"id": "tv"})
        self.request("POST", "/api/screens/hello", {"id": "tv"})
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(len(payload["state"]["screens"]), 1)

    def test_name_is_cleaned_up(self):
        self.request("POST", "/api/screens/hello", {"id": "Wohn Zimmer/../x"})
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["state"]["screens"][0]["id"], "wohnzimmerx")

    def test_empty_name_is_rejected(self):
        status, _body, _type = self.request("POST", "/api/screens/hello", {"id": "///"})
        self.assertEqual(status, 400)

    def test_admin_assigns_a_view(self):
        self.request("POST", "/api/screens/hello", {"id": "tv"})
        status, body, _type = self.request(
            "POST", "/api/screens/assign", {"screenId": "tv", "view": "/qr"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["screen"]["view"], "/qr")

    def test_unknown_view_is_rejected(self):
        self.request("POST", "/api/screens/hello", {"id": "tv"})
        status, _body, _type = self.request(
            "POST", "/api/screens/assign", {"screenId": "tv", "view": "/etc/passwd"}
        )
        self.assertEqual(status, 400)

    def test_assigning_an_unknown_screen_is_a_404(self):
        status, _body, _type = self.request(
            "POST", "/api/screens/assign", {"screenId": "gibtsnicht", "view": "/qr"}
        )
        self.assertEqual(status, 404)

    def test_admin_can_rename_and_remove(self):
        self.request("POST", "/api/screens/hello", {"id": "tv"})
        self.request("POST", "/api/screens/rename", {"screenId": "tv", "name": "Wohnzimmer"})
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["state"]["screens"][0]["name"], "Wohnzimmer")

        self.request("POST", "/api/screens/delete", {"screenId": "tv"})
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["state"]["screens"], [])

    def test_view_list_is_offered_to_the_admin(self):
        _status, payload, _type = self.request("GET", "/api/state")
        paths = [view["path"] for view in payload["views"]]
        self.assertIn("/", paths)
        self.assertIn("/qr", paths)
        self.assertTrue(all(view["label"] for view in payload["views"]))

    def test_team_join_pages_are_offered_and_assignable(self):
        _status, payload, _type = self.request("GET", "/api/state")
        by_path = {view["path"]: view["label"] for view in payload["views"]}
        self.assertIn("/mitmachen/a", by_path)
        self.assertIn("/mitmachen/b", by_path)
        self.assertIn("Team 1", by_path["/mitmachen/a"])

        self.request("POST", "/api/screens/hello", {"id": "tv"})
        status, body, _type = self.request(
            "POST", "/api/screens/assign", {"screenId": "tv", "view": "/mitmachen/a"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["screen"]["view"], "/mitmachen/a")

    def test_team_join_view_label_follows_a_renamed_team(self):
        self.request("POST", "/api/sos/teams", {"names": {"a": "Die Adler"}})
        _status, payload, _type = self.request("GET", "/api/state")
        by_path = {view["path"]: view["label"] for view in payload["views"]}
        self.assertEqual(by_path["/mitmachen/a"], "Anmeldung Die Adler")


class BoardsPageTest(ServerTestCase):
    """Eigene Leaderboards ueber die HTTP-Schnittstelle - Routen und Sichtbarkeit."""

    def create(self, name="Schach", kind="score", mode="solo"):
        status, body, _type = self.request(
            "POST", "/api/boards/create", {"name": name, "kind": kind, "mode": mode}
        )
        self.assertEqual(status, 200, body)
        return body["board"]

    def test_board_page_is_public_and_reachable_even_without_a_valid_id(self):
        board = self.create()
        for path in (f"/board/{board['id']}", "/board/gibtsnicht"):
            status, body, content_type = self.request("GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn("text/html", content_type)
            self.assertIn(b"</html>", body)

    def test_created_board_shows_up_in_state_and_view_list(self):
        board = self.create("Mario Kart", "race", "teams")
        _status, payload, _type = self.request("GET", "/api/state")
        names = [b["name"] for b in payload["state"]["boards"]]
        self.assertIn("Mario Kart", names)
        paths = [v["path"] for v in payload["views"]]
        self.assertIn(f"/board/{board['id']}", paths)

    def test_board_can_be_assigned_to_a_screen(self):
        board = self.create()
        self.request("POST", "/api/screens/hello", {"id": "tv"})
        status, body, _type = self.request(
            "POST", "/api/screens/assign", {"screenId": "tv", "view": f"/board/{board['id']}"}
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["screen"]["view"], f"/board/{board['id']}")

    def test_rename_and_delete(self):
        board = self.create("Alt")
        self.request("POST", "/api/boards/rename", {"boardId": board["id"], "name": "Neu"})
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["state"]["boards"][0]["name"], "Neu")

        status, _body, _type = self.request("POST", "/api/boards/delete", {"boardId": board["id"]})
        self.assertEqual(status, 200)
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["state"]["boards"], [])

    def test_race_board_full_round(self):
        board = self.create("Rennen", "race", "teams")
        status, body, _type = self.request(
            "POST", "/api/boards/level", {"boardId": board["id"], "name": "Strecke 1"}
        )
        self.assertEqual(status, 200)
        level_id = body["level"]["id"]

        self.request(
            "POST", "/api/boards/timer",
            {"boardId": board["id"], "levelId": level_id, "team": "a", "action": "finish"},
        )
        self.request(
            "POST", "/api/boards/timer/adjust",
            {"boardId": board["id"], "levelId": level_id, "team": "a", "valueMs": 12_000},
        )
        _status, payload, _type = self.request("GET", "/api/state")
        level = payload["state"]["boards"][0]["levels"][0]
        self.assertTrue(level["timers"]["a"]["finished"])
        self.assertEqual(level["timers"]["a"]["elapsedMs"], 12_000)

    def test_score_board_full_round(self):
        board = self.create("Schach", "score", "teams")
        status, body, _type = self.request(
            "POST", "/api/boards/entrant",
            {"boardId": board["id"], "action": "add", "name": "Mia", "teamId": "a"},
        )
        self.assertEqual(status, 200)
        entrant_id = body["entrant"]["id"]

        self.request(
            "POST", "/api/boards/entrant",
            {"boardId": board["id"], "action": "score", "id": entrant_id, "field": "wins", "delta": 2},
        )
        _status, payload, _type = self.request("GET", "/api/state")
        entrant = payload["state"]["boards"][0]["entrants"][0]
        self.assertEqual(entrant["wins"], 2)
        self.assertEqual(entrant["teamId"], "a")

    def test_reset_only_touches_the_named_board(self):
        keep = self.create("Bleibt", "score", "solo")
        gone = self.create("Wird geleert", "score", "solo")
        self.request("POST", "/api/boards/entrant", {"boardId": keep["id"], "action": "add", "name": "Mia"})
        self.request("POST", "/api/boards/entrant", {"boardId": gone["id"], "action": "add", "name": "Tom"})
        entrant_tom = self.request("GET", "/api/state")[1]["state"]["boards"]
        tom_id = next(b for b in entrant_tom if b["id"] == gone["id"])["entrants"][0]["id"]
        self.request(
            "POST", "/api/boards/entrant",
            {"boardId": gone["id"], "action": "score", "id": tom_id, "field": "wins", "delta": 3},
        )

        self.request("POST", "/api/boards/reset", {"boardId": gone["id"]})
        _status, payload, _type = self.request("GET", "/api/state")
        by_id = {b["id"]: b for b in payload["state"]["boards"]}
        self.assertEqual(by_id[gone["id"]]["entrants"][0]["wins"], 0)
        self.assertEqual(len(by_id[keep["id"]]["entrants"]), 1)

    def test_board_limit_gives_a_clean_error(self):
        for index in range(lb.MAX_BOARDS):
            self.create(f"Spiel {index}")
        status, body, _type = self.request(
            "POST", "/api/boards/create", {"name": "Zu viel", "kind": "score", "mode": "solo"}
        )
        self.assertEqual(status, 400)
        self.assertIn("schon", body["error"])


class ScreenSplitTest(ServerTestCase):
    """Geteilte Bildschirme (zwei Ansichten nebeneinander) - direkt am
    Bildschirm gespeichert (screen.split), keine eigene Sammlung/Seite."""

    def test_split_screen_and_read_it_back(self):
        self.request("POST", "/api/screens/hello", {"id": "tv"})
        status, body, _type = self.request(
            "POST", "/api/screens/split", {"screenId": "tv", "left": "/", "right": "/qr"}
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(body["screen"]["split"], {"left": "/", "right": "/qr"})

        _status, payload, _type = self.request("GET", "/api/state")
        screen = payload["state"]["screens"][0]
        self.assertEqual(screen["split"], {"left": "/", "right": "/qr"})

    def test_clearing_the_split_falls_back_to_the_normal_view(self):
        self.request("POST", "/api/screens/hello", {"id": "tv"})
        self.request("POST", "/api/screens/split", {"screenId": "tv", "left": "/", "right": "/qr"})
        status, body, _type = self.request("POST", "/api/screens/split/clear", {"screenId": "tv"})
        self.assertEqual(status, 200, body)
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertIsNone(payload["state"]["screens"][0]["split"])

    def test_assigning_a_normal_view_exits_split_mode(self):
        self.request("POST", "/api/screens/hello", {"id": "tv"})
        self.request("POST", "/api/screens/split", {"screenId": "tv", "left": "/", "right": "/qr"})
        self.request("POST", "/api/screens/assign", {"screenId": "tv", "view": "/teams"})
        _status, payload, _type = self.request("GET", "/api/state")
        screen = payload["state"]["screens"][0]
        self.assertIsNone(screen["split"])
        self.assertEqual(screen["view"], "/teams")

    def test_a_side_can_show_an_own_board(self):
        status, body, _type = self.request(
            "POST", "/api/boards/create", {"name": "Schach", "kind": "score", "mode": "solo"}
        )
        board = body["board"]
        self.request("POST", "/api/screens/hello", {"id": "tv"})
        status, body, _type = self.request(
            "POST", "/api/screens/split", {"screenId": "tv", "left": f"/board/{board['id']}", "right": "/"}
        )
        self.assertEqual(status, 200, body)

    def test_a_side_rejects_an_unknown_board(self):
        self.request("POST", "/api/screens/hello", {"id": "tv"})
        status, body, _type = self.request(
            "POST", "/api/screens/split", {"screenId": "tv", "left": "/board/gibtsnicht", "right": "/"}
        )
        self.assertEqual(status, 400, body)

    def test_splitting_an_unknown_screen_is_a_404(self):
        status, _body, _type = self.request(
            "POST", "/api/screens/split", {"screenId": "gibtsnicht", "left": "/", "right": "/qr"}
        )
        self.assertEqual(status, 404)

    def test_deleting_a_board_frees_split_sides_showing_it(self):
        status, body, _type = self.request(
            "POST", "/api/boards/create", {"name": "Schach", "kind": "score", "mode": "solo"}
        )
        board = body["board"]
        self.request("POST", "/api/screens/hello", {"id": "tv"})
        self.request(
            "POST", "/api/screens/split", {"screenId": "tv", "left": f"/board/{board['id']}", "right": "/"}
        )
        self.request("POST", "/api/boards/delete", {"boardId": board["id"]})
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["state"]["screens"][0]["split"]["left"], "/")


class ShortcutsPageTest(ServerTestCase):
    """Kurzbefehle ueber die HTTP-Schnittstelle: Baukasten-Seite, CRUD, /run/<id>."""

    def create(self, name="Kurzbefehl", steps=None):
        status, body, _type = self.request(
            "POST", "/api/shortcuts/create", {"name": name, "steps": steps or []}
        )
        self.assertEqual(status, 200, body)
        return body["shortcut"]

    def test_builder_page_is_reachable(self):
        status, body, content_type = self.request("GET", "/kurzbefehle")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn(b"</html>", body)

    def test_create_rename_update_delete(self):
        shortcut = self.create("Alt", [{"type": "set_fx", "fx": "plain"}])
        self.request("POST", "/api/shortcuts/rename", {"shortcutId": shortcut["id"], "name": "Neu"})
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["state"]["shortcuts"][0]["name"], "Neu")

        new_steps = [{"type": "reset", "scope": "all"}]
        self.request("POST", "/api/shortcuts/steps", {"shortcutId": shortcut["id"], "steps": new_steps})
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["state"]["shortcuts"][0]["steps"], new_steps)

        status, _body, _type = self.request("POST", "/api/shortcuts/delete", {"shortcutId": shortcut["id"]})
        self.assertEqual(status, 200)
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["state"]["shortcuts"], [])

    def test_run_url_executes_and_redirects_to_admin(self):
        self.request("POST", "/api/screens/hello", {"id": "tv"})
        shortcut = self.create("Start", [{"type": "assign_screen", "screenId": "tv", "view": "/qr"}])

        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", f"/run/{shortcut['id']}")
        response = connection.getresponse()
        response.read()
        location = response.getheader("Location")
        connection.close()

        self.assertEqual(response.status, 302)
        self.assertEqual(location, "/admin")

        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["state"]["screens"][0]["view"], "/qr")

    def test_unknown_run_id_still_redirects_without_error(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", "/run/gibtsnicht")
        response = connection.getresponse()
        response.read()
        location = response.getheader("Location")
        connection.close()
        self.assertEqual(response.status, 302)
        self.assertEqual(location, "/admin")

    def test_run_skips_a_broken_step_but_runs_the_rest(self):
        shortcut = self.create("Teilweise kaputt", [
            {"type": "assign_screen", "screenId": "gibtsnicht", "view": "/qr"},
            {"type": "set_info", "text": "trotzdem da"},
        ])
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", f"/run/{shortcut['id']}")
        connection.getresponse().read()
        connection.close()

        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["state"]["info"]["text"], "trotzdem da")

    def test_run_creates_a_board_end_to_end(self):
        shortcut = self.create("Board anlegen", [
            {"type": "create_board", "name": "Mario Kart", "kind": "score", "mode": "solo"},
        ])
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", f"/run/{shortcut['id']}")
        connection.getresponse().read()
        connection.close()

        _status, payload, _type = self.request("GET", "/api/state")
        names = [b["name"] for b in payload["state"]["boards"]]
        self.assertIn("Mario Kart", names)

    def test_unknown_step_type_is_rejected_on_save(self):
        shortcut = self.create("Halb kaputt", [
            {"type": "set_fx", "fx": "plain"},
            {"type": "flieg_zum_mond"},
        ])
        self.assertEqual(len(shortcut["steps"]), 1)

    def test_shortcut_limit_gives_a_clean_error(self):
        for index in range(lb.MAX_SHORTCUTS):
            self.create(f"Kurzbefehl {index}")
        status, body, _type = self.request(
            "POST", "/api/shortcuts/create", {"name": "Zu viel", "steps": []}
        )
        self.assertEqual(status, 400)
        self.assertIn("schon", body["error"])

    def test_reorder_puts_shortcuts_in_the_given_order(self):
        first = self.create("Erster")
        second = self.create("Zweiter")
        third = self.create("Dritter")
        status, _body, _type = self.request(
            "POST", "/api/shortcuts/reorder",
            {"order": [third["id"], first["id"], second["id"]]},
        )
        self.assertEqual(status, 200)
        _status, payload, _type = self.request("GET", "/api/state")
        ids = [s["id"] for s in payload["state"]["shortcuts"]]
        self.assertEqual(ids, [third["id"], first["id"], second["id"]])

    def test_reorder_keeps_unmentioned_shortcuts_at_the_end(self):
        first = self.create("Erster")
        second = self.create("Zweiter")
        self.request("POST", "/api/shortcuts/reorder", {"order": [first["id"]]})
        _status, payload, _type = self.request("GET", "/api/state")
        ids = [s["id"] for s in payload["state"]["shortcuts"]]
        self.assertEqual(ids, [first["id"], second["id"]])

    def test_reorder_ignores_unknown_ids(self):
        first = self.create("Erster")
        status, _body, _type = self.request(
            "POST", "/api/shortcuts/reorder", {"order": ["gibtsnicht", first["id"]]}
        )
        self.assertEqual(status, 200)
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["state"]["shortcuts"][0]["id"], first["id"])

    def test_run_by_name_works_just_like_by_id(self):
        self.create("Party-Start", [{"type": "set_fx", "fx": "plain"}])
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", "/run/Party-Start")
        response = connection.getresponse()
        response.read()
        connection.close()
        self.assertEqual(response.status, 302)
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["state"]["display"]["fx"], "plain")

    def test_run_by_name_url_encodes_spaces(self):
        self.create("Party Start", [{"type": "set_fx", "fx": "plain"}])
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", "/run/Party%20Start")
        response = connection.getresponse()
        response.read()
        connection.close()
        self.assertEqual(response.status, 302)
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["state"]["display"]["fx"], "plain")

    def test_duplicate_names_run_the_first_match(self):
        first = self.create("Zwilling", [{"type": "set_fx", "fx": "plain"}])
        self.create("Zwilling", [{"type": "set_fx", "fx": "full"}])
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", "/run/Zwilling")
        connection.getresponse().read()
        connection.close()
        _status, payload, _type = self.request("GET", "/api/state")
        # Der zuerst angelegte steht in der Liste vorn (neuester zuerst
        # heisst: der zweite "Zwilling" landet vor dem ersten) - wichtig ist
        # nur, dass irgendein eindeutiges Ergebnis herauskommt, nicht welches.
        self.assertEqual(payload["state"]["display"]["fx"], "full")
        self.assertEqual(payload["state"]["shortcuts"][1]["id"], first["id"])

    def test_level_name_prompt_shows_a_form_instead_of_running(self):
        shortcut = self.create("Level mit Frage", [
            {"type": "start_level", "name": "", "autostart": False, "promptName": True},
        ])
        status, body, content_type = self.request("GET", f"/run/{shortcut['id']}")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn(b"<form", body)
        self.assertIn(b"Level mit Frage", body)
        # Ohne Namen ausgefuehrt: kein Level angelegt, es kam ja nur das Formular.
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["state"]["sos"]["levels"], [])

    def test_level_name_prompt_runs_with_the_submitted_name(self):
        shortcut = self.create("Level mit Frage", [
            {"type": "start_level", "name": "", "autostart": False, "promptName": True},
        ])
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", f"/run/{shortcut['id']}?name=Boss-Kampf")
        response = connection.getresponse()
        response.read()
        connection.close()
        self.assertEqual(response.status, 302)

        _status, payload, _type = self.request("GET", "/api/state")
        levels = payload["state"]["sos"]["levels"]
        self.assertEqual(len(levels), 1)
        self.assertEqual(levels[0]["name"], "Boss-Kampf")

    def test_level_name_prompt_without_promptname_runs_immediately(self):
        shortcut = self.create("Normal", [
            {"type": "start_level", "name": "Fest", "autostart": False, "promptName": False},
        ])
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("GET", f"/run/{shortcut['id']}")
        response = connection.getresponse()
        response.read()
        connection.close()
        self.assertEqual(response.status, 302)
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["state"]["sos"]["levels"][0]["name"], "Fest")


class SwitchesTest(ServerTestCase):
    def test_effects_can_be_switched(self):
        self.request("POST", "/api/display", {"fx": "plain"})
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(payload["state"]["display"]["fx"], "plain")

        status, _body, _type = self.request("POST", "/api/display", {"fx": "disco"})
        self.assertEqual(status, 400)

    def test_fan_switch_writes_the_file_for_the_other_service(self):
        self.request("POST", "/api/fan", {"force": True})

        path = os.path.join(self.dir, "fan-override.json")
        with open(path, "r", encoding="utf-8") as fh:
            self.assertTrue(json.load(fh)["force"])

        self.request("POST", "/api/fan", {"force": False})
        with open(path, "r", encoding="utf-8") as fh:
            self.assertFalse(json.load(fh)["force"])

    def test_info_text_is_stored_and_shortened(self):
        self.request("POST", "/api/info", {"text": "WLAN: geheim\nAblauf: 20 Uhr"})
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertIn("WLAN: geheim", payload["state"]["info"]["text"])

        self.request("POST", "/api/info", {"text": "x" * 9000})
        _status, payload, _type = self.request("GET", "/api/state")
        self.assertEqual(len(payload["state"]["info"]["text"]), 4000)

    def test_system_endpoint_reports_the_basics(self):
        _status, payload, _type = self.request("GET", "/api/system")
        self.assertIn("fan", payload)
        self.assertIn("address", payload)
        self.assertIsInstance(payload["connections"], int)


if __name__ == "__main__":
    unittest.main()
