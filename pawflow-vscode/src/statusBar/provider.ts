import * as vscode from 'vscode';

export class StatusBarProvider implements vscode.Disposable {
  private item: vscode.StatusBarItem;

  constructor() {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    this.item.command = 'pawflow.openChat';
    this.setIdle();
    this.item.show();
  }

  setIdle(): void {
    this.item.text = '$(comment-discussion) PawFlow';
    this.item.tooltip = 'PawFlow - Click to open chat';
    this.item.backgroundColor = undefined;
  }

  setConnected(username: string): void {
    this.item.text = `$(comment-discussion) PawFlow: ${username}`;
    this.item.tooltip = `Connected as ${username}`;
  }

  setThinking(agent: string, verb: string): void {
    this.item.text = `$(sync~spin) ${agent}: ${verb}...`;
    this.item.tooltip = `${agent} is working...`;
  }

  setError(msg: string): void {
    this.item.text = `$(error) PawFlow: ${msg}`;
    this.item.backgroundColor = new vscode.ThemeColor('statusBarItem.errorBackground');
  }

  setTokens(tokensIn: number, tokensOut: number): void {
    this.item.text = `$(comment-discussion) PawFlow ${tokensIn}↑ ${tokensOut}↓`;
  }

  dispose(): void {
    this.item.dispose();
  }
}
