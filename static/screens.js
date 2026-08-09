// Bildschirm-Steuerung im Admin: Ansicht auf einen Bildschirm ziehen.
//
// Umgesetzt mit Pointer-Events statt HTML5-Drag-and-Drop - nur so funktioniert
// dasselbe Verfahren mit der Maus und mit dem Finger auf dem Handy. Zusaetzlich
// gibt es einen Antipp-Weg (Ansicht antippen, dann Bildschirm antippen), der
// auch mit Tastatur bedienbar ist.

import { api } from '/static/common.js';

const DRAG_THRESHOLD_PX = 6;   // darunter gilt es als Tippen, nicht als Ziehen
const OFFLINE_AFTER_MS = 120_000;

let onAssign = () => {};
let picked = null;             // per Antippen vorgemerkte Ansicht
let ghost = null;
let activeDrag = false;
let lastViewsKey = null;

/**
 * Waehrend eines laufenden Zugs darf niemand die Kacheln unter dem Finger
 * neu aufbauen - das war der eigentliche Grund, warum sich das Ziehen
 * "irgendwie nicht richtig" angefuehlt hat: der Server schickt bei jeder
 * Kleinigkeit (ein Bildschirm meldet sich, ein Gast tritt bei, ...) einen
 * Zustandsschub an alle offenen Seiten, auch an die, auf der gerade
 * gezogen wird. renderScreens()/renderViews() ersetzen sonst bei jedem
 * Schub komplett den Inhalt von #screens bzw. #views per replaceChildren()
 * - das Ziel rutscht buchstaeblich unter dem Zeiger weg, und beim
 * Neuaufbau der Palette wuerde sogar der gerade gezogene Baustein selbst
 * verschwinden. renderScreens/renderViews ueberspringen den Neuaufbau
 * deshalb, solange isDragActive() wahr ist; der naechste Schub danach
 * (z.B. durch die Zuweisung selbst ausgeloest) holt alles nach.
 */
export function isDragActive() {
  return activeDrag;
}

/** Kachel unter dem Finger/Zeiger finden. */
function screenCardAt(x, y) {
  const element = document.elementFromPoint(x, y);
  return element ? element.closest('.screen-card') : null;
}

function clearTargets() {
  for (const card of document.querySelectorAll('.screen-card.drop-target')) {
    card.classList.remove('drop-target');
  }
}

function removeGhost() {
  ghost?.remove();
  ghost = null;
}

function setPicked(view, label) {
  picked = view ? { view, label } : null;
  for (const chip of document.querySelectorAll('.view-chip')) {
    chip.classList.toggle('picked', Boolean(picked) && chip.dataset.view === picked.view);
  }
}

/** Ziehen (Maus oder Finger) an einem Ansichts-Chip. */
function startDrag(chip, event) {
  const view = chip.dataset.view;
  const label = chip.textContent.trim();
  const startX = event.clientX;
  const startY = event.clientY;
  let dragging = false;

  chip.setPointerCapture(event.pointerId);

  const move = (moveEvent) => {
    const dx = moveEvent.clientX - startX;
    const dy = moveEvent.clientY - startY;
    if (!dragging && Math.hypot(dx, dy) < DRAG_THRESHOLD_PX) return;

    if (!dragging) {
      dragging = true;
      activeDrag = true;
      ghost = document.createElement('div');
      ghost.id = 'drag-ghost';
      ghost.textContent = label;
      document.body.append(ghost);
    }
    ghost.style.left = `${moveEvent.clientX}px`;
    ghost.style.top = `${moveEvent.clientY}px`;

    clearTargets();
    screenCardAt(moveEvent.clientX, moveEvent.clientY)?.classList.add('drop-target');
  };

  const finish = (upEvent) => {
    chip.removeEventListener('pointermove', move);
    chip.removeEventListener('pointerup', finish);
    chip.removeEventListener('pointercancel', cancel);
    removeGhost();
    clearTargets();
    activeDrag = false;

    if (!dragging) {
      // Kein Ziehen, sondern ein Tippen: Ansicht vormerken.
      setPicked(picked?.view === view ? null : view, label);
      return;
    }
    const card = screenCardAt(upEvent.clientX, upEvent.clientY);
    if (card) onAssign(card.dataset.screenId, view);
  };

  const cancel = () => {
    chip.removeEventListener('pointermove', move);
    chip.removeEventListener('pointerup', finish);
    chip.removeEventListener('pointercancel', cancel);
    removeGhost();
    clearTargets();
    activeDrag = false;
  };

  chip.addEventListener('pointermove', move);
  chip.addEventListener('pointerup', finish);
  chip.addEventListener('pointercancel', cancel);
}

/**
 * Palette der verfuegbaren Ansichten aufbauen - auch fuer eigene
 * Leaderboards, die erst nach dem ersten Aufruf dazukommen. Wird ohne
 * inhaltliche Aenderung erneut aufgerufen (bei jedem Zustandsschub),
 * baut aber nur dann wirklich neu, wenn sich die Liste selbst geaendert
 * hat - unnoetiges Geflacker vermeiden, ohne neue Ansichten zu verpassen.
 */
export function renderViews(container, views) {
  if (activeDrag) return;
  const key = views.map((v) => `${v.path} ${v.label}`).join('');
  if (key === lastViewsKey) return;
  lastViewsKey = key;
  container.replaceChildren();
  for (const view of views) {
    const chip = document.createElement('div');
    chip.className = 'view-chip';
    chip.dataset.view = view.path;
    chip.textContent = view.label;
    chip.tabIndex = 0;
    chip.setAttribute('role', 'button');
    chip.title = `${view.label} auf einen Bildschirm ziehen oder antippen`;

    chip.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      startDrag(chip, event);
    });
    chip.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        setPicked(picked?.view === view.path ? null : view.path, view.label);
      }
    });
    container.append(chip);
  }
}

/** Kacheln der angemeldeten Bildschirme aufbauen. */
export function renderScreens(container, screens, views, now, handlers) {
  onAssign = handlers.assign;
  if (activeDrag) return;
  container.replaceChildren();

  const labelOf = (path) => views.find((v) => v.path === path)?.label || path;

  for (const screen of screens) {
    const card = document.createElement('div');
    card.className = 'screen-card';
    card.dataset.screenId = screen.id;

    const offline = now - (screen.seenAt || 0) > OFFLINE_AFTER_MS;

    const left = document.createElement('div');
    const name = document.createElement('div');
    name.className = 'screen-name';
    name.textContent = screen.name;
    const url = document.createElement('div');
    url.className = 'screen-url';
    url.textContent = `/screen/${screen.id}`;
    left.append(name, url);

    const right = document.createElement('div');
    right.className = 'screen-actions';
    const badge = document.createElement('span');
    badge.className = `badge ${offline ? 'offline' : 'online'}`;
    badge.textContent = offline ? 'offline' : 'live';
    const remove = document.createElement('button');
    remove.className = 'danger small';
    remove.type = 'button';
    remove.textContent = 'Entfernen';
    remove.addEventListener('click', () => {
      if (confirm(`Bildschirm „${screen.name}“ aus der Liste entfernen?`)) {
        handlers.remove(screen.id);
      }
    });
    right.append(badge, remove);

    const current = document.createElement('div');
    current.className = 'screen-view';

    // Liste als zuverlaessige Alternative zum Ziehen - besonders auf
    // Geraeten oder Browsern, bei denen sich Pointer-Events launisch
    // anfuehlen, funktioniert eine Auswahlliste immer.
    const select = document.createElement('select');
    select.className = 'screen-view-select';
    select.setAttribute('aria-label', `Ansicht fuer ${screen.name} auswaehlen`);
    for (const view of views) {
      const option = document.createElement('option');
      option.value = view.path;
      option.textContent = view.label;
      if (view.path === screen.view) option.selected = true;
      select.append(option);
    }
    select.addEventListener('change', () => handlers.assign(screen.id, select.value));
    select.addEventListener('click', (event) => event.stopPropagation());

    current.append('zeigt: ', select);
    current.hidden = Boolean(screen.split);
    card.append(left, right, current);
    card.append(renderSplitControls(screen, views, handlers));

    // Antipp-Weg: erst Ansicht antippen, dann hier tippen. Ergibt bei einem
    // geteilten Bildschirm keinen Sinn (welche Haelfte waere gemeint?) -
    // deshalb nur, solange nicht geteilt.
    card.addEventListener('click', (event) => {
      if (event.target.closest('button, select, .screen-split')) return;
      if (!picked || screen.split) return;
      handlers.assign(screen.id, picked.view);
      setPicked(null);
    });

    container.append(card);
  }
}

/**
 * Umschalter "Bildschirm teilen" plus zwei Auswahllisten fuer links/rechts -
 * direkt an der Bildschirm-Kachel, kein eigener Bereich dafuer. Ersetzt bei
 * einem geteilten Bildschirm die einzelne "zeigt:"-Auswahl oben.
 */
function renderSplitControls(screen, views, handlers) {
  const wrap = document.createElement('div');
  wrap.className = 'screen-split';

  const toggleRow = document.createElement('label');
  toggleRow.className = 'screen-split-toggle';
  const toggle = document.createElement('input');
  toggle.type = 'checkbox';
  toggle.checked = Boolean(screen.split);
  const toggleText = document.createElement('span');
  toggleText.textContent = 'Bildschirm teilen';
  toggleRow.append(toggle, toggleText);
  wrap.append(toggleRow);

  const sidesRow = document.createElement('div');
  sidesRow.className = 'screen-split-sides';
  sidesRow.hidden = !screen.split;

  const buildSide = (label, current) => {
    const field = document.createElement('div');
    field.className = 'field';
    const fieldLabel = document.createElement('label');
    fieldLabel.textContent = label;
    const select = document.createElement('select');
    for (const view of views) {
      const option = document.createElement('option');
      option.value = view.path;
      option.textContent = view.label;
      if (view.path === current) option.selected = true;
      select.append(option);
    }
    field.append(fieldLabel, select);
    return { field, select };
  };

  const leftSide = buildSide('Links', screen.split?.left ?? screen.view);
  const rightSide = buildSide('Rechts', screen.split?.right ?? (views[1]?.path || views[0]?.path || '/'));
  sidesRow.append(leftSide.field, rightSide.field);
  wrap.append(sidesRow);

  const saveSides = () => handlers.setSplit(screen.id, leftSide.select.value, rightSide.select.value);
  leftSide.select.addEventListener('change', saveSides);
  rightSide.select.addEventListener('change', saveSides);

  toggle.addEventListener('change', () => {
    if (toggle.checked) {
      sidesRow.hidden = false;
      saveSides();
    } else {
      sidesRow.hidden = true;
      handlers.clearSplit(screen.id);
    }
  });

  return wrap;
}

export function pickedView() {
  return picked;
}
