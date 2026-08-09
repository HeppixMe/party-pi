// Kaderuebersicht im Admin: Spieler per Ziehen zwischen den Teamspalten
// verschieben, um die Teams neu zu verteilen. Dasselbe Muster wie bei der
// Bildschirm-Zuweisung (screens.js): Pointer-Events statt HTML5-Drag-and-Drop
// (funktioniert mit Maus und Finger), ein Antipp-Weg als Alternative, und ein
// activeDrag-Schalter, der verhindert, dass ein per SSE ausgeloester
// Neuaufbau die gerade gezogene Karte unter dem Finger wegreisst.

const DRAG_THRESHOLD_PX = 6;

let onAssign = () => {};
let picked = null;             // per Antippen vorgemerkte Spieler-Id
let ghost = null;
let activeDrag = false;
let lastPlayersKey = null;

export function isDragActive() {
  return activeDrag;
}

function columnAt(x, y) {
  const element = document.elementFromPoint(x, y);
  return element ? element.closest('.roster-col') : null;
}

function clearTargets() {
  for (const col of document.querySelectorAll('.roster-col.drop-target')) {
    col.classList.remove('drop-target');
  }
}

function removeGhost() {
  ghost?.remove();
  ghost = null;
}

function setPicked(playerId) {
  picked = playerId;
  for (const card of document.querySelectorAll('.roster-card')) {
    card.classList.toggle('picked', card.dataset.playerId === picked);
  }
}

function startDrag(card, event) {
  const playerId = card.dataset.playerId;
  const label = card.textContent.trim();
  const startX = event.clientX;
  const startY = event.clientY;
  let dragging = false;

  card.setPointerCapture(event.pointerId);

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
    columnAt(moveEvent.clientX, moveEvent.clientY)?.classList.add('drop-target');
  };

  const finish = (upEvent) => {
    card.removeEventListener('pointermove', move);
    card.removeEventListener('pointerup', finish);
    card.removeEventListener('pointercancel', cancel);
    removeGhost();
    clearTargets();
    activeDrag = false;

    if (!dragging) {
      // Kein Ziehen, sondern ein Tippen. Ist schon ein anderer Spieler
      // vorgemerkt, zaehlt das Antippen dieser Karte als Ziel - die Spalte,
      // in der die angetippte Karte liegt, ist das Drop-Ziel. Nur ohne
      // Vormerkung (oder beim erneuten Antippen derselben Karte) wird
      // vorgemerkt bzw. die Vormerkung aufgehoben. Sonst waere eine Spalte,
      // die schon Karten enthaelt, per Antippen kaum zu treffen - der Klick
      // laueft dort fast immer auf einer bestehenden Karte auf.
      if (picked && picked !== playerId) {
        const col = card.closest('.roster-col');
        onAssign(picked, col ? (col.dataset.teamId || null) : null);
        setPicked(null);
        return;
      }
      setPicked(picked === playerId ? null : playerId);
      return;
    }
    const col = columnAt(upEvent.clientX, upEvent.clientY);
    if (col) onAssign(playerId, col.dataset.teamId || null);
  };

  const cancel = () => {
    card.removeEventListener('pointermove', move);
    card.removeEventListener('pointerup', finish);
    card.removeEventListener('pointercancel', cancel);
    removeGhost();
    clearTargets();
    activeDrag = false;
  };

  card.addEventListener('pointermove', move);
  card.addEventListener('pointerup', finish);
  card.addEventListener('pointercancel', cancel);
}

/**
 * Baut die drei Spalten (Team a, Team b, ohne Team) neu auf. `cols` ist eine
 * Zuordnung teamId ('a'/'b'/'') -> Container-Element. Wird bei jedem
 * Zustandsschub aufgerufen, ueberspringt den Neuaufbau aber, solange
 * gezogen wird.
 */
export function renderRoster(cols, players, handlers) {
  onAssign = handlers.assign;
  if (activeDrag) return;

  // Wie bei renderViews() in screens.js: nur wirklich neu aufbauen, wenn
  // sich Spielerliste oder Team-Zuordnung tatsaechlich geaendert haben.
  // Sonst wuerde jeder Zustandsschub (Bildschirm meldet sich, Gast tritt
  // bei, Level laeuft, ...) alle Karten wegreissen und neu erzeugen -
  // unnoetiges Geflacker und ein bewegliches Ziel fuer den Anfang eines
  // gerade erst begonnenen Zugs.
  const key = players.map((p) => `${p.id}:${p.name}:${p.teamId || ''}`).join('|');
  if (key === lastPlayersKey) return;
  lastPlayersKey = key;

  const byTeam = { a: [], b: [], '': [] };
  for (const player of players) {
    byTeam[player.teamId || ''].push(player);
  }

  for (const [teamId, container] of Object.entries(cols)) {
    container.replaceChildren();
    for (const player of byTeam[teamId] || []) {
      const card = document.createElement('div');
      card.className = 'roster-card';
      card.dataset.playerId = player.id;
      card.textContent = player.name;
      card.tabIndex = 0;
      card.setAttribute('role', 'button');
      card.title = `${player.name} auf ein anderes Team ziehen oder antippen`;
      card.classList.toggle('picked', picked === player.id);

      card.addEventListener('pointerdown', (event) => {
        event.preventDefault();
        startDrag(card, event);
      });
      card.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          setPicked(picked === player.id ? null : player.id);
        }
      });
      container.append(card);
    }
    if (!byTeam[teamId]?.length) {
      const empty = document.createElement('p');
      empty.className = 'empty roster-empty';
      empty.textContent = 'leer';
      container.append(empty);
    }
  }
}

/** Spalten sollen antippbar sein, um eine vorgemerkte Karte dorthin zu
 * verschieben - dieselbe Antipp-Logik wie bei den Bildschirmen. */
export function wireRosterColumns(cols) {
  for (const [teamId, container] of Object.entries(cols)) {
    const col = container.closest('.roster-col');
    if (!col) continue;
    col.addEventListener('click', (event) => {
      if (event.target.closest('.roster-card')) return;
      if (!picked) return;
      onAssign(picked, teamId || null);
      setPicked(null);
    });
  }
}
