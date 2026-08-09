// Gesteuerter Bildschirm.
//
// Der Name steht im Pfad (/screen/tv). Die Seite meldet sich damit selbst an
// und zeigt danach die Ansicht, die der Admin ihr zuweist - eine einzelne
// oder, bei einem geteilten Bildschirm, zwei nebeneinander.

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
const halves = document.getElementById('halves');
const frameLeft = document.getElementById('frame-left');
const frameRight = document.getElementById('frame-right');
const idle = document.getElementById('idle');
const status = document.getElementById('status');

document.getElementById('name').textContent = screenId || '?';
document.title = `${screenId || 'Bildschirm'} – LAN-Party`;

// Welche der drei Anzeigen gerade zu sehen ist - noetig, um beim Wechsel
// zwischen ihnen (z.B. geteilt -> einzeln) zuverlaessig neu aufzubauen,
// auch wenn sich die Ansicht selbst zufaellig nicht geaendert hat.
let mode = null;       // 'idle' | 'single' | 'split'
let currentView = null;
let currentLeft = null;
let currentRight = null;

async function sayHello() {
  try {
    await api('/api/screens/hello', { id: screenId });
  } catch (err) {
    // Nicht schlimm: beim naechsten Mal wieder. Der Bildschirm zeigt
    // weiterhin, was er hat.
    console.warn('Anmeldung des Bildschirms fehlgeschlagen:', err.message);
  }
}

function showIdle() {
  if (mode === 'idle') return;
  mode = 'idle';
  currentView = null;
  halves.hidden = true;
  frame.hidden = true;
  frame.removeAttribute('src');
  idle.hidden = false;
}

/**
 * Einzelne Ansicht zeigen - das iframe wird nur neu geladen, wenn sich die
 * Ansicht wirklich geaendert hat. Jede Zustandsaenderung im Haus schickt
 * eine Nachricht an alle Seiten; wuerde die Huelle daraufhin stur neu
 * laden, flackerte der Fernseher den ganzen Abend.
 */
function showView(view) {
  const changingMode = mode !== 'single';
  mode = 'single';
  halves.hidden = true;
  idle.hidden = true;

  if (view === currentView && !changingMode) return;
  currentView = view;

  if (changingMode || frame.hidden) {
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

/** Geteilter Bildschirm: zwei Ansichten nebeneinander, je iframe nur bei
 * Aenderung neu geladen - derselbe Grund wie bei showView(). */
function showSplit(left, right) {
  const changingMode = mode !== 'split';
  mode = 'split';
  idle.hidden = true;
  frame.hidden = true;
  frame.removeAttribute('src');
  halves.hidden = false;

  if (left !== currentLeft || changingMode) {
    currentLeft = left;
    frameLeft.src = left;
  }
  if (right !== currentRight || changingMode) {
    currentRight = right;
    frameRight.src = right;
  }
}

function applyScreen(screen) {
  if (!screen) showIdle();
  else if (screen.split) showSplit(screen.split.left, screen.split.right);
  else showView(screen.view);
}

connectEvents({
  onState: (payload) => {
    const screen = (payload.state?.screens || []).find((s) => s.id === screenId);
    applyScreen(screen);
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
