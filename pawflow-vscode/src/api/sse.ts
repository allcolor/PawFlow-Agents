import * as http from 'http';
import * as https from 'https';
import { URL } from 'url';
import { randomUUID } from 'crypto';
import { EventEmitter } from 'events';
import { SSE_PATH } from '../constants';
import { SSEEvent } from './types';

export class SSEClient extends EventEmitter {
  private serverUrl: string;
  private sessionToken: string;
  private gatewayCookie: string;
  private request: http.ClientRequest | null = null;
  private connected = false;
  private shouldReconnect = true;
  private retryDelay = 1000;
  private retryCount = 0;
  // True once we've connected at least once, so the next (re)connection is a
  // RECONNECT and must trigger a history catch-up.
  private everConnected = false;
  // Liveness watchdog: the server sends an sse_ping (and `: keepalive`
  // comment) every ~15s. If no bytes arrive for this long the socket is
  // silently half-open (laptop sleep, proxy idle-kill after going to the
  // browser for a while) even though Node never fired 'end'/'error' — force
  // a reconnect so the live stream resumes like the webchat does.
  private lastActivity = 0;
  private watchdog: NodeJS.Timeout | null = null;
  private static readonly STALE_MS = 45000;
  // Set when the previous stream was dead long enough that buffered replay
  // can't be trusted to cover the gap — the next successful connect then
  // emits 'reconnected' so the panel does a full history catch-up. A routine
  // ~10min server-lifetime bounce (pings were flowing) leaves this false, so
  // an idle session does NOT reload every few minutes.
  private pendingCatchUp = false;
  private static readonly CATCHUP_GAP_MS = 30000;
  // Stable per-client id sent on every (re)connect. Without it the server
  // can't correlate a reconnect with the stream it replaces (it keys stale-
  // subscriber replacement on conversation_id + client_id), so each watchdog/
  // lifetime/error reconnect would leak a server-side SSE subscriber until
  // the dead socket is lazily reaped. Generated once per SSEClient instance
  // and reused across all reconnects, mirroring the webchat's sessionStorage id.
  private readonly clientId = `vscode-${randomUUID()}`;

  constructor(serverUrl: string, sessionToken: string, gatewayCookie: string = '') {
    super();
    this.serverUrl = serverUrl.replace(/\/$/, '');
    this.sessionToken = sessionToken;
    this.gatewayCookie = gatewayCookie;
  }

  connect(conversationId: string): void {
    this.shouldReconnect = true;
    this._startWatchdog(conversationId);
    this._connect(conversationId);
  }

  disconnect(): void {
    this.shouldReconnect = false;
    if (this.watchdog) { clearInterval(this.watchdog); this.watchdog = null; }
    if (this.request) {
      this.request.destroy();
      this.request = null;
    }
    this.connected = false;
  }

  private _markGap(): void {
    // If the stream was quiet for a while before it dropped, the next
    // connect should catch up from history; a fresh drop (pings were
    // flowing) is covered by the server's buffered replay.
    if (this.lastActivity && Date.now() - this.lastActivity > SSEClient.CATCHUP_GAP_MS) {
      this.pendingCatchUp = true;
    }
  }

  private _startWatchdog(conversationId: string): void {
    if (this.watchdog) { clearInterval(this.watchdog); }
    this.watchdog = setInterval(() => {
      if (!this.shouldReconnect || !this.lastActivity) { return; }
      if (Date.now() - this.lastActivity > SSEClient.STALE_MS) {
        // Stream is silently dead — tear it down and reconnect, and flag a
        // catch-up since we likely missed events while it was half-open.
        this.lastActivity = 0;
        this.pendingCatchUp = true;
        if (this.request) {
          try { this.request.removeAllListeners(); this.request.destroy(); } catch { /* noop */ }
          this.request = null;
        }
        this.connected = false;
        this._connect(conversationId);
      }
    }, 10000);
  }

  isConnected(): boolean {
    return this.connected;
  }

  private _connect(conversationId: string): void {
    const path = `${SSE_PATH}?conversation_id=${encodeURIComponent(conversationId)}`
      + `&token=${encodeURIComponent(this.sessionToken)}`
      + `&client_id=${encodeURIComponent(this.clientId)}`;
    const url = new URL(this.serverUrl + path);
    const isHttps = url.protocol === 'https:';
    const mod = isHttps ? https : http;

    const sseHeaders: Record<string, string> = {
      'Accept': 'text/event-stream',
      'Authorization': `Bearer ${this.sessionToken}`,
      'Cache-Control': 'no-cache',
    };
    if (this.gatewayCookie) {
      sseHeaders['Cookie'] = `_pf_gw=${this.gatewayCookie}`;
    }
    const options: http.RequestOptions = {
      hostname: url.hostname,
      port: url.port,
      path: url.pathname + url.search,
      method: 'GET',
      headers: sseHeaders,
      timeout: 0, // no timeout for SSE
    };

    // Destroy previous request before creating a new one — prevents socket leak
    if (this.request) {
      this.request.removeAllListeners();
      this.request.destroy();
      this.request = null;
    }

    this.request = mod.request(options, (res) => {
      if (res.statusCode !== 200) {
        this.emit('error', new Error(`SSE connection failed: ${res.statusCode}`));
        this._scheduleReconnect(conversationId);
        return;
      }

      this.connected = true;
      this.retryDelay = 1000;
      this.retryCount = 0;
      this.lastActivity = Date.now();
      this.emit('connected');
      // Only catch up from history when the previous stream was dead long
      // enough that buffered replay can't cover the gap. Routine server
      // lifetime bounces (pings flowing) don't set pendingCatchUp, so an
      // idle session does not reload the panel every few minutes.
      if (this.everConnected && this.pendingCatchUp) {
        this.emit('reconnected');
      }
      this.pendingCatchUp = false;
      this.everConnected = true;

      let eventType = '';
      let dataLines: string[] = [];
      let buffer = '';

      res.on('data', (chunk: Buffer) => {
        // Any byte — event, sse_ping, or `: keepalive` comment — proves the
        // socket is alive; refresh the watchdog clock.
        this.lastActivity = Date.now();
        buffer += chunk.toString('utf-8');
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.replace(/\r$/, '');
          if (trimmed.startsWith('event:')) {
            eventType = trimmed.slice(6).trim();
          } else if (trimmed.startsWith('data:')) {
            dataLines.push(trimmed.slice(5).trim());
          } else if (trimmed === '') {
            if (eventType || dataLines.length) {
              const rawData = dataLines.join('\n');
              let parsed: Record<string, any> = {};
              try { parsed = JSON.parse(rawData); } catch { parsed = { raw: rawData }; }
              // sse_ping / sse_reconnect are transport control events.
              if (eventType === 'sse_ping') {
                // keepalive only — already refreshed lastActivity above.
              } else if (eventType === 'sse_reconnect') {
                // Server intentionally closed this long-lived stream; open a
                // fresh one (its 'end' will also fire, but reconnect is
                // idempotent via the destroy-previous-request guard).
                this.connected = false;
                this._connect(conversationId);
              } else {
                const event: SSEEvent = { event: eventType || 'message', data: parsed };
                this.emit('event', event);
              }
            }
            eventType = '';
            dataLines = [];
          }
        }
      });

      res.on('end', () => {
        this.connected = false;
        this._markGap();
        this.emit('disconnected');
        this._scheduleReconnect(conversationId);
      });

      res.on('error', (e) => {
        this.connected = false;
        this._markGap();
        this.emit('error', e);
        this._scheduleReconnect(conversationId);
      });
    });

    this.request.on('error', (e) => {
      this.connected = false;
      this._markGap();
      this.emit('error', e);
      this._scheduleReconnect(conversationId);
    });

    this.request.end();
  }

  private _scheduleReconnect(conversationId: string): void {
    if (!this.shouldReconnect) { return; }
    this.retryCount++;
    setTimeout(() => {
      if (this.shouldReconnect) {
        this._connect(conversationId);
      }
    }, this.retryDelay);
    // Exponential backoff: 1s → 2s → 4s → 8s → 16s → 30s → 60s
    this.retryDelay = Math.min(this.retryDelay * 2, 60000);
  }
}
