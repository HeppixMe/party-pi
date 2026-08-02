# LAN-Party-Leaderboard

Live-Leaderboard für einen LAN-Abend, gedacht für einen Raspberry Pi am Fernseher
oder Beamer:

* **Anzeige-Seite** (`/`) – läuft im Vollbild auf dem Bildschirm im Raum und
  aktualisiert sich von selbst. Kein Klicken, kein Neuladen.
* **Steuerseite** (`/admin`) – passwortgeschützt, funktioniert auch auf dem Handy
  im selben WLAN.
* **Lüfterkurve** – eigener kleiner Dienst, der den Pi früher kühlt als die
  Werkseinstellung.

Zwei Wertungen:

* **S.O.S.** – zwei Teams, pro Level laufen **zwei Uhren gleichzeitig**. Startet
  man ein neues Level, bleiben alle bisherigen Zeiten eingefroren stehen und
  darunter beginnt eine neue Zeile bei 0.
* **Smash Bros** – Rangliste nach Siegen, dazu Niederlagen und Siegquote.

Alles läuft mit **Python aus der Standardbibliothek**: kein `pip install`, kein
`npm`, kein Build-Schritt. Auf einem frischen Raspberry Pi OS sofort startklar –
und am Party-Abend zur Not in zwei Minuten reparierbar.

---

## Schnellstart auf dem Pi

```bash
git clone <dieses-Repo>
cd claude_code
sudo ./LAN-Party-Leaderboard/scripts/install-raspberrypi.sh
```

Das Skript fragt nach einem Admin-Passwort und richtet alles ein: Kopie nach
`/opt/lanparty`, einen eigenen Systembenutzer, zwei systemd-Dienste und optional
den Kiosk-Modus. Danach:

| | |
|---|---|
| Anzeige (Beamer) | `http://<pi-ip>/` |
| Steuerung | `http://<pi-ip>/admin` |

Nützliche Schalter:

```bash
# Port 8080 statt 80, Kiosk-Modus, Lüfter an GPIO 14
sudo LAN_HTTP_PORT=8080 LAN_SETUP_KIOSK=1 \
     LAN_FAN_BACKEND=gpio LAN_FAN_GPIO_PIN=14 \
     ./LAN-Party-Leaderboard/scripts/install-raspberrypi.sh
```

| Variable | Bedeutung | Standard |
|---|---|---|
| `LAN_HTTP_PORT` | Port der Weboberfläche | `80` |
| `LAN_ADMIN_PASSWORD` | Passwort, sonst wird gefragt | – |
| `LAN_SETUP_KIOSK` | `1` = Chromium-Vollbild beim Booten | `0` |
| `LAN_KIOSK_USER` | Benutzer der Desktop-Sitzung | der sudo-Aufrufer |
| `LAN_FAN_BACKEND` | `auto`/`trip-points`/`pwm`/`gpio`/`monitor` | `auto` |
| `LAN_FAN_GPIO_PIN` | BCM-Pin für `backend=gpio` | – |
| `LAN_SKIP_SERVICES` | `1` = nur kopieren, keine Dienste | `0` |

### Vorher auf dem Laptop ausprobieren

```bash
cd LAN-Party-Leaderboard
python3 leaderboard.py --port 8000 --data ./data
```

Beim ersten Start wird ein zufälliges Admin-Passwort erzeugt und **einmalig im
Terminal angezeigt**. Änderbar mit:

```bash
python3 leaderboard.py --data ./data --set-password
```

---

## Bedienung am Abend

### S.O.S.

1. Oben die beiden **Teamnamen** eintragen (speichert beim Verlassen des Feldes).
2. **„Neues Level starten"** – optional mit Namen, sonst „Level 1", „Level 2", …
   Beide Uhren laufen sofort los.
3. Schafft ein Team das Level: **„Fertig"** bei diesem Team. Seine Uhr bleibt
   stehen, die des anderen Teams läuft weiter.
4. Nächstes Level: wieder **„Neues Level starten"**. Alles Bisherige friert genau
   so ein, wie es steht.

Verklickt? Unter *Alle Level* lässt sich jede Zeit direkt überschreiben
(`mm:ss.z`, auch `1:23` oder `83` werden verstanden), jedes Level umbenennen,
löschen und jede Uhr einzeln starten/stoppen.

**Wer führt:** Wer mehr Level gewonnen hat. Ein Level zählt erst, wenn **beide**
Teams dort auf „Fertig" stehen – sonst wäre das gerade schnellere Team schon vor
dem Ziel Sieger. Bei gleichem Punktestand entscheidet die kleinere Gesamtzeit.

### Smash Bros

Spieler eintragen, dann pro Partie **„Match eintragen"** (Sieger + Verlierer in
einem Schritt). Einzelne `+1`/`−1`-Knöpfe gibt es für Korrekturen. Sortiert wird
nach Siegen, dann Siegquote, dann weniger Niederlagen.

### Gut zu wissen

* Die Steuerseite darf **auf mehreren Geräten gleichzeitig** offen sein – alle
  sehen sofort denselben Stand.
* Uhren laufen auf Basis von Zeitstempeln, nicht als hochzählender Zähler:
  Seite neu laden, WLAN-Aussetzer oder ein Neustart des Dienstes ändern die
  angezeigten Zeiten nicht.
* Die Anzeige zeigt die letzten 9 Level; ältere werden als Hinweiszeile
  zusammengefasst. Gesamtzeit und gewonnene Level zählen natürlich alle.
* Die Uhr des anzeigenden Rechners muss nicht stimmen – der Zeitversatz zum Pi
  wird herausgerechnet.

---

## Lüfterkurve

Eigener Dienst (`fan/fan_control.py`), Einstellungen in `/etc/lanparty-fan.conf`.
Standardkurve:

```
50 °C → 30 %    60 °C → 50 %    70 °C → 75 %    75 °C → 100 %
```

Dazu 4 K Hysterese und eine Mindesthaltezeit, damit der Lüfter an der
Schaltgrenze nicht dauernd auf- und abdreht. Hochgeschaltet wird immer sofort.

Nach jeder Änderung:

```bash
sudo systemctl restart lanparty-fan
journalctl -u lanparty-fan -f      # zeigt Verfahren, Temperatur und Stufe
```

### Welches Verfahren wird benutzt?

Der Dienst sucht sich das passende beim Start selbst; `backend=` in der
Konfiguration überschreibt die Erkennung.

| Verfahren | Wofür | Erkennung |
|---|---|---|
| `trip-points` | Pi 5 mit offiziellem Active Cooler und alles, was der Kernel selbst regelt | **automatisch** |
| `pwm` | PWM-Lüfter an GPIO12/13/18/19 über `/sys/class/pwm` | nur mit `backend=pwm` |
| `gpio` | einfacher Lüfter über Transistor, nur an/aus | nur mit `gpio_pin=` |
| `monitor` | kein steuerbarer Lüfter gefunden: nur beobachten | Rückfallebene |

Beim offiziellen Kühler regelt der Kernel den Lüfter selbst. Ein direkt
geschriebenes `cur_state` würde er sofort wieder überschreiben – deshalb
verschiebt der Dienst dort stattdessen die **Schaltpunkte der Thermal-Zone**.
Das ist der einzige Weg, der dauerhaft hält.

Für einen Lüfter an einem GPIO **muss** der Pin in der Konfiguration stehen –
raten kann den kein Programm:

```ini
[fan]
backend = gpio
gpio_pin = 14
```

Für `backend = pwm` zusätzlich `dtoverlay=pwm,pin=18,func=2` (o. ä.) in
`/boot/firmware/config.txt` eintragen und neu starten.

Ohne Änderung an der Hardware ausprobieren:

```bash
sudo python3 fan/fan_control.py --config /etc/lanparty-fan.conf --once --dry-run
```

---

## Prüfen und Fehler suchen

```bash
# Alle Tests (keine Hardware nötig, ~2 s)
cd LAN-Party-Leaderboard && python3 -m unittest discover -s tests

systemctl status lanparty-leaderboard lanparty-fan
journalctl -u lanparty-leaderboard -f
```

| Problem | Wo nachsehen |
|---|---|
| Anzeige zeigt „keine Verbindung" | läuft der Dienst? `systemctl status lanparty-leaderboard` |
| Uhren stehen still | Steuerseite neu laden; der Stand liegt auf dem Pi, es geht nichts verloren |
| Lüfter dreht nicht | `journalctl -u lanparty-fan` – steht dort `Verfahren: monitor`, wurde kein steuerbarer Lüfter gefunden (bei GPIO-Lüftern `gpio_pin` setzen) |
| Passwort vergessen | `sudo -u lanparty python3 /opt/lanparty/leaderboard.py --data /opt/lanparty/data --set-password` |
| Kiosk startet nicht | `/opt/lanparty/kiosk.sh` von Hand starten und die Ausgabe ansehen |

Spielstand und Konfiguration liegen in `/opt/lanparty/data/`
(`state.json`, `config.json`). Zum Aufheben einfach `state.json` kopieren.

---

## Aufbau

```
leaderboard.py     HTTP-Server: API, Live-Stream (SSE), Zustand, Anmeldung
static/            Anzeige- und Steuerseite (kein Framework, keine Abhängigkeiten)
fan/fan_control.py Lüfter-Dienst mit Erkennung des Ansteuerungsverfahrens
scripts/           Installationsskript für den Pi
tests/             Tests für Wertung, Uhren, HTTP und Lüfterkurve
```

Der Server hält den Zustand im Speicher und schreibt ihn nach jeder Änderung
atomar nach `data/state.json`. Alle offenen Seiten hängen an einem
Server-Sent-Events-Stream und bekommen jede Änderung sofort; die Uhren tickt der
Browser selbst weiter, damit die Anzeige flüssig bleibt.

---

## Sicherheit

Das Leaderboard spricht **reines HTTP ohne Verschlüsselung** und gehört in das
eigene, vertrauenswürdige Heim- oder Party-Netz. Das Admin-Passwort hält
mitspielende Gäste von der Steuerung fern – es ist **kein** Schutz gegen einen
Angreifer im selben Netz und nichts davon gehört ungeschützt ins Internet.

Umgesetzt sind: Passwort als PBKDF2-Hash (`config.json` nur für den Besitzer
lesbar), signiertes Sitzungs-Cookie mit Ablauf (`HttpOnly`, `SameSite=Strict`),
gebremste Anmeldeversuche und eine Herkunftsprüfung bei schreibenden Anfragen.
Der Webdienst läuft als eigener Benutzer ohne Rechte; nur die Lüftersteuerung
braucht root, weil die Schaltpunkte unter `/sys` liegen.
