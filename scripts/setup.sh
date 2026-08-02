#!/usr/bin/env bash
#
# party-pi - ein Skript fuer alles.
#
#   sudo ./scripts/setup.sh              installieren bzw. aktualisieren
#   sudo ./scripts/setup.sh --status     nachsehen, was laeuft
#   sudo ./scripts/setup.sh --uninstall  wieder entfernen
#        ./scripts/setup.sh --no-services   nur vorbereiten (z.B. auf dem Laptop)
#
# Richtet ein:
#   - Kopie des Projekts nach /opt/lanparty
#   - Systembenutzer 'lanparty' fuer den Webdienst (laeuft nicht als root)
#   - Admin-Passwort und leeren Spielstand
#   - systemd-Dienst fuers Leaderboard und einen fuer die Luefterkurve
#   - optional Chromium im Vollbild-Kiosk
#
# Anpassbar ueber Umgebungsvariablen:
#   LAN_HTTP_PORT       Port der Weboberflaeche                (Standard 80)
#   LAN_ADMIN_PASSWORD  Admin-Passwort, sonst wird gefragt
#   LAN_PUBLIC_URL      Adresse im QR-Code, z.B. http://192.168.178.42
#   LAN_SETUP_KIOSK     1 = Chromium-Vollbild beim Booten      (Standard 0)
#   LAN_KIOSK_PAGE      Startseite des Kiosk                   (Standard /screen/tv)
#   LAN_KIOSK_USER      Benutzer der Desktop-Sitzung           (Standard: sudo-Aufrufer)
#   LAN_FAN_BACKEND     auto|trip-points|pwm|gpio|monitor      (Standard auto)
#   LAN_FAN_GPIO_PIN    BCM-Pin fuer backend=gpio
#   LAN_SKIP_TESTS      1 = Selbsttest ueberspringen           (Standard 0)
#
# Sicherheitshinweis: Das Leaderboard spricht reines HTTP ohne Verschluesselung
# und gehoert in das eigene, vertrauenswuerdige Heim-/Party-Netz. Das
# Admin-Passwort haelt mitspielende Gaeste von der Steuerung fern - es ist kein
# Schutz gegen einen Angreifer im selben Netz.

set -euo pipefail

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
HTTP_PORT="${LAN_HTTP_PORT:-80}"
PUBLIC_URL="${LAN_PUBLIC_URL:-}"
SETUP_KIOSK="${LAN_SETUP_KIOSK:-0}"
KIOSK_PAGE="${LAN_KIOSK_PAGE:-/screen/tv}"
FAN_BACKEND="${LAN_FAN_BACKEND:-auto}"
FAN_GPIO_PIN="${LAN_FAN_GPIO_PIN:-}"
SKIP_TESTS="${LAN_SKIP_TESTS:-0}"

INSTALL_DIR="/opt/lanparty"
DATA_DIR="${INSTALL_DIR}/data"
SERVICE_USER="lanparty"
WEB_SERVICE="lanparty-leaderboard"
FAN_SERVICE="lanparty-fan"
FAN_CONF="/etc/lanparty-fan.conf"

MODE="install"
case "${1:-}" in
  --status)      MODE="status" ;;
  --uninstall)   MODE="uninstall" ;;
  --no-services) MODE="prepare" ;;
  --help|-h)     sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
  "")            ;;
  *)             echo "Unbekannter Schalter: $1 (--help zeigt die Moeglichkeiten)" >&2; exit 1 ;;
esac

# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
log()  { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;35m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(dirname "$SCRIPT_DIR")"

address_of_pi() {
  hostname -I 2>/dev/null | awk '{print $1}' || true
}

url_base() {
  local address suffix=""
  address="$(address_of_pi)"
  [ -n "$address" ] || address="<Pi-IP-Adresse>"
  [ "$HTTP_PORT" != "80" ] && suffix=":${HTTP_PORT}"
  printf 'http://%s%s' "$address" "$suffix"
}

# ---------------------------------------------------------------------------
# --status : kurzer Gesundheitsbericht
# ---------------------------------------------------------------------------
if [ "$MODE" = "status" ]; then
  log "Zustand"
  for service in "$WEB_SERVICE" "$FAN_SERVICE"; do
    if systemctl is-active --quiet "$service" 2>/dev/null; then
      ok "$service laeuft"
    else
      warn "$service laeuft NICHT  (journalctl -u $service -n 30)"
    fi
  done
  [ -d "$INSTALL_DIR" ] && ok "Installiert in $INSTALL_DIR" || warn "Nicht installiert"
  if [ -f "${DATA_DIR}/fan-status.json" ]; then
    ok "Luefter: $(cat "${DATA_DIR}/fan-status.json")"
  else
    warn "Keine Luefter-Statusdatei - laeuft der Dienst?"
  fi
  echo
  echo "    Anzeige   : $(url_base)/"
  echo "    Steuerung : $(url_base)/admin"
  echo "    Mitmachen : $(url_base)/mitmachen"
  exit 0
fi

# ---------------------------------------------------------------------------
# --uninstall : Dienste weg, Daten auf Wunsch behalten
# ---------------------------------------------------------------------------
if [ "$MODE" = "uninstall" ]; then
  [ "$(id -u)" -eq 0 ] || die "Bitte als root ausfuehren (sudo)."
  log "Entferne die Dienste..."
  for service in "$WEB_SERVICE" "$FAN_SERVICE"; do
    systemctl disable --now "${service}.service" 2>/dev/null || true
    rm -f "/etc/systemd/system/${service}.service"
  done
  systemctl daemon-reload
  ok "Dienste entfernt."

  if [ -d "$DATA_DIR" ]; then
    printf 'Spielstand in %s ebenfalls loeschen? [j/N] ' "$DATA_DIR"
    read -r answer
    if [ "${answer:-n}" = "j" ] || [ "${answer:-n}" = "J" ]; then
      rm -rf "$INSTALL_DIR" "$FAN_CONF"
      ok "Alles entfernt."
    else
      rm -rf "${INSTALL_DIR:?}"/{leaderboard.py,qr.py,static,fan,tests,scripts,README.md} 2>/dev/null || true
      ok "Programm entfernt, $DATA_DIR behalten."
    fi
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
# 1. Vorbedingungen
# ---------------------------------------------------------------------------
log "Pruefe die Voraussetzungen..."
[ -f "$SOURCE_DIR/leaderboard.py" ] || die "leaderboard.py nicht gefunden - liegt das Skript im Ordner scripts/ des Projekts?"

command -v python3 >/dev/null 2>&1 || die "python3 wird benoetigt (auf Raspberry Pi OS vorinstalliert)."
PY_OK="$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 9) else 0)')"
[ "$PY_OK" = "1" ] || die "Python 3.9 oder neuer noetig (gefunden: $(python3 -V))."
ok "$(python3 -V) - zusaetzliche Pakete braucht es nicht."

HAVE_SYSTEMD=0
command -v systemctl >/dev/null 2>&1 && HAVE_SYSTEMD=1
[ "$MODE" = "prepare" ] && HAVE_SYSTEMD=0

if [ "$HAVE_SYSTEMD" = "1" ]; then
  [ "$(id -u)" -eq 0 ] || die "Bitte als root ausfuehren (sudo) - oder mit --no-services aufrufen."
fi

# ---------------------------------------------------------------------------
# 2. Selbsttest - lieber hier abbrechen als eine kaputte Installation abliefern
# ---------------------------------------------------------------------------
if [ "$SKIP_TESTS" = "1" ]; then
  warn "Selbsttest uebersprungen (LAN_SKIP_TESTS=1)."
else
  log "Selbsttest..."
  if (cd "$SOURCE_DIR" && python3 -m unittest discover -s tests >/tmp/lanparty-tests.log 2>&1); then
    ok "$(grep -oE 'Ran [0-9]+ tests' /tmp/lanparty-tests.log | tail -1) - alles gruen."
  else
    tail -20 /tmp/lanparty-tests.log >&2
    die "Selbsttest fehlgeschlagen (volles Protokoll: /tmp/lanparty-tests.log)."
  fi
fi

# ---------------------------------------------------------------------------
# 3. Nur vorbereiten (Laptop / ohne systemd)
# ---------------------------------------------------------------------------
if [ "$HAVE_SYSTEMD" = "0" ]; then
  LOCAL_DATA="${SOURCE_DIR}/data"
  mkdir -p "$LOCAL_DATA"
  if [ -n "${LAN_ADMIN_PASSWORD:-}" ]; then
    LAN_ADMIN_PASSWORD="$LAN_ADMIN_PASSWORD" \
      python3 "$SOURCE_DIR/leaderboard.py" --data "$LOCAL_DATA" --set-password >/dev/null
    ok "Admin-Passwort gesetzt."
  fi
  echo '{"force": false}' > "${LOCAL_DATA}/fan-override.json"
  echo
  log "Vorbereitet - ohne Dienste."
  echo "    Start:  python3 $SOURCE_DIR/leaderboard.py --port 8000 --data $LOCAL_DATA"
  echo "    Luefter: sudo python3 $SOURCE_DIR/fan/fan_control.py --config $SOURCE_DIR/fan/fan.conf.example --once --dry-run"
  exit 0
fi

# ---------------------------------------------------------------------------
# 4. Benutzer und Dateien
# ---------------------------------------------------------------------------
if id "$SERVICE_USER" >/dev/null 2>&1; then
  ok "Benutzer '$SERVICE_USER' ist schon da."
else
  log "Lege Systembenutzer '$SERVICE_USER' an..."
  useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

log "Kopiere das Projekt nach $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
# data/ bleibt bei einer erneuten Installation stehen - dort liegt der Spielstand.
for entry in leaderboard.py qr.py static fan tests scripts README.md; do
  [ -e "$SOURCE_DIR/$entry" ] || continue
  rm -rf "${INSTALL_DIR:?}/${entry}"
  cp -r "$SOURCE_DIR/$entry" "$INSTALL_DIR/"
done

mkdir -p "$DATA_DIR"
chown -R root:root "$INSTALL_DIR"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"
ok "Dateien liegen bereit."

# ---------------------------------------------------------------------------
# 5. Admin-Passwort
# ---------------------------------------------------------------------------
ADMIN_PASSWORD="${LAN_ADMIN_PASSWORD:-}"
if [ -z "$ADMIN_PASSWORD" ] && [ -f "$DATA_DIR/config.json" ] \
   && grep -q '"password"' "$DATA_DIR/config.json" 2>/dev/null; then
  ok "Admin-Passwort ist schon gesetzt - bleibt unveraendert."
else
  if [ -z "$ADMIN_PASSWORD" ]; then
    if [ -t 0 ]; then
      printf 'Admin-Passwort fuer die Steuerseite: '
      read -rs ADMIN_PASSWORD
      printf '\n'
    else
      die "Kein Passwort angegeben. LAN_ADMIN_PASSWORD setzen oder im Terminal starten."
    fi
  fi
  [ -n "$ADMIN_PASSWORD" ] || die "Leeres Passwort - abgebrochen."
  sudo -u "$SERVICE_USER" LAN_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
    python3 "$INSTALL_DIR/leaderboard.py" --data "$DATA_DIR" --set-password >/dev/null
  unset ADMIN_PASSWORD
  ok "Admin-Passwort gesetzt."
fi

# ---------------------------------------------------------------------------
# 6. Datenverzeichnis initialisieren
# ---------------------------------------------------------------------------
if [ ! -f "${DATA_DIR}/fan-override.json" ]; then
  echo '{"force": false}' > "${DATA_DIR}/fan-override.json"
fi
chown "$SERVICE_USER":"$SERVICE_USER" "${DATA_DIR}/fan-override.json"
chmod 664 "${DATA_DIR}/fan-override.json"
ok "Datenverzeichnis vorbereitet."

# ---------------------------------------------------------------------------
# 7. Luefter-Konfiguration
# ---------------------------------------------------------------------------
if [ -f "$FAN_CONF" ]; then
  ok "$FAN_CONF ist schon da - bleibt unveraendert."
else
  log "Lege $FAN_CONF an (backend=$FAN_BACKEND)..."
  sed \
    -e "s|^backend = .*|backend = ${FAN_BACKEND}|" \
    -e "s|^gpio_pin =.*|gpio_pin = ${FAN_GPIO_PIN}|" \
    -e "s|^status_file = .*|status_file = ${DATA_DIR}/fan-status.json|" \
    -e "s|^override_file = .*|override_file = ${DATA_DIR}/fan-override.json|" \
    "$SOURCE_DIR/fan/fan.conf.example" > "$FAN_CONF"
  chmod 644 "$FAN_CONF"
fi

# ---------------------------------------------------------------------------
# 8. systemd-Dienste
# ---------------------------------------------------------------------------
log "Richte die Dienste ein..."
cat > "/etc/systemd/system/${WEB_SERVICE}.service" <<EOF
[Unit]
Description=LAN-Party Leaderboard (Anzeige und Steuerung)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${INSTALL_DIR}
Environment=LAN_PUBLIC_URL=${PUBLIC_URL}
ExecStart=/usr/bin/env python3 ${INSTALL_DIR}/leaderboard.py --port ${HTTP_PORT} --data ${DATA_DIR}
Restart=always
RestartSec=2
# Port 80 darf ein normaler Benutzer sonst nicht oeffnen.
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=${DATA_DIR}

[Install]
WantedBy=multi-user.target
EOF

cat > "/etc/systemd/system/${FAN_SERVICE}.service" <<EOF
[Unit]
Description=LAN-Party Leaderboard - Luefterkurve
After=multi-user.target

[Service]
Type=simple
# Schaltpunkte, PWM und GPIO liegen unter /sys und brauchen root.
User=root
ExecStart=/usr/bin/env python3 ${INSTALL_DIR}/fan/fan_control.py --config ${FAN_CONF}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${WEB_SERVICE}.service"
systemctl enable --now "${FAN_SERVICE}.service"
sleep 1
systemctl is-active --quiet "${WEB_SERVICE}" && ok "$WEB_SERVICE laeuft." \
  || warn "$WEB_SERVICE startet nicht - journalctl -u $WEB_SERVICE -n 30"
systemctl is-active --quiet "${FAN_SERVICE}" && ok "$FAN_SERVICE laeuft." \
  || warn "$FAN_SERVICE startet nicht - journalctl -u $FAN_SERVICE -n 30"

# ---------------------------------------------------------------------------
# 9. Optional: Kiosk-Modus
# ---------------------------------------------------------------------------
setup_kiosk() {
  local kiosk_user="${LAN_KIOSK_USER:-${SUDO_USER:-}}"
  if [ -z "$kiosk_user" ] || [ "$kiosk_user" = "root" ]; then
    kiosk_user="$(getent passwd | awk -F: '$3 >= 1000 && $3 < 65000 {print $1; exit}')"
  fi
  [ -n "$kiosk_user" ] || { warn "Keinen Desktop-Benutzer gefunden - Kiosk uebersprungen."; return; }

  local home_dir
  home_dir="$(getent passwd "$kiosk_user" | cut -d: -f6)"
  [ -d "$home_dir" ] || { warn "Kein Home-Verzeichnis fuer '$kiosk_user' - Kiosk uebersprungen."; return; }

  local browser=""
  for candidate in chromium-browser chromium; do
    command -v "$candidate" >/dev/null 2>&1 && { browser="$candidate"; break; }
  done
  [ -n "$browser" ] || { warn "Chromium fehlt (apt install chromium-browser) - Kiosk uebersprungen."; return; }

  cat > "${INSTALL_DIR}/kiosk.sh" <<EOF
#!/usr/bin/env bash
# Startet die Anzeige im Vollbild. Wird von der Desktop-Sitzung aufgerufen.
set -u

# Warten, bis das Leaderboard antwortet - der Desktop ist oft schneller da.
for _ in \$(seq 1 60); do
  if exec 3<>/dev/tcp/127.0.0.1/${HTTP_PORT} 2>/dev/null; then exec 3>&-; break; fi
  sleep 1
done

# Bildschirmschoner und Energiesparen aus, soweit die Sitzung das kennt.
command -v xset >/dev/null 2>&1 && { xset s off; xset -dpms; xset s noblank; } 2>/dev/null || true

exec ${browser} --kiosk --noerrdialogs --disable-infobars --incognito \\
  --disable-session-crashed-bubble --check-for-update-interval=31536000 \\
  --start-fullscreen "http://localhost:${HTTP_PORT}${KIOSK_PAGE}"
EOF
  chmod 755 "${INSTALL_DIR}/kiosk.sh"

  local installed=0
  if [ -d "${home_dir}/.config/labwc" ]; then          # Pi OS Bookworm, Pi 5
    touch "${home_dir}/.config/labwc/autostart"
    grep -q "lanparty/kiosk.sh" "${home_dir}/.config/labwc/autostart" 2>/dev/null \
      || echo "${INSTALL_DIR}/kiosk.sh &" >> "${home_dir}/.config/labwc/autostart"
    installed=1; ok "Kiosk ueber labwc eingerichtet."
  fi
  if [ -f "${home_dir}/.config/wayfire.ini" ]; then    # Pi OS Bookworm, Pi 4
    if ! grep -q "lanparty/kiosk.sh" "${home_dir}/.config/wayfire.ini"; then
      grep -q '^\[autostart\]' "${home_dir}/.config/wayfire.ini" \
        || printf '\n[autostart]\n' >> "${home_dir}/.config/wayfire.ini"
      printf 'lanparty = %s/kiosk.sh\n' "$INSTALL_DIR" >> "${home_dir}/.config/wayfire.ini"
    fi
    installed=1; ok "Kiosk ueber wayfire eingerichtet."
  fi
  if [ "$installed" = "0" ] || [ -d "${home_dir}/.config/autostart" ]; then
    mkdir -p "${home_dir}/.config/autostart"
    cat > "${home_dir}/.config/autostart/lanparty-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=LAN-Party Leaderboard (Kiosk)
Exec=${INSTALL_DIR}/kiosk.sh
X-GNOME-Autostart-enabled=true
EOF
    installed=1; ok "Kiosk ueber XDG-Autostart eingerichtet."
  fi

  chown -R "$kiosk_user":"$kiosk_user" "${home_dir}/.config" 2>/dev/null || true
  [ "$installed" = "1" ] \
    && ok "Kiosk fuer '$kiosk_user' auf ${KIOSK_PAGE} - wirkt nach dem naechsten Neustart." \
    || warn "Desktop-Sitzung nicht erkannt. Von Hand: ${INSTALL_DIR}/kiosk.sh beim Anmelden starten."
}

[ "$SETUP_KIOSK" = "1" ] && setup_kiosk

# ---------------------------------------------------------------------------
# Fertig - Adressen und QR-Code zum sofortigen Ausprobieren
# ---------------------------------------------------------------------------
BASE="$(url_base)"
JOIN_URL="${PUBLIC_URL:+${PUBLIC_URL%/}}"
[ -n "$JOIN_URL" ] || JOIN_URL="$BASE"
JOIN_URL="${JOIN_URL}/mitmachen"

echo
log "Fertig."
echo "    Anzeige       ${BASE}/"
echo "    Steuerung     ${BASE}/admin"
echo "    Mitmachen     ${JOIN_URL}"
echo "    Alle Seiten   ${BASE}/seiten"
echo "    Bildschirm    ${BASE}/screen/<name>   (Name frei waehlbar)"
echo
echo "    Status    sudo $0 --status"
echo "    Logs      journalctl -u ${WEB_SERVICE} -f"
echo "    Luefter   journalctl -u ${FAN_SERVICE} -f   (zeigt das erkannte Verfahren)"
echo "    Kurve     ${FAN_CONF}"
echo

if [ "$(address_of_pi)" != "" ]; then
  echo "    Zum Ausprobieren einmal mit dem Handy abscannen:"
  echo
  python3 "$INSTALL_DIR/qr.py" --text "$JOIN_URL" | sed 's/^/    /'
  echo
fi

echo "    Nur im eigenen, vertrauenswuerdigen Netz betreiben - siehe Hinweis im Skriptkopf."
