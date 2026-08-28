'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pawflowDesktop', {
  profiles: {
    list: () => ipcRenderer.invoke('profile:list'),
    save: input => ipcRenderer.invoke('profile:save', input),
    remove: id => ipcRenderer.invoke('profile:remove', id),
    connect: id => ipcRenderer.invoke('profile:connect', id),
  },
  auth: {
    builtin: input => ipcRenderer.invoke('auth:builtin', input),
    oauth: input => ipcRenderer.invoke('auth:oauth', input),
  },
  chat: {
    add: profileId => ipcRenderer.invoke('chat:add', profileId),
    activate: tabId => ipcRenderer.invoke('chat:activate', tabId),
    close: tabId => ipcRenderer.invoke('chat:close', tabId),
    navigate: action => ipcRenderer.invoke('chat:navigate', action),
    show: () => ipcRenderer.invoke('chat:show'),
    hide: () => ipcRenderer.invoke('chat:hide'),
  },
  onTabs: callback => ipcRenderer.on('chat:tabs', (_event, value) => callback(value)),
  onAuthRequired: callback => ipcRenderer.on('auth:required', (_event, value) => callback(value)),
  onOAuthComplete: callback => ipcRenderer.on('auth:oauth-complete', (_event, value) => callback(value)),
  onDownload: callback => ipcRenderer.on('chat:download', (_event, value) => callback(value)),
  onError: callback => ipcRenderer.on('app:error', (_event, value) => callback(value)),
});
