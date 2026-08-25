// Durable loader shared by every UiSurface producer.
let _uiSurfaceLoadConversation = '';

function loadUiSurfaces(cid) {
  _uiSurfaceLoadConversation = cid || '';
  if (typeof resetUiSurfaces === 'function') resetUiSurfaces(cid);
  if (!cid) return;
  action$('ui_surface_list', { conversation_id: cid }).subscribe({
    next: data => {
      if (_uiSurfaceLoadConversation !== cid || data.error) return;
      (data.surfaces || []).forEach(surface => {
        if (typeof uiSurfaceUpsert === 'function') uiSurfaceUpsert(surface);
      });
    },
    error: () => {},
  });
}
