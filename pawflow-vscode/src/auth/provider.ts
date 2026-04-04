import * as vscode from 'vscode';
import * as http from 'http';
import { URL } from 'url';
import { AUTH_LOGIN_PATH } from '../constants';

export class PawFlowAuth {
  private context: vscode.ExtensionContext;
  private token: string = '';
  private username: string = '';

  constructor(context: vscode.ExtensionContext) {
    this.context = context;
  }

  async getSession(serverUrl: string): Promise<{ token: string; username: string } | null> {
    // Return cached token even if locally expired — server may silently
    // refresh via OAuth refresh tokens. Caller validates with server.
    const cached = await this.context.secrets.get('pawflow.token');
    const cachedUser = await this.context.secrets.get('pawflow.username');
    if (cached && cachedUser) {
      this.token = cached;
      this.username = cachedUser;
      return { token: cached, username: cachedUser };
    }
    return null;
  }

  async login(serverUrl: string): Promise<{ token: string; username: string }> {
    return new Promise((resolve, reject) => {
      // Start local callback server
      const server = http.createServer((req, res) => {
        const url = new URL(req.url || '/', `http://localhost`);
        const token = url.searchParams.get('token');
        const username = url.searchParams.get('username');

        res.writeHead(200, { 'Content-Type': 'text/html' });
        res.end(`<!DOCTYPE html><html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#1a1a2e;color:#e0e0e0">
          <h2>&#10004; PawFlow VSCode authenticated</h2>
          <p>You can close this window.</p></body></html>`);

        server.close();

        if (token && username) {
          this.token = token;
          this.username = username;
          // Cache for 8 hours
          const expiry = (Date.now() / 1000 + 8 * 3600).toString();
          this.context.secrets.store('pawflow.token', token);
          this.context.secrets.store('pawflow.username', username);
          this.context.secrets.store('pawflow.expiry', expiry);
          resolve({ token, username });
        } else {
          reject(new Error('No token received'));
        }
      });

      server.listen(0, '127.0.0.1', () => {
        const port = (server.address() as any).port;
        const callbackUrl = encodeURIComponent(`http://127.0.0.1:${port}/callback`);
        const authUrl = `${serverUrl}${AUTH_LOGIN_PATH}?relay_callback=${callbackUrl}`;
        vscode.env.openExternal(vscode.Uri.parse(authUrl));
      });

      // Timeout after 120s
      setTimeout(() => {
        server.close();
        reject(new Error('Auth timeout (120s)'));
      }, 120000);
    });
  }

  getToken(): string { return this.token; }
  getUsername(): string { return this.username; }

  async logout(): Promise<void> {
    await this.context.secrets.delete('pawflow.token');
    await this.context.secrets.delete('pawflow.username');
    await this.context.secrets.delete('pawflow.expiry');
    this.token = '';
    this.username = '';
  }
}
