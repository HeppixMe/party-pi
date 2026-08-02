// Steuerseite: alle Aenderungen gehen als POST an den Pi, der Zustand kommt
// ueber denselben Live-Stream zurueck wie bei der Anzeige. Dadurch sind
// mehrere geoeffnete Admin-Seiten (Laptop + Handy) immer gleich.

import {
  TEAM_IDS,
  api,
  connectEvents,
  formatTime,
  isRunning,
  serverNow,
  sortPlayers,
  timerValue,
  winRate,
} from '/static/common.js';

const $ = (id) => document.getElementById(id);
let state = null;

// -- Kleinkram ---------------------------------------------------------------
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
    await api(path, body);
  } catch (err) {
    if (err.message.includes('Nicht angemeldet')) {
      window.location.href = '/admin';
      return;
    }
    toast(err.message, 'error');
  }
}

/** "1:23", "1:23.4", "83" oder "0:01:23" -> Millisekunden (null bei Unsinn). */
export function parseTime(text) {
  const raw = String(text ?? '').trim().replace(',', '.');
  if (!raw) return null;
  const parts = raw.split(':');
  if (parts.length > 3 || parts.some((p) => p === '')) return null;
  const seconds = Number(parts.pop());
  if (!Number.isFinite(seconds) || seconds < 0) return null;
  let ms = Math.round(seconds * 1000);
  const minutes = parts.length ? Number(parts.pop()) : 0;
  const hours = parts.length ? Number(parts.pop()) : 0;
  if (!Number.isFinite(minutes) || !Number.isFinite(hours)) return null;
  ms += minutes * 60000 + hours * 3600000;
  return ms;
}

/**
 * Der Live-Stream baut die Listen neu auf. Damit dabei nicht mitten im Tippen
 * das Feld verlorengeht, merken wir uns das fokussierte Feld ueber data-key
 * und stellen es danach wieder her - inklusive des bereits getippten Textes.
 */
function withFocusPreserved(render) {
  const active = document.activeElement;
  const key = active?.dataset?.key;
  const value = key ? active.value : null;
  const start = key ? active.selectionStart : null;
  const end = key ? active.selectionEnd : null;

  render();

  if (!key) return;
  const restored = document.querySelector(`[data-key="${CSS.escape(key)}"]`);
  if (!restored) return;
  restored.value = value;
  restored.focus();
  try {
    restored.setSelectionRange(start, end);
  } catch (err) {
    /* Feldtypen ohne Auswahlbereich ignorieren */
  }
}

function teamName(id) {
  return state?.sos?.teams?.find((t) => t.id === id)?.name || (id === 'a' ? 'Team 1' : 'Team 2');
}

function activeLevel() {
  const levels = state?.sos?.levels || [];
  return [...levels].reverse().find((lv) => lv.active) || levels[levels.length - 1] || null;
}

/**
 * Ein Eingabefeld auf den Serverwert nachziehen, ohne dabei etwas zu
 * ueberschreiben, das gerade getippt und noch nicht gespeichert wurde.
 * data-server haelt fest, welcher Wert zuletzt vom Server kam - weicht der
 * Feldinhalt davon ab, hat der Benutzer ihn geaendert und behaelt Vorrang.
 */
function syncInput(input, value) {
  if (input.value === value) {
    input.dataset.server = value;
    return;
  }
  if (document.activeElement === input) return;
  if (input.dataset.server !== undefined && input.value !== input.dataset.server) return;
  input.value = value;
  input.dataset.server = value;
}

// -- SOS ---------------------------------------------------------------------
function renderTeams() {
  for (const team of TEAM_IDS) {
    syncInput($(`name-${team}`), teamName(team));
    $(`label-${team}`).textContent = teamName(team);
  }
}

function saveTeamNames() {
  return send('/api/sos/teams', { names: { a: $('name-a').value, b: $('name-b').value } });
}

function renderCurrent() {
  const level = activeLevel();
  $('current-name').textContent = level ? level.name : '– kein Level gestartet –';
  $('level-name').placeholder = `Level ${(state?.sos?.levels?.length || 0) + 1}`;

  for (const team of TEAM_IDS) {
    const timer = level?.timers?.[team];
    // Nach data-role suchen, nicht nach der Beschriftung oder der Aktion:
    // die aendert sich je nach Zustand, das Feld zum Finden darf das nicht.
    const toggle = document.querySelector(`button[data-team="${team}"][data-role="toggle"]`);
    const finish = document.querySelector(`button[data-team="${team}"][data-role="finish"]`);
    const running = isRunning(timer);

    toggle.disabled = !level;
    finish.disabled = !level;
    toggle.textContent = running ? 'Stopp' : 'Start';
    toggle.classList.toggle('running', running);
    finish.textContent = timer?.finished ? 'Doch nicht fertig' : 'Fertig';

    $(`note-${team}`).textContent = timer?.finished
      ? 'Level abgeschlossen'
      : running
        ? 'läuft'
        : timer?.elapsedMs
          ? 'angehalten'
          : '';
    $(`clock-${team}`).classList.toggle('finished', Boolean(timer?.finished));
  }
}

function renderLevels() {
  const levels = state?.sos?.levels || [];
  const list = $('levels');
  $('levels-empty').hidden = levels.length > 0;
  $('level-count').textContent = levels.length ? `(${levels.length})` : '';
  list.replaceChildren();

  for (const level of [...levels].reverse()) {
    const item = document.createElement('div');
    item.className = 'list-item' + (level.active ? ' is-active' : '');

    const head = document.createElement('div');
    head.className = 'head';
    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.value = level.name;
    nameInput.maxLength = 60;
    nameInput.dataset.key = `level-name-${level.id}`;
    nameInput.addEventListener('change', () =>
      send('/api/sos/level/rename', { levelId: level.id, name: nameInput.value }));
    const del = document.createElement('button');
    del.className = 'danger small';
    del.type = 'button';
    del.textContent = 'Löschen';
    del.addEventListener('click', () => {
      if (confirm(`Level „${level.name}“ wirklich löschen?`)) {
        send('/api/sos/level/delete', { levelId: level.id });
      }
    });
    head.append(nameInput, del);

    const times = document.createElement('div');
    times.className = 'times';
    for (const team of TEAM_IDS) {
      const timer = level.timers?.[team];
      const field = document.createElement('div');
      field.className = `time-field ${team}`;
      const label = document.createElement('label');
      label.textContent = teamName(team);
      const input = document.createElement('input');
      input.className = 'time';
      input.type = 'text';
      input.inputMode = 'decimal';
      input.dataset.key = `time-${level.id}-${team}`;
      input.value = formatTime(timerValue(timer));
      if (isRunning(timer)) input.dataset.live = '1';
      input.addEventListener('change', () => {
        const ms = parseTime(input.value);
        if (ms === null) {
          toast('Zeit bitte als mm:ss.z eingeben', 'error');
          input.value = formatTime(timerValue(timer));
          return;
        }
        send('/api/sos/timer/adjust', { levelId: level.id, team, valueMs: ms });
      });
      field.append(label, input);

      const buttons = document.createElement('div');
      buttons.className = 'row';
      const toggle = document.createElement('button');
      toggle.className = 'small' + (isRunning(timer) ? ' running' : '');
      toggle.type = 'button';
      toggle.textContent = isRunning(timer) ? 'Stopp' : 'Start';
      toggle.addEventListener('click', () =>
        send('/api/sos/timer', {
          levelId: level.id,
          team,
          action: isRunning(timer) ? 'stop' : 'start',
        }));
      const finish = document.createElement('button');
      finish.className = 'small';
      finish.type = 'button';
      finish.textContent = timer?.finished ? 'offen setzen' : 'fertig';
      finish.addEventListener('click', () =>
        send('/api/sos/timer', {
          levelId: level.id,
          team,
          action: timer?.finished ? 'unfinish' : 'finish',
        }));
      buttons.append(toggle, finish);
      field.append(buttons);
      times.append(field);
    }

    item.append(head, times);
    list.append(item);
  }
}

// -- Smash -------------------------------------------------------------------
function renderPlayers() {
  const players = sortPlayers(state?.smash?.players || []);
  const list = $('players');
  $('players-empty').hidden = players.length > 0;
  list.replaceChildren();

  for (const player of players) {
    const item = document.createElement('div');
    item.className = 'list-item';

    const head = document.createElement('div');
    head.className = 'player-row';
    const nameInput = document.createElement('input');
    nameInput.type = 'text';
    nameInput.value = player.name;
    nameInput.maxLength = 60;
    nameInput.dataset.key = `player-name-${player.id}`;
    nameInput.addEventListener('change', () =>
      send('/api/smash/player', { action: 'rename', id: player.id, name: nameInput.value }));
    const score = document.createElement('span');
    score.className = 'score';
    score.textContent = `${player.wins}:${player.losses} · ${winRate(player)} %`;
    head.append(nameInput, score);

    const buttons = document.createElement('div');
    buttons.className = 'row';
    const mk = (text, cls, handler) => {
      const button = document.createElement('button');
      button.className = cls;
      button.type = 'button';
      button.textContent = text;
      button.addEventListener('click', handler);
      return button;
    };
    const score1 = (field, delta) =>
      send('/api/smash/player', { action: 'score', id: player.id, field, delta });
    buttons.append(
      mk('+1 Sieg', 'primary', () => score1('wins', 1)),
      mk('−1', 'small', () => score1('wins', -1)),
      mk('+1 Niederlage', '', () => score1('losses', 1)),
      mk('−1', 'small', () => score1('losses', -1)),
      mk('Löschen', 'danger small', () => {
        if (confirm(`Spieler „${player.name}“ wirklich löschen?`)) {
          send('/api/smash/player', { action: 'delete', id: player.id });
        }
      }),
    );

    item.append(head, buttons);
    list.append(item);
  }

  for (const id of ['winner', 'loser']) {
    const select = $(id);
    const previous = select.value;
    select.replaceChildren();
    for (const player of players) {
      const option = document.createElement('option');
      option.value = player.id;
      option.textContent = player.name;
      select.append(option);
    }
    if (players.some((p) => p.id === previous)) select.value = previous;
  }

  // Nicht denselben Spieler auf beiden Seiten vorauswaehlen - sonst laeuft der
  // erste Klick auf "Eintragen" garantiert in eine Fehlermeldung.
  if (players.length > 1 && $('winner').value === $('loser').value) {
    const other = players.find((p) => p.id !== $('winner').value);
    if (other) $('loser').value = other.id;
  }
}

// -- Uhren ticken ------------------------------------------------------------
function tick() {
  const level = activeLevel();
  const now = serverNow();
  for (const team of TEAM_IDS) {
    $(`clock-${team}`).textContent = formatTime(timerValue(level?.timers?.[team], now));
  }
  for (const level2 of state?.sos?.levels || []) {
    for (const team of TEAM_IDS) {
      const timer = level2.timers?.[team];
      if (!isRunning(timer)) continue;
      const input = document.querySelector(`[data-key="time-${level2.id}-${team}"]`);
      if (input && document.activeElement !== input) {
        input.value = formatTime(timerValue(timer, now));
      }
    }
  }
  requestAnimationFrame(tick);
}

// -- Bedienung ---------------------------------------------------------------
$('save-teams').addEventListener('click', saveTeamNames);
// Zusaetzlich beim Verlassen des Feldes speichern - der Knopf wird sonst
// gerne vergessen.
for (const team of TEAM_IDS) {
  $(`name-${team}`).addEventListener('change', saveTeamNames);
}

$('start-level').addEventListener('click', async () => {
  const name = $('level-name').value.trim();
  await send('/api/sos/level', { name: name || null, autostart: true });
  $('level-name').value = '';
  toast('Neues Level läuft');
});

for (const button of document.querySelectorAll('#current button[data-team]')) {
  button.addEventListener('click', () => {
    const level = activeLevel();
    if (!level) return;
    const team = button.dataset.team;
    const timer = level.timers?.[team];
    // Die Aktion ergibt sich aus dem aktuellen Zustand, nicht aus dem Knopf.
    const action = button.dataset.role === 'toggle'
      ? (isRunning(timer) ? 'stop' : 'start')
      : (timer?.finished ? 'unfinish' : 'finish');
    send('/api/sos/timer', { levelId: level.id, team, action });
  });
}

$('add-player').addEventListener('click', async () => {
  const name = $('player-name').value.trim();
  if (!name) return toast('Bitte einen Namen eingeben', 'error');
  await send('/api/smash/player', { action: 'add', name });
  $('player-name').value = '';
  $('player-name').focus();
});
$('player-name').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') $('add-player').click();
});

$('add-match').addEventListener('click', () => {
  const winnerId = $('winner').value;
  const loserId = $('loser').value;
  if (!winnerId || !loserId) return toast('Bitte zwei Spieler auswählen', 'error');
  if (winnerId === loserId) return toast('Sieger und Verlierer müssen verschieden sein', 'error');
  send('/api/smash/result', { winnerId, loserId });
});

for (const button of document.querySelectorAll('button[data-reset]')) {
  button.addEventListener('click', () => {
    const scope = button.dataset.reset;
    const what = { sos: 'die SOS-Level', smash: 'die Smash-Wertung', all: 'ALLES' }[scope];
    if (confirm(`Wirklich ${what} zurücksetzen? Das lässt sich nicht rückgängig machen.`)) {
      send('/api/reset', { scope });
    }
  });
}

$('logout').addEventListener('click', async () => {
  await send('/api/logout', {});
  window.location.href = '/admin';
});

// -- Start -------------------------------------------------------------------
connectEvents({
  onState: (payload) => {
    state = payload.state;
    withFocusPreserved(() => {
      renderTeams();
      renderCurrent();
      renderLevels();
      renderPlayers();
    });
  },
  onStatus: (online) => {
    const node = $('conn');
    node.textContent = online ? 'live' : 'keine Verbindung';
    node.classList.toggle('online', online);
    node.classList.toggle('offline', !online);
  },
});

requestAnimationFrame(tick);
