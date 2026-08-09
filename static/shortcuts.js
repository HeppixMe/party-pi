// Kurzbefehl-Baukasten: Bausteine aus der Palette per Ziehen oder Antippen
// in einen Ablauf stecken, wie bei Scratch. Gespeichert wird der Ablauf erst
// auf Knopfdruck; bis dahin lebt er nur hier im Browser (state.shortcuts vom
// Server aendert sich currently nicht deswegen automatisch mit).

import { api, connectEvents, withFocusPreserved } from '/static/common.js';

const $ = (id) => document.getElementById(id);
let state = null;
let views = [];

// -- Kleinkram ----------------------------------------------------------------
function toast(message, kind = 'ok') {
  const node = $('toast');
  node.textContent = message;
  node.className = kind;
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.hidden = true; }, kind === 'error' ? 5000 : 2000);
}

async function send(path, body) {
  try {
    return await api(path, body);
  } catch (err) {
    toast(err.message, 'error');
    throw err;
  }
}

function mkBtn(text, handler, cls = 'small') {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = cls;
  button.textContent = text;
  button.addEventListener('click', handler);
  return button;
}

// -- kleine Bausteine fuer die Baustein-Formulare -----------------------------
function hintSpan(text) {
  const span = document.createElement('span');
  span.className = 'field-hint';
  span.textContent = text;
  return span;
}

function selectField(options, value, onChange) {
  const select = document.createElement('select');
  for (const [val, text] of options) {
    const option = document.createElement('option');
    option.value = val;
    option.textContent = text;
    if (val === value) option.selected = true;
    select.append(option);
  }
  select.addEventListener('change', () => {
    onChange(select.value);
    renderSequence();
  });
  return select;
}

function textField(value, placeholder, dataKey, onChange) {
  const input = document.createElement('input');
  input.type = 'text';
  input.value = value || '';
  input.placeholder = placeholder || '';
  input.maxLength = 60;
  if (dataKey) input.dataset.key = dataKey;
  input.addEventListener('change', () => {
    onChange(input.value);
    renderSequence();
  });
  return input;
}

function textareaField(value, dataKey, onChange) {
  const textarea = document.createElement('textarea');
  textarea.value = value || '';
  textarea.maxLength = 4000;
  if (dataKey) textarea.dataset.key = dataKey;
  textarea.addEventListener('change', () => {
    onChange(textarea.value);
    renderSequence();
  });
  return textarea;
}

function checkboxField(checked, labelText, onChange) {
  const label = document.createElement('label');
  const box = document.createElement('input');
  box.type = 'checkbox';
  box.checked = Boolean(checked);
  box.addEventListener('change', () => {
    onChange(box.checked);
    renderSequence();
  });
  label.append(box, document.createTextNode(labelText));
  return label;
}

// -- Bausteinarten --------------------------------------------------------------
const FX_OPTIONS = [['full', 'Volles Programm'], ['plain', 'Schlicht']];
const RESET_OPTIONS = [
  ['all', 'Alles'], ['sos', 'SOS-Level'], ['smash', 'Smash-Siege'], ['players', 'Spieler'],
];
const KIND_OPTIONS = [['score', 'Rangliste (Sieg/Niederlage)'], ['race', 'Rennen (Zeit-Uhren)']];
const MODE_OPTIONS = [['teams', 'Teams'], ['solo', 'Einzelspieler']];

const BLOCK_DEFS = {
  assign_screen: {
    label: 'Bildschirm zuweisen',
    hint: 'weist einem Bildschirm eine Ansicht zu',
    make: () => ({ type: 'assign_screen', screenId: '', view: views[0]?.path || '/' }),
    fields(step, index) {
      const wrap = document.createElement('div');
      wrap.className = 'block-fields';
      const screens = state?.screens || [];
      if (!screens.length) {
        wrap.append(hintSpan('Noch kein Bildschirm angemeldet – zuerst /screen/<name> auf dem Gerät öffnen.'));
        return wrap;
      }
      if (!step.screenId || !screens.some((s) => s.id === step.screenId)) step.screenId = screens[0].id;
      if (!views.some((v) => v.path === step.view)) step.view = views[0]?.path || '/';
      wrap.append(
        selectField(screens.map((s) => [s.id, s.name]), step.screenId, (v) => { step.screenId = v; }),
        hintSpan('zeigt'),
        selectField(views.map((v) => [v.path, v.label]), step.view, (v) => { step.view = v; }),
      );
      return wrap;
    },
  },
  create_board: {
    label: 'Leaderboard erstellen',
    hint: 'legt ein neues eigenes Leaderboard an',
    make: () => ({ type: 'create_board', name: '', kind: 'score', mode: 'solo' }),
    fields(step, index) {
      const wrap = document.createElement('div');
      wrap.className = 'block-fields';
      wrap.append(
        textField(step.name, 'Name des Spiels', `shortcut-board-name-${index}`, (v) => { step.name = v; }),
        selectField(KIND_OPTIONS, step.kind, (v) => {
          step.kind = v;
          if (v === 'race') step.mode = 'teams';
        }),
        selectField(MODE_OPTIONS, step.mode, (v) => { step.mode = v; }),
      );
      return wrap;
    },
  },
  start_level: {
    label: 'SOS-Level starten',
    hint: 'startet ein neues Level im eingebauten SOS-Rennen',
    make: () => ({ type: 'start_level', name: '', autostart: true }),
    fields(step, index) {
      const wrap = document.createElement('div');
      wrap.className = 'block-fields';
      wrap.append(
        textField(step.name, 'Levelname (optional)', `shortcut-level-name-${index}`, (v) => { step.name = v; }),
        checkboxField(step.autostart, 'sofort starten', (v) => { step.autostart = v; }),
      );
      return wrap;
    },
  },
  board_start_level: {
    label: 'Level in eigenem Leaderboard starten',
    hint: 'startet ein Level in einem selbst angelegten Renn-Leaderboard',
    make: () => ({ type: 'board_start_level', boardId: '', name: '', autostart: true }),
    fields(step, index) {
      const wrap = document.createElement('div');
      wrap.className = 'block-fields';
      const raceBoards = (state?.boards || []).filter((b) => b.kind === 'race');
      if (!raceBoards.length) {
        wrap.append(hintSpan('Noch kein Renn-Leaderboard angelegt.'));
        return wrap;
      }
      if (!step.boardId || !raceBoards.some((b) => b.id === step.boardId)) step.boardId = raceBoards[0].id;
      wrap.append(
        selectField(raceBoards.map((b) => [b.id, b.name]), step.boardId, (v) => { step.boardId = v; }),
        textField(step.name, 'Levelname (optional)', `shortcut-board-level-name-${index}`, (v) => { step.name = v; }),
        checkboxField(step.autostart, 'sofort starten', (v) => { step.autostart = v; }),
      );
      return wrap;
    },
  },
  set_fx: {
    label: 'Effekte umschalten',
    hint: 'volles Programm oder schlicht, für alle Geräte',
    make: () => ({ type: 'set_fx', fx: 'full' }),
    fields(step) {
      const wrap = document.createElement('div');
      wrap.className = 'block-fields';
      wrap.append(selectField(FX_OPTIONS, step.fx, (v) => { step.fx = v; }));
      return wrap;
    },
  },
  set_info: {
    label: 'Info-Tafel setzen',
    hint: 'ersetzt den Text auf der Info-Tafel',
    make: () => ({ type: 'set_info', text: '' }),
    fields(step, index) {
      const wrap = document.createElement('div');
      wrap.className = 'block-fields';
      wrap.append(textareaField(step.text, `shortcut-info-text-${index}`, (v) => { step.text = v; }));
      return wrap;
    },
  },
  reset: {
    label: 'Zurücksetzen',
    hint: 'setzt SOS, Smash, Spieler oder alles zurück',
    make: () => ({ type: 'reset', scope: 'all' }),
    fields(step) {
      const wrap = document.createElement('div');
      wrap.className = 'block-fields';
      wrap.append(selectField(RESET_OPTIONS, step.scope, (v) => { step.scope = v; }));
      return wrap;
    },
  },
  board_reset: {
    label: 'Eigenes Leaderboard zurücksetzen',
    hint: 'leert Level bzw. setzt Punkte auf 0 - nur für dieses eine Leaderboard',
    make: () => ({ type: 'board_reset', boardId: '' }),
    fields(step) {
      const wrap = document.createElement('div');
      wrap.className = 'block-fields';
      const boards = state?.boards || [];
      if (!boards.length) {
        wrap.append(hintSpan('Noch kein eigenes Leaderboard angelegt.'));
        return wrap;
      }
      if (!step.boardId || !boards.some((b) => b.id === step.boardId)) step.boardId = boards[0].id;
      wrap.append(selectField(boards.map((b) => [b.id, b.name]), step.boardId, (v) => { step.boardId = v; }));
      return wrap;
    },
  },
};

// -- Ablauf: rein lokaler Entwurf, erst beim Speichern zum Server ------------
let draftId = null;
let steps = [];

function addStep(type) {
  if (!BLOCK_DEFS[type]) return;
  steps.push(BLOCK_DEFS[type].make());
  renderSequence();
}

function moveStep(index, delta) {
  const target = index + delta;
  if (target < 0 || target >= steps.length) return;
  [steps[index], steps[target]] = [steps[target], steps[index]];
  renderSequence();
}

function renderSequence() {
  const container = $('sequence');
  container.replaceChildren();
  $('sequence-empty').hidden = steps.length > 0;

  steps.forEach((step, index) => {
    const def = BLOCK_DEFS[step.type];
    if (!def) return;
    const block = document.createElement('div');
    block.className = 'step-block';

    const head = document.createElement('div');
    head.className = 'step-head';
    const title = document.createElement('span');
    title.className = 'step-title';
    title.textContent = `${index + 1}. ${def.label}`;

    const actions = document.createElement('div');
    actions.className = 'step-actions';
    const up = mkBtn('↑', () => moveStep(index, -1));
    up.disabled = index === 0;
    const down = mkBtn('↓', () => moveStep(index, 1));
    down.disabled = index === steps.length - 1;
    const del = mkBtn('✕', () => { steps.splice(index, 1); renderSequence(); }, 'danger small');
    actions.append(up, down, del);
    head.append(title, actions);

    block.append(head, def.fields(step, index));
    container.append(block);
  });
}

// -- Palette: Ziehen (wie bei den Bildschirm-Ansichten) oder Antippen --------
const DRAG_THRESHOLD_PX = 6;
let ghost = null;

function sequencePanel() {
  return document.querySelector('.sequence-panel');
}

function isOverSequence(x, y) {
  const el = document.elementFromPoint(x, y);
  return Boolean(el?.closest('.sequence-panel'));
}

function startPaletteDrag(chip, type, event) {
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
      ghost.textContent = BLOCK_DEFS[type].label;
      document.body.append(ghost);
    }
    ghost.style.left = `${moveEvent.clientX}px`;
    ghost.style.top = `${moveEvent.clientY}px`;
    sequencePanel()?.classList.toggle('drop-target', isOverSequence(moveEvent.clientX, moveEvent.clientY));
  };

  const finish = (upEvent) => {
    chip.removeEventListener('pointermove', move);
    chip.removeEventListener('pointerup', finish);
    chip.removeEventListener('pointercancel', cancel);
    ghost?.remove();
    ghost = null;
    sequencePanel()?.classList.remove('drop-target');
    // Kein Ziehen, sondern ein Tippen: direkt hinzufuegen. Absichtlich hier
    // und nicht ueber einen separaten 'click'-Listener - setPointerCapture()
    // verschiebt auch das nach dem Loslassen synthetisierte 'click'-Ereignis
    // auf dieses Element, ein zusaetzlicher Klick-Handler wuerde also bei
    // jedem Ziehen ein zweites Mal feuern und den Baustein doppelt einfuegen.
    if (dragging) {
      if (isOverSequence(upEvent.clientX, upEvent.clientY)) addStep(type);
    } else {
      addStep(type);
    }
  };

  const cancel = () => {
    chip.removeEventListener('pointermove', move);
    chip.removeEventListener('pointerup', finish);
    chip.removeEventListener('pointercancel', cancel);
    ghost?.remove();
    ghost = null;
    sequencePanel()?.classList.remove('drop-target');
  };

  chip.addEventListener('pointermove', move);
  chip.addEventListener('pointerup', finish);
  chip.addEventListener('pointercancel', cancel);
}

function renderPalette() {
  const container = $('palette');
  container.replaceChildren();
  for (const [type, def] of Object.entries(BLOCK_DEFS)) {
    const chip = document.createElement('div');
    chip.className = 'palette-chip';
    chip.tabIndex = 0;
    chip.setAttribute('role', 'button');
    chip.title = `${def.label} in den Ablauf ziehen oder antippen`;
    const strong = document.createElement('strong');
    strong.textContent = def.label;
    const small = document.createElement('small');
    small.textContent = def.hint;
    chip.append(strong, small);

    chip.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      startPaletteDrag(chip, type, event);
    });
    chip.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        addStep(type);
      }
    });
    container.append(chip);
  }
}

// -- Speichern / Neu / Bearbeiten ---------------------------------------------
function setEditingNote(name) {
  $('editing-note').textContent = name ? `bearbeitet: „${name}“` : '';
}

$('new-shortcut').addEventListener('click', () => {
  draftId = null;
  $('shortcut-name').value = '';
  steps = [];
  setEditingNote('');
  renderSequence();
});

$('save-shortcut').addEventListener('click', async () => {
  const name = $('shortcut-name').value.trim();
  if (!name) return toast('Bitte einen Namen für den Kurzbefehl eingeben', 'error');
  if (!steps.length) return toast('Bitte mindestens einen Baustein hinzufügen', 'error');
  try {
    if (draftId) {
      await send('/api/shortcuts/rename', { shortcutId: draftId, name });
      await send('/api/shortcuts/steps', { shortcutId: draftId, steps });
    } else {
      const result = await send('/api/shortcuts/create', { name, steps });
      draftId = result.shortcut.id;
    }
    setEditingNote(name);
    toast('Kurzbefehl gespeichert');
  } catch (err) {
    /* send() hat den Fehler schon angezeigt */
  }
});

function loadShortcutIntoBuilder(shortcut) {
  draftId = shortcut.id;
  $('shortcut-name').value = shortcut.name;
  steps = shortcut.steps.map((step) => ({ ...step }));
  setEditingNote(shortcut.name);
  renderSequence();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// -- Liste der gespeicherten Kurzbefehle --------------------------------------
function renderShortcutList() {
  const shortcuts = state?.shortcuts || [];
  $('shortcuts-empty').hidden = shortcuts.length > 0;
  $('shortcut-count').textContent = shortcuts.length ? `(${shortcuts.length})` : '';
  const container = $('shortcuts');
  container.replaceChildren();

  for (const shortcut of shortcuts) {
    const card = document.createElement('div');
    card.className = 'list-item shortcut-card';

    const head = document.createElement('div');
    head.className = 'head';
    const name = document.createElement('strong');
    name.textContent = shortcut.name;
    const count = document.createElement('span');
    count.className = 'hint';
    const n = shortcut.steps.length;
    count.textContent = `${n} Baustein${n === 1 ? '' : 'e'}`;
    head.append(name, count);

    const urlRow = document.createElement('div');
    urlRow.className = 'run-url';
    const urlInput = document.createElement('input');
    urlInput.type = 'text';
    urlInput.readOnly = true;
    urlInput.value = `${location.origin}/run/${shortcut.id}`;
    urlInput.addEventListener('click', () => urlInput.select());
    const copyBtn = mkBtn('Kopieren', async () => {
      try {
        await navigator.clipboard.writeText(urlInput.value);
        toast('Adresse kopiert');
      } catch (err) {
        urlInput.select();
        toast('Bitte von Hand kopieren (Strg/Cmd+C)', 'error');
      }
    }, 'ghost small');
    urlRow.append(urlInput, copyBtn);

    const buttons = document.createElement('div');
    buttons.className = 'row';
    buttons.append(
      mkBtn('Jetzt ausführen', async () => {
        await fetch(`/run/${shortcut.id}`);
        toast(`„${shortcut.name}“ ausgeführt`);
      }, 'primary small'),
      mkBtn('Bearbeiten', () => loadShortcutIntoBuilder(shortcut), 'small'),
      mkBtn('Löschen', () => {
        if (confirm(`Kurzbefehl „${shortcut.name}“ wirklich löschen?`)) {
          send('/api/shortcuts/delete', { shortcutId: shortcut.id });
          if (draftId === shortcut.id) $('new-shortcut').click();
        }
      }, 'danger small'),
    );

    card.append(head, urlRow, buttons);
    container.append(card);
  }
}

// -- Start ---------------------------------------------------------------------
renderPalette();
renderSequence();

connectEvents({
  onState: (payload) => {
    state = payload.state;
    views = payload.views || [];
    withFocusPreserved(() => {
      renderShortcutList();
      // Nicht den Ablauf selbst aus dem Server-Zustand neu aufbauen - das
      // wuerde einen gerade in Bearbeitung befindlichen Entwurf verwerfen.
      // Nur die Auswahllisten in den Bausteinen (Bildschirme, Ansichten,
      // Leaderboards) sollen aktuell bleiben.
      renderSequence();
    });
  },
  onStatus: (online) => {
    const node = $('conn');
    node.textContent = online ? 'live' : 'keine Verbindung';
    node.classList.toggle('online', online);
    node.classList.toggle('offline', !online);
  },
});
