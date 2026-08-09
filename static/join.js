// Spieler-Anmeldung: Name eintragen, Team waehlen. Ohne Passwort - das ist
// der Sinn des QR-Codes. Wer schon angemeldet ist, wird am Cookie erkannt und
// aendert seinen Eintrag, statt einen zweiten anzulegen.

import { api, connectEvents, fetchState, teamName } from '/static/common.js';

const $ = (id) => document.getElementById(id);

// /mitmachen/a bzw. /mitmachen/b: dieselbe Seite, aber mit fest
// vorgegebenem Team - eigene Adresse je Team, z.B. auf zwei getrennten
// QR-Codes fuer die beiden Tische. /mitmachen ohne Team bleibt unveraendert.
const forcedTeamMatch = location.pathname.match(/^\/mitmachen\/(a|b)\/?$/);
const forcedTeam = forcedTeamMatch ? forcedTeamMatch[1] : null;

let state = null;
let me = null;
let chosen = forcedTeam;      // vom Benutzer angetipptes Team
let editing = false;    // true = Formular offen, obwohl schon angemeldet

function render() {
  $('team-choice').hidden = Boolean(forcedTeam);
  if (forcedTeam) chosen = forcedTeam;
  for (const team of ['a', 'b']) {
    $(`team-${team}`).textContent = teamName(state, team);
    $(`team-${team}`).classList.toggle('chosen', chosen === team);
  }
  if (forcedTeam) {
    $('forced-team-note').hidden = false;
    $('forced-team-note').textContent = `Team: ${teamName(state, forcedTeam)}`;
  }

  const known = Boolean(me);
  $('state').hidden = !known;
  $('form').hidden = known && !editing;

  if (known) {
    $('state').textContent =
      `Du bist eingetragen als ${me.name} · ${teamName(state, me.teamId)}`;
    $('lead').textContent = editing
      ? 'Name oder Team ändern'
      : 'Viel Erfolg! Du kannst deinen Eintrag jederzeit ändern.';
    $('submit').textContent = 'Änderung speichern';
  } else {
    $('lead').textContent = 'Name eintragen und Team wählen';
    $('submit').textContent = 'Eintragen';
  }

  // Knopf zum Aufklappen, wenn schon angemeldet und Formular zu.
  let change = $('change');
  if (known && !editing) {
    if (!change) {
      change = document.createElement('button');
      change.id = 'change';
      change.type = 'button';
      change.className = 'ghost';
      change.style.width = '100%';
      change.textContent = 'Eintrag ändern';
      change.addEventListener('click', () => {
        editing = true;
        $('name').value = me.name;
        chosen = me.teamId;
        render();
        $('name').focus();
      });
      $('state').after(change);
    }
    change.hidden = false;
  } else if (change) {
    change.hidden = true;
  }
}

for (const team of ['a', 'b']) {
  $(`team-${team}`).addEventListener('click', () => {
    chosen = team;
    $('error').hidden = true;
    render();
  });
}

$('form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('error').hidden = true;

  const name = $('name').value.trim();
  if (!name) return showError('Bitte einen Namen eingeben.');
  if (!chosen) return showError('Bitte ein Team auswählen.');

  $('submit').disabled = true;
  try {
    const result = await api('/api/join', { name, teamId: chosen });
    me = result.me;
    editing = false;
    render();
  } catch (err) {
    showError(err.message);
  } finally {
    $('submit').disabled = false;
  }
});

function showError(message) {
  $('error').textContent = message;
  $('error').hidden = false;
}

// Wer an diesem Geraet sitzt, weiss nur der Server (anhand des Cookies) - und
// das steht in /api/state, nicht im Live-Strom. Deshalb einmal beim Laden.
(async () => {
  try {
    const payload = await fetchState();
    state = payload.state;
    me = payload.me;
    if (me) chosen = me.teamId;
  } catch (err) {
    /* der Live-Strom liefert den Zustand gleich ohnehin nach */
  }
  render();
})();

connectEvents({
  onState: (payload) => {
    state = payload.state;
    // Der eigene Eintrag kann sich geaendert haben (z.B. hat der Admin
    // umbenannt) - anhand der ID nachziehen, ohne die Bedienung zu stoeren.
    if (me) me = (state.players || []).find((p) => p.id === me.id) || null;
    render();
  },
});
