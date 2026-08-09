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
  teamName as teamNameOf,
  timerValue,
  winRate,
  withFocusPreserved,
} from '/static/common.js';
import { renderScreens, renderViews } from '/static/screens.js';
import { renderRoster, wireRosterColumns } from '/static/roster.js';

const $ = (id) => document.getElementById(id);
let state = null;
let views = [];

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
  $('start-both').disabled = !level;

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
  const players = sortPlayers(state?.players || []);
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

    // Team-Zuordnung: erneutes Antippen desselben Teams loest die Zuordnung
    // wieder - so lassen sich versehentliche Klicks zuruecknehmen.
    const teamPick = document.createElement('div');
    teamPick.className = 'team-pick';
    for (const team of TEAM_IDS) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = teamNameOf(state, team);
      if (player.teamId === team) button.classList.add(`on-${team}`);
      button.addEventListener('click', () => send('/api/smash/player', {
        action: 'team',
        id: player.id,
        teamId: player.teamId === team ? null : team,
      }));
      teamPick.append(button);
    }

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

    item.append(head, teamPick, buttons);
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

// -- Kader: Spieler per Ziehen zwischen den Teams verschieben -----------------
const rosterCols = { a: $('roster-a'), b: $('roster-b'), '': $('roster-none') };
wireRosterColumns(rosterCols);

function renderRosterBlock() {
  const players = sortPlayers(state?.players || []);
  $('roster-empty').hidden = players.length > 0;
  $('roster-label-a').textContent = teamNameOf(state, 'a');
  $('roster-label-b').textContent = teamNameOf(state, 'b');
  renderRoster(rosterCols, players, {
    assign: (id, teamId) => send('/api/smash/player', { action: 'team', id, teamId }),
  });
}

// -- Eigene Leaderboards ------------------------------------------------------
// Zusaetzliche, frei anlegbare Leaderboards fuer weitere Spiele - dieselben
// Bausteine wie oben bei S.O.S. und Smash, nur parametrisiert auf ein
// bestimmtes Board statt fest auf state.sos/state.players verdrahtet.

function boardTeamName(board, id) {
  return board?.teams?.find((t) => t.id === id)?.name || (id === 'a' ? 'Team 1' : 'Team 2');
}

function renderBoards() {
  const boards = state?.boards || [];
  const list = $('boards');
  $('boards-empty').hidden = boards.length > 0;
  $('board-count').textContent = boards.length ? `(${boards.length})` : '';
  list.replaceChildren();
  for (const board of boards) {
    list.append(renderBoardCard(board));
  }
}

function renderBoardCard(board) {
  const card = document.createElement('div');
  card.className = 'list-item board-card';
  card.dataset.leaderboard = `board:${board.id}`;

  const head = document.createElement('div');
  head.className = 'head';
  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.value = board.name;
  nameInput.maxLength = 60;
  nameInput.dataset.key = `board-name-${board.id}`;
  nameInput.addEventListener('change', () =>
    send('/api/boards/rename', { boardId: board.id, name: nameInput.value }));
  const del = document.createElement('button');
  del.className = 'danger small';
  del.type = 'button';
  del.textContent = 'Löschen';
  del.addEventListener('click', () => {
    if (confirm(`Leaderboard „${board.name}“ wirklich löschen? Das lässt sich nicht rückgängig machen.`)) {
      send('/api/boards/delete', { boardId: board.id });
    }
  });
  head.append(nameInput, del);
  card.append(head);

  // Typ/Modus und ein Vorschau-Link in einer eigenen Zeile - im Kartenkopf
  // neben dem Namensfeld wuerden sie es auf schmalen Bildschirmen fast ganz
  // verdraengen.
  const metaRow = document.createElement('div');
  metaRow.className = 'row';
  const badge = document.createElement('span');
  badge.className = 'hint';
  badge.textContent = `${board.kind === 'race' ? 'Rennen' : 'Rangliste'}`
    + ` · ${board.mode === 'teams' ? 'Teams' : 'Einzelspieler'}`;
  const open = document.createElement('a');
  open.href = `/board/${board.id}`;
  open.target = '_blank';
  const openBtn = document.createElement('button');
  openBtn.className = 'ghost small';
  openBtn.type = 'button';
  openBtn.textContent = 'Ansehen ↗';
  open.append(openBtn);
  metaRow.append(badge, open);
  card.append(metaRow);

  if (board.mode === 'teams') {
    const teamRow = document.createElement('div');
    teamRow.className = 'two-col';
    for (const team of TEAM_IDS) {
      const field = document.createElement('div');
      field.className = 'field';
      const label = document.createElement('label');
      label.textContent = `Team ${team === 'a' ? '1' : '2'}`;
      const input = document.createElement('input');
      input.type = 'text';
      input.maxLength = 60;
      input.value = boardTeamName(board, team);
      input.dataset.key = `board-team-${board.id}-${team}`;
      input.addEventListener('change', () => {
        const names = { a: boardTeamName(board, 'a'), b: boardTeamName(board, 'b') };
        names[team] = input.value;
        send('/api/boards/teams', { boardId: board.id, names });
      });
      field.append(label, input);
      teamRow.append(field);
    }
    card.append(teamRow);
  }

  card.append(board.kind === 'race' ? renderBoardRaceControls(board) : renderBoardScoreControls(board));

  const resetRow = document.createElement('div');
  resetRow.className = 'row';
  const reset = document.createElement('button');
  reset.className = 'danger small';
  reset.type = 'button';
  reset.textContent = board.kind === 'race' ? 'Level leeren' : 'Punkte auf 0';
  reset.addEventListener('click', () => {
    if (confirm(`„${board.name}“ wirklich zurücksetzen?`)) {
      send('/api/boards/reset', { boardId: board.id });
    }
  });
  resetRow.append(reset);
  card.append(resetRow);

  return card;
}

function renderBoardRaceControls(board) {
  const wrap = document.createElement('div');

  const startRow = document.createElement('div');
  startRow.className = 'row';
  const nameInput = document.createElement('input');
  nameInput.className = 'grow';
  nameInput.type = 'text';
  nameInput.placeholder = `Level ${(board.levels?.length || 0) + 1}`;
  nameInput.dataset.key = `board-level-name-${board.id}`;
  const startBtn = document.createElement('button');
  startBtn.className = 'primary small';
  startBtn.type = 'button';
  startBtn.textContent = 'Neues Level starten';
  startBtn.addEventListener('click', async () => {
    const name = nameInput.value.trim();
    await send('/api/boards/level', { boardId: board.id, name: name || null, autostart: false });
    nameInput.value = '';
  });
  startRow.append(nameInput, startBtn);
  wrap.append(startRow);

  const bothRow = document.createElement('div');
  bothRow.className = 'row';
  const bothBtn = document.createElement('button');
  bothBtn.className = 'primary small';
  bothBtn.type = 'button';
  bothBtn.textContent = 'Beide Uhren starten';
  bothBtn.disabled = !board.levels?.some((l) => l.active);
  bothBtn.addEventListener('click', () => send('/api/boards/timer/start-all', { boardId: board.id }));
  bothRow.append(bothBtn);
  wrap.append(bothRow);

  const levels = document.createElement('div');
  levels.className = 'list';
  for (const level of [...(board.levels || [])].reverse()) {
    levels.append(renderBoardLevelItem(board, level));
  }
  wrap.append(levels);
  if (!board.levels?.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = 'Noch keine Level.';
    wrap.append(empty);
  }
  return wrap;
}

function renderBoardLevelItem(board, level) {
  const item = document.createElement('div');
  item.className = 'list-item' + (level.active ? ' is-active' : '');

  const head = document.createElement('div');
  head.className = 'head';
  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.value = level.name;
  nameInput.maxLength = 60;
  nameInput.dataset.key = `board-level-rename-${board.id}-${level.id}`;
  nameInput.addEventListener('change', () =>
    send('/api/boards/level/rename', { boardId: board.id, levelId: level.id, name: nameInput.value }));
  const del = document.createElement('button');
  del.className = 'danger small';
  del.type = 'button';
  del.textContent = 'Löschen';
  del.addEventListener('click', () => {
    if (confirm(`Level „${level.name}“ wirklich löschen?`)) {
      send('/api/boards/level/delete', { boardId: board.id, levelId: level.id });
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
    label.textContent = boardTeamName(board, team);
    // Derselbe Schluessel wie bei S.O.S. (nur nach Level-Id, nicht nach
    // Board) - tick() unten aktualisiert beide Listen mit demselben Muster,
    // und Level-Ids sind global eindeutig.
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
      send('/api/boards/timer/adjust', { boardId: board.id, levelId: level.id, team, valueMs: ms });
    });
    field.append(label, input);

    const buttons = document.createElement('div');
    buttons.className = 'row';
    const toggle = document.createElement('button');
    toggle.className = 'small' + (isRunning(timer) ? ' running' : '');
    toggle.type = 'button';
    toggle.textContent = isRunning(timer) ? 'Stopp' : 'Start';
    toggle.addEventListener('click', () =>
      send('/api/boards/timer', {
        boardId: board.id, levelId: level.id, team,
        action: isRunning(timer) ? 'stop' : 'start',
      }));
    const finish = document.createElement('button');
    finish.className = 'small';
    finish.type = 'button';
    finish.textContent = timer?.finished ? 'offen setzen' : 'fertig';
    finish.addEventListener('click', () =>
      send('/api/boards/timer', {
        boardId: board.id, levelId: level.id, team,
        action: timer?.finished ? 'unfinish' : 'finish',
      }));
    buttons.append(toggle, finish);
    field.append(buttons);
    times.append(field);
  }

  item.append(head, times);
  return item;
}

function renderBoardScoreControls(board) {
  const wrap = document.createElement('div');

  const addRow = document.createElement('div');
  addRow.className = 'row';
  const nameInput = document.createElement('input');
  nameInput.className = 'grow';
  nameInput.type = 'text';
  nameInput.placeholder = 'Name';
  nameInput.maxLength = 60;
  nameInput.dataset.key = `board-entrant-name-${board.id}`;
  const addBtn = document.createElement('button');
  addBtn.className = 'primary small';
  addBtn.type = 'button';
  addBtn.textContent = 'Hinzufügen';
  const doAdd = async () => {
    const name = nameInput.value.trim();
    if (!name) return toast('Bitte einen Namen eingeben', 'error');
    await send('/api/boards/entrant', { boardId: board.id, action: 'add', name });
    nameInput.value = '';
    nameInput.focus();
  };
  addBtn.addEventListener('click', doAdd);
  nameInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') doAdd();
  });
  addRow.append(nameInput, addBtn);
  wrap.append(addRow);

  const entrants = document.createElement('div');
  entrants.className = 'list';
  for (const entrant of sortPlayers(board.entrants || [])) {
    entrants.append(renderBoardEntrantItem(board, entrant));
  }
  wrap.append(entrants);
  if (!board.entrants?.length) {
    const empty = document.createElement('p');
    empty.className = 'empty';
    empty.textContent = 'Noch keine Teilnehmer.';
    wrap.append(empty);
  }
  return wrap;
}

function renderBoardEntrantItem(board, entrant) {
  const item = document.createElement('div');
  item.className = 'list-item';

  const head = document.createElement('div');
  head.className = 'player-row';
  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.value = entrant.name;
  nameInput.maxLength = 60;
  nameInput.dataset.key = `board-entrant-rename-${board.id}-${entrant.id}`;
  nameInput.addEventListener('change', () =>
    send('/api/boards/entrant', {
      boardId: board.id, action: 'rename', id: entrant.id, name: nameInput.value,
    }));
  const score = document.createElement('span');
  score.className = 'score';
  score.textContent = `${entrant.wins}:${entrant.losses} · ${winRate(entrant)} %`;
  head.append(nameInput, score);
  item.append(head);

  if (board.mode === 'teams') {
    const teamPick = document.createElement('div');
    teamPick.className = 'team-pick';
    for (const team of TEAM_IDS) {
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = boardTeamName(board, team);
      if (entrant.teamId === team) button.classList.add(`on-${team}`);
      button.addEventListener('click', () => send('/api/boards/entrant', {
        boardId: board.id,
        action: 'team',
        id: entrant.id,
        teamId: entrant.teamId === team ? null : team,
      }));
      teamPick.append(button);
    }
    item.append(teamPick);
  }

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
    send('/api/boards/entrant', { boardId: board.id, action: 'score', id: entrant.id, field, delta });
  buttons.append(
    mk('+1 Sieg', 'primary small', () => score1('wins', 1)),
    mk('−1', 'small', () => score1('wins', -1)),
    mk('+1 Niederlage', 'small', () => score1('losses', 1)),
    mk('−1', 'small', () => score1('losses', -1)),
    mk('Löschen', 'danger small', () => {
      if (confirm(`„${entrant.name}“ wirklich löschen?`)) {
        send('/api/boards/entrant', { boardId: board.id, action: 'delete', id: entrant.id });
      }
    }),
  );
  item.append(buttons);

  return item;
}

$('add-board').addEventListener('click', async () => {
  const name = $('board-name').value.trim();
  if (!name) return toast('Bitte einen Namen für das Spiel eingeben', 'error');
  await send('/api/boards/create', { name, kind: $('board-kind').value, mode: $('board-mode').value });
  $('board-name').value = '';
  $('board-name').focus();
  toast('Leaderboard erstellt');
});
$('board-name').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') $('add-board').click();
});

// -- Welches Leaderboard angezeigt wird ---------------------------------------
// state.display.active - serverseitig gespeichert, deshalb blendet dieselbe
// Auswahl nicht nur hier die anderen Leaderboards aus, sondern bestimmt auch,
// was die Haupt-Leaderboard-Seite (/) auf dem Fernseher zeigt.
function renderBoardFilterOptions() {
  const select = $('board-filter');
  const wanted = state?.display?.active || 'all';

  // Feste Optionen bleiben stehen, eigene Leaderboards werden neu aufgebaut -
  // sonst geht bei jedem Zustandsschub die gerade offene Auswahlliste zu.
  for (const option of [...select.options]) {
    if (option.value.startsWith('board:')) option.remove();
  }
  for (const board of state?.boards || []) {
    const option = document.createElement('option');
    option.value = `board:${board.id}`;
    option.textContent = board.name;
    select.append(option);
  }

  // Zeigte die Auswahl ein inzwischen geloeschtes Leaderboard, faellt der
  // Server bereits auf "all" zurueck (siehe op_delete_board) - hier nur die
  // Auswahlliste selbst nachziehen.
  const stillValid = [...select.options].some((o) => o.value === wanted);
  select.value = stillValid ? wanted : 'all';
}

function applyBoardFilter() {
  const value = state?.display?.active || 'all';
  for (const el of document.querySelectorAll('[data-leaderboard]')) {
    el.hidden = value !== 'all' && el.dataset.leaderboard !== value;
  }
}

$('board-filter').addEventListener('change', () => {
  send('/api/display/active', { active: $('board-filter').value });
});
// Ein Zeitrennen braucht zwei vergleichbare Bahnen - der Modus ist bei
// "Rennen" deshalb immer "Teams", die Auswahl wird gesperrt statt eine
// Kombination anzubieten, die es serverseitig gar nicht gibt.
$('board-kind').addEventListener('change', () => {
  const isRace = $('board-kind').value === 'race';
  $('board-mode').disabled = isRace;
  if (isRace) $('board-mode').value = 'teams';
});

// -- Uhren ticken ------------------------------------------------------------
function tickLevelInputs(levels, now) {
  for (const level of levels) {
    for (const team of TEAM_IDS) {
      const timer = level.timers?.[team];
      if (!isRunning(timer)) continue;
      const input = document.querySelector(`[data-key="time-${level.id}-${team}"]`);
      if (input && document.activeElement !== input) {
        input.value = formatTime(timerValue(timer, now));
      }
    }
  }
}

function tick() {
  const level = activeLevel();
  const now = serverNow();
  for (const team of TEAM_IDS) {
    $(`clock-${team}`).textContent = formatTime(timerValue(level?.timers?.[team], now));
  }
  tickLevelInputs(state?.sos?.levels || [], now);
  for (const board of state?.boards || []) {
    if (board.kind === 'race') tickLevelInputs(board.levels || [], now);
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
  await send('/api/sos/level', { name: name || null, autostart: false });
  $('level-name').value = '';
  toast('Neues Level angelegt – Uhren warten auf „Start“');
});

$('start-both').addEventListener('click', () => send('/api/sos/timer/start-all', {}));

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
    const what = {
      sos: 'die SOS-Level',
      smash: 'alle Siege und Niederlagen',
      players: 'alle Spieler',
      all: 'ALLES',
    }[scope];
    if (confirm(`Wirklich ${what} zurücksetzen? Das lässt sich nicht rückgängig machen.`)) {
      send('/api/reset', { scope });
    }
  });
}

// -- Bildschirme -------------------------------------------------------------
function renderScreenBlock(payload) {
  views = payload.views || [];
  const screens = payload.state?.screens || [];

  // renderViews() baut nur neu, wenn sich die Ansichtsliste wirklich
  // geaendert hat, und nie waehrend ein Baustein gerade gezogen wird -
  // siehe die Erklaerung in screens.js.
  renderViews($('views'), views);

  $('screens-empty').hidden = screens.length > 0;
  $('screen-count').textContent = screens.length ? `(${screens.length})` : '';
  renderScreens($('screens'), screens, views, serverNow(), {
    assign: (screenId, view) => send('/api/screens/assign', { screenId, view }),
    remove: (screenId) => send('/api/screens/delete', { screenId }),
    setSplit: (screenId, left, right) => send('/api/screens/split', { screenId, left, right }),
    clearSplit: (screenId) => send('/api/screens/split/clear', { screenId }),
  });
}

// -- Effekte, Luefter, Info-Tafel --------------------------------------------
function renderSettings() {
  const full = state?.display?.fx === 'full';
  const fx = $('fx-toggle');
  fx.textContent = full ? 'Volles Programm' : 'Schlicht';
  fx.classList.toggle('primary', full);

  const forced = Boolean(state?.fan?.force);
  const fan = $('fan-toggle');
  fan.textContent = forced ? '100 % (an)' : 'Nach Kurve';
  fan.classList.toggle('danger', forced);

  syncInput($('info-text'), state?.info?.text ?? '');
}

$('fx-toggle').addEventListener('click', () =>
  send('/api/display', { fx: state?.display?.fx === 'full' ? 'plain' : 'full' }));

$('fan-toggle').addEventListener('click', () =>
  send('/api/fan', { force: !state?.fan?.force }));

$('save-info').addEventListener('click', async () => {
  await send('/api/info', { text: $('info-text').value });
  $('info-saved').textContent = 'gespeichert';
  setTimeout(() => { $('info-saved').textContent = ''; }, 2000);
});

// Der Luefter-Schalter nuetzt nichts, wenn der Dienst kein steuerbares
// Verfahren gefunden hat - dann soll das dranstehen statt einer wirkungslosen
// Schaltflaeche.
async function pollFanBackend() {
  try {
    const status = await (await fetch('/api/fan')).json();
    const note = $('fan-note');
    if (status.backend === 'monitor') {
      note.textContent = 'kein steuerbarer Lüfter gefunden – Schalter ohne Wirkung';
      $('fan-toggle').disabled = true;
    } else {
      const parts = [];
      if (status.backend) parts.push(status.backend);
      if (typeof status.tempC === 'number') parts.push(`${status.tempC.toFixed(1)} °C`);
      note.textContent = 'dauerhaft 100 % statt nach Kurve'
        + (parts.length ? ` (${parts.join(' · ')})` : '');
      $('fan-toggle').disabled = false;
    }
  } catch (err) {
    /* nicht schlimm - der Schalter funktioniert trotzdem */
  }
}
pollFanBackend();
setInterval(pollFanBackend, 5000);

// -- Start -------------------------------------------------------------------
connectEvents({
  onState: (payload) => {
    state = payload.state;
    withFocusPreserved(() => {
      renderTeams();
      renderCurrent();
      renderLevels();
      renderPlayers();
      renderRosterBlock();
      renderBoards();
      renderScreenBlock(payload);
      renderSettings();
      renderBoardFilterOptions();
      applyBoardFilter();
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
