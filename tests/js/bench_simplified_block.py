"""Render the simplified block against the real stylesheet and the real controller.

Layout, height and legibility are not things a DOM stub can answer: the stub has
no CSS. This builds a page from template.html's own <style> and tasks/io/chat_ui/
turn_view.js as they ship, drives one turn through it, and leaves an HTML file a
browser can open. It is a bench, not a test -- what it proves is only what is
looked at in the screenshot it produces.

    python tests/js/bench_simplified_block.py [out.html]
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHAT_UI = ROOT / "tasks" / "io" / "chat_ui"


def page(out_dir: Path) -> str:
    template = (CHAT_UI / "template.html").read_text(encoding="utf-8")
    styles = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", template, re.S))
    # Side files rather than inline blocks: the stylesheet is the product's own
    # and carries sequences an inline <style> in a generated page cannot be
    # trusted to survive -- one of them ended the head early and the whole page
    # rendered as text.
    (out_dir / "bench_chat.css").write_text(styles, encoding="utf-8")
    (out_dir / "bench_turn_view.js").write_text(
        (CHAT_UI / "turn_view.js").read_text(encoding="utf-8"), encoding="utf-8")
    return f"""<!doctype html>
<html data-theme="dark"><head><meta charset="utf-8"><title>simplified block bench</title>
<link rel="stylesheet" href="bench_chat.css">
<style>body {{ margin: 0; }} #messages {{ display: flex; flex-direction: column;
  padding: 16px; gap: 4px; height: 100vh; overflow: auto; box-sizing: border-box; }}</style>
</head><body class="simplified-chat-view">
<div id="messages"></div>
<script>
function escapeHtml(s) {{ return String(s == null ? '' : s)
  .replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c])); }}
function t(k) {{ return k; }}
function displayAgentName(n) {{ return n; }}
</script>
<script src="bench_turn_view.js"></script>
<script>
const messages = document.getElementById('messages');
function row(id, cls, html) {{
  const el = document.createElement('div');
  el.className = 'msg ' + (cls || '');
  el.dataset.msgid = id;
  el.innerHTML = html || '';
  messages.appendChild(el);
  return el;
}}
turnViewSetMode('simplified');

// A finished turn, then a running one: the reader sees both states at once.
const u0 = row('u0', 'user', '<div>relis le patch et dis-moi ce qui casse</div>');
turnViewRegisterUser({{ msg_id: 'u0' }}, u0);
turnViewIngest('assistant', {{ msg_id: 'a0', agent_name: 'claude',
  llm_service: 'claude_code_interactive_llm_service', content: 'Trois defauts, tous reproduits.' }},
  row('a0', 'assistant', '<div>Trois defauts, tous reproduits. La suite complete passe, ' +
    'et le correctif tient sur les trois chemins.</div>'));
turnViewFinalize({{ msg_id: 'a0', final_msg_id: 'a0' }});

const u1 = row('u1', 'user', '<div>et le compteur dans le header ?</div>');
turnViewRegisterUser({{ msg_id: 'u1' }}, u1);
const call = row('c1', 'tool-call',
  '<div class="tc-head"><span class="tc-bullet pending"></span>' +
  '<strong>edit</strong> <code>tasks/io/chat_ui/turn_view.js</code></div>' +
  '<pre class="tc-args">old_string: "const TURN_CUE_LIFETIME_MS = 2600;"\\n' +
  'new_string: "const TURN_ELAPSED_TICK_MS = 1000;"</pre>');
turnViewIngest('tool_call', {{ msg_id: 'c1', tc_id: 'tc-1', agent_name: 'claude',
  llm_service: 'claude_code_interactive_llm_service' }}, call);
turnViewIngest('thinking', {{ msg_id: 't1',
  content: 'Le compteur doit geler a la fin: le total que le tour a pris vaut ' +
           'autant apres coup que pendant.' }}, row('t1', 'thinking', ''));
turnViewIngest('assistant', {{ msg_id: 'a1',
  content: 'Je place le compteur dans le header, a gauche du statut, et je le gele au done.' }},
  row('a1', 'assistant', '<div>Je place le compteur dans le header.</div>'));

// Measured, not eyeballed: the geometry of every cue body, dumped where the
// harness can read it back. A cue whose text is in the DOM, laid out, and
// zero pixels wide looks exactly like a cue with no text at all.
setTimeout(() => {{
  const dbg = document.createElement('pre');
  dbg.id = 'bench-metrics';
  dbg.style.cssText = 'position:fixed;right:8px;top:8px;font-size:11px;color:#8fb;max-width:44vw';
  const lines = [];
  for (const cue of document.querySelectorAll('.simple-turn-cue')) {{
    const body = cue.querySelector('.simple-turn-ephemeral-text, .simple-turn-ephemeral-node');
    const r = body ? body.getBoundingClientRect() : null;
    const cs = body ? getComputedStyle(body) : null;
    const cr = cue.getBoundingClientRect();
    lines.push([cue.className,
      'cue ' + Math.round(cr.width) + 'x' + Math.round(cr.height),
      r ? 'body ' + Math.round(r.width) + 'x' + Math.round(r.height) : 'no-body',
      body ? 'scroll ' + body.scrollHeight : '',
      cs ? cs.display + ' mh=' + cs.maxHeight + ' lh=' + cs.lineHeight + ' fs=' + cs.fontSize : '',
      'op=' + cue.style.opacity,
      JSON.stringify((body ? body.textContent : '').slice(0, 24))].join(' | '));
    for (const child of cue.children) {{
      const b = child.getBoundingClientRect();
      lines.push('    child ' + child.className + ' ' + Math.round(b.width) + 'x'
        + Math.round(b.height) + ' ' + getComputedStyle(child).flex);
    }}
  }}
  const surface = document.querySelector('.turn-working .simple-turn-ephemeral');
  if (surface) lines.push('surface ' + Math.round(surface.getBoundingClientRect().height) + 'px');
  // Bisect, in one load: toggle one property at a time on the offending body
  // and report the height after each. Whichever line moves it off zero is the
  // one to blame -- no more guessing at a cascade from the outside.
  const victim = document.querySelector('.simple-turn-cue.messages .simple-turn-ephemeral-text');
  if (victim) {{
    const h = () => Math.round(victim.getBoundingClientRect().height);
    lines.push('bisect as-is ' + h());
    victim.style.overflow = 'visible'; lines.push('  overflow:visible -> ' + h());
    victim.style.maxHeight = 'none'; lines.push('  max-height:none -> ' + h());
    victim.style.flex = '0 1 auto'; lines.push('  flex:0 1 auto -> ' + h());
    victim.parentNode.style.alignItems = 'stretch'; lines.push('  cue align:stretch -> ' + h());
    surface.style.height = 'auto'; lines.push('  surface height:auto -> ' + h());
  }}
  dbg.textContent = lines.join('\\n');
  document.body.appendChild(dbg);
  window.__benchReady = true;
}}, 700);
</script>
</body></html>
"""


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/bench_simplified.html")
    out.write_text(page(out.parent), encoding="utf-8")
    print(out)
