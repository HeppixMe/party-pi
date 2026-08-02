"""Tests fuer Zustand, Wertung und HTTP-Schnittstelle des Leaderboards."""

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


def level_with(a_ms, b_ms, a_done=True, b_done=True, name="Level"):
    level = lb.new_level(name)
    level["timers"]["a"] = {"elapsedMs": a_ms, "runningSince": None, "finished": a_done}
    level["timers"]["b"] = {"elapsedMs": b_ms, "runningSince": None, "finished": b_done}
    return level


class TimerTest(unittest.TestCase):
    def test_stopped_timer_shows_stored_value(self):
        timer = {"elapsedMs": 5000, "runningSince": None, "finished": False}
        self.assertEqual(lb.timer_value_ms(timer, 999_999), 5000)

    def test_running_timer_adds_elapsed_wall_clock(self):
        timer = {"elapsedMs": 5000, "runningSince": 1000, "finished": False}
        self.assertEqual(lb.timer_value_ms(timer, 4000), 8000)

    def test_freeze_keeps_exact_value(self):
        timer = {"elapsedMs": 1000, "runningSince": 10_000, "finished": False}
        lb.freeze_timer(timer, 13_500)
        self.assertEqual(timer["elapsedMs"], 4500)
        self.assertIsNone(timer["runningSince"])
        # Nach dem Einfrieren bewegt sich der Wert nicht mehr.
        self.assertEqual(lb.timer_value_ms(timer, 99_999), 4500)

    def test_start_stop_start_accumulates(self):
        state = lb.default_state()
        lb.op_start_level(state, "L1", autostart=True, now=0)
        level_id = state["sos"]["levels"][0]["id"]
        lb.op_timer(state, level_id, "a", "stop", now=3000)
        lb.op_timer(state, level_id, "a", "start", now=10_000)
        timer = state["sos"]["levels"][0]["timers"]["a"]
        self.assertEqual(lb.timer_value_ms(timer, 12_000), 5000)

    def test_adjust_running_timer_continues_from_new_value(self):
        state = lb.default_state()
        lb.op_start_level(state, "L1", autostart=True, now=0)
        level_id = state["sos"]["levels"][0]["id"]
        lb.op_adjust_timer(state, level_id, "a", 60_000, now=5000)
        timer = state["sos"]["levels"][0]["timers"]["a"]
        self.assertEqual(lb.timer_value_ms(timer, 7000), 62_000)

    def test_reset_clears_timer(self):
        state = lb.default_state()
        lb.op_start_level(state, "L1", autostart=True, now=0)
        level_id = state["sos"]["levels"][0]["id"]
        lb.op_timer(state, level_id, "a", "reset", now=9000)
        self.assertEqual(lb.timer_value_ms(state["sos"]["levels"][0]["timers"]["a"], 99_999), 0)

    def test_unknown_action_is_rejected(self):
        state = lb.default_state()
        lb.op_start_level(state, "L1", autostart=True, now=0)
        level_id = state["sos"]["levels"][0]["id"]
        with self.assertRaises(ValueError):
            lb.op_timer(state, level_id, "a", "fliegen", now=0)


class LevelFlowTest(unittest.TestCase):
    def test_new_level_freezes_everything_before(self):
        state = lb.default_state()
        lb.op_start_level(state, "Level 1", autostart=True, now=0)
        lb.op_start_level(state, "Level 2", autostart=True, now=30_000)

        first, second = state["sos"]["levels"]
        # Erste Zeile steht fest bei 30 s, auch viel spaeter noch.
        for team in ("a", "b"):
            self.assertIsNone(first["timers"][team]["runningSince"])
            self.assertEqual(lb.timer_value_ms(first["timers"][team], 999_999), 30_000)
            # Zweite Zeile faengt bei 0 an und laeuft.
            self.assertEqual(lb.timer_value_ms(second["timers"][team], 30_000), 0)
            self.assertEqual(lb.timer_value_ms(second["timers"][team], 45_000), 15_000)

        self.assertFalse(first["active"])
        self.assertTrue(second["active"])

    def test_finish_stops_one_team_while_the_other_runs(self):
        state = lb.default_state()
        lb.op_start_level(state, "Level 1", autostart=True, now=0)
        level_id = state["sos"]["levels"][0]["id"]
        lb.op_timer(state, level_id, "a", "finish", now=20_000)

        timers = state["sos"]["levels"][0]["timers"]
        self.assertEqual(lb.timer_value_ms(timers["a"], 60_000), 20_000)
        self.assertTrue(timers["a"]["finished"])
        self.assertEqual(lb.timer_value_ms(timers["b"], 60_000), 60_000)
        self.assertFalse(timers["b"]["finished"])

    def test_level_without_autostart_stays_at_zero(self):
        state = lb.default_state()
        lb.op_start_level(state, "Level 1", autostart=False, now=0)
        timers = state["sos"]["levels"][0]["timers"]
        self.assertEqual(lb.timer_value_ms(timers["a"], 60_000), 0)

    def test_default_level_name_counts_up(self):
        state = lb.default_state()
        lb.op_start_level(state, None, autostart=True, now=0)
        lb.op_start_level(state, "", autostart=True, now=1)
        names = [level["name"] for level in state["sos"]["levels"]]
        self.assertEqual(names, ["Level 1", "Level 2"])

    def test_delete_level_marks_last_one_active(self):
        state = lb.default_state()
        lb.op_start_level(state, "Level 1", autostart=True, now=0)
        lb.op_start_level(state, "Level 2", autostart=True, now=1000)
        lb.op_delete_level(state, state["sos"]["levels"][1]["id"])
        self.assertEqual(len(state["sos"]["levels"]), 1)
        self.assertTrue(state["sos"]["levels"][0]["active"])

    def test_delete_unknown_level_raises(self):
        state = lb.default_state()
        with self.assertRaises(KeyError):
            lb.op_delete_level(state, "gibtsnicht")


class StandingsTest(unittest.TestCase):
    def test_totals_include_running_timers(self):
        state = lb.default_state()
        lb.op_start_level(state, "Level 1", autostart=True, now=0)
        standings = lb.compute_standings(state, now=12_000)
        self.assertEqual(standings["totals"], {"a": 12_000, "b": 12_000})

    def test_level_counts_only_when_both_teams_finished(self):
        state = lb.default_state()
        state["sos"]["levels"] = [level_with(10_000, 20_000, a_done=True, b_done=False)]
        standings = lb.compute_standings(state, now=0)
        self.assertEqual(standings["levelWins"], {"a": 0, "b": 0})

        state["sos"]["levels"][0]["timers"]["b"]["finished"] = True
        standings = lb.compute_standings(state, now=0)
        self.assertEqual(standings["levelWins"], {"a": 1, "b": 0})

    def test_more_level_wins_beats_lower_total_time(self):
        state = lb.default_state()
        state["sos"]["levels"] = [
            level_with(10_000, 11_000),  # a gewinnt knapp
            level_with(10_000, 11_000),  # a gewinnt knapp
            level_with(90_000, 10_000),  # b gewinnt deutlich
        ]
        standings = lb.compute_standings(state, now=0)
        self.assertEqual(standings["levelWins"], {"a": 2, "b": 1})
        self.assertGreater(standings["totals"]["a"], standings["totals"]["b"])
        self.assertEqual(standings["leader"], "a")

    def test_total_time_decides_when_level_wins_are_equal(self):
        state = lb.default_state()
        state["sos"]["levels"] = [level_with(10_000, 20_000), level_with(20_000, 5_000)]
        standings = lb.compute_standings(state, now=0)
        self.assertEqual(standings["levelWins"], {"a": 1, "b": 1})
        self.assertEqual(standings["leader"], "b")

    def test_dead_heat_has_no_leader(self):
        state = lb.default_state()
        state["sos"]["levels"] = [level_with(10_000, 10_000)]
        standings = lb.compute_standings(state, now=0)
        self.assertIsNone(standings["leader"])
        self.assertEqual(standings["levelWins"], {"a": 0, "b": 0})

    def test_empty_board_has_no_leader(self):
        self.assertIsNone(lb.compute_standings(lb.default_state(), now=0)["leader"])


class SmashTest(unittest.TestCase):
    def make(self, *entries):
        state = lb.default_state()
        for name, wins, losses in entries:
            player = lb.op_add_player(state, name)
            player["wins"], player["losses"] = wins, losses
        return state

    def test_sorted_by_wins_then_rate_then_losses(self):
        state = self.make(("Ana", 3, 5), ("Ben", 5, 0), ("Cem", 3, 1))
        order = [p["name"] for p in lb.sort_players(state["smash"]["players"])]
        self.assertEqual(order, ["Ben", "Cem", "Ana"])

    def test_match_updates_both_players(self):
        state = self.make(("Ana", 0, 0), ("Ben", 0, 0))
        ana, ben = state["smash"]["players"]
        lb.op_match(state, ana["id"], ben["id"])
        self.assertEqual((ana["wins"], ana["losses"]), (1, 0))
        self.assertEqual((ben["wins"], ben["losses"]), (0, 1))

    def test_match_against_yourself_is_rejected(self):
        state = self.make(("Ana", 0, 0))
        ana = state["smash"]["players"][0]
        with self.assertRaises(ValueError):
            lb.op_match(state, ana["id"], ana["id"])

    def test_score_never_goes_below_zero(self):
        state = self.make(("Ana", 1, 0))
        ana = state["smash"]["players"][0]
        lb.op_player_score(state, ana["id"], "wins", -5)
        self.assertEqual(ana["wins"], 0)

    def test_player_without_name_is_rejected(self):
        with self.assertRaises(ValueError):
            lb.op_add_player(lb.default_state(), "   ")


class PersistenceTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = os.path.join(self.dir, "state.json")

    def test_running_timer_survives_a_restart(self):
        store = lb.Store(self.path)
        store.mutate(lambda s: lb.op_start_level(s, "Level 1", True, 1_000))

        reloaded = lb.Store(self.path)
        timer = reloaded.state["sos"]["levels"][0]["timers"]["a"]
        # Die Uhr laeuft auf Basis der gespeicherten Startzeit einfach weiter.
        self.assertEqual(timer["runningSince"], 1_000)
        self.assertEqual(lb.timer_value_ms(timer, 61_000), 60_000)

    def test_broken_file_is_moved_aside_and_board_still_starts(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{kaputt")
        store = lb.Store(self.path)
        self.assertEqual(store.state["sos"]["levels"], [])
        self.assertTrue(os.path.exists(self.path + ".broken"))

    def test_migration_fills_in_missing_fields(self):
        raw = {"sos": {"levels": [{"name": "Alt"}]}, "smash": {"players": [{"name": "Ana"}]}}
        state = lb.migrate_state(raw)
        level = state["sos"]["levels"][0]
        self.assertEqual(set(level["timers"]), {"a", "b"})
        self.assertEqual(level["timers"]["a"]["elapsedMs"], 0)
        self.assertEqual(state["smash"]["players"][0]["wins"], 0)
        self.assertTrue(state["smash"]["players"][0]["id"])

    def test_migration_survives_complete_garbage(self):
        self.assertEqual(lb.migrate_state("nein danke")["sos"]["levels"], [])
        self.assertEqual(lb.migrate_state({"sos": 42, "smash": None})["smash"]["players"], [])


class BroadcastTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.store = lb.Store(os.path.join(self.dir, "state.json"))

    def test_every_change_reaches_the_open_pages(self):
        client = self.store.subscribe()
        self.store.mutate(lambda s: lb.op_start_level(s, "Level 1", True, 0))
        payload = json.loads(client.get(timeout=1))
        self.assertEqual(payload["state"]["sos"]["levels"][0]["name"], "Level 1")
        self.assertIn("serverNow", payload)

    def test_slow_client_keeps_getting_the_newest_state(self):
        # Ein Client, der nicht abholt, darf nicht stumm veralten: der Puffer
        # wird geleert und behaelt den aktuellen Stand.
        client = self.store.subscribe()
        for index in range(client.maxsize + 10):
            self.store.mutate(lambda s, i=index: lb.op_add_player(s, f"Spieler {i}"))

        newest = json.loads(list(client.queue)[-1])
        self.assertEqual(len(newest["state"]["smash"]["players"]), client.maxsize + 10)
        self.assertLessEqual(client.qsize(), client.maxsize)

    def test_unsubscribed_client_gets_nothing_more(self):
        client = self.store.subscribe()
        self.store.unsubscribe(client)
        self.store.mutate(lambda s: lb.op_start_level(s, "Level 1", True, 0))
        self.assertTrue(client.empty())


class AuthTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.auth = lb.Auth(os.path.join(self.dir, "config.json"))

    def test_password_roundtrip(self):
        self.auth.set_password("geheim123")
        self.assertTrue(self.auth.verify("geheim123"))
        self.assertFalse(self.auth.verify("geheim124"))

    def test_token_is_accepted_only_with_valid_signature(self):
        token = self.auth.make_token()
        self.assertTrue(self.auth.check_token(token))
        self.assertFalse(self.auth.check_token(token[:-1] + ("0" if token[-1] != "0" else "1")))
        self.assertFalse(self.auth.check_token("quatsch"))
        self.assertFalse(self.auth.check_token(None))

    def test_expired_token_is_rejected(self):
        expired = "1000000000.abcd"
        signature = lb.hmac.new(
            self.auth.config["secret"].encode(), expired.encode(), lb.hashlib.sha256
        ).hexdigest()
        self.assertFalse(self.auth.check_token(f"{expired}.{signature}"))

    def test_config_file_is_only_readable_by_owner(self):
        self.auth.set_password("geheim")
        mode = os.stat(self.auth.path).st_mode & 0o777
        self.assertEqual(mode, 0o600)


class HttpTest(unittest.TestCase):
    """Ende-zu-Ende ueber echte HTTP-Aufrufe gegen einen laufenden Server."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.httpd = lb.build_server("127.0.0.1", 0, cls.dir)
        cls.httpd.auth.set_password("party")
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
        self.cookie = None
        self.httpd.store.mutate(lambda s: lb.op_reset(s, "all"))

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        payload = json.dumps(body or {}) if method == "POST" else None
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        set_cookie = response.getheader("Set-Cookie")
        connection.close()
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            parsed = raw
        return response.status, parsed, set_cookie

    def login(self, password="party"):
        status, _body, cookie = self.request("POST", "/api/login", {"password": password})
        if cookie:
            self.cookie = cookie.split(";", 1)[0]
        return status

    def test_display_is_public(self):
        status, body, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Leaderboard", body)

    def test_admin_needs_login(self):
        status, _body, _ = self.request("GET", "/admin")
        self.assertEqual(status, 401)
        self.assertEqual(self.login(), 200)
        status, _body, _ = self.request("GET", "/admin")
        self.assertEqual(status, 200)

    def test_writing_without_login_is_rejected(self):
        status, body, _ = self.request("POST", "/api/sos/level", {"name": "Level 1"})
        self.assertEqual(status, 401)
        self.assertFalse(body.get("ok"))

    def test_rejected_post_leaves_the_connection_usable(self):
        # Wird der Rumpf einer abgelehnten Anfrage nicht gelesen, deutet der
        # Server ihn auf derselben Keep-Alive-Verbindung als naechste Anfrage.
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request(
            "POST",
            "/api/sos/level",
            body=json.dumps({"name": "Level 1"}),
            headers={"Content-Type": "application/json"},
        )
        first = connection.getresponse()
        first.read()
        self.assertEqual(first.status, 401)

        connection.request("GET", "/api/state")
        second = connection.getresponse()
        payload = json.loads(second.read().decode("utf-8"))
        connection.close()
        self.assertEqual(second.status, 200)
        self.assertFalse(payload["authed"])

    def test_wrong_password_is_rejected(self):
        self.assertEqual(self.login("falsch"), 401)
        self.assertIsNone(self.cookie)

    def test_full_round_of_sos(self):
        self.assertEqual(self.login(), 200)
        self.request("POST", "/api/sos/teams", {"names": {"a": "Die Roten", "b": "Die Blauen"}})
        status, body, _ = self.request("POST", "/api/sos/level", {"name": "Level 1"})
        self.assertEqual(status, 200)
        level_id = body["level"]["id"]

        self.request("POST", "/api/sos/timer", {"levelId": level_id, "team": "a", "action": "finish"})
        self.request(
            "POST", "/api/sos/timer/adjust", {"levelId": level_id, "team": "a", "valueMs": 61_000}
        )
        self.request("POST", "/api/sos/timer", {"levelId": level_id, "team": "b", "action": "finish"})
        self.request(
            "POST", "/api/sos/timer/adjust", {"levelId": level_id, "team": "b", "valueMs": 95_000}
        )

        status, body, _ = self.request("GET", "/api/state")
        self.assertEqual(body["state"]["sos"]["teams"][0]["name"], "Die Roten")
        self.assertEqual(body["standings"]["levelWins"], {"a": 1, "b": 0})
        self.assertEqual(body["standings"]["leader"], "a")
        self.assertTrue(body["authed"])

    def test_smash_round(self):
        self.assertEqual(self.login(), 200)
        _s, ana, _c = self.request("POST", "/api/smash/player", {"action": "add", "name": "Ana"})
        _s, ben, _c = self.request("POST", "/api/smash/player", {"action": "add", "name": "Ben"})
        status, _body, _ = self.request(
            "POST", "/api/smash/result", {"winnerId": ana["player"]["id"], "loserId": ben["player"]["id"]}
        )
        self.assertEqual(status, 200)

        _status, body, _ = self.request("GET", "/api/state")
        players = {p["name"]: p for p in body["state"]["smash"]["players"]}
        self.assertEqual((players["Ana"]["wins"], players["Ana"]["losses"]), (1, 0))
        self.assertEqual((players["Ben"]["wins"], players["Ben"]["losses"]), (0, 1))

    def test_unknown_ids_give_clean_errors(self):
        self.assertEqual(self.login(), 200)
        status, body, _ = self.request(
            "POST", "/api/sos/timer", {"levelId": "gibtsnicht", "team": "a", "action": "start"}
        )
        self.assertEqual(status, 404)
        self.assertIn("unbekannt", body["error"].lower())

    def test_state_survives_a_server_restart(self):
        self.assertEqual(self.login(), 200)
        self.request("POST", "/api/sos/level", {"name": "Level 1"})

        reloaded = lb.Store(os.path.join(self.dir, "state.json"))
        self.assertEqual(reloaded.state["sos"]["levels"][0]["name"], "Level 1")


if __name__ == "__main__":
    unittest.main()
