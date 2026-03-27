// ── Typing indicators ─────────────────────────────────────────────
let typingInterval = null;
const TYPING_COLORS = [
  '#a78bfa','#f472b6','#34d399','#fbbf24','#60a5fa',
  '#fb923c','#e879f9','#2dd4bf','#f87171','#a3e635',
  '#818cf8','#fb7185','#4ade80','#facc15','#38bdf8',
  '#f97316','#c084fc','#22d3ee','#ef4444','#84cc16',
];
let typingColorIdx = 0;

function randomVerb() {
  return FUN_VERBS[Math.floor(Math.random() * FUN_VERBS.length)];
}

function randomColor() {
  typingColorIdx = (typingColorIdx + 1) % TYPING_COLORS.length;
  return TYPING_COLORS[typingColorIdx];
}

function showTyping() {
  // If already showing, don't recreate (avoids layout thrashing)
  if (document.getElementById('typing')) return;
  if (typingInterval) { clearInterval(typingInterval); typingInterval = null; }
  const el = document.createElement('div');
  el.className = 'typing';
  el.id = 'typing';
  const color = randomColor();
  el.innerHTML = '<span class="spinner" style="color:' + color + '">✻</span>'
    + '<span class="verb" style="color:' + color + '">' + randomVerb() + '...</span>';
  document.getElementById('messages').appendChild(el);
  scrollBottom();
  typingInterval = setInterval(() => {
    const t = document.getElementById('typing');
    if (t) {
      const c = randomColor();
      t.innerHTML = '<span class="spinner" style="color:' + c + '">✻</span>'
        + '<span class="verb" style="color:' + c + '">' + randomVerb() + '...</span>';
    }
  }, 3000);
}

function hideTyping() {
  if (typingInterval) { clearInterval(typingInterval); typingInterval = null; }
  const el = document.getElementById('typing');
  if (el) el.remove();
}

let contextOpInterval = null;
function showContextOp(label) {
  hideContextOp();
  const el = document.createElement('div');
  el.className = 'typing';
  el.id = 'contextOpTyping';
  const c = randomColor();
  el.innerHTML = '<span class="spinner" style="color:' + c + '">✻</span>'
    + '<em style="color:' + c + '">' + label + '</em> '
    + '<span class="verb" style="color:' + c + '">' + randomVerb() + '...</span>';
  document.getElementById('messages').appendChild(el);
  scrollBottom();
  contextOpInterval = setInterval(() => {
    const t = document.getElementById('contextOpTyping');
    if (t) {
      const c2 = randomColor();
      t.innerHTML = '<span class="spinner" style="color:' + c2 + '">✻</span>'
        + '<em style="color:' + c2 + '">' + label + '</em> '
        + '<span class="verb" style="color:' + c2 + '">' + randomVerb() + '...</span>';
    }
  }, 3000);
}

function hideContextOp() {
  if (contextOpInterval) { clearInterval(contextOpInterval); contextOpInterval = null; }
  const el = document.getElementById('contextOpTyping');
  if (el) el.remove();
}

startActiveSync();
