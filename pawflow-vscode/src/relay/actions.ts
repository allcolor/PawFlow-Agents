/**
 * Filesystem actions — TypeScript native implementation.
 * Mirrors tools/fs_actions.py for the most common operations.
 */

import * as fs from 'fs';
import * as path from 'path';
import * as cp from 'child_process';
import { diff_match_patch } from 'diff-match-patch';

const MAX_FILE_SIZE = 50 * 1024 * 1024;
const MAX_EXEC_OUTPUT = 10 * 1024 * 1024;

const WRITE_ACTIONS = new Set([
  'write_file', 'delete_file', 'mkdir', 'find_replace', 'edit',
  'exec', 'exec_stream', 'batch_edit', 'apply_patch',
  'edit_notebook', 'project_init',
  'screen_click', 'screen_double_click', 'screen_type', 'screen_key', 'screen_move', 'screen_scroll',
]);

// Resolve fs://service_id/path → real filesystem path
function resolveFsUrl(fsUrl: string, relayId: string, rootDir: string): string {
  const prefix = `fs://${relayId}/`;
  if (fsUrl.startsWith(prefix)) {
    return fsUrl.slice(prefix.length);
  }
  // Also handle generic fs://xxx/ where xxx is any relay
  const m = fsUrl.match(/^fs:\/\/[^/]+\/(.*)$/);
  if (m) { return m[1]; }
  return fsUrl;
}

// Convert absolute paths in text to fs:// URLs
function pathToFsUrl(text: string, relayId: string, rootDir: string): string {
  const root = path.resolve(rootDir).replace(/\\/g, '/');
  return text.replace(new RegExp(root.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '/?', 'gi'),
    `fs://${relayId}/`);
}

function resolvePath(rootDir: string, relPath: string): string | null {
  const root = path.resolve(rootDir);
  const target = path.resolve(root, relPath);
  if (!target.startsWith(root + path.sep) && target !== root) { return null; }
  return target;
}

function rel(absPath: string, root: string): string {
  try {
    return path.relative(root, absPath).replace(/\\/g, '/');
  } catch {
    return absPath;
  }
}

export function executeAction(
  rootDir: string, action: string, relPath: string, req: Record<string, any>,
  readonly: boolean, allowExec: boolean, relayId: string = '',
): { ok: boolean; data?: any; error?: string } {

  // Resolve fs:// URLs in path and all string fields
  const _resolveFs = (s: string) => resolveFsUrl(s, relayId, rootDir);
  relPath = _resolveFs(relPath);
  if (req.source_path) req.source_path = _resolveFs(req.source_path);
  if (req.dest_path) req.dest_path = _resolveFs(req.dest_path);

  if (readonly && WRITE_ACTIONS.has(action)) {
    return { ok: false, error: 'Operation not allowed in readonly mode' };
  }

  const absPathOrNull = resolvePath(rootDir, relPath);
  if (!absPathOrNull) {
    return { ok: false, error: `Path traversal blocked: ${relPath}` };
  }
  const absPath: string = absPathOrNull;

  try {
    switch (action) {
      case 'list_dir': {
        const entries = fs.readdirSync(absPath, { withFileTypes: true })
          .sort((a, b) => a.name.localeCompare(b.name))
          .map(e => {
            const st = fs.statSync(path.join(absPath, e.name));
            return {
              name: e.name,
              kind: e.isDirectory() ? 'directory' : 'file',
              size: e.isFile() ? st.size : 0,
              modified: st.mtime.toISOString(),
            };
          });
        return { ok: true, data: entries };
      }

      case 'read_file': {
        const size = fs.statSync(absPath).size;
        if (size > MAX_FILE_SIZE) {
          return { ok: false, error: `File too large (${size} bytes, max ${MAX_FILE_SIZE})` };
        }
        const content = fs.readFileSync(absPath);
        return { ok: true, data: { content: content.toString('base64'), size: content.length } };
      }

      case 'write_file': {
        const contentStr = req.content || '';
        const raw = req.base64 ? Buffer.from(contentStr, 'base64') : Buffer.from(contentStr, 'utf-8');
        const dir = path.dirname(absPath);
        if (!fs.existsSync(dir)) { fs.mkdirSync(dir, { recursive: true }); }
        fs.writeFileSync(absPath, raw);
        return { ok: true, data: { written: raw.length, path: rel(absPath, rootDir) } };
      }

      case 'delete_file': {
        if (fs.statSync(absPath).isDirectory()) {
          fs.rmSync(absPath, { recursive: true, force: true });
        } else {
          fs.unlinkSync(absPath);
        }
        return { ok: true, data: { deleted: rel(absPath, rootDir) } };
      }

      case 'mkdir': {
        fs.mkdirSync(absPath, { recursive: true });
        return { ok: true, data: { created: rel(absPath, rootDir) } };
      }

      case 'stat': {
        const st = fs.statSync(absPath);
        return { ok: true, data: {
          name: path.basename(absPath),
          kind: st.isDirectory() ? 'directory' : 'file',
          size: st.size,
          modified: st.mtime.toISOString(),
          created: st.birthtime.toISOString(),
        }};
      }

      case 'exists': {
        return { ok: true, data: { exists: fs.existsSync(absPath) } };
      }

      case 'search': {
        const pattern = req.pattern || '*';
        const recursive = req.recursive !== false;
        // Simple glob implementation
        const results: string[] = [];
        const walk = (dir: string): void => {
          for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
            const full = path.join(dir, entry.name);
            const relP = path.relative(absPath, full).replace(/\\/g, '/');
            if (matchGlob(entry.name, pattern)) { results.push(relP); }
            if (entry.isDirectory() && recursive && results.length < 500) { walk(full); }
          }
        }
        walk(absPath);
        return { ok: true, data: results.slice(0, 500) };
      }

      case 'grep': {
        const regex = req.regex || '';
        if (!regex) { return { ok: false, error: 'Missing regex parameter' }; }
        const compiled = new RegExp(regex, 'i');
        const recursive = req.recursive !== false;
        const results: any[] = [];
        const grepWalk = (dir: string): void => {
          for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
            const full = path.join(dir, entry.name);
            if (entry.isFile()) {
              try {
                const text = fs.readFileSync(full, 'utf-8');
                text.split('\n').forEach((line, i) => {
                  if (compiled.test(line) && results.length < 200) {
                    results.push({
                      path: path.relative(absPath, full).replace(/\\/g, '/'),
                      line_number: i + 1,
                      line: line.slice(0, 500),
                    });
                  }
                });
              } catch {}
            } else if (entry.isDirectory() && recursive) {
              grepWalk(full);
            }
          }
        }
        grepWalk(absPath);
        return { ok: true, data: results };
      }

      case 'edit': {
        const oldString = req.old_string || '';
        const newString = req.new_string || '';
        const replaceAll = req.replace_all || false;
        const startLine = req.start_line || 0;
        const endLine = req.end_line || 0;
        let text = fs.readFileSync(absPath, 'utf-8');

        if (startLine > 0 && endLine > 0) {
          // Line-based edit: replace lines start_line..end_line with new_string
          const lines = text.split('\n');
          const s = Math.max(0, startLine - 1);
          const e = Math.min(lines.length, endLine);
          const removed = lines.slice(s, e);
          const newLines = newString.split('\n');
          lines.splice(s, e - s, ...newLines);
          text = lines.join('\n');
          fs.writeFileSync(absPath, text, 'utf-8');
          return { ok: true, data: { lines_replaced: `${startLine}-${endLine}`, lines_removed: removed.length, lines_inserted: newLines.length, path: rel(absPath, rootDir) } };
        }

        if (!oldString) { return { ok: false, error: 'Missing old_string (or use start_line/end_line)' }; }
        let count = text.split(oldString).length - 1;

        // Fuzzy match when exact match fails
        if (count === 0) {
          // Strategy 1: diff-match-patch fuzzy find
          try {
            const dmp = new diff_match_patch();
            dmp.Match_Threshold = 0.5;
            dmp.Match_Distance = 2000;
            // match_main only handles patterns <= ~32 chars internally
            const pattern = oldString.split('\n')[0].trim().slice(0, 32);
            if (pattern.length >= 8) {
              const loc = dmp.match_main(text, pattern, 0);
              if (loc !== -1) {
                // Found start — find end via last line
                const lastLine = oldString.split('\n').pop()!.trim().slice(0, 32);
                const searchFrom = Math.max(0, loc + oldString.length - 200);
                const endLoc = lastLine.length >= 8 ? dmp.match_main(text, lastLine, searchFrom) : -1;
                const actualEnd = endLoc !== -1 ? endLoc + lastLine.length : loc + oldString.length;
                text = text.slice(0, loc) + newString + text.slice(actualEnd);
                fs.writeFileSync(absPath, text, 'utf-8');
                return { ok: true, data: { replacements: 1, fuzzy: true, match_offset: loc, path: rel(absPath, rootDir) } };
              }
            }
          } catch { /* diff-match-patch error — try next strategy */ }

          // Strategy 2: line-by-line trimmed match (whitespace tolerance)
          const oldLines = oldString.split('\n').map((l: string) => l.trim());
          const textLines = text.split('\n');
          for (let i = 0; i <= textLines.length - oldLines.length; i++) {
            let match = true;
            for (let j = 0; j < oldLines.length; j++) {
              if (textLines[i + j].trim() !== oldLines[j]) { match = false; break; }
            }
            if (match) {
              const newLines = newString.split('\n');
              textLines.splice(i, oldLines.length, ...newLines);
              text = textLines.join('\n');
              fs.writeFileSync(absPath, text, 'utf-8');
              return { ok: true, data: { replacements: 1, fuzzy: true, line: i + 1, path: rel(absPath, rootDir) } };
            }
          }

          // All fuzzy strategies failed — give helpful hint
          const lines = text.split('\n');
          const needle = oldString.split('\n')[0].trim();
          let bestLine = -1, bestScore = 0;
          for (let li = 0; li < lines.length; li++) {
            const line = lines[li].trim();
            if (needle && line.includes(needle.slice(0, 30))) {
              const score = line.length;
              if (score > bestScore) { bestScore = score; bestLine = li + 1; }
            }
          }
          const hint = bestLine > 0
            ? ` Closest match near line ${bestLine}: "${lines[bestLine-1].trim().slice(0, 80)}". Try edit with start_line=${bestLine}/end_line=${bestLine}.`
            : ' Try using start_line/end_line instead of old_string.';
          return { ok: false, error: `old_string not found in ${path.basename(absPath)}.${hint}` };
        }
        if (count > 1 && !replaceAll) { return { ok: false, error: `old_string found ${count} times (use replace_all)` }; }
        if (replaceAll) {
          text = text.split(oldString).join(newString);
        } else {
          text = text.replace(oldString, newString);
        }
        fs.writeFileSync(absPath, text, 'utf-8');
        return { ok: true, data: { replacements: replaceAll ? count : 1, path: rel(absPath, rootDir) } };
      }

      case 'apply_patch': {
        const patchText = req.patch || '';
        if (!patchText) { return { ok: false, error: 'Missing patch content' }; }
        const dmp = new diff_match_patch();
        const patches = dmp.patch_fromText(patchText);
        let text = fs.readFileSync(absPath, 'utf-8');
        dmp.Match_Threshold = 0.4;
        dmp.Patch_DeleteThreshold = 0.4;
        const [patched, results] = dmp.patch_apply(patches, text);
        const applied = results.filter((r: boolean) => r).length;
        const failed = results.length - applied;
        if (applied === 0) { return { ok: false, error: 'No hunks applied' }; }
        fs.writeFileSync(absPath, patched, 'utf-8');
        return { ok: true, data: { hunks_applied: applied, hunks_failed: failed, path: rel(absPath, rootDir) } };
      }

      case 'batch_edit': {
        const edits = req.edits || [];
        if (!edits.length) { return { ok: false, error: 'Missing edits list' }; }
        const results: any[] = [];
        const filesModified = new Set<string>();
        for (const edit of edits) {
          const ePath = edit.path || '';
          const oldStr = edit.old_string || '';
          const newStr = edit.new_string || '';
          const replAll = edit.replace_all || false;
          if (!ePath || !oldStr) {
            results.push({ path: ePath, error: 'missing path or old_string' });
            continue;
          }
          const eAbs = path.resolve(rootDir, ePath);
          if (!eAbs.startsWith(rootDir)) {
            results.push({ path: ePath, error: 'path escapes root' });
            continue;
          }
          try {
            let text = fs.readFileSync(eAbs, 'utf-8');
            const count = text.split(oldStr).length - 1;
            if (count === 0) { results.push({ path: ePath, error: 'old_string not found' }); continue; }
            if (count > 1 && !replAll) { results.push({ path: ePath, error: `found ${count} times (use replace_all)` }); continue; }
            text = replAll ? text.split(oldStr).join(newStr) : text.replace(oldStr, newStr);
            fs.writeFileSync(eAbs, text, 'utf-8');
            filesModified.add(ePath);
            results.push({ path: ePath, replacements: replAll ? count : 1 });
          } catch (e: any) {
            results.push({ path: ePath, error: e.message });
          }
        }
        return { ok: true, data: {
          edits_applied: results.filter(r => r.replacements).length,
          files_modified: Array.from(filesModified).sort(),
          details: results,
        }};
      }

      case 'exec': {
        if (!allowExec) { return { ok: false, error: 'Shell execution disabled' }; }
        const command = req.command || '';
        const timeout = req.timeout ? req.timeout * 1000 : undefined;
        if (!command) { return { ok: false, error: 'Missing command' }; }

        // Like Claude Code: use native Windows shell (cmd.exe).
        // For python -c with complex nested quotes, write to a temp file.
        let execCommand = command;
        if (process.platform === 'win32') {
          const pyMatch = command.match(/^((?:cd\s+[^&]+&&\s*)?)python(?:3)?\s+-c\s+["']([\s\S]+)["']\s*(.*)$/i);
          if (pyMatch) {
            const tmpDir = require('os').tmpdir();
            const tmpFile = path.join(tmpDir, `pawflow_exec_${Date.now()}.py`);
            fs.writeFileSync(tmpFile, pyMatch[2], 'utf-8');
            const prefix = pyMatch[1].trim();
            const suffix = pyMatch[3].trim();
            execCommand = prefix
              ? `${prefix} python "${tmpFile}" ${suffix}`
              : `python "${tmpFile}" ${suffix}`;
            try {
              const result = cp.execSync(execCommand, {
                cwd: rootDir, timeout, encoding: 'utf-8',
                maxBuffer: MAX_EXEC_OUTPUT,
                env: { ...process.env, PYTHONIOENCODING: 'utf-8', PAWFLOW_FS_ROOT: rootDir },
              });
              try { fs.unlinkSync(tmpFile); } catch {}
              return { ok: true, data: { stdout: result.slice(0, MAX_EXEC_OUTPUT), stderr: '', returncode: 0 } };
            } catch (e: any) {
              try { fs.unlinkSync(tmpFile); } catch {}
              return { ok: true, data: {
                stdout: (e.stdout || '').slice(0, MAX_EXEC_OUTPUT),
                stderr: (e.stderr || '').slice(0, 5000),
                returncode: e.status ?? 1,
              }};
            }
          }
        }

        try {
          const result = cp.execSync(execCommand, {
            cwd: rootDir, timeout, encoding: 'utf-8',
            maxBuffer: MAX_EXEC_OUTPUT,
            env: { ...process.env, PYTHONIOENCODING: 'utf-8', PAWFLOW_FS_ROOT: rootDir },
          });
          return { ok: true, data: { stdout: result.slice(0, MAX_EXEC_OUTPUT), stderr: '', returncode: 0 } };
        } catch (e: any) {
          return { ok: true, data: {
            stdout: (e.stdout || '').slice(0, MAX_EXEC_OUTPUT),
            stderr: (e.stderr || '').slice(0, MAX_EXEC_OUTPUT),
            returncode: e.status ?? -1,
          }};
        }
      }

      case 'exec_stream': {
        // exec_stream: uses spawnSync in worker thread, posts output chunks via parentPort.
        // The main thread forwards exec_output frames to the WS socket.
        if (!allowExec) { return { ok: false, error: 'Shell execution disabled' }; }
        const sCommand = req.command || '';
        const sTimeout = req.timeout ? req.timeout * 1000 : undefined;
        if (!sCommand) { return { ok: false, error: 'Missing command' }; }
        const emitOutput = (stream: string, data: string) => {
          try {
            const { parentPort: pp } = require('worker_threads');
            if (pp) { pp.postMessage({ _type: 'exec_output', stream, data }); }
          } catch {}
        };
        const spawnResult = cp.spawnSync(sCommand, {
          cwd: rootDir, shell: true, timeout: sTimeout,
          encoding: 'utf-8', maxBuffer: MAX_EXEC_OUTPUT,
          env: { ...process.env, PYTHONIOENCODING: 'utf-8', PAWFLOW_FS_ROOT: rootDir },
        });
        const so = (spawnResult.stdout || '').slice(0, MAX_EXEC_OUTPUT);
        const se = (spawnResult.stderr || '').slice(0, MAX_EXEC_OUTPUT);
        if (so) { emitOutput('stdout', so); }
        if (se) { emitOutput('stderr', se); }
        return { ok: true, data: { stdout: so, stderr: se, returncode: spawnResult.status ?? -1 } };
      }

      case 'project_context': {
        // Minimal project context
        const entries = fs.readdirSync(rootDir, { withFileTypes: true }).slice(0, 100).map(e => ({
          name: e.name, kind: e.isDirectory() ? 'dir' : 'file',
          size: e.isFile() ? fs.statSync(path.join(rootDir, e.name)).size : 0,
        }));
        const types: string[] = [];
        const names = new Set(entries.filter(e => e.kind === 'file').map(e => e.name));
        if (names.has('package.json')) types.push('Node.js');
        if (names.has('pyproject.toml') || names.has('requirements.txt')) types.push('Python');
        if (names.has('Cargo.toml')) types.push('Rust');
        if (names.has('go.mod')) types.push('Go');
        return { ok: true, data: { root: rootDir, files: entries, project_types: types } };
      }

      // ── File format actions ──

      case 'find_replace': {
        const pattern = req.pattern || '';
        const replacement = req.replacement || '';
        if (!pattern) { return { ok: false, error: 'Missing pattern' }; }
        let text = fs.readFileSync(absPath, 'utf-8');
        const regex = new RegExp(pattern, 'g');
        const matches = (text.match(regex) || []).length;
        if (matches === 0) { return { ok: true, data: { replacements: 0 } }; }
        text = text.replace(regex, replacement);
        fs.writeFileSync(absPath, text, 'utf-8');
        return { ok: true, data: { replacements: matches, path: rel(absPath, rootDir) } };
      }

      case 'read_pdf': {
        // PDFs can't be read natively in Node — return error suggesting exec
        return { ok: false, error: 'read_pdf not supported in relay. Use exec with python: python -c "import fitz; ..."' };
      }

      case 'read_notebook': {
        try {
          const raw = JSON.parse(fs.readFileSync(absPath, 'utf-8'));
          const cells = (raw.cells || []).map((c: any, i: number) => ({
            index: i, type: c.cell_type || 'code',
            source: (c.source || []).join(''),
            output: (c.outputs || []).map((o: any) => (o.text || []).join('')).join('\n').slice(0, 500),
          }));
          return { ok: true, data: { total_cells: cells.length, kernel: raw.metadata?.kernelspec?.display_name || '?', cells } };
        } catch (e: any) { return { ok: false, error: e.message }; }
      }

      case 'edit_notebook': {
        const cellIndex = req.cell_index ?? -1;
        const op = req.operation || 'edit';
        try {
          const raw = JSON.parse(fs.readFileSync(absPath, 'utf-8'));
          if (op === 'edit' && cellIndex >= 0 && cellIndex < raw.cells.length) {
            raw.cells[cellIndex].source = (req.new_source || '').split('\n').map((l: string) => l + '\n');
            if (req.cell_type) raw.cells[cellIndex].cell_type = req.cell_type;
          } else if (op === 'insert') {
            raw.cells.splice(cellIndex >= 0 ? cellIndex : raw.cells.length, 0, {
              cell_type: req.cell_type || 'code', source: (req.new_source || '').split('\n').map((l: string) => l + '\n'),
              metadata: {}, outputs: [],
            });
          } else if (op === 'delete' && cellIndex >= 0) {
            raw.cells.splice(cellIndex, 1);
          }
          fs.writeFileSync(absPath, JSON.stringify(raw, null, 1), 'utf-8');
          return { ok: true, data: { cells: raw.cells.length, operation: op } };
        } catch (e: any) { return { ok: false, error: e.message }; }
      }

      // ── Store operations (server-side, relay returns error) ──

      case 'copy_between':
      case 'copy_to_store':
      case 'list_store':
      case 'delete_from_store': {
        return { ok: false, error: `${action} is a server-side operation. Use the filesystem service on the server.` };
      }

      case 'project_init': {
        // Generate a basic .pawflow.md
        const mdPath = path.join(rootDir, '.pawflow.md');
        const content = `# PawFlow Project\n\nRoot: ${rootDir}\nGenerated: ${new Date().toISOString()}\n`;
        fs.writeFileSync(mdPath, content, 'utf-8');
        return { ok: true, data: { path: '.pawflow.md', size: content.length } };
      }

      // Screen automation — delegate to Python (nut-js is complex to bundle in VS Code)
      case 'screen_screenshot':
      case 'screen_click':
      case 'screen_double_click':
      case 'screen_type':
      case 'screen_key':
      case 'screen_move':
      case 'screen_scroll':
      case 'screen_mouse_position': {
        // Use Python pyautogui via subprocess — simpler than bundling native deps
        const screenAction = action.replace('screen_', '');
        const pyArgs = JSON.stringify(req).replace(/"/g, '\\"');
        const pyScript = `
import json, sys
args = json.loads("${pyArgs}")
action = "${screenAction}"
try:
    import pyautogui
    pyautogui.FAILSAFE = True
    if action == "screenshot":
        import mss, base64
        from mss.tools import to_png
        with mss.mss() as sct:
            img = sct.grab(sct.monitors[0])
            png = to_png(img.rgb, img.size)
        print(json.dumps(base64.b64encode(png).decode()))
    elif action == "click":
        pyautogui.click(int(args.get("x",0)), int(args.get("y",0)), button=args.get("button","left"))
        print(json.dumps({"x": args.get("x",0), "y": args.get("y",0)}))
    elif action == "double_click":
        pyautogui.doubleClick(int(args.get("x",0)), int(args.get("y",0)))
        print(json.dumps({"x": args.get("x",0), "y": args.get("y",0)}))
    elif action == "type":
        pyautogui.write(args.get("text",""), interval=0.02)
        print(json.dumps({"typed": len(args.get("text",""))}))
    elif action == "key":
        k = args.get("key","")
        if "+" in k:
            pyautogui.hotkey(*[x.strip() for x in k.split("+")])
        else:
            pyautogui.press(k)
        print(json.dumps({"pressed": k}))
    elif action == "move":
        pyautogui.moveTo(int(args.get("x",0)), int(args.get("y",0)), duration=0.2)
        print(json.dumps({"moved": True}))
    elif action == "scroll":
        pyautogui.scroll(int(args.get("amount",3)), x=int(args.get("x",0)), y=int(args.get("y",0)))
        print(json.dumps({"scrolled": args.get("amount",3)}))
    elif action == "mouse_position":
        p = pyautogui.position()
        print(json.dumps({"x": p.x, "y": p.y}))
except ImportError:
    print(json.dumps({"error": "pyautogui not installed. Run: pip install pyautogui mss"}))
    sys.exit(1)
`.trim();
        const tmpFile = path.join(require('os').tmpdir(), `pawflow_screen_${Date.now()}.py`);
        fs.writeFileSync(tmpFile, pyScript, 'utf-8');
        try {
          const result = cp.execSync(`python "${tmpFile}"`, { encoding: 'utf-8' });
          fs.unlinkSync(tmpFile);
          const parsed = JSON.parse(result.trim());
          if (parsed.error) { return { ok: false, error: parsed.error }; }
          return { ok: true, data: parsed };
        } catch (e: any) {
          try { fs.unlinkSync(tmpFile); } catch {}
          return { ok: false, error: `Screen action failed: ${e.message}` };
        }
      }

      default:
        return { ok: false, error: `Unknown action: ${action}` };
    }
  } catch (e: any) {
    return { ok: false, error: e.message };
  }
}

function matchGlob(name: string, pattern: string): boolean {
  const regex = pattern.replace(/\./g, '\\.').replace(/\*/g, '.*').replace(/\?/g, '.');
  return new RegExp(`^${regex}$`, 'i').test(name);
}
