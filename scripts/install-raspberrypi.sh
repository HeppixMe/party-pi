#!/usr/bin/env bash
#
# LAN-Party-Leaderboard - Installation auf einem Raspberry Pi.
#
# Richtet in einem Rutsch alles ein:
#   - Kopie des Projekts nach /opt/lanparty
#   - Systembenutzer 'lanparty' fuer den Webserver (laeuft nicht als root)
#   - Admin-Passwort fuer die Steuerseite
#   - systemd-Dienst fuer das Leaderboard (Anzeige + Steuerung)
#   - systemd-Dienst fuer die Luefterkurve (laeuft als root, braucht sysfs)
#   - optional: Chromium im Vollbild-Kiosk auf die Anzeige-Seite
#
# Aufruf (als root bzw. via sudo):
#   sudo ./LAN-Party-Leaderboard/scripts/install-raspberrypi.sh
#
# Anpassbar ueber Umgebungsvariablen:
#   LAN_HTTP_PORT       Port der Weboberflaeche                 (Standard 80)
#   LAN_ADMIN_PASSWORD  Admin-Passwort, sonst wird gefragt
#   LAN_SETUP_KIOSK     1 = Chromium-Vollbild beim Booten       (Standard 0)
#   LAN_KIOSK_USER      Benutzer der Desktop-Sitzung            (Standard: der sudo-Aufrufer)
#   LAN_FAN_BACKEND     auto|trip-points|pwm|gpio|monitor       (Standard auto)
#   LAN_FAN_GPIO_PIN    BCM-Pin fuer backend=gpio
#   LAN_SKIP_SERVICES   1 = nur kopieren, keine Dienste         (Standard 0)
#
# Beispiel: Leaderboard auf Port 8080, Luefter an GPIO 14, Kiosk an:
#   sudo LAN_HTTP_PORT=8080 LAN_FAN_BACKEND=gpio LAN_FAN_GPIO_PIN=14 \
#        LAN_SETUP_KIOSK=1 ./scripts/install-raspberrypi.sh
#
# Sicherheitshinweis: Das Leaderboard spricht reines HTTP ohne Verschluesselung.
# Es gehoert in das eigene, vertrauenswuerdige Heim-/Party-Netz. Das Admin-
# Passwort haelt mitspielende Gaeste von der Steuerung fern - es ist kein
# Schutz gegen einen Angreifer im selben Netz.

set -euo pipefail

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
HTTP_PORT="${LAN_HTTP_PORT:-80}"
SETUP_KIOSK="${LAN_SETUP_KIOSK:-0}"
SKIP_SERVICES="${LAN_SKIP_SERVICES:-0}"
FAN_BACKEND="${LAN_FAN_BACKEND:-auto}"
FAN_GPIO_PIN="${LAN_FAN_GPIO_PIN:-}"

INSTALL_DIR="/opt/lanparty"
DATA_DIR="${INSTALL_DIR}/data"
SERVICE_USER="lanparty"
WEB_SERVICE="lanparty-leaderboard"
FAN_SERVICE="lanparty-fan"
FAN_CONF="/etc/lanparty-fan.conf"

# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(dirname "$SCRIPT_DIR")"

# ---------------------------------------------------------------------------
# Vorbedingungen
# ---------------------------------------------------------------------------
[ "$(id -u)" -eq 0 ] || die "Bitte als root ausfuehren (z.B. mit 'sudo')."
[ -f "$SOURCE_DIR/leaderboard.py" ] || die "leaderboard.py nicht gefunden - liegt das Skript unter LAN-Party-Leaderboard/scripts/?"

command -v python3 >/dev/null 2>&1 || die "python3 wird benoetigt (auf Raspberry Pi OS vorinstalliert)."
PY_OK="$(python3 -c 'import sys; print(1 if sys.version_info >= (3, 9) else 0)')"
[ "$PY_OK" = "1" ] || die "Python 3.9 oder neuer wird benoetigt (gefunden: $(python3 -V))."
log "Python: $(python3 -V) - zusaetzliche Pakete sind nicht noetig."

command -v systemctl >/dev/null 2>&1 || SKIP_SERVICES=1

# ---------------------------------------------------------------------------
# 1. Benutzer und Verzeichnisse
# ---------------------------------------------------------------------------
if id "$SERVICE_USER" >/dev/null 2>&1; then
  log "Benutzer '$SERVICE_USER' ist bereits vorhanden."
else
  log "Lege Systembenutzer '$SERVICE_USER' an..."
  useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

log "Kopiere das Projekt nach $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"
# data/ bleibt beim erneuten Installieren erhalten - dort liegt der Spielstand.
for entry in leaderboard.py static fan tests README.md; do
  [ -e "$SOURCE_DIR/$entry" ] || continue
  rm -rf "${INSTALL_DIR:?}/${entry}"
  cp -r "$SOURCE_DIR/$entry" "$INSTALL_DIR/"
done

mkdir -p "$DATA_DIR"
chown -R root:root "$INSTALL_DIR"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$DATA_DIR"
chmod 750 "$DATA_DIR"

# ---------------------------------------------------------------------------
# 2. Admin-Passwort
# ---------------------------------------------------------------------------
ADMIN_PASSWORD="${LAN_ADMIN_PASSWORD:-}"
if [ -z "$ADMIN_PASSWORD" ] && [ -f "$DATA_DIR/config.json" ] \
   && grep -q '"password"' "$DATA_DIR/config.json" 2>/dev/null; then
  log "Admin-Passwort ist bereits gesetzt - bleibt unveraendert."
  log "Aenderbar mit: sudo -u $SERVICE_USER python3 $INSTALL_DIR/leaderboard.py --data $DATA_DIR --set-password"
else
  if [ -z "$ADMIN_PASSWORD" ]; then
    if [ -t 0 ]; then
      printf 'Admin-Passwort fuer die Steuerseite: '
      read -rs ADMIN_PASSWORD
      printf '\n'
    else
      die "Kein Passwort angegeben. LAN_ADMIN_PASSWORD setzen oder das Skript im Terminal starten."
    fi
  fi
  [ -n "$ADMIN_PASSWORD" ] || die "Leeres Passwort - abgebrochen."
  log "Setze das Admin-Passwort..."
  sudo -u "$SERVICE_USER" LAN_ADMIN_PASSWORD="$ADMIN_PASSWORD" \
    python3 "$INSTALL_DIR/leaderboard.py" --data "$DATA_DIR" --set-password >/dev/null
  unset ADMIN_PASSWORD
fi

# ---------------------------------------------------------------------------
# 3. Luefter-Konfiguration
# ---------------------------------------------------------------------------
if [ -f "$FAN_CONF" ]; then
  log "$FAN_CONF ist bereits vorhanden - bleibt unveraendert."
else
  log "Lege $FAN_CONF an (backend=$FAN_BACKEND)..."
  sed \
    -e "s|^backend = .*|backend = ${FAN_BACKEND}|" \
    -e "s|^gpio_pin =.*|gpio_pin = ${FAN_GPIO_PIN}|" \
    -e "s|^status_file = .*|status_file = ${DATA_DIR}/fan-status.json|" \
    "$SOURCE_DIR/fan/fan.conf.example" > "$FAN_CONF"
  chmod 644 "$FAN_CONF"
fi

if [ "$SKIP_SERVICES" = "1" ]; then
  log "Ueberspringe die Dienste (LAN_SKIP_SERVICES=1 oder kein systemd)."
  log "Manuell startbar mit:"
  echo "    sudo -u $SERVICE_USER python3 $INSTALL_DIR/leaderboard.py --port $HTTP_PORT --data $DATA_DIR"
  echo "    sudo python3 $INSTALL_DIR/fan/fan_control.py --config $FAN_CONF"
  exit 0
fi

# ---------------------------------------------------------------------------
# 4. systemd-Dienst: Leaderboard
# ---------------------------------------------------------------------------
log "Richte den Dienst '$WEB_SERVICE' ein (Port $HTTP_PORT)..."
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

# ---------------------------------------------------------------------------
# 5. systemd-Dienst: Luefterkurve
# ---------------------------------------------------------------------------
log "Richte den Dienst '$FAN_SERVICE' ein..."
cat > "/etc/systemd/system/${FAN_SERVICE}.service" <<EOF
[Unit]
Description=LAN-Party Leaderboard - Luefterkurve
After=multi-user.target

[Service]
Type=simple
# Schaltpunkte und PWM/GPIO liegen unter /sys und brauchen root.
User=root
ExecStart=/usr/bin/env python3 ${INSTALL_DIR}/fan/fan_control.py --config ${FAN_CONF}
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

log "Starte die Dienste..."
systemctl daemon-reload
systemctl enable --now "${WEB_SERVICE}.service"
systemctl enable --now "${FAN_SERVICE}.service"

# ---------------------------------------------------------------------------
# 6. Optional: Kiosk-Modus auf dem angeschlossenen Bildschirm
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
  [ -n "$browser" ] || { warn "Chromium ist nicht installiert (apt install chromium-browser) - Kiosk uebersprungen."; return; }

  local url="http://localhost:${HTTP_PORT}/"
  cat > "${INSTALL_DIR}/kiosk.sh" <<EOF
#!/usr/bin/env bash
# Startet die Anzeige im Vollbild. Wird von der Desktop-Sitzung aufgerufen.
set -u

# Warten, bis das Leaderboard antwortet (der Desktop ist oft schneller da).
for _ in \$(seq 1 60); do
  if exec 3<>/dev/tcp/127.0.0.1/${HTTP_PORT} 2>/dev/null; then exec 3>&-; break; fi
  sleep 1
done

# Bildschirmschoner und Energiesparen aus, soweit die Sitzung das kennt.
command -v xset >/dev/null 2>&1 && { xset s off; xset -dpms; xset s noblank; } 2>/dev/null || true

exec ${browser} --kiosk --noerrdialogs --disable-infobars --incognito \\
  --disable-session-crashed-bubble --check-for-update-interval=31536000 \\
  --start-fullscreen "${url}"
EOF
  chmod 755 "${INSTALL_DIR}/kiosk.sh"

  local installed=0
  # labwc (Raspberry Pi OS Bookworm auf dem Pi 5)
  if [ -d "${home_dir}/.config/labwc" ]; then
    touch "${home_dir}/.config/labwc/autostart"
    grep -q "lanparty/kiosk.sh" "${home_dir}/.config/labwc/autostart" 2>/dev/null \
      || echo "${INSTALL_DIR}/kiosk.sh &" >> "${home_dir}/.config/labwc/autostart"
    installed=1
    log "Kiosk ueber labwc-autostart eingerichtet."
  fi
  # wayfire (Raspberry Pi OS Bookworm auf dem Pi 4)
  if [ -f "${home_dir}/.config/wayfire.ini" ]; then
    if ! grep -q "lanparty/kiosk.sh" "${home_dir}/.config/wayfire.ini"; then
      grep -q '^\[autostart\]' "${home_dir}/.config/wayfire.ini" \
        || printf '\n[autostart]\n' >> "${home_dir}/.config/wayfire.ini"
      printf 'lanparty = %s/kiosk.sh\n' "$INSTALL_DIR" >> "${home_dir}/.config/wayfire.ini"
    fi
    installed=1
    log "Kiosk ueber wayfire.ini eingerichtet."
  fi
  # X11/LXDE und alles, was den XDG-Autostart benutzt
  if [ "$installed" = "0" ] || [ -d "${home_dir}/.config/autostart" ]; then
    mkdir -p "${home_dir}/.config/autostart"
    cat > "${home_dir}/.config/autostart/lanparty-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=LAN-Party Leaderboard (Kiosk)
Exec=${INSTALL_DIR}/kiosk.sh
X-GNOME-Autostart-enabled=true
EOF
    installed=1
    log "Kiosk ueber XDG-Autostart eingerichtet."
  fi

  chown -R "$kiosk_user":"$kiosk_user" "${home_dir}/.config" 2>/dev/null || true

  if [ "$installed" = "1" ]; then
    log "Kiosk fuer Benutzer '$kiosk_user' eingerichtet - wirkt nach dem naechsten Neustart."
  else
    warn "Desktop-Sitzung nicht erkannt. Von Hand: ${INSTALL_DIR}/kiosk.sh beim Anmelden starten."
  fi
}

if [ "$SETUP_KIOSK" = "1" ]; then
  setup_kiosk
fi

# ---------------------------------------------------------------------------
# Fertig
# ---------------------------------------------------------------------------
IP_ADDR="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$IP_ADDR" ] || IP_ADDR="<Pi-IP-Adresse>"
URL_SUFFIX=""
[ "$HTTP_PORT" != "80" ] && URL_SUFFIX=":${HTTP_PORT}"

FAN_MODE="$(journalctl -u "${FAN_SERVICE}" -n 50 --no-pager 2>/dev/null | grep -o 'Verfahren: [a-z-]*' | tail -1 || true)"

echo
log "Installation abgeschlossen."
echo "    Anzeige (Beamer):  http://${IP_ADDR}${URL_SUFFIX}/"
echo "    Steuerung:         http://${IP_ADDR}${URL_SUFFIX}/admin"
echo
echo "    Status:   systemctl status ${WEB_SERVICE} ${FAN_SERVICE}"
echo "    Logs:     journalctl -u ${WEB_SERVICE} -f"
echo "    Luefter:  journalctl -u ${FAN_SERVICE} -f   ${FAN_MODE:+(${FAN_MODE})}"
echo "    Kurve:    ${FAN_CONF}  (danach: systemctl restart ${FAN_SERVICE})"
echo
echo "    Nur im eigenen, vertrauenswuerdigen Netz betreiben - siehe Hinweis oben."
