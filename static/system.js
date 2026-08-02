// Systemseite: Werte vom Pi als Terminal-Ausgabe.
// Die Systemwerte werden abgefragt, Spieler- und Bildschirmzahl kommen ueber
// den Live-Strom - dafuer braucht es keine zusaetzliche Abfrage.

import { connectEvents } from '/static/common.js';

const $ = (id) => document.getElementById(id);
const conn = $('conn');

function formatUptime(seconds) {
  if (seconds == null) return 'unbekannt';
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor(seconds / 3600) % 24;
  const minutes = Math.floor(seconds / 60) % 60;
  if (days) return `${days} d ${hours} h ${minutes} min`;
  if (hours) return `${hours} h ${minutes} min`;
  return `${minutes} min`;
}

async function pollSystem() {
  try {
    const status = await (await fetch('/api/system')).json();
    const fan = status.fan || {};

    const temp = typeof fan.tempC === 'number' ? fan.tempC : null;
    $('temp').textContent = temp === null ? 'kein Sensor' : `${temp.toFixed(1)} °C`;
    // Ab etwa 80 Grad drosselt der Pi sich selbst - das soll ins Auge fallen.
    $('temp').className = temp === null ? '' : temp >= 75 ? 'warn' : 'good';

    if (typeof fan.percent === 'number') {
      $('fan').textContent = fan.force ? `${fan.percent} % · VOLLLAST` : `${fan.percent} %`;
      $('fan').className = fan.force ? 'warn' : '';
    } else {
      $('fan').textContent = 'unbekannt';
      $('fan').className = '';
    }
    $('backend').textContent = fan.backend || 'unbekannt';

    $('uptime').textContent = formatUptime(status.uptimeSeconds);
    $('load').textContent = status.load
      ? status.load.map((value) => value.toFixed(2)).join('   ')
      : 'unbekannt';
    $('address').textContent = status.address || 'unbekannt';
    $('connections').textContent = status.connections ?? '–';
  } catch (err) {
    $('temp').textContent = 'nicht erreichbar';
    $('temp').className = 'warn';
  }
}

connectEvents({
  onState: (payload) => {
    const state = payload.state || {};
    $('players').textContent = (state.players || []).length;
    $('screens').textContent = (state.screens || []).length;
  },
  onStatus: (online) => {
    conn.textContent = online ? 'live' : 'keine Verbindung';
    conn.classList.toggle('online', online);
    conn.classList.toggle('offline', !online);
  },
});

pollSystem();
setInterval(pollSystem, 3000);
