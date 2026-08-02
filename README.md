# LAN-Party-Leaderboard

Live-Leaderboard und kleine Party-Infrastruktur für einen LAN-Abend, gedacht für einen
Raspberry Pi am Fernseher oder Beamer — im orangen Cyberpunk-Look.

* **SOS**: zwei Teams, pro Level laufen **zwei Uhren gleichzeitig**. Startet man ein neues
  Level, bleiben alle bisherigen Zeiten eingefroren stehen und darunter beginnt eine neue
  Zeile bei 0.
* **Smash Bros**: Rangliste nach Siegen, dazu Niederlagen und Siegquote.
* **Gäste melden sich per QR-Code selbst an** — Name eintragen, Team wählen. Die Anmeldung
  legt sie zugleich als Smash-Spieler an.
* **Mehrere Bildschirme**, jeder unter eigener Adresse. Was welcher zeigt, entscheidest du
  im Admin per Ziehen — mit Maus oder Finger.
* **Lüfterkurve** für den Pi, plus Schalter für dauerhafte Volllast.

Alles mit **Python aus der Standardbibliothek**: kein `pip`, kein `npm`, kein Build.
Auch der QR-Code wird selbst erzeugt.

---

## Installation

Ein Skript macht alles:

```bash
git clone <dieses-Repo>
cd claude_code
sudo ./LAN-Party-Leaderboard/scripts/setup.sh
```

Es prüft die Voraussetzungen, läuft einmal durch die Tests, legt Benutzer, Dienste und
Passwort an — und zeigt am Ende alle Adressen samt QR-Code direkt im Terminal, sodass du
gleich mit dem Handy testen kannst.

| Aufruf | Wirkung |
|---|---|
| `sudo ./scripts/setup.sh` | installieren bzw. aktualisieren |
| `sudo ./scripts/setup.sh --status` | läuft alles? welches Lüfterverfahren? welche Adresse? |
| `sudo ./scripts/setup.sh --uninstall` | Dienste entfernen (Spielstand auf Nachfrage behalten) |
| `./scripts/setup.sh --no-services` | nur vorbereiten, z. B. auf dem Laptop |

Anpassbar über Umgebungsvariablen:

| Variable | Bedeutung | Standard |
|---|---|---|
| `LAN_HTTP_PORT` | Port der Weboberfläche | `80` |
| `LAN_ADMIN_PASSWORD` | Passwort, sonst wird gefragt | – |
| `LAN_PUBLIC_URL` | Adresse im QR-Code, z. B. `http://192.168.178.42` | selbst ermittelt |
| `LAN_SETUP_KIOSK` | `1` = Chromium-Vollbild beim Booten | `0` |
| `LAN_KIOSK_PAGE` | Startseite des Kiosk | `/screen/tv` |
| `LAN_FAN_BACKEND` | `auto`/`trip-points`/`pwm`/`gpio`/`monitor` | `auto` |
| `LAN_FAN_GPIO_PIN` | BCM-Pin für `backend=gpio` | – |

Beispiel — Port 8080, Lüfter an GPIO 14, Kiosk auf dem Leaderboard:

```bash
sudo LAN_HTTP_PORT=8080 LAN_FAN_BACKEND=gpio LAN_FAN_GPIO_PIN=14 \
     LAN_SETUP_KIOSK=1 LAN_KIOSK_PAGE=/ \
     ./LAN-Party-Leaderboard/scripts/setup.sh
```

### Vorher auf dem Laptop ausprobieren

```bash
cd LAN-Party-Leaderboard
python3 leaderboard.py --port 8000 --data ./data
```

Beim ersten Start wird ein zufälliges Admin-Passwort erzeugt und **einmalig im Terminal
angezeigt**. Ändern: `python3 leaderboard.py --data ./data --set-password`

---

## Die Seiten

| Adresse | Was drauf ist |
|---|---|
| `/` | Leaderboard: SOS mit beiden Uhren, Smash-Rangliste |
| `/qr` | großer QR-Code zum Mitmachen, plus Adresse zum Abtippen |
| `/mitmachen` | Anmeldung für Gäste (Name + Team), fürs Handy |
| `/teams` | Kader: wer ist in welchem Team |
| `/system` | Pi-Terminal: Temperatur, Lüfter, Laufzeit, Adresse, verbundene Geräte |
| `/info` | Info-Tafel — WLAN, Ablauf, Regeln; Text im Admin bearbeitbar |
| `/seiten` | Übersicht über alle Seiten |
| `/screen/<name>` | gesteuerter Bildschirm (siehe unten) |
| `/admin` | Steuerung, Passwort nötig |

---

## Bedienung am Abend

### SOS

1. Oben die **Teamnamen** eintragen (speichert beim Verlassen des Feldes).
2. **„Neues Level starten"** — optional mit Namen. Beide Uhren laufen sofort los.
3. Schafft ein Team das Level: **„Fertig"** bei diesem Team. Seine Uhr bleibt stehen,
   die des anderen läuft weiter.
4. Nächstes Level: wieder **„Neues Level starten"**. Alles Bisherige friert genau so ein,
   wie es steht.

Verklickt? Unter *Alle Level* lässt sich jede Zeit überschreiben (`mm:ss.z`, auch `1:23`
oder `83` werden verstanden), jedes Level umbenennen, löschen und jede Uhr einzeln
starten oder stoppen.

**Wer führt:** wer mehr Level gewonnen hat. Ein Level zählt erst, wenn **beide** Teams
dort auf „Fertig" stehen — sonst wäre das gerade schnellere Team schon vor dem Ziel
Sieger. Bei gleichem Punktestand entscheidet die kleinere Gesamtzeit.

### Gäste anmelden

`/qr` auf einen Bildschirm legen. Wer scannt, landet auf `/mitmachen`, trägt seinen Namen
ein und wählt ein Team — fertig. Öffnet dieselbe Person die Seite später erneut, sieht sie
ihren Eintrag und kann Name oder Team ändern, statt einen Doppelgänger anzulegen (das
merkt sich ein Cookie).

**Ergebnisse setzt weiterhin nur der Admin.** Gäste können ausschließlich ihren eigenen
Eintrag ändern — keine Zeiten, keine Siege, keine Bildschirme.

### Smash

Match eintragen: Sieger und Verlierer wählen, ein Klick setzt beides. Einzelne
`+1`/`−1`-Knöpfe gibt es für Korrekturen. Über die zwei kleinen Team-Knöpfe lässt sich
jeder Spieler einem Team zuordnen (nochmal antippen löst die Zuordnung wieder).

### Bildschirme steuern

Auf dem Gerät einmal `http://<pi>/screen/tv` öffnen — der Name ist frei wählbar. Der
Bildschirm meldet sich selbst an und erscheint im Admin unter *Bildschirme*. Dort eine
Ansicht auf die Kachel **ziehen** (Maus oder Finger) oder **antippen und dann die Kachel
antippen**. Der Wechsel ist sofort da.

Solange nichts zugewiesen ist, zeigt der Bildschirm seinen eigenen Namen groß an — so
weißt du, welche Kachel zu welchem Gerät gehört. Kacheln ohne Lebenszeichen werden nach
zwei Minuten als „offline" markiert.

### Effekte und Lüfter

Im Admin unter *Anzeige & Pi*:

* **Effekte**: „Volles Programm" (Scanlines, Flackern, Glitch beim Levelwechsel,
  driftendes Raster) oder „Schlicht". Gilt sofort für **alle** Geräte.
* **Lüfter**: „100 % (an)" lässt den Lüfter unabhängig von der Temperatur durchlaufen —
  praktisch, wenn es im Raum warm wird. Findet der Dienst keinen steuerbaren Lüfter, steht
  das statt einer wirkungslosen Schaltfläche dran.

Gemessen auf einem 1080p-Bildschirm kosten die Effekte **keine messbare zusätzliche
Rechenzeit** (0,65 s gegenüber 0,88 s CPU je 12 s, über mehrere Durchgänge in beiden
Reihenfolgen): Alle Animationen laufen ausschließlich über `transform` und `opacity` und
damit im Compositor. Wer trotzdem lieber Ruhe hat, schaltet auf „Schlicht".
`prefers-reduced-motion` wird unabhängig davon immer beachtet.

### Gut zu wissen

* Die Steuerseite darf **auf mehreren Geräten gleichzeitig** offen sein.
* Uhren laufen auf Basis von Zeitstempeln, nicht als hochzählender Zähler: Seite neu
  laden, WLAN-Aussetzer oder ein Neustart des Dienstes ändern die angezeigten Zeiten
  nicht.
* Die Anzeige zeigt die letzten 9 Level; ältere werden zu einer Hinweiszeile
  zusammengefasst. Gesamtzeit und gewonnene Level zählen natürlich alle.
* Die Uhr des anzeigenden Rechners muss nicht stimmen — der Zeitversatz zum Pi wird
  herausgerechnet.

---

## Lüfterkurve

Eigener Dienst (`fan/fan_control.py`), Einstellungen in `/etc/lanparty-fan.conf`.
Standardkurve:

```
50 °C → 30 %    60 °C → 50 %    70 °C → 75 %    75 °C → 100 %
```

Dazu 4 K Hysterese und eine Mindesthaltezeit, damit der Lüfter an der Schaltgrenze nicht
dauernd auf- und abdreht. Hochgeschaltet wird immer sofort.

```bash
sudo systemctl restart lanparty-fan
journalctl -u lanparty-fan -f      # zeigt Verfahren, Temperatur und Stufe
```

### Welches Verfahren?

Der Dienst sucht es sich beim Start selbst; `backend=` in der Konfiguration überschreibt
die Erkennung.

| Verfahren | Wofür | Erkennung |
|---|---|---|
| `trip-points` | Pi 5 mit offiziellem Active Cooler und alles, was der Kernel selbst regelt | **automatisch** |
| `pwm` | PWM-Lüfter an GPIO12/13/18/19 über `/sys/class/pwm` | nur mit `backend=pwm` |
| `gpio` | einfacher Lüfter über Transistor, nur an/aus | nur mit `gpio_pin=` |
| `monitor` | kein steuerbarer Lüfter gefunden: nur beobachten | Rückfallebene |

Beim offiziellen Kühler regelt der Kernel den Lüfter selbst. Ein direkt geschriebenes
`cur_state` würde er sofort wieder überschreiben — deshalb verschiebt der Dienst dort
stattdessen die **Schaltpunkte der Thermal-Zone**. Das ist der einzige Weg, der hält.
Dieselbe Mechanik nutzt der Volllast-Schalter: er zieht die Schaltpunkte kurzzeitig ganz
nach unten und stellt danach die Kurve wieder her.

Für einen Lüfter an einem GPIO **muss** der Pin in der Konfiguration stehen:

```ini
[fan]
backend = gpio
gpio_pin = 14
```

Ohne Änderung an der Hardware ausprobieren:

```bash
sudo python3 fan/fan_control.py --config /etc/lanparty-fan.conf --once --dry-run
```

---

## Prüfen und Fehler suchen

```bash
cd LAN-Party-Leaderboard && python3 -m unittest discover -s tests   # 178 Tests, ~7 s
sudo ./scripts/setup.sh --status
journalctl -u lanparty-leaderboard -f
```

| Problem | Wo nachsehen |
|---|---|
| Anzeige zeigt „keine Verbindung" | läuft der Dienst? `systemctl status lanparty-leaderboard` |
| Uhren stehen still | Seite neu laden; der Stand liegt auf dem Pi, es geht nichts verloren |
| QR-Code führt ins Leere | `LAN_PUBLIC_URL` setzen — der Pi rät die Adresse sonst selbst |
| Bildschirm bleibt leer | zeigt er seinen Namen? Dann ist er angemeldet und wartet auf eine Zuweisung im Admin |
| Lüfter dreht nicht | `journalctl -u lanparty-fan` — steht dort `Verfahren: monitor`, wurde kein steuerbarer Lüfter gefunden |
| Passwort vergessen | `sudo -u lanparty python3 /opt/lanparty/leaderboard.py --data /opt/lanparty/data --set-password` |
| Kiosk startet nicht | `/opt/lanparty/kiosk.sh` von Hand starten und die Ausgabe ansehen |

Spielstand und Konfiguration liegen in `/opt/lanparty/data/` (`state.json`,
`config.json`). Zum Aufheben einfach `state.json` kopieren.

---

## Aufbau

```
leaderboard.py     HTTP-Server: Seiten, API, Live-Stream (SSE), Zustand, Anmeldung
qr.py              QR-Encoder (Reed-Solomon, Maskierung, SVG) - eigenständig nutzbar
static/            alle Seiten; theme.css trägt Farben und Effekte, kein Framework
fan/fan_control.py Lüfterdienst mit Erkennung des Ansteuerungsverfahrens
scripts/setup.sh   Installation, Status, Deinstallation
tests/             Tests für Wertung, Uhren, HTTP, Seiten, Anmeldung, QR und Lüfter
```

Der Server hält den Zustand im Speicher und schreibt ihn nach jeder Änderung atomar nach
`data/state.json`. Alle offenen Seiten hängen an einem Server-Sent-Events-Strom und
bekommen jede Änderung sofort; die Uhren tickt der Browser selbst weiter, damit die
Anzeige flüssig bleibt.

`qr.py` lässt sich auch allein benutzen:

```bash
python3 qr.py "http://beispiel/mitmachen" > code.svg
python3 qr.py --text "http://beispiel/mitmachen"     # als ASCII-Bild im Terminal
```

---

## Sicherheit

Das Leaderboard spricht **reines HTTP ohne Verschlüsselung** und gehört in das eigene,
vertrauenswürdige Heim- oder Party-Netz. Das Admin-Passwort hält mitspielende Gäste von
der Steuerung fern — es ist **kein** Schutz gegen einen Angreifer im selben Netz, und
nichts davon gehört ungeschützt ins Internet.

Umgesetzt sind: Passwort als PBKDF2-Hash (`config.json` nur für den Besitzer lesbar),
signiertes Sitzungs-Cookie mit Ablauf (`HttpOnly`, `SameSite=Strict`), gebremste
Anmeldeversuche, Herkunftsprüfung bei schreibenden Anfragen und getrennte Cookies für
Admin und Gäste. Das Gäste-Cookie berechtigt ausschließlich dazu, den **eigenen** Eintrag
zu ändern.

Bewusst ohne Passwort erreichbar sind nur zwei Dinge: die Spieler-Anmeldung (das ist der
Sinn des QR-Codes, begrenzt auf 100 Spieler mit Drosselung pro Adresse) und die
Selbstanmeldung eines Bildschirms (begrenzt auf 20). Der Webdienst läuft als eigener
Benutzer ohne besondere Rechte; nur die Lüftersteuerung braucht root, weil die
Schaltpunkte unter `/sys` liegen.
