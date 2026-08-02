// Gesteuerter Bildschirm.
//
// Der Name steht im Pfad (/screen/tv). Die Seite meldet sich damit selbst an
// und zeigt danach die Ansicht, die der Admin ihr zuweist.

import { api, connectEvents } from '/static/common.js';

// Wie oft sich der Bildschirm meldet. Der Server markiert ihn nach zwei
// Minuten Stille als offline - 45 s lassen also Luft fuer einen Ausfall.
const HELLO_INTERVAL_MS = 45_000;
const FADE_MS = 350;

const screenId = decodeURIComponent(window.location.pathname.replace(/^\/screen\//, ''))
  .toLowerCase()
  .replace(/[^a-z0-9_-]/g, '')
  .slice(0, 24);

const frame = document.getElementById('frame');
const idle = document.getElementById('idle');
const status = document.getElementById('status');

document.getElementById('name').textContent = screenId || '?';
document.title = `${screenId || 'Bildschirm'} – LAN-Party`;

let currentView = null;

async function sayHello() {
  try {
    await api('/api/screens/hello', { id: screenId });
  } catch (err) {
    // Nicht schlimm: beim naechsten Mal wieder. Der Bildschirm zeigt
    // weiterhin, was er hat.
    console.warn('Anmeldung des Bildschirms fehlgeschlagen:', err.message);
  }
}

/**
 * Ansicht wechseln - aber nur, wenn sie sich wirklich geaendert hat.
 * Jede Zustandsaenderung im Haus schickt eine Nachricht an alle Seiten; wuerde
 * die Huelle daraufhin stur neu laden, flackerte der Fernseher den ganzen
 * Abend.
 */
function showView(view) {
  if (view === currentView) return;
  currentView = view;

  if (!view) {
    frame.hidden = true;
    frame.removeAttribute('src');
    idle.hidden = false;
    return;
  }

  idle.hidden = true;
  if (frame.hidden) {
    frame.hidden = false;
    frame.src = view;
    return;
  }

  frame.classList.add('fading');
  setTimeout(() => {
    frame.src = view;
    frame.classList.remove('fading');
  }, FADE_MS);
}

connectEvents({
  onState: (payload) => {
    const screen = (payload.state?.screens || []).find((s) => s.id === screenId);
    showView(screen ? screen.view : null);
  },
  onStatus: (online) => {
    status.textContent = online ? screenId : 'keine Verbindung';
    status.classList.toggle('online', online);
    status.classList.toggle('offline', !online);
  },
});

if (screenId) {
  sayHello();
  setInterval(sayHello, HELLO_INTERVAL_MS);
} else {
  document.getElementById('name').textContent = 'Ungültiger Name';
}
