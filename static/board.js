// Generische Anzeige fuer ein selbst angelegtes Leaderboard: rendert je nach
// Typ entweder eine Renntabelle (wie S.O.S.) oder eine Sieg/Niederlage-
// Rangliste (wie Smash) - dieselben CSS-Klassen wie display.html, damit sich
// beide Seiten aus mehreren Metern gleich gut lesen lassen. Die eigentliche
// Zeichenlogik steckt in boardview.js, dieselbe Grundlage nutzt auch
// display.js, wenn dort ein bestimmtes eigenes Leaderboard ausgewaehlt ist.

import { connectEvents } from '/static/common.js';
import { createBoardView } from '/static/boardview.js';

const boardId = decodeURIComponent(location.pathname.replace(/^\/board\//, '').replace(/\/+$/, ''));

const el = {
  kindBadge: document.getElementById('kind-badge'),
  connBadge: document.getElementById('conn-badge'),
  racePanel: document.getElementById('race-panel'),
  raceHeading: document.getElementById('race-heading'),
  raceLead: document.getElementById('race-lead'),
  raceTable: document.getElementById('race-table'),
  raceBody: document.getElementById('race-body'),
  raceEmpty: document.getElementById('race-empty'),
  headA: document.getElementById('head-a'),
  headB: document.getElementById('head-b'),
  totalA: document.getElementById('total-a'),
  totalB: document.getElementById('total-b'),
  winsA: document.getElementById('wins-a'),
  winsB: document.getElementById('wins-b'),
  scorePanel: document.getElementById('score-panel'),
  scoreHeading: document.getElementById('score-heading'),
  scoreList: document.getElementById('score-list'),
  scoreEmpty: document.getElementById('score-empty'),
  missingPanel: document.getElementById('missing-panel'),
};

const view = createBoardView(el);

function updateTitle() {
  const board = view.getBoard();
  document.title = board ? `${board.name} – LAN-Party Leaderboard` : 'Leaderboard';
}

connectEvents({
  onState: (payload) => {
    view.setBoard((payload.state?.boards || []).find((b) => b.id === boardId) || null);
    updateTitle();
  },
  onStatus: (online) => {
    el.connBadge.textContent = online ? 'live' : 'keine Verbindung';
    el.connBadge.classList.toggle('online', online);
    el.connBadge.classList.toggle('offline', !online);
  },
});

function loop() {
  view.tick();
  requestAnimationFrame(loop);
}
requestAnimationFrame(loop);
