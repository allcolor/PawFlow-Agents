const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pawflowRelay', {
  list: () => ipcRenderer.invoke('relay:list'),
  addServer: input => ipcRenderer.invoke('relay:add-server', input),
  deleteServer: name => ipcRenderer.invoke('relay:delete-server', name),
  loginServer: name => ipcRenderer.invoke('relay:login-server', name),
  addWorkspace: input => ipcRenderer.invoke('relay:add-workspace', input),
  deleteWorkspace: name => ipcRenderer.invoke('relay:delete-workspace', name),
  start: name => ipcRenderer.invoke('relay:start', name),
  stop: name => ipcRenderer.invoke('relay:stop', name),
  running: () => ipcRenderer.invoke('relay:running'),
  selectDirectory: currentPath => ipcRenderer.invoke('relay:select-directory', currentPath),
  listDockerImages: () => ipcRenderer.invoke('relay:docker-images'),
  relayImageCatalog: () => ipcRenderer.invoke('relay:image-catalog'),
  buildRelayImage: input => ipcRenderer.invoke('relay:build-image', input),
  downloadRelayImage: input => ipcRenderer.invoke('relay:download-image', input),
  onLog: callback => ipcRenderer.on('relay-log', (_event, payload) => callback(payload)),
});
