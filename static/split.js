// Geteilter Bildschirm: zwei Ansichten nebeneinander in je einem iframe.
// Die Kennung steckt im Pfad (/split/<id>), wie bei /board/<id>. Ein iframe
// wird nur neu geladen, wenn sich seine Ansicht wirklich geaendert hat -
// sonst wuerde jeder Zustandsschub beide Haelften flackern lassen.

import { connectEvents } from '/static/common.js';

const splitId = decodeURIComponent(location.pathname.replace(/^\/split\//, '').replace(/\/+$/, ''));

const halves = document.getElementById('halves');
const missing = document.getElementById('missing');
const leftFrame = document.getElementById('left');
const rightFrame = document.getElementById('right');

let currentLeft = null;
let currentRight = null;

function setSide(frame, view, current) {
  if (view === current) return current;
  frame.src = view;
  return view;
}

connectEvents({
  onState: (payload) => {
    const split = (payload.state?.splits || []).find((s) => s.id === splitId);
    if (!split) {
      halves.hidden = true;
      missing.hidden = false;
      document.title = 'Geteilter Bildschirm – LAN-Party Leaderboard';
      return;
    }
    missing.hidden = true;
    halves.hidden = false;
    document.title = `${split.name} – LAN-Party Leaderboard`;
    currentLeft = setSide(leftFrame, split.left, currentLeft);
    currentRight = setSide(rightFrame, split.right, currentRight);
  },
});
