# party-pi

Live-Leaderboard und kleine Party-Infrastruktur für einen LAN-Abend, gedacht für einen
Raspberry Pi am Fernseher oder Beamer — im orangen Cyberpunk-Look.

* **SOS**: zwei Teams, pro Level laufen **zwei Uhren gleichzeitig**. Startet man ein neues
  Level, bleiben alle bisherigen Zeiten eingefroren stehen und darunter beginnt eine neue
  Zeile bei 0.
* **Smash Bros**: Rangliste nach Siegen, dazu Niederlagen und Siegquote.
* **Gäste melden sich per QR-Code selbst an** — Name eintragen, Team wählen. Die Anmeldung
  legt sie zugleich als Smash-Spieler an.
* **Eigene Leaderboards**: im Admin beliebig viele weitere Spiele anlegen — mit eigenem
  Namen, wahlweise als Zeitrennen oder Sieg/Niederlage-Rangliste, mit Teams oder
  Einzelspielern.
* **Mehrere Bildschirme**, jeder unter eigener Adresse. Was welcher zeigt — SOS, Smash,
  ein eigenes Leaderboard oder eine der anderen Seiten — entscheidest du im Admin per
  Ziehen — mit Maus oder Finger.
* **Lüfterkurve** für den Pi, plus Schalter für dauerhafte Volllast.

Alles mit **Python aus der Standardbibliothek**: kein `pip`, kein `npm`, kein Build.
Auch der QR-Code wird selbst erzeugt. **Kein Passwort, keine Anmeldung** — `/admin` ist
offen wie jede andere Seite, damit der Einstieg so einfach wie möglich ist.

---

## Schnellstart (Windows, Laptop, ohne Pi)

Kein Raspberry Pi zur Hand? Auf jedem Rechner mit Python reicht das hier:

* **Windows**: Projekt herunterladen (oder `git clone`), dann **`Windows-Starten.bat`**
  doppelklicken. Sucht Python, startet den Server in einem eigenen Fenster und öffnet die
  Steuerung im Browser — fertig.
* **macOS / Linux**:
  ```bash
  python3 leaderboard.py --port 8000 --data ./data
  ```
  und `http://localhost:8000/admin` öffnen.

Beide Wege starten sofort ohne Passwort. Andere Geräte im selben WLAN erreichen die Seite
über die im Terminal/Server-Fenster angezeigte Adresse (z. B. `http://192.168.1.23:8000/`).
Fragt Windows nach der Firewall, „Zugriff erlauben" antippen — nur so finden Handys im
selben Netz die Mitmach-Seite.

Für einen dauerhaften Aufbau auf dem Raspberry Pi mit eigenem Systemdienst, Kiosk-Modus und
Lüftersteuerung: weiter mit der [Installation](#installation) unten. Wer nicht einmal
Python installieren will, findet unter [`standalone/`](standalone/) zwei Seiten, die ganz
ohne Server auskommen.

---

## Installation

Ein Skript macht alles:

```bash
git clone https://github.com/HeppixMe/party-pi.git
cd party-pi
sudo ./scripts/setup.sh
```

Später auf den neuesten Stand bringen:

```bash
cd party-pi && git pull && sudo ./scripts/setup.sh
```

Ein erneuter Durchlauf lässt Spielstand und Lüfterkonfiguration unangetastet — er tauscht
nur das Programm aus und startet die Dienste neu.

Es prüft die Voraussetzungen, läuft einmal durch die Tests, legt Benutzer und Dienste an —
und zeigt am Ende alle Adressen samt QR-Code direkt im Terminal, sodass du gleich mit dem
Handy testen kannst.

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
| `LAN_PUBLIC_URL` | Adresse im QR-Code, z. B. `http://192.168.178.42` | selbst ermittelt |
| `LAN_SETUP_KIOSK` | `1` = Chromium-Vollbild beim Booten | `0` |
| `LAN_KIOSK_PAGE` | Startseite des Kiosk | `/screen/tv` |
| `LAN_FAN_BACKEND` | `auto`/`trip-points`/`pwm`/`gpio`/`monitor` | `auto` |
| `LAN_FAN_GPIO_PIN` | BCM-Pin für `backend=gpio` | – |

Beispiel — Port 8080, Lüfter an GPIO 14, Kiosk auf dem Leaderboard:

```bash
sudo LAN_HTTP_PORT=8080 LAN_FAN_BACKEND=gpio LAN_FAN_GPIO_PIN=14 \
     LAN_SETUP_KIOSK=1 LAN_KIOSK_PAGE=/ \
     ./scripts/setup.sh
```

### Vorher auf dem Laptop ausprobieren

```bash
python3 leaderboard.py --port 8000 --data ./data
```

Startet sofort ohne Passwort — siehe auch den [Schnellstart](#schnellstart-windows-laptop-ohne-pi)
oben, der genau das per Doppelklick für Windows anbietet.

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
| `/board/<id>` | ein selbst angelegtes Leaderboard (siehe „Eigene Leaderboards") |
| `/admin` | Steuerung — kein Passwort nötig |
| `/kurzbefehle` | Kurzbefehle zusammenstecken (siehe unten) |
| `/run/<id>` | führt einen Kurzbefehl aus und leitet auf `/admin` weiter |

---

## Bedienung am Abend

### SOS

1. Oben die **Teamnamen** eintragen (speichert beim Verlassen des Feldes).
2. **„Neues Level starten"** — optional mit Namen. Alles Bisherige friert genau so ein,
   wie es steht, die beiden neuen Uhren stehen aber erstmal auf 0 und laufen noch nicht.
3. **„Beide Uhren starten"** löst beide gleichzeitig aus, sobald es wirklich losgeht — oder
   die einzelnen **„Start"**-Knöpfe je Team, wenn ein Team später dran ist als das andere.
4. Schafft ein Team das Level: **„Fertig"** bei diesem Team. Seine Uhr bleibt stehen,
   die des anderen läuft weiter.
5. Nächstes Level: wieder **„Neues Level starten"**.

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

Es gibt kein separates Admin-Passwort: `/admin` ist für alle im selben Netz offen, genau
wie die Mitmach-Seite. Das ist bewusst so einfach gehalten — auf einem Party-Abend im
eigenen, vertrauenswürdigen WLAN ist das kein Problem. Wer das nicht möchte, öffnet `/admin`
einfach nicht auf einem Gerät, das Gäste in die Hand bekommen.

### Smash

Match eintragen: Sieger und Verlierer wählen, ein Klick setzt beides. Einzelne
`+1`/`−1`-Knöpfe gibt es für Korrekturen. Über die zwei kleinen Team-Knöpfe lässt sich
jeder Spieler einem Team zuordnen (nochmal antippen löst die Zuordnung wieder).

### Kader

Im Admin unter *Kader* stehen alle Spieler in drei Spalten — Team 1, Team 2, Ohne Team —
und lassen sich zwischen ihnen **verschieben**: Karte auf eine andere Spalte ziehen (Maus
oder Finger) oder antippen und danach die Zielspalte antippen. Praktisch, um Teams am
Abend neu zu mischen, ohne für jeden Spieler einzeln durch die Smash-Liste zu suchen. Die
Zuordnung ist dieselbe wie bei den Team-Knöpfen unter *Smash* — beide Wege ändern
denselben Wert, betrifft also auch, wie Team-Leaderboards die Spieler zählen.

### Eigene Leaderboards

Im Admin unter *Eigene Leaderboards* lässt sich für **jedes weitere Spiel** ein eigenes
Leaderboard anlegen — SOS und Smash Bros bleiben davon unberührt und sind immer da.

1. **Name des Spiels** eintragen, z. B. „Mario Kart" oder „Schach".
2. **Typ** wählen:
   * **Rennen (Zeit-Uhren)** — genau wie SOS: zwei Teams, Level mit zwei gleichzeitig
     laufenden Uhren.
   * **Rangliste (Sieg/Niederlage)** — genau wie Smash: Teilnehmer mit Siegen, Niederlagen
     und Siegquote.
3. **Modus** wählen: **Teams** oder **Einzelspieler**. Ein Zeitrennen braucht zwei
   vergleichbare Bahnen und ist deshalb immer im Team-Modus.
4. **„Leaderboard erstellen"** — die Karte erscheint sofort oben in der Liste und wird
   genauso bedient wie SOS bzw. Smash: Level starten/stoppen/korrigieren bzw. Teilnehmer
   hinzufügen und Punkte vergeben.

Jedes eigene Leaderboard bekommt automatisch eine eigene Seite (**„Ansehen ↗"** öffnet sie)
und taucht in der Ansichten-Palette bei *Bildschirme* auf — einem Fernseher zuweisen
funktioniert genauso wie bei den fest eingebauten Ansichten. Über **„Löschen"** verschwindet
ein Leaderboard wieder vollständig (Bildschirme, die es gerade zeigten, fallen auf das
Haupt-Leaderboard zurück); **„Level leeren"** bzw. **„Punkte auf 0"** setzt nur den Inhalt
dieses einen Leaderboards zurück, ohne die anderen zu berühren.

Werden es viele Leaderboards, hilft **„Anzeige"** oben auf der Steuerseite: damit lässt
sich gezielt nur S.O.S., nur Smash Bros oder nur ein bestimmtes eigenes Leaderboard
einblenden. Die Auswahl ist serverseitig gespeichert und gilt an zwei Stellen zugleich —
im Admin blendet sie die anderen Leaderboards nur hier aus, auf der Haupt-Leaderboard-Seite
(dem Fernseher unter `/`) bestimmt sie tatsächlich, was dort läuft. Wird das gerade
angezeigte eigene Leaderboard gelöscht, springt die Auswahl automatisch auf „Alle" zurück.

### Bildschirme steuern

Auf dem Gerät einmal `http://<pi>/screen/tv` öffnen — der Name ist frei wählbar. Der
Bildschirm meldet sich selbst an und erscheint im Admin unter *Bildschirme*. Eine Ansicht
zuweisen geht auf drei Wegen:

* **Ziehen** (Maus oder Finger) von der Ansichten-Palette auf die Kachel,
* **Antippen und dann die Kachel antippen** — der Weg fürs Handy ohne Ziehen,
* oder direkt bei der Kachel aus der **Liste „zeigt:“** wählen — die zuverlässigste
  Variante, falls sich Ziehen auf einem Gerät mal launisch anfühlt.

Der Wechsel ist bei allen drei Wegen sofort da. Solange nichts zugewiesen ist, zeigt der
Bildschirm seinen eigenen Namen groß an — so weißt du, welche Kachel zu welchem Gerät
gehört. Kacheln ohne Lebenszeichen werden nach zwei Minuten als „offline" markiert.

### Kurzbefehle

Unter `/kurzbefehle` (auch von der Steuerseite aus verlinkt) lassen sich eigene
Bausteinfolgen zusammenstecken — wie bei Scratch: Baustein aus der Palette links **in den
Ablauf rechts ziehen oder antippen**, in beliebiger Reihenfolge, mit Pfeil-Knöpfen wieder
umsortierbar. Verfügbare Bausteine:

| Baustein | Wirkung |
|---|---|
| Bildschirm zuweisen | weist einer Ansicht einen Bildschirm zu |
| Leaderboard erstellen | legt ein neues eigenes Leaderboard an |
| SOS-Level starten | legt ein Level im eingebauten SOS-Rennen an (Häkchen „Uhren sofort starten“ optional) |
| SOS-Uhren starten | startet beide Uhren des zuletzt angelegten SOS-Levels |
| Level in eigenem Leaderboard starten | legt ein Level in einem Renn-Leaderboard an (dieselbe Option) |
| Uhren in eigenem Leaderboard starten | startet beide Uhren des zuletzt angelegten Levels dort |
| Effekte umschalten | volles Programm oder schlicht |
| Info-Tafel setzen | ersetzt den Text auf der Info-Tafel |
| Zurücksetzen | SOS, Smash, Spieler oder alles |
| Eigenes Leaderboard zurücksetzen | nur für ein bestimmtes Leaderboard |

Level starten sich standardmäßig nicht mehr von selbst — genau wie im Admin warten die
Uhren auf einen eigenen Start (Häkchen setzen oder den passenden „…Uhren starten“-Baustein
direkt danach einreihen, z. B. für ein „Party-Start“, das ein Level anlegt und die Uhren im
selben Rutsch loslaufen lässt).

Nach dem Speichern bekommt jeder Kurzbefehl eine eigene Adresse (`/run/<id>`, über
„Kopieren“ neben dem Kurzbefehl greifbar). Sie beim **Aufrufen** aus — ohne dass die Seite
selbst irgendetwas anzeigt — und leitet danach auf `/admin` weiter. Praktisch als
Lesezeichen, als Kachel auf dem Handy-Homescreen oder als Knopf auf einem Stream Deck:
„Party-Start“ könnte z. B. den Beamer auf den QR-Code legen, den Fernseher aufs
Leaderboard und in einem Rutsch ein neues Turnier-Leaderboard anlegen. Ein Baustein, der
auf einen inzwischen entfernten Bildschirm oder ein gelöschtes Leaderboard zeigt, wird beim
Ausführen einfach übersprungen — die übrigen Bausteine laufen trotzdem.

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
python3 -m unittest discover -s tests      # 223 Tests, ~10 s
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
| Kiosk startet nicht | `/opt/lanparty/kiosk.sh` von Hand starten und die Ausgabe ansehen |
| Windows: `Windows-Starten.bat` findet Python nicht | Python von [python.org](https://www.python.org/downloads/) installieren, dabei „Add python.exe to PATH" anhaken |

Spielstand und Konfiguration liegen in `/opt/lanparty/data/` (`state.json`,
`config.json`). Zum Aufheben einfach `state.json` kopieren.

---

## Aufbau

```
leaderboard.py       HTTP-Server: Seiten, API, Live-Stream (SSE), Zustand
qr.py                QR-Encoder (Reed-Solomon, Maskierung, SVG) - eigenständig nutzbar
static/              alle Seiten; theme.css trägt Farben und Effekte, kein Framework
fan/fan_control.py   Lüfterdienst mit Erkennung des Ansteuerungsverfahrens
scripts/setup.sh     Installation, Status, Deinstallation (Raspberry Pi)
Windows-Starten.bat  Ein-Klick-Start für Windows, ohne Installation
standalone/          zwei Seiten ganz ohne Server (Leaderboard, Ambient-Anzeige)
tests/               Tests für Wertung, Uhren, HTTP, Seiten, Spieler-Anmeldung, QR, Lüfter und Kurzbefehle
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

Das Leaderboard spricht **reines HTTP ohne Verschlüsselung und ohne Anmeldung** und gehört
in das eigene, vertrauenswürdige Heim- oder Party-Netz. Jeder im selben Netz kann die
Steuerung bedienen, Spielstände ändern und Bildschirme umschalten — bewusst so einfach
gehalten, genau wie am Spieltisch nebenan. Nichts davon gehört ungeschützt ins Internet.

Umgesetzt sind trotzdem: Herkunftsprüfung (Origin) bei allen schreibenden Anfragen gegen
fremde Webseiten, ein signiertes Cookie für die Spieler-Wiedererkennung (`HttpOnly`,
`SameSite=Lax`, der Signierschlüssel liegt nur für den Besitzer lesbar in `config.json`)
und eine gebremste Anmeldung über `/mitmachen`, damit dort niemand die Spielerliste
vollspammen kann. Der Webdienst läuft als eigener Benutzer ohne besondere Rechte; nur die
Lüftersteuerung braucht root, weil die Schaltpunkte unter `/sys` liegen.

Wer eine echte Zugriffsbeschränkung braucht (offenes Gäste-WLAN, öffentlicher Raum), stellt
am besten einen Reverse-Proxy mit eigener Anmeldung davor, statt sich auf das Leaderboard
selbst zu verlassen — dafür ist es nicht gebaut.
