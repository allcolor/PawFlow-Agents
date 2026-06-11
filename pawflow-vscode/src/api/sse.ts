import * as http from 'http';
import * as https from 'https';
import { URL } from 'url';
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

  private _startWatchdog(conversationId: string): void {
    if (this.watchdog) { clearInterval(this.watchdog); }
    this.watchdog = setInterval(() => {
      if (!this.shouldReconnect || !this.lastActivity) { return; }
      if (Date.now() - this.lastActivity > SSEClient.STALE_MS) {
        // Stream is silently dead — tear it down and reconnect.
        this.lastActivity = 0;
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
    const path = `${SSE_PATH}?conversation_id=${conversationId}&token=${this.sessionToken}`;
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
      // A reconnect (not the first connect) means we may have missed events
      // while the stream was down — tell listeners to catch up from history.
      if (this.everConnected) {
        this.emit('reconnected');
      }
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
        this.emit('disconnected');
        this._scheduleReconnect(conversationId);
      });

      res.on('error', (e) => {
        this.connected = false;
        this.emit('error', e);
        this._scheduleReconnect(conversationId);
      });
    });

    this.request.on('error', (e) => {
      this.connected = false;
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
