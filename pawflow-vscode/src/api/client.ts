import * as http from 'http';
import * as https from 'https';
import { URL } from 'url';
import { API_PATH } from '../constants';
import { AgentResponse, SendMessageRequest } from './types';

export class AgentAPIClient {
  private serverUrl: string;
  private sessionToken: string;
  private _onAuthExpired: (() => void) | null = null;
  private _authExpiredFired = false;

  constructor(serverUrl: string, sessionToken: string) {
    this.serverUrl = serverUrl.replace(/\/$/, '');
    this.sessionToken = sessionToken;
  }

  setToken(token: string): void {
    this.sessionToken = token;
    this._authExpiredFired = false; // reset on new token
  }

  onAuthExpired(callback: () => void): void {
    this._onAuthExpired = callback;
  }

  async sendMessage(request: SendMessageRequest): Promise<AgentResponse> {
    return this.post(API_PATH, request);
  }

  async sendAction(action: string, params: Record<string, any> = {}): Promise<AgentResponse> {
    return this.post(API_PATH, { action, ...params });
  }

  private async post(path: string, body: Record<string, any>): Promise<AgentResponse> {
    return new Promise((resolve, reject) => {
      const url = new URL(this.serverUrl + path);
      const isHttps = url.protocol === 'https:';
      const options: http.RequestOptions = {
        hostname: url.hostname,
        port: url.port,
        path: url.pathname,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${this.sessionToken}`,
        },
        timeout: 30000,
      };

      const payload = JSON.stringify(body);
      (options.headers as Record<string, string>)['Content-Length'] = Buffer.byteLength(payload).toString();
      console.log(`[PawFlow API] POST ${path} body=${payload.slice(0, 200)}`);

      const mod = isHttps ? https : http;
      const req = mod.request(options, (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          if (res.statusCode === 401 || res.statusCode === 403) {
            resolve({ error: 'Session expired — please re-login', _auth_expired: true });
            // Trigger auto-relogin (once per expiry cycle)
            if (this._onAuthExpired && !this._authExpiredFired) {
              this._authExpiredFired = true;
              this._onAuthExpired();
            }
            return;
          }
          try {
            resolve(JSON.parse(data));
          } catch {
            resolve({ error: data || `HTTP ${res.statusCode}` });
          }
        });
      });

      req.on('error', (e) => reject(e));
      req.on('timeout', () => { req.destroy(); reject(new Error('Request timeout')); });
      req.write(payload);
      req.end();
    });
  }
}
