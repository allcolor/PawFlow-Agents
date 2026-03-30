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
  private static readonly MAX_RETRIES = 30;

  constructor(serverUrl: string, sessionToken: string, gatewayCookie: string = '') {
    super();
    this.serverUrl = serverUrl.replace(/\/$/, '');
    this.sessionToken = sessionToken;
    this.gatewayCookie = gatewayCookie;
  }

  connect(conversationId: string): void {
    this.shouldReconnect = true;
    this._connect(conversationId);
  }

  disconnect(): void {
    this.shouldReconnect = false;
    if (this.request) {
      this.request.destroy();
      this.request = null;
    }
    this.connected = false;
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
      this.emit('connected');

      let eventType = '';
      let dataLines: string[] = [];
      let buffer = '';

      res.on('data', (chunk: Buffer) => {
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
              const event: SSEEvent = { event: eventType || 'message', data: parsed };
              this.emit('event', event);
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
