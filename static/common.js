// Gemeinsame Helfer fuer Anzeige- und Admin-Seite.

export const TEAM_IDS = ['a', 'b'];

// Zeitversatz zwischen Pi und diesem Browser. Der Server schickt in jeder
// Nachricht seine eigene Uhrzeit mit; damit rechnen wir alle Uhren auf die
// Pi-Zeit um und die Anzeige stimmt auch auf einem Rechner mit falscher Uhr.
let clockOffset = 0;

export function noteServerTime(serverNow) {
  if (typeof serverNow === 'number') {
    clockOffset = serverNow - Date.now();
  }
}

export function serverNow() {
  return Date.now() + clockOffset;
}

/** Laeuft diese Uhr gerade? (0 ist ein gueltiger Startzeitpunkt, daher != null) */
export function isRunning(timer) {
  return Boolean(timer) && timer.runningSince != null;
}

/** Angezeigte Zeit einer Uhr: eingefrorener Anteil plus laufender Anteil. */
export function timerValue(timer, now = serverNow()) {
  if (!timer) return 0;
  let value = timer.elapsedMs || 0;
  if (isRunning(timer)) {
    value += Math.max(0, now - timer.runningSince);
  }
  return value;
}

/** mm:ss.z, ab einer Stunde h:mm:ss.z */
export function formatTime(ms, withTenths = true) {
  const total = Math.max(0, Math.floor(ms));
  const tenths = Math.floor((total % 1000) / 100);
  const seconds = Math.floor(total / 1000) % 60;
  const minutes = Math.floor(total / 60000) % 60;
  const hours = Math.floor(total / 3600000);
  const pad = (n) => String(n).padStart(2, '0');
  const base = hours > 0
    ? `${hours}:${pad(minutes)}:${pad(seconds)}`
    : `${pad(minutes)}:${pad(seconds)}`;
  return withTenths ? `${base}.${tenths}` : base;
}

/** Gesamtwertung - dieselbe Regel wie compute_standings() im Server. */
export function computeStandings(state, now = serverNow()) {
  const totals = { a: 0, b: 0 };
  const wins = { a: 0, b: 0 };
  const levels = state?.sos?.levels || [];

  for (const level of levels) {
    const values = {};
    for (const team of TEAM_IDS) {
      values[team] = timerValue(level.timers?.[team], now);
      totals[team] += values[team];
    }
    const bothDone = TEAM_IDS.every((t) => level.timers?.[t]?.finished);
    if (bothDone && values.a !== values.b) {
      wins[values.a < values.b ? 'a' : 'b'] += 1;
    }
  }

  let leader = null;
  if (wins.a !== wins.b) {
    leader = wins.a > wins.b ? 'a' : 'b';
  } else if (totals.a !== totals.b && (totals.a || totals.b)) {
    leader = totals.a < totals.b ? 'a' : 'b';
  }
  return { totals, levelWins: wins, leader };
}

/** Smash-Rangliste: meiste Siege, dann bessere Quote, dann weniger Niederlagen. */
export function sortPlayers(players) {
  return [...(players || [])].sort((x, y) => {
    if (y.wins !== x.wins) return y.wins - x.wins;
    const rate = (p) => (p.wins + p.losses ? p.wins / (p.wins + p.losses) : 0);
    if (rate(y) !== rate(x)) return rate(y) - rate(x);
    if (x.losses !== y.losses) return x.losses - y.losses;
    return x.name.localeCompare(y.name);
  });
}

export function winRate(player) {
  const played = (player.wins || 0) + (player.losses || 0);
  return played ? Math.round((player.wins / played) * 100) : 0;
}

// Wiederverbindung: schnell genug, dass ein Neustart des Dienstes auf dem
// Bildschirm im Raum kaum auffaellt, aber nicht so hektisch, dass ein laenger
// abwesender Server dauerhaft angeklopft wird.
const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 5000;

/**
 * Live-Verbindung zum Server. Nach einem Abbruch wird selbst neu verbunden -
 * der eingebaute Automatismus von EventSource wartet nach mehreren
 * Fehlschlaegen immer laenger, und dann steht die Anzeige minutenlang auf
 * "keine Verbindung", obwohl der Pi laengst wieder da ist.
 * Beim Verbinden schickt der Server sofort den vollen Zustand.
 */
export function connectEvents({ onState, onStatus }) {
  let source = null;
  let retryTimer = null;
  let retryDelay = RECONNECT_MIN_MS;

  const connect = () => {
    source = new EventSource('/api/events');
    source.onopen = () => {
      retryDelay = RECONNECT_MIN_MS;
      onStatus?.(true);
    };
    source.onerror = () => {
      onStatus?.(false);
      source.close();
      clearTimeout(retryTimer);
      retryTimer = setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 1.5, RECONNECT_MAX_MS);
    };
    source.onmessage = (event) => {
      let payload;
      try {
        payload = JSON.parse(event.data);
      } catch (err) {
        console.error('Unlesbare Nachricht vom Server:', err);
        return;
      }
      noteServerTime(payload.serverNow);
      onStatus?.(true);
      // Ein Fehler beim Zeichnen darf die naechste Nachricht nicht verhindern -
      // die Seite soll sich vom naechsten Update wieder fangen.
      try {
        onState?.(payload);
      } catch (err) {
        console.error('Fehler beim Aktualisieren der Seite:', err);
      }
    };
  };
  connect();
  return () => {
    clearTimeout(retryTimer);
    source?.close();
  };
}

/** POST an die API; wirft mit der Fehlermeldung des Servers. */
export async function api(path, body = {}) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  let data = {};
  try {
    data = await response.json();
  } catch (err) {
    /* leerer Body ist in Ordnung */
  }
  if (!response.ok) {
    throw new Error(data.error || `Fehler ${response.status}`);
  }
  return data;
}
