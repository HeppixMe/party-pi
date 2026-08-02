"""Tests fuer Kurve, Hysterese und Regelschleife der Lueftersteuerung.

Alles laeuft ohne echte Hardware: die Temperatur wird eingespeist und ein
Attrappen-Backend merkt sich, was geschaltet worden waere.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "fan"))

import fan_control as fc  # noqa: E402


class RecordingBackend(fc.Backend):
    name = "test"

    def __init__(self):
        self.calls = []

    def set_percent(self, percent):
        self.calls.append(percent)

    def read_percent(self):
        return self.calls[-1] if self.calls else None


def make_controller(**overrides):
    config = fc.load_config(None)
    for key, value in overrides.items():
        config[key] = str(value)
    controller = fc.Controller(config, dry_run=True)
    controller.backend = RecordingBackend()
    return controller


class CurveTest(unittest.TestCase):
    def test_parse_sorts_and_clamps(self):
        steps = fc.parse_curve("70:150, 50:30, 60:-5")
        self.assertEqual(steps, [(50.0, 30), (60.0, 0), (70.0, 100)])

    def test_parse_accepts_semicolons_and_spaces(self):
        self.assertEqual(fc.parse_curve(" 55 : 40 ; 65:80 "), [(55.0, 40), (65.0, 80)])

    def test_empty_or_broken_curve_is_rejected(self):
        with self.assertRaises(ValueError):
            fc.parse_curve("")
        with self.assertRaises(ValueError):
            fc.parse_curve("warm:schnell")


class StepTest(unittest.TestCase):
    def setUp(self):
        self.steps = [(50.0, 30), (60.0, 50), (70.0, 75), (75.0, 100)]

    def test_below_first_step_means_off(self):
        index = fc.next_step(self.steps, 40, -1, hysteresis=4)
        self.assertEqual(index, -1)
        self.assertEqual(fc.percent_for_index(self.steps, index, idle_percent=0), 0)

    def test_speeds_up_immediately(self):
        self.assertEqual(fc.next_step(self.steps, 62, -1, hysteresis=4), 1)
        self.assertEqual(fc.next_step(self.steps, 78, 1, hysteresis=4), 3)

    def test_stays_up_inside_the_hysteresis_band(self):
        # Stufe 1 gilt ab 60 C; bei 4 K Hysterese wird erst unter 56 C wieder
        # heruntergeschaltet - genau das verhindert das dauernde Pumpen.
        self.assertEqual(fc.next_step(self.steps, 58, 1, hysteresis=4), 1)
        self.assertEqual(fc.next_step(self.steps, 57, 1, hysteresis=4), 1)

    def test_steps_down_once_the_band_is_left(self):
        self.assertEqual(fc.next_step(self.steps, 56, 1, hysteresis=4), 0)
        self.assertEqual(fc.next_step(self.steps, 30, 3, hysteresis=4), -1)

    def test_zero_hysteresis_switches_direkt_an_der_schwelle(self):
        # Ohne Hysterese faellt die Stufe, sobald die Schwelle unterschritten ist.
        self.assertEqual(fc.next_step(self.steps, 59, 1, hysteresis=0), 0)
        self.assertEqual(fc.next_step(self.steps, 60, 1, hysteresis=0), 1)


class TripPointMappingTest(unittest.TestCase):
    def test_more_curve_points_than_trips_are_spread_out(self):
        steps = [(50.0, 30), (60.0, 50), (70.0, 75), (75.0, 100)]
        self.assertEqual(fc.map_curve_to_trips(steps, 2), [50, 75])
        self.assertEqual(fc.map_curve_to_trips(steps, 4), [50, 60, 70, 75])

    def test_fewer_curve_points_are_extended_upwards(self):
        self.assertEqual(fc.map_curve_to_trips([(55.0, 40)], 3), [55, 60, 65])

    def test_result_is_always_strictly_increasing(self):
        steps = [(60.0, 30), (60.0, 60), (60.0, 100)]
        temps = fc.map_curve_to_trips(steps, 3)
        self.assertEqual(temps, sorted(set(temps)))
        self.assertEqual(len(temps), 3)

    def test_no_trips_means_no_values(self):
        self.assertEqual(fc.map_curve_to_trips([(50.0, 30)], 0), [])


class TripPointBackendTest(unittest.TestCase):
    """
    Der Pi-5-Pfad laesst sich hier nicht an echter Hardware pruefen - deshalb
    gegen ein nachgebautes sysfs-Verzeichnis: dieselben Dateien, dieselben
    Namen, nur unter /tmp.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.zone = os.path.join(self.dir, "thermal_zone0")
        os.makedirs(self.zone)
        self._write("temp", "58000")
        self._write("type", "cpu-thermal")
        # Vier aktive Schaltpunkte wie beim offiziellen Kuehler, dazu ein
        # kritischer, den wir nicht anfassen duerfen.
        for index, temp in enumerate((50000, 60000, 67500, 75000)):
            self._write(f"trip_point_{index}_type", "active")
            self._write(f"trip_point_{index}_temp", str(temp))
            self._write(f"trip_point_{index}_hyst", "5000")
        self._write("trip_point_4_type", "critical")
        self._write("trip_point_4_temp", "110000")

    def _write(self, name, value):
        with open(os.path.join(self.zone, name), "w", encoding="utf-8") as fh:
            fh.write(value)

    def _read(self, name):
        with open(os.path.join(self.zone, name), "r", encoding="utf-8") as fh:
            return fh.read().strip()

    def test_only_active_trip_points_are_used(self):
        backend = fc.TripPointBackend(self.zone, None)
        self.assertEqual(len(backend.trips), 4)

    def test_curve_is_written_to_the_trip_points(self):
        backend = fc.TripPointBackend(self.zone, None)
        backend.apply_curve([(45.0, 30), (55.0, 50), (62.0, 75), (68.0, 100)], hysteresis=4)
        written = [int(self._read(f"trip_point_{i}_temp")) for i in range(4)]
        self.assertEqual(written, [45000, 55000, 62000, 68000])
        self.assertEqual(self._read("trip_point_0_hyst"), "4000")
        # Der kritische Schaltpunkt bleibt unangetastet.
        self.assertEqual(self._read("trip_point_4_temp"), "110000")

    def test_raising_the_curve_keeps_the_order_valid(self):
        # Werte nach oben zu schieben ist der heikle Fall: wuerde man stur von
        # unten nach oben schreiben, stuende trip0 zwischendurch ueber trip1.
        backend = fc.TripPointBackend(self.zone, None)
        backend.apply_curve([(70.0, 30), (78.0, 50), (84.0, 75), (90.0, 100)], hysteresis=3)
        written = [int(self._read(f"trip_point_{i}_temp")) for i in range(4)]
        self.assertEqual(written, [70000, 78000, 84000, 90000])
        self.assertEqual(written, sorted(written))

    def test_percent_is_read_from_the_cooling_device(self):
        cooling = os.path.join(self.dir, "cooling_device0")
        os.makedirs(cooling)
        for name, value in (("type", "pwm-fan"), ("cur_state", "3"), ("max_state", "4")):
            with open(os.path.join(cooling, name), "w", encoding="utf-8") as fh:
                fh.write(value)
        backend = fc.TripPointBackend(self.zone, cooling)
        self.assertEqual(backend.read_percent(), 75)

    def test_temperature_is_read_from_the_zone(self):
        self.assertEqual(fc.read_temperature(self.zone), 58.0)

    def test_zone_without_active_trips_does_not_crash(self):
        empty = os.path.join(self.dir, "thermal_zone9")
        os.makedirs(empty)
        backend = fc.TripPointBackend(empty, None)
        backend.apply_curve([(50.0, 30)], hysteresis=4)  # darf nur warnen
        self.assertEqual(backend.trips, [])


class ControllerTest(unittest.TestCase):
    def test_switches_up_and_down_along_the_curve(self):
        controller = make_controller(min_hold=0)
        self.assertEqual(controller.step(45, now=0), 0)
        self.assertEqual(controller.step(52, now=1), 30)
        self.assertEqual(controller.step(72, now=2), 75)
        self.assertEqual(controller.step(40, now=3), 0)
        self.assertEqual(controller.backend.calls, [30, 75, 0])

    def test_min_hold_delays_only_the_step_down(self):
        controller = make_controller(min_hold=30)
        controller.step(72, now=100)  # hoch: sofort
        self.assertEqual(controller.percent, 75)
        controller.step(40, now=110)  # runter: noch gesperrt
        self.assertEqual(controller.percent, 75)
        controller.step(40, now=140)  # jetzt erlaubt
        self.assertEqual(controller.percent, 0)

    def test_step_up_ignores_min_hold(self):
        controller = make_controller(min_hold=300)
        controller.step(52, now=0)
        controller.step(80, now=1)
        self.assertEqual(controller.percent, 100)

    def test_missing_sensor_keeps_the_last_level(self):
        controller = make_controller(min_hold=0)
        controller.step(72, now=0)
        self.assertEqual(controller.step(None, now=10), 75)

    def test_idle_percent_is_used_below_the_curve(self):
        controller = make_controller(min_hold=0, idle_percent=20)
        self.assertEqual(controller.step(30, now=0), 20)

    def test_broken_curve_stops_the_service_with_a_clear_error(self):
        config = fc.load_config(None)
        config["curve"] = "unsinn"
        with self.assertRaises(ValueError):
            fc.Controller(config, dry_run=True)


class StatusFileTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = os.path.join(self.dir, "fan-status.json")

    def test_status_file_is_written_for_the_display(self):
        controller = make_controller(min_hold=0, status_file=self.path)
        controller.step(72, now=0)
        controller.write_status(72.4)

        with open(self.path, "r", encoding="utf-8") as fh:
            status = json.load(fh)
        self.assertEqual(status["tempC"], 72.4)
        self.assertEqual(status["percent"], 75)
        self.assertEqual(status["backend"], "test")
        # Die Anzeige laeuft als anderer Benutzer und muss mitlesen koennen.
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o644)

    def test_status_without_sensor_says_so(self):
        controller = make_controller(min_hold=0, status_file=self.path)
        controller.write_status(None)
        with open(self.path, "r", encoding="utf-8") as fh:
            self.assertIsNone(json.load(fh)["tempC"])


class ForceMaxTest(unittest.TestCase):
    """Der Volllast-Schalter aus der Weboberflaeche."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.override = os.path.join(self.dir, "fan-override.json")
        self.status = os.path.join(self.dir, "fan-status.json")

    def controller(self):
        controller = make_controller(min_hold=0, status_file=self.status,
                                     override_file=self.override)
        controller.backend = RecordingBackend()
        return controller

    def set_override(self, force):
        with open(self.override, "w", encoding="utf-8") as fh:
            json.dump({"force": force}, fh)

    def test_missing_file_means_off(self):
        self.assertFalse(self.controller().read_override())

    def test_broken_file_means_off(self):
        with open(self.override, "w", encoding="utf-8") as fh:
            fh.write("{kaputt")
        self.assertFalse(self.controller().read_override())

    def test_override_pushes_the_fan_to_full(self):
        controller = self.controller()
        controller.step(52, now=0)                 # nach Kurve: 30 %
        self.assertEqual(controller.percent, 30)

        self.set_override(True)
        controller.apply_override(controller.read_override())
        self.assertTrue(controller.forced)
        self.assertEqual(controller.backend.calls[-1], 100)
        # Auch eine niedrige Temperatur aendert daran nichts mehr.
        self.assertEqual(controller.step(20, now=10), 100)

    def test_switching_back_returns_to_the_curve(self):
        controller = self.controller()
        self.set_override(True)
        controller.apply_override(controller.read_override())
        self.set_override(False)
        controller.apply_override(controller.read_override())

        self.assertFalse(controller.forced)
        # Die Regelung faengt neu an und folgt wieder der Temperatur.
        self.assertEqual(controller.step(72, now=100), 75)

    def test_no_repeated_switching_without_change(self):
        controller = self.controller()
        self.set_override(True)
        controller.apply_override(True)
        before = len(controller.backend.calls)
        controller.apply_override(True)
        controller.apply_override(True)
        self.assertEqual(len(controller.backend.calls), before)

    def test_status_file_reports_the_switch(self):
        controller = self.controller()
        self.set_override(True)
        controller.apply_override(True)
        controller.write_status(48.0)
        with open(self.status, "r", encoding="utf-8") as fh:
            status = json.load(fh)
        self.assertTrue(status["force"])
        self.assertTrue(status["controllable"])

    def test_monitor_backend_reports_that_it_cannot_help(self):
        controller = self.controller()
        controller.backend = fc.MonitorBackend()
        controller.apply_override(True)
        controller.write_status(48.0)
        with open(self.status, "r", encoding="utf-8") as fh:
            self.assertFalse(json.load(fh)["controllable"])

    def test_override_file_defaults_next_to_the_status_file(self):
        controller = make_controller(status_file=self.status)
        self.assertEqual(controller.override_file, self.override)


class TripPointForceTest(unittest.TestCase):
    """Beim kernelgeregelten Luefter heisst Volllast: Schaltpunkte nach unten."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.zone = os.path.join(self.dir, "thermal_zone0")
        os.makedirs(self.zone)
        for index, temp in enumerate((50000, 60000, 67500, 75000)):
            self._write(f"trip_point_{index}_type", "active")
            self._write(f"trip_point_{index}_temp", str(temp))
            self._write(f"trip_point_{index}_hyst", "5000")

    def _write(self, name, value):
        with open(os.path.join(self.zone, name), "w", encoding="utf-8") as fh:
            fh.write(value)

    def _temps(self):
        values = []
        for index in range(4):
            with open(os.path.join(self.zone, f"trip_point_{index}_temp"), encoding="utf-8") as fh:
                values.append(int(fh.read()))
        return values

    def test_force_lowers_all_trip_points(self):
        backend = fc.TripPointBackend(self.zone, None)
        steps = [(50.0, 30), (60.0, 50), (70.0, 75), (75.0, 100)]
        backend.force_max(True, steps, 4)
        # Alle Schaltpunkte liegen jetzt so tief, dass die hoechste Stufe greift.
        self.assertTrue(all(value <= 5000 for value in self._temps()), self._temps())

    def test_releasing_restores_the_configured_curve(self):
        backend = fc.TripPointBackend(self.zone, None)
        steps = [(45.0, 30), (55.0, 50), (62.0, 75), (68.0, 100)]
        backend.force_max(True, steps, 4)
        backend.force_max(False, steps, 4)
        self.assertEqual(self._temps(), [45000, 55000, 62000, 68000])


class BackendChoiceTest(unittest.TestCase):
    def test_monitor_can_be_forced(self):
        config = fc.load_config(None)
        config["backend"] = "monitor"
        self.assertIsInstance(fc.build_backend(config, None, dry_run=True), fc.MonitorBackend)

    def test_gpio_without_pin_falls_back_to_monitoring(self):
        config = fc.load_config(None)
        config["backend"] = "auto"
        config["gpio_pin"] = ""
        backend = fc.build_backend(config, None, dry_run=True)
        # Ohne Pin-Angabe und ohne kernelgeregelten Luefter bleibt nur Beobachten.
        self.assertIn(backend.name, ("monitor", "trip-points"))

    def test_explicit_gpio_without_pin_is_an_error(self):
        config = fc.load_config(None)
        config["backend"] = "gpio"
        config["gpio_pin"] = ""
        with self.assertRaises(RuntimeError):
            fc.build_backend(config, None, dry_run=True)


class GpioLevelTest(unittest.TestCase):
    def test_active_low_inverts_the_output(self):
        normal = fc.GpioOnOffBackend(pin=14, active_low=False, dry_run=True)
        inverted = fc.GpioOnOffBackend(pin=14, active_low=True, dry_run=True)
        self.assertEqual((normal._level(True), normal._level(False)), (1, 0))
        self.assertEqual((inverted._level(True), inverted._level(False)), (0, 1))

    def test_any_percent_above_zero_means_on(self):
        backend = fc.GpioOnOffBackend(pin=14, active_low=False, dry_run=True)
        backend.set_percent(30)
        self.assertEqual(backend.read_percent(), 100)
        backend.set_percent(0)
        self.assertEqual(backend.read_percent(), 0)


if __name__ == "__main__":
    unittest.main()
