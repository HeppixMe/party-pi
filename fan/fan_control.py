#!/usr/bin/env python3
"""
Luefterkurve fuer den Raspberry Pi - eigener kleiner Dienst, nur Standardbibliothek.

Liest regelmaessig die CPU-Temperatur und stellt den Luefter danach ein. Die
Kurve steht in fan.conf; der Standard dreht deutlich frueher auf als die
Werkseinstellung, damit der Pi unter Dauerlast nicht warm wird.

Weil die Luefter-Hardware je nach Aufbau anders angesteuert wird, sucht sich
der Dienst beim Start selbst das passende Verfahren:

  trip-points  Pi 5 mit offiziellem Active Cooler (oder jeder Luefter, den der
               Kernel selbst regelt). Hier schreibt der Dienst die Schaltpunkte
               der Thermal-Zone - der Kernel-Regler behaelt die Kontrolle, was
               der einzige zuverlaessige Weg ist: ein direkt geschriebenes
               cur_state wuerde er sofort wieder ueberschreiben.
  pwm          PWM-Luefter an einem Hardware-PWM-Pin (GPIO12/13/18/19) ueber
               /sys/class/pwm. Braucht 'dtoverlay=pwm' in config.txt.
  gpio         Einfacher Luefter, der ueber einen Transistor an einem GPIO nur
               an- oder ausgeschaltet wird.
  monitor      Kein steuerbarer Luefter gefunden: nur beobachten und loggen.

pwm und gpio brauchen zwingend die Pin-Angabe in fan.conf - die kann kein
Programm erraten. trip-points wird von allein erkannt.

Aufruf:
    sudo python3 fan_control.py --config /etc/lanparty-fan.conf
    python3 fan_control.py --dry-run          # nur anzeigen, nichts schalten
"""

from __future__ import annotations

import argparse
import configparser
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import time

DEFAULTS = {
    "backend": "auto",
    "curve": "50:30, 60:50, 70:75, 75:100",
    "hysteresis": "4",
    "interval": "2",
    "min_hold": "15",
    "idle_percent": "0",
    "status_file": "",
    "override_file": "",
    "thermal_zone": "auto",
    "gpio_pin": "",
    "gpio_active_low": "false",
    "pwm_chip": "0",
    "pwm_channel": "0",
    "pwm_period_ns": "40000",
    "pwm_invert": "false",
}

THERMAL_ROOT = "/sys/class/thermal"


def log(message: str) -> None:
    print(message, flush=True)


# ---------------------------------------------------------------------------
# Kurve (reine Logik - ohne Hardware testbar)
# ---------------------------------------------------------------------------
def parse_curve(text: str) -> list[tuple[float, int]]:
    """'50:30, 60:50' -> [(50.0, 30), (60.0, 50)], aufsteigend nach Temperatur."""
    steps: list[tuple[float, int]] = []
    for chunk in text.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        temp_text, _, percent_text = chunk.partition(":")
        try:
            temp = float(temp_text.strip())
            percent = int(float(percent_text.strip()))
        except ValueError as err:
            raise ValueError(f"Ungueltiger Kurvenpunkt '{chunk}'") from err
        steps.append((temp, max(0, min(100, percent))))
    if not steps:
        raise ValueError("Die Kurve enthaelt keinen einzigen Punkt")
    return sorted(steps, key=lambda step: step[0])


def next_step(steps, temp: float, current_index: int, hysteresis: float) -> int:
    """
    Neue Stufe bestimmen.

    Hochschalten passiert sofort (Sicherheit geht vor), runter erst, wenn die
    Temperatur die Schwelle der aktuellen Stufe um die Hysterese unterschreitet.
    Sonst pumpt der Luefter direkt an der Schaltgrenze staendig auf und ab.
    """
    index = -1
    for position, (step_temp, _percent) in enumerate(steps):
        if temp >= step_temp:
            index = position

    if index > current_index:
        return index
    if index < current_index and 0 <= current_index < len(steps):
        if temp <= steps[current_index][0] - hysteresis:
            return index
        return current_index
    return index if current_index < 0 else current_index


def percent_for_index(steps, index: int, idle_percent: int) -> int:
    if index < 0:
        return idle_percent
    return steps[index][1]


def map_curve_to_trips(steps, count: int) -> list[int]:
    """
    Kurventemperaturen auf die vorhandenen Schaltpunkte der Thermal-Zone
    verteilen. Der Kernel hat eine feste Zahl aktiver Trip-Points (beim
    offiziellen Kuehler vier), unsere Kurve kann mehr oder weniger Punkte
    haben - deshalb gleichmaessig verteilen bzw. nach oben fortschreiben.
    """
    if count <= 0:
        return []
    temps = [temp for temp, _percent in steps]
    if len(temps) >= count:
        if count == 1:
            picked = [temps[0]]
        else:
            picked = [
                temps[round(i * (len(temps) - 1) / (count - 1))] for i in range(count)
            ]
    else:
        picked = list(temps)
        while len(picked) < count:
            picked.append(picked[-1] + 5)

    # Der Kernel verlangt streng aufsteigende Schaltpunkte.
    result: list[int] = []
    for value in picked:
        candidate = int(round(value))
        if result and candidate <= result[-1]:
            candidate = result[-1] + 1
        result.append(candidate)
    return result


# ---------------------------------------------------------------------------
# Temperatur
# ---------------------------------------------------------------------------
def find_thermal_zone(preferred: str = "auto") -> str | None:
    if preferred and preferred != "auto":
        path = preferred if preferred.startswith("/") else f"{THERMAL_ROOT}/{preferred}"
        return path if os.path.isdir(path) else None
    zones = sorted(glob.glob(f"{THERMAL_ROOT}/thermal_zone*"))
    for zone in zones:
        if read_text(os.path.join(zone, "type")) in ("cpu-thermal", "cpu_thermal"):
            return zone
    return zones[0] if zones else None


def read_text(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def write_text(path: str, value: str) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(value)
        return True
    except OSError:
        return False


def read_temperature(zone: str | None) -> float | None:
    if not zone:
        return None
    raw = read_text(os.path.join(zone, "temp"))
    try:
        return int(raw) / 1000.0
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Ansteuerungs-Verfahren
# ---------------------------------------------------------------------------
class Backend:
    name = "none"
    #: Kann dieses Verfahren ueberhaupt etwas schalten? Beim reinen Beobachten
    #: nicht - dann soll der Admin einen Hinweis statt einer wirkungslosen
    #: Schaltflaeche sehen.
    controllable = True

    def apply_curve(self, steps, hysteresis: float) -> None:
        """Einmalig beim Start - nur fuer Verfahren, bei denen der Kernel regelt."""

    def force_max(self, on: bool, steps, hysteresis: float) -> None:
        """
        Volllast erzwingen oder zurueck zur Kurve.

        Standardweg fuer alle Verfahren, die die Leistung direkt setzen; die
        Kernel-Variante ueberschreibt das, weil dort die Schaltpunkte zaehlen.
        """
        if on:
            self.set_percent(100)

    def set_percent(self, percent: int) -> None:
        """Luefterleistung setzen (0-100)."""

    def read_percent(self) -> int | None:
        """Tatsaechliche Leistung, falls auslesbar."""
        return None

    def close(self) -> None:
        pass


class MonitorBackend(Backend):
    name = "monitor"
    controllable = False


class TripPointBackend(Backend):
    """
    Pi 5 / offizieller Active Cooler: der Kernel regelt den Luefter selbst.
    Die Kurve wird dadurch verschoben, dass wir die Schaltpunkte der
    Thermal-Zone neu setzen.
    """

    name = "trip-points"

    def __init__(self, zone: str, cooling_device: str | None, dry_run: bool = False):
        self.zone = zone
        self.cooling_device = cooling_device
        self.dry_run = dry_run
        self.trips = self._active_trips()

    def _active_trips(self) -> list[str]:
        trips = []
        for path in sorted(glob.glob(os.path.join(self.zone, "trip_point_*_type"))):
            if read_text(path) == "active":
                trips.append(path[: -len("_type")])
        return trips

    def apply_curve(self, steps, hysteresis: float) -> None:
        if not self.trips:
            log("[!] Thermal-Zone hat keine aktiven Schaltpunkte - Kurve bleibt unveraendert.")
            return
        temps = map_curve_to_trips(steps, len(self.trips))
        log(f"    Schaltpunkte: {', '.join(f'{t} C' for t in temps)}")
        if self.dry_run:
            return

        # Der Kernel verlangt, dass die Schaltpunkte jederzeit aufsteigend
        # sind. Je nachdem, ob die neuen Werte hoeher oder niedriger als die
        # alten liegen, muss die Schreibreihenfolge anders herum sein - zwei
        # Durchlaeufe in beide Richtungen erledigen beide Faelle.
        for order in (range(len(self.trips)), reversed(range(len(self.trips)))):
            for index in order:
                write_text(f"{self.trips[index]}_temp", str(temps[index] * 1000))
                write_text(f"{self.trips[index]}_hyst", str(int(hysteresis * 1000)))

        written = [read_text(f"{trip}_temp") for trip in self.trips]
        actual = [int(value) // 1000 for value in written if value and value.isdigit()]
        if actual != temps:
            log(f"[!] Schaltpunkte weichen ab (gesetzt: {actual}) - Kernel hat Werte angepasst.")

    def force_max(self, on: bool, steps, hysteresis: float) -> None:
        """
        Beim kernelgeregelten Luefter laesst sich die Leistung nicht direkt
        setzen - der Regler wuerde sie sofort ueberschreiben. Stattdessen
        werden die Schaltpunkte so weit nach unten gezogen, dass die hoechste
        Stufe immer greift. Beim Zurueckschalten kommt die Kurve wieder.
        """
        if on:
            low = [(float(index), 100) for index in range(len(self.trips) or 1)]
            self.apply_curve(low, 0)
        else:
            self.apply_curve(steps, hysteresis)

    def read_percent(self) -> int | None:
        if not self.cooling_device:
            return None
        current = read_text(os.path.join(self.cooling_device, "cur_state"))
        maximum = read_text(os.path.join(self.cooling_device, "max_state"))
        try:
            current_value, max_value = int(current), int(maximum)
        except (TypeError, ValueError):
            return None
        if max_value <= 0:
            return None
        return round(current_value * 100 / max_value)


class SysfsPwmBackend(Backend):
    """PWM-Luefter an einem Hardware-PWM-Pin ueber /sys/class/pwm."""

    name = "pwm"

    def __init__(self, chip: int, channel: int, period_ns: int, invert: bool, dry_run=False):
        self.chip_path = f"/sys/class/pwm/pwmchip{chip}"
        self.channel_path = os.path.join(self.chip_path, f"pwm{channel}")
        self.channel = channel
        self.period_ns = max(1000, period_ns)
        self.invert = invert
        self.dry_run = dry_run
        self.current = -1
        if not dry_run:
            self._export()

    def _export(self) -> None:
        if not os.path.isdir(self.channel_path):
            write_text(os.path.join(self.chip_path, "export"), str(self.channel))
            for _ in range(20):
                if os.path.isdir(self.channel_path):
                    break
                time.sleep(0.05)
        if not os.path.isdir(self.channel_path):
            raise RuntimeError(f"PWM-Kanal {self.channel_path} liess sich nicht anlegen")
        write_text(os.path.join(self.channel_path, "period"), str(self.period_ns))
        if self.invert:
            write_text(os.path.join(self.channel_path, "polarity"), "inversed")

    def set_percent(self, percent: int) -> None:
        if self.dry_run:
            return
        duty = int(self.period_ns * max(0, min(100, percent)) / 100)
        write_text(os.path.join(self.channel_path, "duty_cycle"), str(duty))
        write_text(os.path.join(self.channel_path, "enable"), "1")
        self.current = percent

    def read_percent(self) -> int | None:
        return self.current if self.current >= 0 else None


class GpioOnOffBackend(Backend):
    """
    Luefter, der ueber einen Transistor an einem GPIO nur an- oder ausgeht.
    Die Kurve wird damit zu Schaltschwellen: alles ueber 0 % bedeutet 'an'.

    Bevorzugt 'pinctrl' (auf Raspberry Pi OS Bookworm dabei und auch auf dem
    Pi 5 zustaendig), weil ein damit gesetzter Pin auch nach dem Programmende
    gesetzt bleibt. Fallbacks: ein dauerhaft laufender gpioset-Prozess und das
    alte /sys/class/gpio-Interface.
    """

    name = "gpio"

    def __init__(self, pin: int, active_low: bool, dry_run: bool = False):
        self.pin = pin
        self.active_low = active_low
        self.dry_run = dry_run
        self.current = -1
        self._gpioset = None
        self._sysfs_path = None
        self.method = self._pick_method()
        log(f"    GPIO-Verfahren: {self.method} (Pin {pin}, active_low={active_low})")

    def _pick_method(self) -> str:
        if shutil.which("pinctrl"):
            return "pinctrl"
        if shutil.which("gpioset"):
            return "gpioset"
        if os.path.isdir("/sys/class/gpio"):
            return "sysfs"
        return "none"

    def _level(self, on: bool) -> int:
        return int(on != self.active_low)

    def set_percent(self, percent: int) -> None:
        on = percent > 0
        if self.current == int(on):
            return
        self.current = int(on)
        if self.dry_run:
            return
        level = self._level(on)
        if self.method == "pinctrl":
            subprocess.run(
                ["pinctrl", "set", str(self.pin), "op", "dh" if level else "dl"],
                check=False,
                capture_output=True,
            )
        elif self.method == "gpioset":
            self._gpioset_set(level)
        elif self.method == "sysfs":
            self._sysfs_set(level)

    def _gpioset_set(self, level: int) -> None:
        # gpioset gibt die Leitung frei, sobald es sich beendet - deshalb muss
        # der Prozess laufen bleiben. Erst die Syntax von libgpiod 2 versuchen,
        # dann die von Version 1.
        if self._gpioset:
            self._gpioset.terminate()
            self._gpioset = None
        variants = [
            ["gpioset", "-t0", "gpiochip0", f"{self.pin}={level}"],
            ["gpioset", "--mode=signal", "0", f"{self.pin}={level}"],
        ]
        for command in variants:
            try:
                process = subprocess.Popen(
                    command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except OSError:
                continue
            time.sleep(0.15)
            if process.poll() is None:
                self._gpioset = process
                return
        log("[!] gpioset liess sich nicht starten - Luefter bleibt ungeregelt.")

    def _sysfs_set(self, level: int) -> None:
        path = f"/sys/class/gpio/gpio{self.pin}"
        if not os.path.isdir(path):
            write_text("/sys/class/gpio/export", str(self.pin))
            time.sleep(0.1)
        write_text(os.path.join(path, "direction"), "out")
        write_text(os.path.join(path, "value"), str(level))

    def read_percent(self) -> int | None:
        return 100 if self.current == 1 else 0 if self.current == 0 else None

    def close(self) -> None:
        if self._gpioset:
            self._gpioset.terminate()


def find_fan_cooling_device() -> str | None:
    for path in sorted(glob.glob(f"{THERMAL_ROOT}/cooling_device*")):
        kind = (read_text(os.path.join(path, "type")) or "").lower()
        if "fan" in kind:
            return path
    return None


def build_backend(config, zone: str | None, dry_run: bool) -> Backend:
    """Verfahren aus der Konfiguration waehlen bzw. selbst erkennen."""
    wanted = config.get("backend", "auto").strip().lower()
    pin_text = config.get("gpio_pin", "").strip()

    def make_pwm():
        return SysfsPwmBackend(
            chip=int(config.get("pwm_chip", "0")),
            channel=int(config.get("pwm_channel", "0")),
            period_ns=int(config.get("pwm_period_ns", "40000")),
            invert=config.getboolean("pwm_invert", fallback=False),
            dry_run=dry_run,
        )

    def make_gpio():
        if not pin_text:
            raise RuntimeError("gpio_pin ist nicht gesetzt")
        return GpioOnOffBackend(
            pin=int(pin_text),
            active_low=config.getboolean("gpio_active_low", fallback=False),
            dry_run=dry_run,
        )

    def make_trip():
        cooling = find_fan_cooling_device()
        if not zone:
            raise RuntimeError("keine Thermal-Zone gefunden")
        if not cooling:
            raise RuntimeError("kein Luefter als cooling_device gefunden")
        return TripPointBackend(zone, cooling, dry_run=dry_run)

    if wanted in ("trip", "trip-points", "kernel"):
        return make_trip()
    if wanted == "pwm":
        return make_pwm()
    if wanted == "gpio":
        return make_gpio()
    if wanted == "monitor":
        return MonitorBackend()
    if wanted != "auto":
        log(f"[!] Unbekanntes backend='{wanted}' - erkenne selbst.")

    # Automatik: erst der vom Kernel geregelte Luefter (offizieller Kuehler),
    # danach ein in der Konfiguration eingetragener GPIO-Luefter. Ohne
    # Pin-Angabe laesst sich ein GPIO-Luefter nicht erraten - dann bleibt es
    # beim reinen Beobachten.
    try:
        backend = make_trip()
        log("==> Erkannt: vom Kernel geregelter Luefter (offizieller Active Cooler o.ae.).")
        return backend
    except (RuntimeError, OSError) as err:
        log(f"    Kein kernelgeregelter Luefter ({err}).")

    if pin_text:
        try:
            backend = make_gpio()
            log("==> Erkannt: GPIO-Luefter laut fan.conf.")
            return backend
        except (RuntimeError, OSError, ValueError) as err:
            log(f"[!] GPIO-Luefter nicht nutzbar ({err}).")

    log(
        "[!] Kein steuerbarer Luefter gefunden - Dienst beobachtet nur die Temperatur.\n"
        "    Fuer einen Luefter an GPIO in fan.conf 'backend' und 'gpio_pin' bzw. die\n"
        "    pwm_*-Werte eintragen."
    )
    return MonitorBackend()


# ---------------------------------------------------------------------------
# Regelschleife
# ---------------------------------------------------------------------------
class Controller:
    def __init__(self, config, dry_run: bool = False):
        self.steps = parse_curve(config.get("curve", DEFAULTS["curve"]))
        self.hysteresis = config.getfloat("hysteresis", fallback=4.0)
        self.interval = max(0.5, config.getfloat("interval", fallback=2.0))
        self.min_hold = config.getfloat("min_hold", fallback=15.0)
        self.idle_percent = config.getint("idle_percent", fallback=0)
        self.status_file = config.get("status_file", "").strip()
        self.zone = find_thermal_zone(config.get("thermal_zone", "auto"))
        self.dry_run = dry_run

        log("==> Luefterkurve: " + ", ".join(f"{t:.0f} C -> {p} %" for t, p in self.steps))
        self.backend = build_backend(config, self.zone, dry_run)
        log(f"==> Verfahren: {self.backend.name}, Thermal-Zone: {self.zone or 'keine'}")
        self.backend.apply_curve(self.steps, self.hysteresis)

        self.index = -1
        self.percent = self.idle_percent
        self.last_change = 0.0
        self.running = True

        # Volllast-Schalter aus der Weboberflaeche. Webserver und dieser Dienst
        # laufen als verschiedene Benutzer in getrennten Prozessen - eine
        # kleine Datei ist hier der einfachste verlaessliche Weg.
        self.override_file = config.get("override_file", "").strip()
        if not self.override_file and self.status_file:
            self.override_file = os.path.join(
                os.path.dirname(os.path.abspath(self.status_file)), "fan-override.json"
            )
        self.forced = False

    def read_override(self) -> bool:
        """Steht der Volllast-Schalter auf an?"""
        if not self.override_file:
            return False
        try:
            with open(self.override_file, "r", encoding="utf-8") as fh:
                return bool(json.load(fh).get("force"))
        except (FileNotFoundError, json.JSONDecodeError, OSError, AttributeError):
            return False

    def apply_override(self, forced: bool) -> None:
        """Umschalten zwischen Volllast und Kurve - nur bei echter Aenderung."""
        if forced == self.forced:
            return
        self.forced = forced
        if not self.backend.controllable:
            log("[!] Volllast angefordert, aber kein steuerbarer Luefter vorhanden.")
            return
        log("==> Luefter: " + ("VOLLLAST (100 %)" if forced else "wieder nach Kurve"))
        self.backend.force_max(forced, self.steps, self.hysteresis)
        if forced:
            self.percent = 100
        else:
            # Regelung neu einschwingen lassen, statt auf einem alten Wert zu
            # verharren.
            self.index = -1
            self.percent = self.idle_percent
            self.last_change = 0.0

    def step(self, temp: float | None, now: float) -> int:
        """Einen Regelschritt rechnen und ausfuehren; gibt die Stufe in % zurueck."""
        if self.forced:
            return 100          # Volllast schlaegt die Kurve
        if temp is None:
            return self.percent
        target = next_step(self.steps, temp, self.index, self.hysteresis)
        if target != self.index:
            going_down = target < self.index
            if going_down and now - self.last_change < self.min_hold:
                return self.percent  # kurz warten, statt staendig zu pumpen
            self.index = target
            self.percent = percent_for_index(self.steps, target, self.idle_percent)
            self.last_change = now
            log(f"    {temp:.1f} C -> Stufe {self.percent} %")
            self.backend.set_percent(self.percent)
        return self.percent

    def write_status(self, temp: float | None) -> None:
        if not self.status_file:
            return
        measured = self.backend.read_percent()
        status = {
            "tempC": round(temp, 1) if temp is not None else None,
            "percent": measured if measured is not None else self.percent,
            "backend": self.backend.name,
            "controllable": self.backend.controllable,
            "force": self.forced,
            "updated": int(time.time()),
        }
        directory = os.path.dirname(os.path.abspath(self.status_file))
        try:
            os.makedirs(directory, exist_ok=True)
            tmp = self.status_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(status, fh)
            os.chmod(tmp, 0o644)
            os.replace(tmp, self.status_file)
        except OSError as err:
            log(f"[!] Statusdatei nicht schreibbar: {err}")

    def run(self) -> None:
        if not self.zone:
            log("[!] Keine Thermal-Zone gefunden - es gibt nichts zu messen.")
        while self.running:
            self.apply_override(self.read_override())
            temp = read_temperature(self.zone)
            self.step(temp, time.monotonic())
            self.write_status(temp)
            time.sleep(self.interval)

    def stop(self, *_args) -> None:
        self.running = False
        # Beim Beenden lieber auf voller Leistung stehen lassen als aus - so
        # kuehlt der Pi weiter, falls der Dienst nicht wieder hochkommt.
        try:
            self.backend.set_percent(100)
        finally:
            self.backend.close()


def load_config(path: str | None) -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser["fan"] = dict(DEFAULTS)
    if path:
        if not os.path.isfile(path):
            log(f"[!] {path} nicht gefunden - benutze die Standardwerte.")
        else:
            parser.read(path, encoding="utf-8")
    if not parser.has_section("fan"):
        parser.add_section("fan")
    return parser["fan"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Luefterkurve fuer den Raspberry Pi")
    parser.add_argument("--config", default="/etc/lanparty-fan.conf", help="Pfad zu fan.conf")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Nur messen und anzeigen, nichts an der Hardware aendern",
    )
    parser.add_argument("--once", action="store_true", help="Nur einmal messen und beenden")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    try:
        controller = Controller(config, dry_run=args.dry_run)
    except ValueError as err:
        log(f"[x] Konfigurationsfehler: {err}")
        return 1

    if args.once:
        controller.apply_override(controller.read_override())
        temp = read_temperature(controller.zone)
        percent = controller.step(temp, time.monotonic())
        controller.write_status(temp)
        log(f"Temperatur: {temp} C, Stufe: {percent} %, Verfahren: {controller.backend.name}")
        return 0

    signal.signal(signal.SIGTERM, controller.stop)
    signal.signal(signal.SIGINT, controller.stop)
    try:
        controller.run()
    finally:
        controller.backend.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
