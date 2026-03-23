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
  'git_commit', 'git_push', 'exec', 'batch_edit', 'apply_patch',
  'edit_notebook', 'git_add', 'git_reset', 'git_stash', 'git_branch',
  'git_merge', 'git_rebase', 'git_cherry_pick', 'git_tag', 'project_init',
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
        let count = text.split(oldString).length - 1;

        // Fuzzy match via diff-match-patch when exact match fails
        if (count === 0) {
          const dmp = new diff_match_patch();
          dmp.Match_Threshold = 0.5;
          dmp.Match_Distance = 2000;

          // Strategy 1: find old_string fuzzily in text, then replace
          const loc = dmp.match_main(text, oldString.slice(0, 64), 0);
          if (loc !== -1) {
            // Found approximate location — find the best end boundary
            const endLoc = dmp.match_main(text, oldString.slice(-64), loc + oldString.length - 100);
            const actualEnd = endLoc !== -1 ? endLoc + 64 : loc + oldString.length;
            // Replace the fuzzy-matched region
            text = text.slice(0, loc) + newString + text.slice(actualEnd);
            fs.writeFileSync(absPath, text, 'utf-8');
            return { ok: true, data: { replacements: 1, fuzzy: true, match_offset: loc, path: rel(absPath, rootDir) } };
          }

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
        const timeout = Math.min((req.timeout || 30) * 1000, 120000);
        if (!command) { return { ok: false, error: 'Missing command' }; }
        // Use bash on Windows — cmd.exe breaks python -c with nested quotes.
        // Convert Windows paths to Unix-style for bash compatibility.
        let shellOpt: Record<string, any> = {};
        let execCommand = command;
        if (process.platform === 'win32') {
          const wslBash = 'C:\\Windows\\System32\\bash.exe';
          const gitBash = 'C:\\Program Files\\Git\\bin\\bash.exe';
          // Prefer WSL (has its own python with /c/ paths) over Git Bash
          // (uses Windows python which doesn't understand /c/)
          if (fs.existsSync(wslBash)) {
            shellOpt = { shell: wslBash };
            // WSL: convert ALL paths C:\x and C:/x to /mnt/c/x
            execCommand = command
              .replace(/([A-Z]):\\([^ ]*)/gi, (_m: string, d: string, r: string) =>
                '/mnt/' + d.toLowerCase() + '/' + r.replace(/\\/g, '/'))
              .replace(/([A-Z]):\/([^ ]*)/gi, (_m: string, d: string, r: string) =>
                '/mnt/' + d.toLowerCase() + '/' + r);
          } else if (fs.existsSync(gitBash)) {
            shellOpt = { shell: gitBash };
            // Git Bash: only convert cd paths (python.exe still uses C:/ paths)
            execCommand = command.replace(
              /^(cd\s+)([A-Z]):[\\\/]([^ &"']*)/i,
              (_m: string, cd: string, drive: string, rest: string) =>
                cd + '/' + drive.toLowerCase() + '/' + rest.replace(/\\/g, '/')
            );
          } else {
            // Fallback: PowerShell (handles quotes better than cmd.exe)
            const pwsh = 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe';
            if (fs.existsSync(pwsh)) { shellOpt = { shell: pwsh }; }
            // else: default cmd.exe
          }
        }
        try {
          const result = cp.execSync(execCommand, {
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

      // ── Missing git actions ──

      case 'git_add': {
        const files = req.files || [];
        if (!files.length) { return { ok: false, error: 'Missing files' }; }
        const r = gitRun(absPath, ['add', '--', ...files]);
        return { ok: true, data: { output: r.stdout, error: r.stderr } };
      }

      case 'git_reset': {
        const mode = req.mode || 'mixed';
        const ref = req.ref || 'HEAD';
        const files = req.files || [];
        const args = files.length ? ['reset', '--', ...files] : ['reset', `--${mode}`, ref];
        const r = gitRun(absPath, args);
        return { ok: true, data: { output: r.stdout, error: r.stderr } };
      }

      case 'git_stash': {
        const sub = (req.sub || 'push').toLowerCase();
        const idx = req.index || 0;
        let args: string[];
        if (sub === 'list') args = ['stash', 'list'];
        else if (sub === 'pop') args = ['stash', 'pop'];
        else if (sub === 'drop') args = ['stash', 'drop', String(idx)];
        else if (sub === 'apply') args = ['stash', 'apply', String(idx)];
        else args = ['stash', 'push', '-m', req.message || 'stash'];
        const r = gitRun(absPath, args);
        return { ok: true, data: { output: r.stdout, error: r.stderr } };
      }

      case 'git_branch': {
        const branch = req.branch || '';
        const del = req.delete || false;
        const force = req.force || false;
        const base = req.base || '';
        if (!branch) {
          const r = gitRun(absPath, ['branch', '-a']);
          return { ok: true, data: { branches: r.stdout.trim().split('\n') } };
        }
        if (del) {
          const r = gitRun(absPath, ['branch', force ? '-D' : '-d', branch]);
          return { ok: true, data: { output: r.stdout, error: r.stderr } };
        }
        const args = base ? ['branch', branch, base] : ['branch', branch];
        const r = gitRun(absPath, args);
        return { ok: true, data: { output: r.stdout, branch } };
      }

      case 'git_merge': {
        const branch = req.branch || req.ref || '';
        if (!branch) { return { ok: false, error: 'Missing branch' }; }
        const args = req.no_ff ? ['merge', '--no-ff', branch] : ['merge', branch];
        const r = gitRun(absPath, args, 60000);
        return { ok: true, data: { output: r.stdout, error: r.stderr } };
      }

      case 'git_rebase': {
        const onto = req.onto || req.ref || '';
        if (!onto) { return { ok: false, error: 'Missing onto' }; }
        const r = gitRun(absPath, ['rebase', onto], 60000);
        return { ok: true, data: { output: r.stdout, error: r.stderr } };
      }

      case 'git_cherry_pick': {
        const commits = req.commits || [];
        if (!commits.length) { return { ok: false, error: 'Missing commits' }; }
        const r = gitRun(absPath, ['cherry-pick', ...commits], 60000);
        return { ok: true, data: { output: r.stdout, error: r.stderr } };
      }

      case 'git_tag': {
        const tag = req.tag || '';
        if (!tag) {
          const r = gitRun(absPath, ['tag', '-l']);
          return { ok: true, data: { tags: r.stdout.trim().split('\n').filter(Boolean) } };
        }
        const message = req.message || '';
        const args = message ? ['tag', '-a', tag, '-m', message] : ['tag', tag];
        const r = gitRun(absPath, args);
        return { ok: true, data: { output: r.stdout, tag } };
      }

      case 'git_blame': {
        const file = req.file || relPath;
        const startLine = req.start_line || 0;
        const endLine = req.end_line || 0;
        const args = ['blame', '--porcelain'];
        if (startLine && endLine) args.push(`-L${startLine},${endLine}`);
        const rPath = resolvePath(rootDir, file);
        if (!rPath) { return { ok: false, error: 'Invalid file path' }; }
        args.push(rPath);
        const r = gitRun(rootDir, args);
        return { ok: true, data: { output: r.stdout.slice(0, 20000) } };
      }

      case 'git_worktree_list': {
        const r = gitRun(absPath, ['worktree', 'list', '--porcelain']);
        return { ok: true, data: { output: r.stdout } };
      }

      case 'git_worktree_add': {
        const wPath = req.worktree_path || '';
        const branch = req.branch || '';
        if (!wPath) { return { ok: false, error: 'Missing worktree_path' }; }
        const args = ['worktree', 'add'];
        if (req.create_new_branch) args.push('-b', branch || 'new-branch');
        args.push(wPath);
        if (branch && !req.create_new_branch) args.push(branch);
        const r = gitRun(absPath, args);
        return { ok: true, data: { output: r.stdout, error: r.stderr } };
      }

      case 'git_worktree_remove': {
        const wPath = req.worktree_path || '';
        if (!wPath) { return { ok: false, error: 'Missing worktree_path' }; }
        const r = gitRun(absPath, ['worktree', 'remove', wPath]);
        return { ok: true, data: { output: r.stdout, error: r.stderr } };
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
