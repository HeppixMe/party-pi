// Gemeinsame Rendering-Logik fuer ein Leaderboard (Rennen mit Zeit-Uhren
// oder Rangliste mit Sieg/Niederlage) - genutzt von board.js (eigene Seite
// je Leaderboard, /board/<id>) und von display.js (Haupt-Leaderboard, wenn
// dort ein bestimmtes eigenes Leaderboard ausgewaehlt ist). Erwartet eine
// Zuordnung von Element-Ids, damit beide Seiten ihre eigenen, nicht
// kollidierenden Ids verwenden koennen.

import {
  TEAM_IDS,
  formatTime,
  isRunning,
  serverNow,
  sortPlayers,
  timerValue,
  winRate,
} from '/static/common.js';

const MAX_ROWS = 9;

/**
 * Baut einen Renderer fuer genau ein Leaderboard. `el` muss dieselben
 * Felder wie in board.js tragen: kindBadge (optional), racePanel,
 * raceHeading, raceLead, raceTable, raceBody, raceEmpty, headA, headB,
 * totalA, totalB, winsA, winsB, scorePanel, scoreHeading, scoreList,
 * scoreEmpty, missingPanel (optional).
 */
export function createBoardView(el) {
  let board = null;
  let liveCells = []; // { node, timer } - nur diese Zellen muessen ticken
  let knownLevelIds = null;

  function teamName(id) {
    const team = board?.teams?.find((t) => t.id === id);
    return team?.name || (id === 'a' ? 'Team 1' : 'Team 2');
  }

  /** Dieselbe Regel wie compute_standings() im Server, nur auf board.levels. */
  function computeRaceStandings(now) {
    const totals = { a: 0, b: 0 };
    const wins = { a: 0, b: 0 };
    for (const level of board?.levels || []) {
      const values = {};
      for (const team of TEAM_IDS) {
        values[team] = timerValue(level.timers?.[team], now);
        totals[team] += values[team];
      }
      const bothDone = TEAM_IDS.every((t) => level.timers?.[t]?.finished);
      if (bothDone && values.a !== values.b) {
        wins[values.a < values.b ? 'a' : 'b'] += 1;
      }
    }
    let leader = null;
    if (wins.a !== wins.b) {
      leader = wins.a > wins.b ? 'a' : 'b';
    } else if (totals.a !== totals.b && (totals.a || totals.b)) {
      leader = totals.a < totals.b ? 'a' : 'b';
    }
    return { totals, levelWins: wins, leader };
  }

  function glitchOnNewLevel(levels) {
    const ids = levels.map((level) => level.id);
    if (knownLevelIds !== null && ids.some((id) => !knownLevelIds.includes(id))) {
      el.raceTable.classList.remove('glitch');
      void el.raceTable.offsetWidth; // Neustart der Animation erzwingen
      el.raceTable.classList.add('glitch');
      setTimeout(() => el.raceTable.classList.remove('glitch'), 800);
    }
    knownLevelIds = ids;
  }

  function renderRace() {
    const levels = board.levels || [];
    el.headA.textContent = teamName('a');
    el.headB.textContent = teamName('b');
    glitchOnNewLevel(levels);

    el.raceTable.hidden = levels.length === 0;
    el.raceEmpty.hidden = levels.length > 0;
    el.raceBody.replaceChildren();
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
      el.raceBody.append(row);
    }

    if (hidden > 0) {
      const row = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 3;
      cell.className = 'more-hint';
      cell.textContent = `… ${hidden} frühere${hidden === 1 ? 's' : ''} Level oberhalb`;
      row.append(cell);
      el.raceBody.prepend(row);
    }
  }

  function renderScore() {
    const entrants = sortPlayers(board.entrants || []);
    el.scoreEmpty.hidden = entrants.length > 0;
    el.scoreList.replaceChildren();

    const medals = ['gold', 'silver', 'bronze'];
    entrants.forEach((entrant, index) => {
      const item = document.createElement('li');
      if (index < 3 && entrant.wins > 0) item.classList.add(medals[index]);
      if (entrant.teamId) item.classList.add(`of-${entrant.teamId}`);

      const rank = document.createElement('span');
      rank.className = 'rank';
      rank.textContent = index + 1;

      const name = document.createElement('span');
      name.className = 'name';
      name.textContent = entrant.name;

      const score = document.createElement('span');
      score.className = 'score';
      const wins = document.createElement('span');
      wins.className = 'wins';
      wins.textContent = `${entrant.wins}`;
      const detail = document.createElement('span');
      detail.className = 'detail';
      detail.textContent = `${entrant.wins}:${entrant.losses} · ${winRate(entrant)} %`;
      score.append(wins, detail);

      item.append(rank, name, score);
      el.scoreList.append(item);
    });
  }

  function render() {
    if (!board) {
      el.racePanel.hidden = true;
      el.scorePanel.hidden = true;
      if (el.missingPanel) el.missingPanel.hidden = false;
      if (el.kindBadge) el.kindBadge.hidden = true;
      return;
    }
    if (el.missingPanel) el.missingPanel.hidden = true;
    if (el.kindBadge) {
      el.kindBadge.hidden = false;
      el.kindBadge.textContent = board.kind === 'race' ? 'Rennen' : 'Rangliste';
    }

    const isRace = board.kind === 'race';
    el.racePanel.hidden = !isRace;
    el.scorePanel.hidden = isRace;

    if (isRace) {
      el.raceHeading.textContent = board.name;
      renderRace();
    } else {
      el.scoreHeading.textContent = board.name;
      renderScore();
    }
  }

  /** Laeuft dauerhaft: nur Textinhalte auffrischen, kein Neuaufbau des DOM. */
  function tick() {
    if (board?.kind === 'race') {
      const now = serverNow();
      for (const { node, timer } of liveCells) {
        node.textContent = formatTime(timerValue(timer, now));
      }

      const standings = computeRaceStandings(now);
      el.totalA.textContent = formatTime(standings.totals.a);
      el.totalB.textContent = formatTime(standings.totals.b);
      el.winsA.textContent = standings.levelWins.a;
      el.winsB.textContent = standings.levelWins.b;
      el.totalA.classList.toggle('leader', standings.leader === 'a');
      el.totalB.classList.toggle('leader', standings.leader === 'b');

      if (!board.levels?.length) {
        el.raceLead.textContent = 'Noch kein Level gestartet';
      } else if (standings.leader) {
        const cls = standings.leader === 'a' ? 'team-a' : 'team-b';
        el.raceLead.innerHTML = `Führt: <strong class="${cls}"></strong>`;
        el.raceLead.querySelector('strong').textContent = teamName(standings.leader);
      } else {
        el.raceLead.textContent = 'Gleichstand';
      }
    }
  }

  return {
    setBoard(next) {
      board = next;
      render();
    },
    getBoard() {
      return board;
    },
    tick,
  };
}
