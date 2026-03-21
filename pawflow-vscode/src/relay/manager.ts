import * as vscode from 'vscode';
import * as crypto from 'crypto';
import * as net from 'net';
import * as path from 'path';
import * as tls from 'tls';
import { AgentAPIClient } from '../api/client';
import { executeAction } from './actions';

/**
 * Generate relay ID matching PawCode CLI format:
 * cli_{username}_{sha256(username:directory)[:8]}
 */
function generateRelayId(username: string, directory: string): string {
  // Normalize path to match Python's Path(directory).resolve() output
  // Python on Windows: C:\Projets\fssandbox (uppercase drive, backslashes)
  let normalized = path.resolve(directory);
  // Uppercase drive letter on Windows (Python's Path.resolve() does this)
  if (/^[a-z]:/.test(normalized)) {
    normalized = normalized[0].toUpperCase() + normalized.slice(1);
  }
  const h = crypto.createHash('sha256').update(`${username}:${normalized}`).digest('hex').slice(0, 8);
  return `cli_${username}_${h}`;
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

export class RelayManager implements vscode.Disposable {
  private socket: net.Socket | tls.TLSSocket | null = null;
  private relayId: string = '';
  private port: number = 0;
  private wsToken: string = '';
  private rootDir: string = '';
  private allowExec: boolean = true;
  private readonly: boolean = false;
  private running = false;
  private reconnectTimer: NodeJS.Timeout | null = null;
  private outputChannel: vscode.OutputChannel;
  private _onStatusChange = new vscode.EventEmitter<string>();
  readonly onDidChangeStatus = this._onStatusChange.event;

  constructor(private context: vscode.ExtensionContext) {
    this.outputChannel = vscode.window.createOutputChannel('PawFlow Relay');
  }

  get isRunning(): boolean { return this.running; }
  getRelayId(): string { return this.relayId; }

  async start(api: AgentAPIClient, username: string, workspaceDir: string, allowExec: boolean): Promise<void> {
    if (this.running) { await this.stop(api); }

    this.rootDir = workspaceDir;
    this.allowExec = allowExec;
    this.relayId = generateRelayId(username, workspaceDir);
    this.wsToken = crypto.randomBytes(24).toString('base64url');
    this.port = await findFreePort();

    // Cleanup old service
    try {
      const uninstResult = await api.sendAction('service_uninstall', { service_id: this.relayId });
      this.outputChannel.appendLine(`[Relay] Uninstall result: ${JSON.stringify(uninstResult).slice(0, 200)}`);
    } catch (e: any) {
      this.outputChannel.appendLine(`[Relay] Uninstall error (ok): ${e.message}`);
    }

    // Create service
    const configStr = `port=${this.port},path=/ws/relay,token=${this.wsToken},mode=readwrite`;
    const installResult = await api.sendAction('service_install', {
      service_type: 'filesystem',
      service_name: this.relayId,
      config_str: configStr,
    });
    this.outputChannel.appendLine(`[Relay] Install result: ${JSON.stringify(installResult).slice(0, 300)}`);

    if (installResult.error) {
      throw new Error(`Service install failed: ${installResult.error}`);
    }

    this.outputChannel.appendLine(`[Relay] Service created: ${this.relayId} on port ${this.port} (token=${this.wsToken.slice(0,8)}...)`);

    // Wait for WS listener to start (may need more time on first start)
    await new Promise(r => setTimeout(r, 3000));

    this.running = true;
    this._connect();
    this._onStatusChange.fire('running');
  }

  async stop(api: AgentAPIClient): Promise<void> {
    this.running = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.socket) {
      this.socket.destroy();
      this.socket = null;
    }
    if (this.relayId) {
      try { await api.sendAction('service_uninstall', { service_id: this.relayId }); } catch {}
      this.outputChannel.appendLine('[Relay] Service deleted');
    }
    this._onStatusChange.fire('stopped');
  }

  dispose(): void {
    this.running = false;
    if (this.socket) { this.socket.destroy(); }
    if (this.reconnectTimer) { clearTimeout(this.reconnectTimer); }
    this._onStatusChange.dispose();
    this.outputChannel.dispose();
  }

  private _connect(): void {
    if (!this.running) { return; }

    const host = 'localhost';
    const wsPath = '/ws/relay';

    this.outputChannel.appendLine(`[Relay] Connecting to wss://${host}:${this.port}${wsPath}`);

    // Connect with TLS (self-signed cert from FilesystemWSListener)
    const socket = tls.connect({
      host, port: this.port,
      rejectUnauthorized: false, // accept self-signed
    }, () => {
      // WS handshake
      const wsKey = crypto.randomBytes(16).toString('base64');
      const handshake = `GET ${wsPath} HTTP/1.1\r\nHost: ${host}:${this.port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: ${wsKey}\r\nSec-WebSocket-Version: 13\r\n\r\n`;
      socket.write(handshake);
    });

    this.socket = socket;

    let buffer = Buffer.alloc(0);
    let handshakeDone = false;

    socket.on('data', (chunk: Buffer) => {
      buffer = Buffer.concat([buffer, chunk]);

      if (!handshakeDone) {
        const headerEnd = buffer.indexOf('\r\n\r\n');
        if (headerEnd < 0) { return; }
        const header = buffer.slice(0, headerEnd).toString();
        if (!header.includes('101')) {
          this.outputChannel.appendLine('[Relay] Handshake failed');
          socket.destroy();
          this._scheduleReconnect();
          return;
        }
        handshakeDone = true;
        buffer = buffer.slice(headerEnd + 4);

        // Send registration
        const regMsg = JSON.stringify({
          type: 'register',
          token: this.wsToken,
          secret: this.wsToken,
          relay_type: 'filesystem',
          relay_id: this.relayId,
          info: { platform: process.platform, root: this.rootDir, mode: 'readwrite' },
        });
        this._wsSend(socket, regMsg);
        this.outputChannel.appendLine(`[Relay] Sent registration for ${this.relayId} (token=${this.wsToken.slice(0,8)}...)`);
      }

      // Parse WS frames
      while (buffer.length >= 2) {
        const frame = this._wsReadFrame(buffer);
        if (!frame) { break; }
        buffer = buffer.slice(frame.totalLength);

        if (frame.opcode === 0x08) { // close
          socket.destroy();
          this._scheduleReconnect();
          return;
        }
        if (frame.opcode === 0x09) { // ping
          this._wsSend(socket, frame.payload.toString(), 0x0A);
          continue;
        }
        if (frame.opcode !== 0x01) { continue; } // only text frames

        try {
          const msg = JSON.parse(frame.payload.toString('utf-8'));
          if (msg.type === 'registered') {
            this.outputChannel.appendLine(`[Relay] ✓ Server confirmed registration: ${msg.relay_id || this.relayId}`);
            this._onStatusChange.fire('running');
          } else if (msg.type === 'error') {
            this.outputChannel.appendLine(`[Relay] ✗ Server error: ${msg.message || JSON.stringify(msg)}`);
          } else if (msg.type === 'command') {
            this._handleCommand(socket, msg);
          } else if (msg.type === 'ping') {
            this._wsSend(socket, JSON.stringify({ type: 'pong' }));
          }
        } catch {}
      }
    });

    socket.on('error', (e) => {
      this.outputChannel.appendLine(`[Relay] Error: ${e.message}`);
      this._scheduleReconnect();
    });

    socket.on('close', () => {
      this._scheduleReconnect();
    });

    socket.setTimeout(60000, () => {
      this._wsSend(socket, JSON.stringify({ type: 'ping' }));
    });
  }

  private _handleCommand(socket: net.Socket | tls.TLSSocket, msg: any): void {
    const action = msg.action || '';
    const relPath = msg.path || '.';
    const requestId = msg.request_id || '';

    const result = executeAction(this.rootDir, action, relPath, msg, this.readonly, this.allowExec);

    const response = JSON.stringify({
      type: 'result',
      request_id: requestId,
      data: result.ok ? result.data : result,
    });
    this._wsSend(socket, response);
  }

  private _scheduleReconnect(): void {
    if (!this.running) { return; }
    this.reconnectTimer = setTimeout(() => {
      if (this.running) { this._connect(); }
    }, 2000);
  }

  // ── WebSocket frame helpers ──

  private _wsSend(socket: net.Socket | tls.TLSSocket, data: string, opcode = 0x01): void {
    const payload = Buffer.from(data, 'utf-8');
    const maskKey = crypto.randomBytes(4);
    const masked = Buffer.alloc(payload.length);
    for (let i = 0; i < payload.length; i++) {
      masked[i] = payload[i] ^ maskKey[i % 4];
    }

    let header: Buffer;
    if (payload.length < 126) {
      header = Buffer.from([0x80 | opcode, 0x80 | payload.length]);
    } else if (payload.length < 65536) {
      header = Buffer.alloc(4);
      header[0] = 0x80 | opcode;
      header[1] = 0x80 | 126;
      header.writeUInt16BE(payload.length, 2);
    } else {
      header = Buffer.alloc(10);
      header[0] = 0x80 | opcode;
      header[1] = 0x80 | 127;
      header.writeBigUInt64BE(BigInt(payload.length), 2);
    }

    socket.write(Buffer.concat([header, maskKey, masked]));
  }

  private _wsReadFrame(buf: Buffer): { opcode: number; payload: Buffer; totalLength: number } | null {
    if (buf.length < 2) { return null; }
    const opcode = buf[0] & 0x0F;
    const masked = !!(buf[1] & 0x80);
    let payloadLen = buf[1] & 0x7F;
    let offset = 2;

    if (payloadLen === 126) {
      if (buf.length < 4) { return null; }
      payloadLen = buf.readUInt16BE(2);
      offset = 4;
    } else if (payloadLen === 127) {
      if (buf.length < 10) { return null; }
      payloadLen = Number(buf.readBigUInt64BE(2));
      offset = 10;
    }

    if (masked) {
      if (buf.length < offset + 4 + payloadLen) { return null; }
      const mask = buf.slice(offset, offset + 4);
      offset += 4;
      const data = Buffer.alloc(payloadLen);
      for (let i = 0; i < payloadLen; i++) {
        data[i] = buf[offset + i] ^ mask[i % 4];
      }
      return { opcode, payload: data, totalLength: offset + payloadLen };
    }

    if (buf.length < offset + payloadLen) { return null; }
    return { opcode, payload: buf.slice(offset, offset + payloadLen), totalLength: offset + payloadLen };
  }
}
