import * as vscode from 'vscode';
import * as crypto from 'crypto';
import * as net from 'net';
import * as path from 'path';
import * as cp from 'child_process';
import { AgentAPIClient } from '../api/client';

/**
 * Generate relay ID — consistent across PawCode CLI, VSCode, and Python relay.
 * Format: fs_{username}_{sha256(username:normalized_dir)[:8]}
 */
function generateRelayId(username: string, directory: string): string {
  let normalized = path.resolve(directory);
  if (/^[a-z]:/.test(normalized)) {
    normalized = normalized[0].toUpperCase() + normalized.slice(1);
  }
  const h = crypto.createHash('sha256').update(`${username}:${normalized}`).digest('hex').slice(0, 8);
  return `fs_${username}_${h}`;
}

function findFreePort(): Promise<number> {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.listen(0, () => {
      const port = (srv.address() as net.AddressInfo).port;
      srv.close(() => resolve(port));
    });
  });
}

/**
 * RelayManager — launches the Python relay as subprocess or Docker container.
 *
 * No TypeScript relay implementation — all relay logic is in the unified
 * pawflow_relay Python package (same as PawCode CLI and standalone).
 */
export class RelayManager implements vscode.Disposable {
  private relayId: string = '';
  private port: number = 0;
  private wsToken: string = '';
  private rootDir: string = '';
  public getRootDir(): string { return this.rootDir; }
  private running = false;
  private outputChannel: vscode.OutputChannel;
  private dockerContainer: string = '';
  private dockerImage: string = '';
  private dockerCpus: string = '2';
  private dockerMemory: string = '4g';
  private relayProcess: cp.ChildProcess | null = null;
  private _onStatusChange = new vscode.EventEmitter<string>();
  readonly onDidChangeStatus = this._onStatusChange.event;

  constructor(private context: vscode.ExtensionContext) {
    this.outputChannel = vscode.window.createOutputChannel('PawFlow Relay');
  }

  get isRunning(): boolean { return this.running; }
  getRelayId(): string { return this.relayId; }

  async start(api: AgentAPIClient, username: string, workspaceDir: string, dockerImage: string = ''): Promise<void> {
    if (this.running) { await this.stop(api); }

    this.rootDir = workspaceDir;
    this.dockerImage = dockerImage;
    const config = vscode.workspace.getConfiguration('pawflow');
    this.dockerCpus = config.get<string>('dockerCpus', '2');
    this.dockerMemory = config.get<string>('dockerMemory', '4g');

    this._killDocker();
    this._killProcess();

    this.relayId = generateRelayId(username, workspaceDir);
    this.wsToken = crypto.randomBytes(24).toString('base64url');
    this.port = await findFreePort();

    // Cleanup old service
    try {
      await api.sendAction('service_uninstall', { service_id: this.relayId });
    } catch {}

    // Create service
    const configStr = `port=${this.port},path=/ws/relay,token=${this.wsToken},mode=readwrite`;
    await api.sendAction('service_install', {
      service_type: 'relay',
      service_name: this.relayId,
      config_str: configStr,
    });

    this.outputChannel.appendLine(`[Relay] Service created: ${this.relayId} on port ${this.port}`);

    // Wait for WS listener
    await new Promise(r => setTimeout(r, 1500));

    this.running = true;

    if (this.dockerImage) {
      await this._startDockerRelay();
    } else {
      this._startNativeRelay();
    }
    this._onStatusChange.fire(this.dockerImage ? 'running-docker' : 'running');
  }

  async stop(api?: AgentAPIClient): Promise<void> {
    this.running = false;
    this._killProcess();
    this._killDocker();
    if (api) {
      try {
        await api.sendAction('service_uninstall', { service_id: this.relayId });
      } catch {}
    }
    this._onStatusChange.fire('stopped');
  }

  dispose(): void {
    this._killProcess();
    this._killDocker();
  }

  /**
   * Start the Python relay as a native subprocess (no Docker).
   * Uses pawflow_relay package — same code as PawCode CLI.
   */
  private _startNativeRelay(): void {
    const serverUrl = vscode.workspace.getConfiguration('pawflow').get<string>('serverUrl', '');
    const wsUrl = `wss://localhost:${this.port}/ws/relay`;

    // Find python
    const python = this._findPython();
    if (!python) {
      this.outputChannel.appendLine('[Relay] Python not found — cannot start native relay');
      vscode.window.showErrorMessage('PawFlow: Python not found. Install Python or use Docker mode.');
      return;
    }

    // Launch: python tools/pawflow_relay.py --server wss://... --token ... --relay-id ... --dir ...
    const relayScript = path.join(this.rootDir, 'tools', 'pawflow_relay.py');
    const args = [
      relayScript,
      '--server', wsUrl,
      '--token', this.wsToken,
      '--relay-id', this.relayId,
      '--dir', this.rootDir,
      '--allow-exec',
      '--allow-automation',
    ];

    this.outputChannel.appendLine(`[Relay] Starting native relay: ${python} ${args.join(' ').slice(0, 200)}`);

    this.relayProcess = cp.spawn(python, args, {
      cwd: this.rootDir,
      stdio: ['ignore', 'pipe', 'pipe'],
    });

    this.relayProcess.stdout?.on('data', (data: Buffer) => {
      const msg = data.toString().trim();
      if (msg) this.outputChannel.appendLine(`[Relay] ${msg}`);
    });

    this.relayProcess.stderr?.on('data', (data: Buffer) => {
      const msg = data.toString().trim();
      if (msg) this.outputChannel.appendLine(`[Relay] ${msg}`);
    });

    this.relayProcess.on('exit', (code) => {
      this.outputChannel.appendLine(`[Relay] Native relay exited (code ${code})`);
      this.relayProcess = null;
      if (this.running) {
        // Auto-restart after 2s
        setTimeout(() => {
          if (this.running) {
            this.outputChannel.appendLine('[Relay] Restarting native relay...');
            this._startNativeRelay();
          }
        }, 2000);
      }
    });
  }

  /**
   * Start Docker container with Python relay inside.
   */
  private async _startDockerRelay(): Promise<void> {
    const safeId = this.relayId.slice(0, 12).replace(/[._]/g, '-');
    const containerName = `pf-${safeId}-relay-${crypto.randomBytes(4).toString('hex')}`;
    this.dockerContainer = containerName;

    const wsUrl = `wss://host.docker.internal:${this.port}/ws/relay`;
    const dockerArgs = [
      'docker', 'run', '-d',
      '--name', containerName,
      '-v', `${this.rootDir}:/workspace`,
      '--add-host', 'host.docker.internal:host-gateway',
      '--cpus', this.dockerCpus, '--memory', this.dockerMemory,
      '-e', 'HOME=/home/pawflow',
      '-e', 'USER=pawflow',
      '--shm-size', '512m',
      '--security-opt', 'no-new-privileges',
      this.dockerImage,
      'python3', '/opt/pawflow/pawflow_relay.py',
      '--server', wsUrl,
      '--token', this.wsToken,
      '--relay-id', this.relayId,
      '--dir', '/workspace',
      '--allow-exec',
      '--allow-automation',
      '--allow-local-screen',
    ];

    this.outputChannel.appendLine(`[Relay] Starting Docker container: ${containerName}`);
    try {
      cp.execSync(dockerArgs.join(' '), { encoding: 'utf-8', timeout: 30000 });
      this.outputChannel.appendLine(`[Relay] Container started: ${containerName}`);
    } catch (e: any) {
      this.outputChannel.appendLine(`[Relay] Docker start failed: ${e.stderr || e.message}`);
      this.dockerContainer = '';
    }
  }

  private _killDocker(): void {
    if (this.dockerContainer) {
      try {
        cp.execSync(`docker rm -f ${this.dockerContainer}`, { timeout: 10000 });
        this.outputChannel.appendLine(`[Relay] Killed container: ${this.dockerContainer}`);
      } catch {}
      this.dockerContainer = '';
    }
  }

  private _killProcess(): void {
    if (this.relayProcess) {
      try {
        this.relayProcess.kill();
      } catch {}
      this.relayProcess = null;
    }
  }

  private _findPython(): string | null {
    for (const bin of ['python3', 'python', 'py']) {
      try {
        const result = cp.execSync(`${bin} --version`, { encoding: 'utf-8', timeout: 5000 });
        if (result.includes('Python 3')) return bin;
      } catch {}
    }
    return null;
  }
}
