// Fernbedienung: alle gespeicherten Kurzbefehle als grosse, antippbare
// Knoepfe - fuers Handy in der Hosentasche, ohne erst ins Admin zu wechseln.
// Loest per Fetch auf /run/<id> aus, bleibt dabei auf dieser Seite (die
// eigentliche /run/-Route leitet sonst auf /admin weiter, das interessiert
// hier niemanden).
//
// Die Knoepfe lassen sich zusaetzlich per Ziehen umsortieren (Maus oder
// Finger, Pointer-Events wie bei den Bildschirmen/dem Kader) - Tippen ohne
// Bewegung fuehrt den Kurzbefehl aus, Ziehen sortiert um. Die Reihenfolge
// wird auf dem Server gespeichert (/api/shortcuts/reorder) und gilt dann
// auch fuer die Kurzbefehl-Liste im Admin - es gibt nur eine Reihenfolge.

import { api, connectEvents, shortcutNeedsNamePrompt } from '/static/common.js';

const $ = (id) => document.getElementById(id);
const DRAG_THRESHOLD_PX = 6;

let activeDrag = false;
let lastShortcutsKey = null;

function toast(message, kind = 'ok') {
  const node = $('toast');
  node.textContent = message;
  node.className = kind;
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.hidden = true; }, kind === 'error' ? 5000 : 2000);
}

async function runShortcut(button, shortcut) {
  // Fragt der Kurzbefehl vor dem Levelstart erst nach einem Namen, muss
  // diese Seite dorthin wechseln, statt still im Hintergrund abzurufen -
  // sonst sieht niemand die Eingabeseite.
  if (shortcutNeedsNamePrompt(shortcut)) {
    location.href = `/run/${shortcut.id}`;
    return;
  }
  button.disabled = true;
  try {
    await fetch(`/run/${shortcut.id}`);
    toast(`„${shortcut.name}“ ausgeführt`);
  } catch (err) {
    toast('Konnte Kurzbefehl nicht ausführen', 'error');
  } finally {
    button.disabled = false;
  }
}

async function saveOrder() {
  const order = [...$('remote-grid').children].map((button) => button.dataset.id);
  try {
    await api('/api/shortcuts/reorder', { order });
  } catch (err) {
    toast(err.message, 'error');
  }
}

/** Ziehen (Maus oder Finger) an einem Fernbedienung-Knopf. */
function startDrag(button, event, shortcut) {
  const startX = event.clientX;
  const startY = event.clientY;
  let dragging = false;

  button.setPointerCapture(event.pointerId);

  const move = (moveEvent) => {
    const dx = moveEvent.clientX - startX;
    const dy = moveEvent.clientY - startY;
    if (!dragging && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;

    if (!dragging) {
      dragging = true;
      activeDrag = true;
      button.classList.add('dragging');
    }

    // Lebendige Umsortierung: den Knopf unter dem Zeiger an die Stelle des
    // gezogenen Knopfes verschieben (nicht umgekehrt!) - wird stattdessen
    // der gezogene Knopf selbst per insertBefore() im DOM bewegt, gibt der
    // Browser dessen Pointer-Capture verloren (Einfuegen zaehlt intern als
    // Entfernen+Wiedereinfuegen), und der Zug bricht nach der ersten
    // Umsortierung stillschweigend ab. Nach DOM-Reihenfolge entschieden
    // statt nach Bildschirm-Koordinaten - das Raster kann sich je nach
    // Fensterbreite in einer Reihe oder mehreren Zeilen anordnen, eine
    // reine Y-Position waere bei einer Reihe falsch.
    const target = document.elementFromPoint(moveEvent.clientX, moveEvent.clientY)?.closest('.remote-btn');
    if (target && target !== button) {
      const grid = target.parentElement;
      const children = [...grid.children];
      const targetWasBefore = children.indexOf(button) > children.indexOf(target);
      grid.insertBefore(target, targetWasBefore ? button.nextSibling : button);
    }
  };

  const finish = async (upEvent) => {
    button.removeEventListener('pointermove', move);
    button.removeEventListener('pointerup', finish);
    button.removeEventListener('pointercancel', cancel);
    button.classList.remove('dragging');
    activeDrag = false;

    if (!dragging) {
      // Kein Ziehen, sondern ein Tippen: ausfuehren. Absichtlich hier und
      // nicht ueber einen separaten 'click'-Listener - setPointerCapture()
      // verschiebt auch das nach dem Loslassen synthetisierte
      // 'click'-Ereignis auf dieses Element, ein zusaetzlicher
      // Klick-Handler wuerde also bei jedem Ziehen ein zweites Mal feuern.
      await runShortcut(button, shortcut);
      return;
    }
    await saveOrder();
  };

  const cancel = () => {
    button.removeEventListener('pointermove', move);
    button.removeEventListener('pointerup', finish);
    button.removeEventListener('pointercancel', cancel);
    button.classList.remove('dragging');
    activeDrag = false;
  };

  button.addEventListener('pointermove', move);
  button.addEventListener('pointerup', finish);
  button.addEventListener('pointercancel', cancel);
}

/**
 * Baut die Knopfliste neu auf - aber nie waehrend gerade gezogen wird
 * (sonst rutscht das Ziel unter dem Finger weg, siehe screens.js), und nur
 * wenn sich die Liste selbst wirklich geaendert hat (sonst wuerde jeder
 * Zustandsschub die gerade gezogene Reihenfolge wieder verwerfen).
 */
function render(shortcuts) {
  if (activeDrag) return;
  const key = shortcuts.map((s) => `${s.id}:${s.name}:${s.steps.length}`).join('|');
  if (key === lastShortcutsKey) return;
  lastShortcutsKey = key;

  const grid = $('remote-grid');
  $('remote-empty').hidden = shortcuts.length > 0;
  grid.replaceChildren();

  for (const shortcut of shortcuts) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'remote-btn';
    button.dataset.id = shortcut.id;

    const label = document.createElement('span');
    label.textContent = shortcut.name;
    const steps = document.createElement('span');
    steps.className = 'steps';
    const n = shortcut.steps.length;
    steps.textContent = `${n} Baustein${n === 1 ? '' : 'e'}`;
    button.append(label, steps);

    button.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      startDrag(button, event, shortcut);
    });

    grid.append(button);
  }
}

connectEvents({
  onState: (payload) => render(payload.state?.shortcuts || []),
  onStatus: (online) => {
    const node = $('conn');
    node.textContent = online ? 'live' : 'keine Verbindung';
    node.classList.toggle('online', online);
    node.classList.toggle('offline', !online);
  },
});
