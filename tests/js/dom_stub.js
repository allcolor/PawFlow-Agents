// Minimal DOM, good enough to run tasks/io/chat_ui/turn_view.js,
// messages_render.js and conversations.js under plain Node. No npm
// dependency is available, so this stub stands in for jsdom. It implements
// only what those files actually touch.

function kebab(k) { return k.replace(/[A-Z]/g, m => '-' + m.toLowerCase()); }
function camel(k) { return k.replace(/-([a-z])/g, (_m, c) => c.toUpperCase()); }

class ClassList {
  constructor() { this._set = new Set(); }
  add(...names) { for (const n of names) if (n) this._set.add(n); }
  remove(...names) { for (const n of names) this._set.delete(n); }
  contains(name) { return this._set.has(name); }
  toggle(name, force) {
    const want = force === undefined ? !this._set.has(name) : !!force;
    if (want) this._set.add(name); else this._set.delete(name);
    return want;
  }
  get value() { return [...this._set].join(' '); }
  set value(v) { this._set = new Set(String(v || '').split(/\s+/).filter(Boolean)); }
}

class TextNode {
  constructor(text) { this.nodeType = 3; this.textContent = String(text); this.parentNode = null; }
  cloneNode() { return new TextNode(this.textContent); }
}

function makeDataset(el) {
  return new Proxy({}, {
    get(_t, k) {
      if (typeof k !== 'string') return undefined;
      return el.attributes.get('data-' + kebab(k));
    },
    set(_t, k, v) { el.attributes.set('data-' + kebab(k), String(v)); return true; },
    has(_t, k) { return el.attributes.has('data-' + kebab(k)); },
    deleteProperty(_t, k) { el.attributes.delete('data-' + kebab(k)); return true; },
    ownKeys() {
      const keys = [];
      for (const a of el.attributes.keys()) if (a.startsWith('data-')) keys.push(camel(a.slice(5)));
      return keys;
    },
    getOwnPropertyDescriptor() { return { enumerable: true, configurable: true }; },
  });
}

class Element {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = String(tag || 'div').toUpperCase();
    this.childNodes = [];
    this.parentNode = null;
    this.attributes = new Map();
    this.classList = new ClassList();
    this.style = {};
    this.hidden = false;
    this.offsetWidth = 0;
    // Read by the load-more path to keep the scroll anchored; the value only
    // has to exist and stay stable, nothing here lays anything out.
    this.scrollHeight = 0;
    this._listeners = new Map();
    this.dataset = makeDataset(this);
    this.focusCount = 0;
  }

  get className() { return this.classList.value; }
  set className(v) { this.classList.value = v; }

  get id() { return this.attributes.get('id') || ''; }
  set id(v) { this.attributes.set('id', String(v)); }

  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { const v = this.attributes.get(name); return v === undefined ? null : v; }
  hasAttribute(name) { return this.attributes.has(name); }

  get children() { return this.childNodes.filter(n => n.nodeType === 1); }
  get firstChild() { return this.childNodes[0] || null; }

  get nextSibling() {
    if (!this.parentNode) return null;
    const sibs = this.parentNode.childNodes;
    const i = sibs.indexOf(this);
    return i >= 0 ? (sibs[i + 1] || null) : null;
  }

  get isConnected() {
    let node = this;
    while (node.parentNode) node = node.parentNode;
    return node === documentElement;
  }

  appendChild(node) {
    // A fragment is a carrier: what gets inserted is its children, and the
    // fragment itself is left empty. The load-more path builds one per page.
    if (node.nodeType === 11) {
      for (const child of [...node.childNodes]) this.appendChild(child);
      return node;
    }
    if (node.parentNode) node.parentNode.removeChild(node);
    this.childNodes.push(node);
    node.parentNode = this;
    return node;
  }

  insertBefore(node, ref) {
    if (node.nodeType === 11) {
      for (const child of [...node.childNodes]) this.insertBefore(child, ref);
      return node;
    }
    // Matches the spec quirk the production code relies on: inserting a node
    // before itself resolves to its own next sibling, i.e. a net no-op move.
    if (ref === node) ref = node.nextSibling;
    if (node.parentNode) node.parentNode.removeChild(node);
    if (!ref) return this.appendChild(node);
    const i = this.childNodes.indexOf(ref);
    if (i < 0) return this.appendChild(node);
    this.childNodes.splice(i, 0, node);
    node.parentNode = this;
    return node;
  }

  removeChild(node) {
    const i = this.childNodes.indexOf(node);
    if (i >= 0) this.childNodes.splice(i, 1);
    node.parentNode = null;
    return node;
  }

  remove() { if (this.parentNode) this.parentNode.removeChild(this); }

  // Deep by default here: the only caller copies a rendered tool-call row into
  // an ephemeral cue, and a shallow copy would drop everything worth showing.
  cloneNode() {
    const copy = new Element(this.tagName);
    copy.classList.value = this.classList.value;
    for (const [name, value] of this.attributes) copy.attributes.set(name, value);
    copy.hidden = this.hidden;
    for (const child of this.childNodes) copy.appendChild(child.cloneNode());
    return copy;
  }

  removeAttribute(name) { this.attributes.delete(name); }

  get textContent() { return this.childNodes.map(n => n.textContent).join(''); }
  set textContent(v) {
    this.childNodes = [];
    if (v !== '' && v !== null && v !== undefined) this.appendChild(new TextNode(v));
  }

  set innerHTML(html) {
    this.childNodes = [];
    for (const node of parseHTML(String(html))) this.appendChild(node);
  }
  get innerHTML() { return this.childNodes.map(serialize).join(''); }

  addEventListener(type, fn) {
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    this._listeners.get(type).push(fn);
  }
  dispatch(type, event) {
    for (const fn of (this._listeners.get(type) || [])) fn(event || {});
  }
  click() { this.dispatch('click', { stopPropagation() {} }); }
  keydown(key) { this.dispatch('keydown', { key, preventDefault() {} }); }

  focus() { this.focusCount++; document.activeElement = this; }

  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  querySelectorAll(sel) {
    const out = [];
    const groups = String(sel).split(',').map(s => s.trim()).filter(Boolean)
      .map(g => g.split(/\s+/).filter(Boolean));
    walk(this, node => {
      if (node === this) return;
      for (const group of groups) {
        if (matchesChain(node, group, this)) { out.push(node); return; }
      }
    });
    return out;
  }
}

function walk(root, fn) {
  fn(root);
  for (const child of root.childNodes) if (child.nodeType === 1) walk(child, fn);
}

// A fragment is an Element that never joins the tree: appendChild and
// insertBefore unwrap it, so it only ever holds nodes in transit.
class DocumentFragment extends Element {
  constructor() { super('#document-fragment'); this.nodeType = 11; }
}

function matchesCompound(node, compound) {
  const parts = compound.match(/(\[[^\]]*\]|[.#][^.#\[]+|^[a-zA-Z][a-zA-Z0-9-]*)/g) || [];
  for (const p of parts) {
    if (p.startsWith('.')) { if (!node.classList.contains(p.slice(1))) return false; }
    else if (p.startsWith('#')) { if (node.id !== p.slice(1)) return false; }
    else if (p.startsWith('[')) {
      const m = p.slice(1, -1).match(/^([^=]+?)(?:=["']?(.*?)["']?)?$/);
      if (!m) return false;
      const name = m[1].trim();
      if (!node.attributes.has(name)) return false;
      if (m[2] !== undefined && node.attributes.get(name) !== m[2]) return false;
    } else if (node.tagName !== p.toUpperCase()) return false;
  }
  return true;
}

// Descendant combinator only -- the production selectors never use `>`.
function matchesChain(node, group, root) {
  if (!matchesCompound(node, group[group.length - 1])) return false;
  let i = group.length - 2;
  let cur = node.parentNode;
  while (i >= 0) {
    if (!cur || cur === root.parentNode) return false;
    if (cur.nodeType === 1 && matchesCompound(cur, group[i])) i--;
    cur = cur.parentNode;
    if (!cur && i >= 0) return false;
  }
  return true;
}

const VOID_TAGS = new Set(['br', 'hr', 'img', 'input', 'meta', 'link', 'source']);
const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ' };

function decodeEntities(s) {
  return s.replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, (full, body) => {
    if (body[0] === '#') {
      const code = body[1] === 'x' || body[1] === 'X'
        ? parseInt(body.slice(2), 16) : parseInt(body.slice(1), 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : full;
    }
    return ENTITIES[body] !== undefined ? ENTITIES[body] : full;
  });
}

function parseHTML(html) {
  const roots = [];
  const stack = [];
  const push = node => (stack.length ? stack[stack.length - 1].appendChild(node) : roots.push(node));
  let i = 0;
  while (i < html.length) {
    if (html[i] !== '<') {
      const next = html.indexOf('<', i);
      const raw = html.slice(i, next < 0 ? html.length : next);
      if (raw) push(new TextNode(decodeEntities(raw)));
      i = next < 0 ? html.length : next;
      continue;
    }
    if (html.startsWith('</', i)) {
      const end = html.indexOf('>', i);
      stack.pop();
      i = end < 0 ? html.length : end + 1;
      continue;
    }
    // Open tag: read the name, then attributes, honouring quoted values so a
    // `/` inside an SVG path never looks like a self-closing marker.
    let j = i + 1;
    while (j < html.length && /[a-zA-Z0-9-]/.test(html[j])) j++;
    const tag = html.slice(i + 1, j);
    const el = new Element(tag);
    let selfClosing = false;
    while (j < html.length) {
      while (j < html.length && /\s/.test(html[j])) j++;
      if (html[j] === '/') { selfClosing = true; j++; continue; }
      if (html[j] === '>') { j++; break; }
      let k = j;
      while (k < html.length && !/[\s=>/]/.test(html[k])) k++;
      const name = html.slice(j, k);
      let value = '';
      while (k < html.length && /\s/.test(html[k])) k++;
      if (html[k] === '=') {
        k++;
        while (k < html.length && /\s/.test(html[k])) k++;
        const quote = html[k];
        if (quote === '"' || quote === "'") {
          const end = html.indexOf(quote, k + 1);
          value = html.slice(k + 1, end < 0 ? html.length : end);
          k = end < 0 ? html.length : end + 1;
        } else {
          let e = k;
          while (e < html.length && !/[\s>]/.test(html[e])) e++;
          value = html.slice(k, e);
          k = e;
        }
      }
      if (name) {
        if (name === 'class') el.className = decodeEntities(value);
        else el.setAttribute(name, decodeEntities(value));
      }
      j = k;
    }
    push(el);
    if (!selfClosing && !VOID_TAGS.has(tag.toLowerCase())) stack.push(el);
    i = j;
  }
  return roots;
}

function serialize(node) {
  if (node.nodeType === 3) return node.textContent;
  const tag = node.tagName.toLowerCase();
  return '<' + tag + '>' + node.childNodes.map(serialize).join('') + '</' + tag + '>';
}

const documentElement = new Element('html');
const document = {
  documentElement,
  activeElement: null,
  createElement: tag => new Element(tag),
  createDocumentFragment: () => new DocumentFragment(),
  // The view menu registers and unregisters an outside-click listener; the
  // tests never dispatch on the document, so recording the pair is enough.
  _listeners: [],
  addEventListener(type, fn) { document._listeners.push([type, fn]); },
  removeEventListener(type, fn) {
    document._listeners = document._listeners.filter(([t, f]) => t !== type || f !== fn);
  },
  getElementById(id) {
    let found = null;
    walk(documentElement, n => { if (!found && n.id === id) found = n; });
    return found;
  },
  querySelector: sel => documentElement.querySelector(sel),
  querySelectorAll: sel => documentElement.querySelectorAll(sel),
};

// Deterministic clock: the transient animator and the coalescing window are
// both timer driven, and real timers would make these tests slow and flaky.
const clock = { now: 0, seq: 1, timers: new Map() };
function setTimeout(fn, ms) {
  const id = clock.seq++;
  clock.timers.set(id, { fn, at: clock.now + (Number(ms) || 0) });
  return id;
}
function clearTimeout(id) { clock.timers.delete(id); }
// Intervals are the same machinery with a period: the elapsed counter reschedules
// itself, so a one-shot timer would tick once and the test would prove nothing
// about a turn that runs for minutes.
function setInterval(fn, ms) {
  const id = clock.seq++;
  const period = Math.max(1, Number(ms) || 0);
  clock.timers.set(id, { fn, at: clock.now + period, every: period });
  return id;
}
function clearInterval(id) { clock.timers.delete(id); }
clock.tick = function (ms) {
  const target = clock.now + ms;
  for (;;) {
    let next = null;
    for (const [id, timer] of clock.timers) {
      if (timer.at <= target && (!next || timer.at < next.timer.at)) next = { id, timer };
    }
    if (!next) break;
    clock.now = next.timer.at;
    if (next.timer.every) next.timer.at = clock.now + next.timer.every;
    else clock.timers.delete(next.id);
    next.timer.fn();
  }
  clock.now = target;
};
// The controller stamps turn start and reads elapsed off Date.now(); the same
// clock drives both, or a tick would move the timers without moving the time
// they are meant to measure.
const Date_ = { now: () => clock.now };

module.exports = { document, documentElement, Element, TextNode, DocumentFragment, clock,
                   setTimeout, clearTimeout, setInterval, clearInterval, Date: Date_ };
