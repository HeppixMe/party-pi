// Anzeige-Seite: baut die Tabelle bei jeder Server-Nachricht neu auf und
// laesst die laufenden Uhren dazwischen selbst weiterticken.

import {
  TEAM_IDS,
  computeStandings,
  connectEvents,
  formatTime,
  isRunning,
  serverNow,
  sortPlayers,
  timerValue,
  winRate,
} from '/static/common.js';
import { createBoardView } from '/static/boardview.js';

// So viele Level passen auf einen Fernseher, ohne dass es klein wird.
const MAX_ROWS = 9;

const el = {
  connBadge: document.getElementById('conn-badge'),
  fanBadge: document.getElementById('fan-badge'),
  boardMain: document.getElementById('board-main'),
  sosPanel: document.getElementById('sos-panel'),
  smashPanel: document.getElementById('smash-panel'),
  sosBody: document.getElementById('sos-body'),
  sosEmpty: document.getElementById('sos-empty'),
  sosTable: document.getElementById('sos-table'),
  sosLead: document.getElementById('sos-lead'),
  headA: document.getElementById('head-a'),
  headB: document.getElementById('head-b'),
  totalA: document.getElementById('total-a'),
  totalB: document.getElementById('total-b'),
  winsA: document.getElementById('wins-a'),
  winsB: document.getElementById('wins-b'),
  smashList: document.getElementById('smash-list'),
  smashEmpty: document.getElementById('smash-empty'),
};

// Anzeige eines vom Admin ausgewaehlten eigenen Leaderboards - dieselbe
// Zeichenlogik wie /board/<id>, nur mit "active-"-Ids gefuettert.
const activeView = createBoardView({
  racePanel: document.getElementById('active-race-panel'),
  raceHeading: document.getElementById('active-race-heading'),
  raceLead: document.getElementById('active-race-lead'),
  raceTable: document.getElementById('active-race-table'),
  raceBody: document.getElementById('active-race-body'),
  raceEmpty: document.getElementById('active-race-empty'),
  headA: document.getElementById('active-head-a'),
  headB: document.getElementById('active-head-b'),
  totalA: document.getElementById('active-total-a'),
  totalB: document.getElementById('active-total-b'),
  winsA: document.getElementById('active-wins-a'),
  winsB: document.getElementById('active-wins-b'),
  scorePanel: document.getElementById('active-score-panel'),
  scoreHeading: document.getElementById('active-score-heading'),
  scoreList: document.getElementById('active-score-list'),
  scoreEmpty: document.getElementById('active-score-empty'),
});

let state = null;
let liveCells = []; // { node, timer } - nur diese Zellen muessen ticken
let knownLevelIds = null;   // null = erster Aufbau, da soll es nicht glitchen

function teamName(id) {
  const team = state?.sos?.teams?.find((t) => t.id === id);
  return team?.name || (id === 'a' ? 'Team 1' : 'Team 2');
}

/**
 * Kurzer Glitch, wenn ein neues Level dazukommt. Beim allerersten Aufbau
 * bleibt es ruhig - sonst zuckt die Anzeige bei jedem Seitenaufruf.
 */
function glitchOnNewLevel(levels) {
  const ids = levels.map((level) => level.id);
  if (knownLevelIds !== null && ids.some((id) => !knownLevelIds.includes(id))) {
    el.sosTable.classList.remove('glitch');
    void el.sosTable.offsetWidth;         // Neustart der Animation erzwingen
    el.sosTable.classList.add('glitch');
    setTimeout(() => el.sosTable.classList.remove('glitch'), 800);
  }
  knownLevelIds = ids;
}

function renderSos() {
  const levels = state?.sos?.levels || [];
  el.headA.textContent = teamName('a');
  el.headB.textContent = teamName('b');
  glitchOnNewLevel(levels);

  el.sosTable.hidden = levels.length === 0;
  el.sosEmpty.hidden = levels.length > 0;
  el.sosBody.replaceChildren();
  liveCells = [];

  if (!levels.length) return;

  const hidden = Math.max(0, levels.length - MAX_ROWS);
  const visible = levels.slice(hidden);

  for (const level of visible) {
    const row = document.createElement('tr');
    if (level.active) row.classList.add('active');

    const label = document.createElement('th');
    label.textContent = level.name;
    row.append(label);

    const values = {};
    for (const team of TEAM_IDS) values[team] = timerValue(level.timers?.[team]);
    const bothDone = TEAM_IDS.every((t) => level.timers?.[t]?.finished);
    const best = bothDone && values.a !== values.b ? (values.a < values.b ? 'a' : 'b') : null;

    for (const team of TEAM_IDS) {
      const timer = level.timers?.[team];
      const cell = document.createElement('td');
      cell.textContent = formatTime(values[team]);
      if (isRunning(timer)) {
        cell.className = 'running';
        liveCells.push({ node: cell, timer });
      } else if (timer?.finished) {
        cell.className = best === team ? 'done best' : 'done';
      } else {
        cell.className = timer?.elapsedMs ? 'done' : 'pending';
      }
      row.append(cell);
    }
    el.sosBody.append(row);
  }

  if (hidden > 0) {
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 3;
    cell.className = 'more-hint';
    cell.textContent = `… ${hidden} frühere${hidden === 1 ? 's' : ''} Level oberhalb`;
    row.append(cell);
    el.sosBody.prepend(row);
  }
}

function renderSmash() {
  const players = sortPlayers(state?.players || []);
  el.smashEmpty.hidden = players.length > 0;
  el.smashList.replaceChildren();

  const medals = ['gold', 'silver', 'bronze'];
  players.forEach((player, index) => {
    const item = document.createElement('li');
    if (index < 3 && player.wins > 0) item.classList.add(medals[index]);
    // Punkt in Teamfarbe vor dem Namen (siehe .ranking li.of-a im CSS).
    if (player.teamId) item.classList.add(`of-${player.teamId}`);

    const rank = document.createElement('span');
    rank.className = 'rank';
    rank.textContent = index + 1;

    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = player.name;

    const score = document.createElement('span');
    score.className = 'score';
    const wins = document.createElement('span');
    wins.className = 'wins';
    wins.textContent = `${player.wins}`;
    const detail = document.createElement('span');
    detail.className = 'detail';
    detail.textContent = `${player.wins}:${player.losses} · ${winRate(player)} %`;
    score.append(wins, detail);

    item.append(rank, name, score);
    el.smashList.append(item);
  });
}

/**
 * Schaltet zwischen den drei Anzeige-Modi um (state.display.active):
 * "all" - beide eingebauten Leaderboards nebeneinander (wie bisher),
 * "sos"/"smash" - nur eines der beiden, einspaltig,
 * "board:<id>" - ein eigenes Leaderboard anstelle der eingebauten, einspaltig.
 */
function applyActive() {
  const active = state?.display?.active || 'all';
  const boardMode = active.startsWith('board:');
  const board = boardMode
    ? (state?.boards || []).find((b) => `board:${b.id}` === active) || null
    : null;

  el.sosPanel.hidden = boardMode || active === 'smash';
  el.smashPanel.hidden = boardMode || active === 'sos';
  activeView.setBoard(board);

  el.boardMain.classList.toggle('board-single', active !== 'all');
}

/** Laeuft dauerhaft: nur Textinhalte auffrischen, kein Neuaufbau des DOM. */
function tick() {
  activeView.tick();
  if (state) {
    const now = serverNow();
    for (const { node, timer } of liveCells) {
      node.textContent = formatTime(timerValue(timer, now));
    }

    const standings = computeStandings(state, now);
    el.totalA.textContent = formatTime(standings.totals.a);
    el.totalB.textContent = formatTime(standings.totals.b);
    el.winsA.textContent = standings.levelWins.a;
    el.winsB.textContent = standings.levelWins.b;
    el.totalA.classList.toggle('leader', standings.leader === 'a');
    el.totalB.classList.toggle('leader', standings.leader === 'b');

    if (!state.sos?.levels?.length) {
      el.sosLead.textContent = 'Noch kein Level gestartet';
    } else if (standings.leader) {
      const cls = standings.leader === 'a' ? 'team-a' : 'team-b';
      el.sosLead.innerHTML =
        `Führt: <strong class="${cls}"></strong>`;
      el.sosLead.querySelector('strong').textContent = teamName(standings.leader);
    } else {
      el.sosLead.textContent = 'Gleichstand';
    }
  }
  requestAnimationFrame(tick);
}

connectEvents({
  onState: (payload) => {
    state = payload.state;
    renderSos();
    renderSmash();
    applyActive();
  },
  onStatus: (online) => {
    el.connBadge.textContent = online ? 'live' : 'keine Verbindung';
    el.connBadge.classList.toggle('online', online);
    el.connBadge.classList.toggle('offline', !online);
  },
});

// Temperatur/Luefterstufe: kleiner Streifen oben rechts, damit man am Abend
// mit einem Blick sieht, dass der Pi kuehl bleibt.
async function pollFan() {
  try {
    const response = await fetch('/api/fan');
    const status = await response.json();
    if (typeof status.tempC === 'number') {
      const speed = typeof status.percent === 'number' ? ` · Lüfter ${status.percent} %` : '';
      const forced = status.force ? ' · MAX' : '';
      el.fanBadge.textContent = `${status.tempC.toFixed(1)} °C${speed}${forced}`;
      el.fanBadge.hidden = false;
    } else {
      el.fanBadge.hidden = true;
    }
  } catch (err) {
    el.fanBadge.hidden = true;
  }
}
pollFan();
setInterval(pollFan, 5000);

requestAnimationFrame(tick);
