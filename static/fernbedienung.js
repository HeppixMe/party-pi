// Fernbedienung: alle gespeicherten Kurzbefehle als grosse, antippbare
// Knoepfe - fuers Handy in der Hosentasche, ohne erst ins Admin zu wechseln.
// Loest per Fetch auf /run/<id> aus, bleibt dabei auf dieser Seite (die
// eigentliche /run/-Route leitet sonst auf /admin weiter, das interessiert
// hier niemanden).

import { connectEvents, shortcutNeedsNamePrompt } from '/static/common.js';

const $ = (id) => document.getElementById(id);

function toast(message, kind = 'ok') {
  const node = $('toast');
  node.textContent = message;
  node.className = kind;
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.hidden = true; }, kind === 'error' ? 5000 : 2000);
}

function render(shortcuts) {
  const grid = $('remote-grid');
  $('remote-empty').hidden = shortcuts.length > 0;
  grid.replaceChildren();

  for (const shortcut of shortcuts) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'remote-btn';

    const label = document.createElement('span');
    label.textContent = shortcut.name;
    const steps = document.createElement('span');
    steps.className = 'steps';
    const n = shortcut.steps.length;
    steps.textContent = `${n} Baustein${n === 1 ? '' : 'e'}`;
    button.append(label, steps);

    button.addEventListener('click', async () => {
      // Fragt der Kurzbefehl vor dem Levelstart erst nach einem Namen,
      // muss diese Seite dorthin wechseln, statt still im Hintergrund
      // abzurufen - sonst sieht niemand die Eingabeseite.
      if (shortcutNeedsNamePrompt(shortcut)) {
        location.href = `/run/${shortcut.id}`;
        return;
      }
      button.disabled = true;
      try {
        await fetch(`/run/${shortcut.id}`);
        toast(`„${shortcut.name}“ ausgeführt`);
      } catch (err) {
        toast('Konnte Kurzbefehl nicht ausführen', 'error');
      } finally {
        button.disabled = false;
      }
    });

    grid.append(button);
  }
}

connectEvents({
  onState: (payload) => render(payload.state?.shortcuts || []),
  onStatus: (online) => {
    const node = $('conn');
    node.textContent = online ? 'live' : 'keine Verbindung';
    node.classList.toggle('online', online);
    node.classList.toggle('offline', !online);
  },
});
