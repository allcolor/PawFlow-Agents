import * as vscode from 'vscode';
import * as cp from 'child_process';
import * as crypto from 'crypto';
import * as path from 'path';
import { AgentAPIClient } from '../api/client';

export class RelayManager implements vscode.Disposable {
  private process: cp.ChildProcess | null = null;
  private relayId: string = '';
  private port: number = 0;
  private wsToken: string = '';
  private outputChannel: vscode.OutputChannel;
  private _onStatusChange = new vscode.EventEmitter<string>();
  readonly onDidChangeStatus = this._onStatusChange.event;

  constructor(private context: vscode.ExtensionContext) {
    this.outputChannel = vscode.window.createOutputChannel('PawFlow Relay');
  }

  get isRunning(): boolean { return this.process !== null; }
  getRelayId(): string { return this.relayId; }

  async start(api: AgentAPIClient, username: string, workspaceDir: string, allowExec: boolean): Promise<void> {
    if (this.process) { await this.stop(api); }

    const hash = crypto.createHash('sha256').update(`${username}:${workspaceDir}`).digest('hex').slice(0, 8);
    this.relayId = `vscode_${username}_${hash}`;
    this.wsToken = crypto.randomBytes(24).toString('base64url');

    // Find free port
    const net = await import('net');
    this.port = await new Promise<number>((resolve) => {
      const srv = net.createServer();
      srv.listen(0, () => {
        const port = (srv.address() as any).port;
        srv.close(() => resolve(port));
      });
    });

    // Cleanup old service
    try { await api.sendAction('service_uninstall', { service_id: this.relayId }); } catch {}

    // Create service
    const configStr = `port=${this.port},path=/ws/relay,token=${this.wsToken},mode=readwrite`;
    await api.sendAction('service_install', {
      service_type: 'filesystem',
      service_name: this.relayId,
      config_str: configStr,
    });

    // Wait for WS listener
    await new Promise(r => setTimeout(r, 1500));

    // Find relay script
    const config = vscode.workspace.getConfiguration('pawflow');
    const pythonPath = config.get<string>('pythonPath', 'python');
    let relayScript = config.get<string>('relayScriptPath', '');
    if (!relayScript) {
      // Try to find it relative to the workspace
      const candidates = [
        path.join(workspaceDir, 'tools', 'pawflow_relay.py'),
        path.join(workspaceDir, '..', 'PyFi2', 'tools', 'pawflow_relay.py'),
      ];
      for (const c of candidates) {
        try {
          await vscode.workspace.fs.stat(vscode.Uri.file(c));
          relayScript = c;
          break;
        } catch {}
      }
    }
    if (!relayScript) {
      throw new Error('Cannot find pawflow_relay.py. Set pawflow.relayScriptPath in settings.');
    }

    const wsUrl = `wss://localhost:${this.port}/ws/relay`;
    this.process = cp.spawn(pythonPath, [
      relayScript,
      '--server', wsUrl,
      '--relay-id', this.relayId,
      '--token', this.wsToken,
      '--dir', workspaceDir,
      ...(allowExec ? ['--allow-exec'] : []),
    ], { stdio: ['ignore', 'pipe', 'pipe'] });

    this.process.stdout?.on('data', (d) => this.outputChannel.append(d.toString()));
    this.process.stderr?.on('data', (d) => this.outputChannel.append(d.toString()));

    this.process.on('exit', (code) => {
      this.outputChannel.appendLine(`[Relay] exited with code ${code}`);
      this.process = null;
      this._onStatusChange.fire('stopped');
    });

    this._onStatusChange.fire('running');
    this.outputChannel.appendLine(`[Relay] Started: ${this.relayId} on port ${this.port}`);
  }

  async stop(api: AgentAPIClient): Promise<void> {
    if (this.process) {
      this.process.kill('SIGTERM');
      this.process = null;
    }
    if (this.relayId) {
      try { await api.sendAction('service_uninstall', { service_id: this.relayId }); } catch {}
    }
    this._onStatusChange.fire('stopped');
  }

  dispose(): void {
    if (this.process) {
      this.process.kill('SIGTERM');
    }
    this._onStatusChange.dispose();
    this.outputChannel.dispose();
  }
}
