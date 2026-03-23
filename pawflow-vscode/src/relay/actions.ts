/**
 * Filesystem actions — TypeScript native implementation.
 * Mirrors tools/fs_actions.py for the most common operations.
 */

import * as fs from 'fs';
import * as path from 'path';
import * as cp from 'child_process';

const MAX_FILE_SIZE = 50 * 1024 * 1024;
const MAX_EXEC_OUTPUT = 10 * 1024 * 1024;

const WRITE_ACTIONS = new Set([
  'write_file', 'delete_file', 'mkdir', 'find_replace', 'edit',
  'git_commit', 'git_push', 'exec', 'batch_edit', 'apply_patch',
  'edit_notebook', 'git_add', 'git_reset', 'git_stash', 'git_branch',
  'git_merge', 'git_rebase', 'git_cherry_pick', 'git_tag', 'project_init',
]);

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

function gitRun(cwd: string, args: string[], timeout = 30000): { stdout: string; stderr: string; returncode: number } {
  try {
    const result = cp.spawnSync('git', args, { cwd, timeout, encoding: 'utf-8' });
    return { stdout: result.stdout || '', stderr: result.stderr || '', returncode: result.status ?? -1 };
  } catch (e: any) {
    return { stdout: '', stderr: e.message, returncode: -1 };
  }
}

export function executeAction(
  rootDir: string, action: string, relPath: string, req: Record<string, any>,
  readonly: boolean, allowExec: boolean,
): { ok: boolean; data?: any; error?: string } {

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
        function walk(dir: string) {
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
        function grepWalk(dir: string) {
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
        const count = text.split(oldString).length - 1;
        if (count === 0) {
          // Find closest match to help the LLM
          const lines = text.split('\n');
          const needle = oldString.split('\n')[0].trim();
          let bestLine = -1, bestScore = 0;
          for (let li = 0; li < lines.length; li++) {
            const line = lines[li].trim();
            if (line.includes(needle.slice(0, 30)) || needle.includes(line.slice(0, 30))) {
              const score = Math.min(line.length, needle.length);
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
        const timeout = Math.min((req.timeout || 30) * 1000, 120000);
        if (!command) { return { ok: false, error: 'Missing command' }; }
        // Use bash on Windows — cmd.exe breaks python -c with nested quotes.
        // Try Git Bash first (native FS access), then WSL bash, then cmd.exe.
        let shellOpt: Record<string, any> = {};
        if (process.platform === 'win32') {
          const gitBash = 'C:\\Program Files\\Git\\bin\\bash.exe';
          const wslBash = 'C:\\Windows\\System32\\bash.exe';
          if (fs.existsSync(gitBash)) { shellOpt = { shell: gitBash }; }
          else if (fs.existsSync(wslBash)) { shellOpt = { shell: wslBash }; }
        }
        try {
          const result = cp.execSync(command, {
            cwd: rootDir, timeout, encoding: 'utf-8', ...shellOpt,
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

      // Git actions
      case 'git_status': {
        const br = gitRun(absPath, ['branch', '--show-current']);
        const st = gitRun(absPath, ['status', '--porcelain']);
        const staged: string[] = [], modified: string[] = [], untracked: string[] = [];
        for (const line of st.stdout.split('\n')) {
          if (line.length < 3) continue;
          const [x, y] = [line[0], line[1]];
          const fname = line.slice(3);
          if (x === '?' && y === '?') untracked.push(fname);
          else if (x !== ' ' && x !== '?') staged.push(fname);
          if (y !== ' ' && y !== '?') modified.push(fname);
        }
        return { ok: true, data: { branch: br.stdout.trim() || 'HEAD', staged, modified, untracked } };
      }

      case 'git_log': {
        const count = req.count || 10;
        const r = gitRun(absPath, ['log', `-${count}`, '--format=%H|%ai|%s']);
        const entries = r.stdout.trim().split('\n').filter(Boolean).map(line => {
          const [hash, date, ...msg] = line.split('|');
          return { hash, date, message: msg.join('|') };
        });
        return { ok: true, data: entries };
      }

      case 'git_diff': {
        const ref = req.ref || '';
        const args = ref ? ['diff', ref] : ['diff'];
        const r = gitRun(absPath, args);
        return { ok: true, data: { diff: r.stdout.slice(0, 50000) } };
      }

      case 'git_commit': {
        const message = req.message || '';
        if (!message) { return { ok: false, error: 'Missing message' }; }
        const files = req.files || [];
        if (files.length) {
          gitRun(absPath, ['add', '--', ...files]);
        } else {
          gitRun(absPath, ['add', '-A']);
        }
        const r = gitRun(absPath, ['commit', '-m', message]);
        return { ok: true, data: { output: r.stdout, hash: '' } };
      }

      case 'git_pull': {
        const r = gitRun(absPath, ['pull'], 60000);
        return { ok: true, data: { output: r.stdout, error: r.stderr } };
      }

      case 'git_push': {
        const r = gitRun(absPath, ['push'], 120000);
        return { ok: true, data: { output: r.stdout, error: r.stderr } };
      }

      case 'git_checkout': {
        const ref = req.ref || '';
        if (!ref) { return { ok: false, error: 'Missing ref' }; }
        gitRun(absPath, ['checkout', ref]);
        return { ok: true, data: { branch: ref } };
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
