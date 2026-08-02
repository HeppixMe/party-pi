// Kader-Anzeige: wer hat sich in welches Team eingetragen. Aktualisiert sich
// live, sobald jemand den QR-Code scannt.

import { TEAM_IDS, connectEvents, playersOfTeam, teamName } from '/static/common.js';

const conn = document.getElementById('conn');

function render(state) {
  for (const team of TEAM_IDS) {
    const players = playersOfTeam(state, team);
    document.getElementById(`name-${team}`).textContent = teamName(state, team);
    document.getElementById(`count-${team}`).textContent =
      players.length === 1 ? '1 Spieler' : `${players.length} Spieler`;
    document.getElementById(`empty-${team}`).hidden = players.length > 0;

    const list = document.getElementById(`list-${team}`);
    list.replaceChildren();
    for (const player of players) {
      const item = document.createElement('li');
      item.textContent = player.name;
      if (player.wins || player.losses) {
        const record = document.createElement('span');
        record.className = 'record';
        record.textContent = `${player.wins}:${player.losses}`;
        item.append(record);
      }
      list.append(item);
    }
  }

  // Spieler, die der Admin von Hand angelegt hat, gehoeren noch keinem Team an.
  const loose = (state.players || []).filter((p) => !p.teamId);
  const note = document.getElementById('unassigned');
  note.hidden = loose.length === 0;
  note.textContent = loose.length
    ? `Ohne Team: ${loose.map((p) => p.name).join(', ')}`
    : '';
}

connectEvents({
  onState: (payload) => render(payload.state),
  onStatus: (online) => {
    conn.textContent = online ? 'live' : 'keine Verbindung';
    conn.classList.toggle('online', online);
    conn.classList.toggle('offline', !online);
  },
});
