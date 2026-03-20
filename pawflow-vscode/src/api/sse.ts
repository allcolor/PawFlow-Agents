import * as http from 'http';
import * as https from 'https';
import { URL } from 'url';
import { EventEmitter } from 'events';
import { SSE_PATH } from '../constants';
import { SSEEvent } from './types';

export class SSEClient extends EventEmitter {
  private serverUrl: string;
  private sessionToken: string;
  private request: http.ClientRequest | null = null;
  private connected = false;
  private shouldReconnect = true;
  private retryDelay = 1000;

  constructor(serverUrl: string, sessionToken: string) {
    super();
    this.serverUrl = serverUrl.replace(/\/$/, '');
    this.sessionToken = sessionToken;
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

    const options: http.RequestOptions = {
      hostname: url.hostname,
      port: url.port,
      path: url.pathname + url.search,
      method: 'GET',
      headers: {
        'Accept': 'text/event-stream',
        'Authorization': `Bearer ${this.sessionToken}`,
        'Cache-Control': 'no-cache',
      },
      timeout: 0, // no timeout for SSE
    };

    this.request = mod.request(options, (res) => {
      if (res.statusCode !== 200) {
        this.emit('error', new Error(`SSE connection failed: ${res.statusCode}`));
        this._scheduleReconnect(conversationId);
        return;
      }

      this.connected = true;
      this.retryDelay = 1000;
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
    setTimeout(() => {
      if (this.shouldReconnect) {
        this._connect(conversationId);
      }
    }, this.retryDelay);
    this.retryDelay = Math.min(this.retryDelay * 2, 15000);
  }
}
