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
  };

  chip.addEventListener('pointermove', move);
  chip.addEventListener('pointerup', finish);
  chip.addEventListener('pointercancel', cancel);
}

/** Palette der verfuegbaren Ansichten aufbauen. */
export function renderViews(container, views) {
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
    current.textContent = `zeigt: ${labelOf(screen.view)}`;

    card.append(left, right, current);

    // Antipp-Weg: erst Ansicht antippen, dann hier tippen.
    card.addEventListener('click', (event) => {
      if (event.target.closest('button')) return;
      if (!picked) return;
      handlers.assign(screen.id, picked.view);
      setPicked(null);
    });

    container.append(card);
  }
}

export function pickedView() {
  return picked;
}
