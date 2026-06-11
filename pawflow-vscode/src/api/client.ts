import * as http from 'http';
import * as https from 'https';
import { URL } from 'url';
import { API_PATH } from '../constants';
import { AgentResponse, SendMessageRequest } from './types';

/**
 * POST /_gateway with the access key, return the _pf_gw cookie value or empty string.
 */
export async function acquireGatewayCookie(serverUrl: string, gatewayKey: string): Promise<string> {
  return new Promise((resolve) => {
    const url = new URL(serverUrl);
    const isHttps = url.protocol === 'https:';
    const body = `secret=${encodeURIComponent(gatewayKey)}&next=/`;
    const options: http.RequestOptions = {
      hostname: url.hostname,
      port: url.port,
      path: '/_gateway',
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        'Content-Length': Buffer.byteLength(body).toString(),
      },
      timeout: 10000,
    };

    const mod = isHttps ? https : http;
    const req = mod.request(options, (res) => {
      res.resume(); // drain
      const setCookie = res.headers['set-cookie'];
      if (setCookie) {
        for (const hdr of setCookie) {
          for (const part of hdr.split(';')) {
            const trimmed = part.trim();
            if (trimmed.startsWith('_pf_gw=')) {
              resolve(trimmed.slice('_pf_gw='.length));
              return;
            }
          }
        }
      }
      resolve('');
    });
    req.on('error', () => resolve(''));
    req.write(body);
    req.end();
  });
}

export class AgentAPIClient {
  private serverUrl: string;
  private sessionToken: string;
  private gatewayCookie: string;
  private _onAuthExpired: (() => void) | null = null;
  private _authExpiredFired = false;

  constructor(serverUrl: string, sessionToken: string, gatewayCookie: string = '') {
    this.serverUrl = serverUrl.replace(/\/$/, '');
    this.sessionToken = sessionToken;
    this.gatewayCookie = gatewayCookie;
  }

  setGatewayCookie(cookie: string): void {
    this.gatewayCookie = cookie;
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

  async uploadFile(filename: string, mimeType: string, base64Data: string,
                   conversationId?: string): Promise<{ file_id?: string; url?: string; error?: string }> {
    return new Promise((resolve, reject) => {
      const url = new URL(this.serverUrl + '/api/upload');
      const isHttps = url.protocol === 'https:';
      const boundary = '----pawflow' + Date.now().toString(16) + Math.random().toString(16).slice(2);
      const safeName = filename.replace(/["\r\n]/g, '_');
      const parts: Buffer[] = [Buffer.from(
        `--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="${safeName}"\r\n` +
        `Content-Type: ${mimeType}\r\n\r\n`)];
      parts.push(Buffer.from(base64Data, 'base64'));
      if (conversationId) {
        parts.push(Buffer.from(
          `\r\n--${boundary}\r\nContent-Disposition: form-data; name="conversation_id"\r\n\r\n${conversationId}`));
      }
      parts.push(Buffer.from(`\r\n--${boundary}--\r\n`));
      const payload = Buffer.concat(parts);

      const headers: Record<string, string> = {
        'Content-Type': `multipart/form-data; boundary=${boundary}`,
        'Content-Length': String(payload.length),
        'Authorization': `Bearer ${this.sessionToken}`,
      };
      if (this.gatewayCookie) {
        headers['Cookie'] = `_pf_gw=${this.gatewayCookie}`;
      }
      const mod = isHttps ? https : http;
      const req = mod.request({
        hostname: url.hostname, port: url.port, path: url.pathname,
        method: 'POST', headers, timeout: 60000,
      }, (res) => {
        let data = '';
        res.on('data', (chunk) => { data += chunk; });
        res.on('end', () => {
          try {
            const parsed = JSON.parse(data);
            if (parsed.ok && parsed.files && parsed.files.length) {
              resolve(parsed.files[0]);
            } else {
              resolve({ error: parsed.error || `HTTP ${res.statusCode}` });
            }
          } catch {
            resolve({ error: data || `HTTP ${res.statusCode}` });
          }
        });
      });
      req.on('error', (e) => reject(e));
      req.on('timeout', () => { req.destroy(); reject(new Error('Upload timeout')); });
      req.write(payload);
      req.end();
    });
  }

  async sendAction(action: string, params: Record<string, any> = {}): Promise<AgentResponse> {
    // _inline_response: the agent actions endpoint otherwise ACKs the HTTP
    // request and publishes the real result on the conversation's SSE
    // channel (webchat behaviour). This client reads results from the HTTP
    // response, so ask for them inline.
    return this.post(API_PATH, { action, _inline_response: true, ...params });
  }

  private async post(path: string, body: Record<string, any>): Promise<AgentResponse> {
    return new Promise((resolve, reject) => {
      const url = new URL(this.serverUrl + path);
      const isHttps = url.protocol === 'https:';
      const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${this.sessionToken}`,
      };
      if (this.gatewayCookie) {
        headers['Cookie'] = `_pf_gw=${this.gatewayCookie}`;
      }
      const options: http.RequestOptions = {
        hostname: url.hostname,
        port: url.port,
        path: url.pathname,
        method: 'POST',
        headers,
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
