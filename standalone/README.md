# Standalone

Zwei eigenständige Seiten, jede eine einzige HTML-Datei ohne Abhängigkeiten -
kein Python, kein Server, keine Installation. Einfach `index.html` öffnen
(Doppelklick oder `file://`) oder auf einem beliebigen Webspace ablegen.

Diese Variante ersetzt **nicht** die volle Pi-Version (`leaderboard.py`) mit
Live-Sync über mehrere Bildschirme, Spieler-Anmeldung per QR-Code, Lüfter- und
Bildschirmsteuerung. Sie ist der schnelle Weg, wenn kein Pi zur Hand ist oder
nur ein einzelner Bildschirm gebraucht wird.

## `leaderboard/`

Ein SOS-Leaderboard wie auf dem Pi - zwei Teams, Level mit zwei gleichzeitig
laufenden Uhren, Gesamtwertung. Über den Knopf **„Bearbeiten"** oben rechts
lassen sich Teamnamen, Level und Uhren direkt auf der Seite steuern; für den
Zuschauer-Blick auf einem Fernseher einfach wieder auf **„Fertig"** tippen.

Der Zustand liegt in `localStorage` dieses Browsers. Mehrere Tabs desselben
Browsers bleiben live synchron; ein zweites Gerät sieht ihn nicht - dafür
braucht es die Pi-Version mit echtem Server.

## `vibe/`

Stimmungs-/Ambient-Anzeige für die Pause zwischen zwei Runden. Mit erlaubtem
Mikrofon reagieren Partikel, der pulsierende Mittelring und die
Spektrum-Balken unten auf die Musik im Raum (Web Audio API,
`AnalyserNode.getByteFrequencyData`). Ohne Mikrofon - oder wenn der Zugriff
abgelehnt wird - läuft stattdessen eine ruhige, zeitgesteuerte Animation mit
denselben Farben; die Seite bleibt so in jedem Fall benutzbar.

Nichts wird aufgenommen oder verschickt: das Mikrofonsignal verlässt nie den
Browser. `F` schaltet Vollbild um (praktisch für Fernseher/Beamer),
Leertaste pausiert.

Bewusst **einfarbig Orange** gehalten (nur die Helligkeit variiert mit der
Musik) — Cyan ist im Cyberpunk-Theme der Pi-Version die Farbe von Team 2 und
gehört nicht auf eine Seite ohne Teams. Das Leaderboard daneben nutzt
weiterhin Orange/Cyan für Team 1/Team 2, wie auf dem Pi.
