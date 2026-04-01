// ── File Explorer ──────────────────────────────────────────────────
let _fe={overlay:null,svc:'',path:'.',entries:[],clip:null,sel:new Set(),svcs:[],ctx:null,preview:null,sort:{col:'name',asc:true}};

async function _feApi(action,params={}){
  try{const r=await fetch(API,{method:'POST',headers:getAuthHeaders(),body:JSON.stringify({action,...params,conversation_id:conversationId}),credentials:'same-origin'});
  return await r.json();}catch(e){return {error:e.message};}
}

function openExplorer(){
  if(_fe.overlay)return;
  const o=document.createElement('div');o.className='fe-overlay';
  o.innerHTML=`<div class="fe-panel"><div class="fe-toolbar"><select id="feSvcSel" onchange="_feSelSvc(this.value)"></select><div class="fe-bc" id="feBc"></div><input class="fe-search" placeholder="Search..." onkeydown="if(event.key==='Enter')_feSearch(this.value)"><button class="btn" onclick="_feRefresh()" title="Refresh">&#x21bb;</button><button class="btn" onclick="_feUpload()">&#x2B06; Upload</button><button class="btn" onclick="closeExplorer()">&#x2715;</button></div><div class="fe-content"><table class="fe-table"><thead><tr><th></th><th onclick="_feSortBy('name')">Name</th><th onclick="_feSortBy('size')">Size</th><th onclick="_feSortBy('modified')">Modified</th></tr></thead><tbody id="feTbody"></tbody></table></div><div class="fe-status"><span id="feCount"></span><span id="feClip" class="fe-clip"></span></div></div>`;
  o.addEventListener('click',e=>{if(e.target===o)closeExplorer();});
  document.body.appendChild(o);_fe.overlay=o;
  document.addEventListener('keydown',_feKeys);
  // Drag-and-drop upload
  const panel=o.querySelector('.fe-panel');
  panel.addEventListener('dragover',e=>{e.preventDefault();e.stopPropagation();panel.classList.add('fe-dragover');});
  panel.addEventListener('dragleave',e=>{e.preventDefault();panel.classList.remove('fe-dragover');});
  panel.addEventListener('drop',e=>{
    e.preventDefault();e.stopPropagation();panel.classList.remove('fe-dragover');
    if(e.dataTransfer.files.length>0)_feUploadFiles(e.dataTransfer.files);
  });
  _feLoadSvcs();
}

function closeExplorer(){
  if(_fe.overlay){_fe.overlay.remove();_fe.overlay=null;}
  if(_fe.ctx){_fe.ctx.remove();_fe.ctx=null;}
  if(_fe.preview){_fe.preview.remove();_fe.preview=null;}
  document.removeEventListener('keydown',_feKeys);
}

async function _feLoadSvcs(){
  const d=await _feApi('fs_list_services');
  _fe.svcs=d.services||[];
  const sel=document.getElementById('feSvcSel');if(!sel)return;
  sel.innerHTML=_fe.svcs.map(s=>`<option value="${s.id}">${s.id} (${s.type})</option>`).join('');
  if(_fe.svcs.length>0){_fe.svc=_fe.svcs[0].id;_feNav('.');}
  else{document.getElementById('feTbody').innerHTML='<tr><td colspan=4 class="fe-empty">No filesystem services available</td></tr>';}
}

function _feSelSvc(id){_fe.svc=id;_feNav('.');}

async function _feNav(path){
  _fe.path=path;_fe.sel.clear();
  const tb=document.getElementById('feTbody');
  tb.innerHTML='<tr><td colspan=4 class="fe-loading">Loading...</td></tr>';
  const d=await _feApi('fs_list_dir',{service:_fe.svc,path});
  if(d.error){tb.innerHTML=`<tr><td colspan=4 class="fe-empty">Error: ${d.error}</td></tr>`;_feBc();return;}
  _fe.entries=d.entries||[];_feRender();_feBc();
}

function _feRender(){
  let entries=[..._fe.entries];
  const c=_fe.sort.col,a=_fe.sort.asc?1:-1;
  entries.sort((x,y)=>{
    if(x.kind!==y.kind)return x.kind==='directory'?-1:1;
    let va=x[c],vb=y[c];
    if(c==='size')return (va-vb)*a;
    if(c==='name')return String(va).localeCompare(String(vb))*a;
    return String(va||'').localeCompare(String(vb||''))*a;
  });
  const tb=document.getElementById('feTbody');
  if(entries.length===0){tb.innerHTML='<tr><td colspan=4 class="fe-empty">Empty directory</td></tr>';_feStatus();return;}
  tb.innerHTML=entries.map(e=>{
    const icon=e.kind==='directory'?'&#128193;':_feIcon(e.name);
    const sz=e.kind==='directory'?'&mdash;':_feFmtSz(e.size);
    const dt=e.modified?new Date(e.modified).toLocaleDateString()+' '+new Date(e.modified).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):'';
    const cls=_fe.sel.has(e.name)?'fe-row selected':(_fe.clip&&_fe.clip.action==='cut'&&_fe.clip.name===e.name&&_fe.clip.service===_fe.svc?'fe-row cut':'fe-row');
    return `<tr class="${cls}" data-name="${_feEsc(e.name)}" data-kind="${e.kind}" onclick="_feClick(event,'${_feEsc(e.name)}','${e.kind}')" ondblclick="_feDbl('${_feEsc(e.name)}','${e.kind}')" oncontextmenu="_feCtx(event,'${_feEsc(e.name)}','${e.kind}')"><td>${icon}</td><td>${_feEsc(e.name)}</td><td>${sz}</td><td>${dt}</td></tr>`;
  }).join('');
  _feStatus();
}

function _feBc(){
  const el=document.getElementById('feBc');if(!el)return;
  const parts=_fe.path==='.'?[]:_fe.path.replace(/\\/g,'/').split('/').filter(Boolean);
  let html=`<span onclick="_feNav('.')">${_feEsc(_fe.svc)}</span>`;
  let p='';
  for(let i=0;i<parts.length;i++){
    p+=(p?'/':'')+parts[i];
    const cls=i===parts.length-1?'cur':'';
    const pp=p;
    html+=` / <span class="${cls}" onclick="_feNav('${_feEsc(pp)}')">${_feEsc(parts[i])}</span>`;
  }
  el.innerHTML=html;
}

function _feStatus(){
  const c=document.getElementById('feCount');
  const cl=document.getElementById('feClip');
  if(c){
    let t=_fe.entries.length+' items';
    if(_fe.sel.size>0)t+=' ('+_fe.sel.size+' selected)';
    c.textContent=t;
  }
  if(cl){
    if(_fe.clip&&_fe.clip.items){
      const names=_fe.clip.items.map(i=>i.name);
      const label=names.length>2?names[0]+' + '+(names.length-1)+' more':names.join(', ');
      cl.textContent=_fe.clip.action+': '+label;
    } else cl.textContent='';
  }
}

function _feClick(e,name,kind){
  if(e.ctrlKey||e.metaKey){_fe.sel.has(name)?_fe.sel.delete(name):_fe.sel.add(name);}
  else{_fe.sel.clear();_fe.sel.add(name);}
  _feRender();
}

function _feDbl(name,kind){
  if(kind==='directory'){_feNav(_fe.path==='.'?name:_fe.path+'/'+name);}
  else{_fePreview(name);}
}

function _feCtx(e,name,kind){
  e.preventDefault();e.stopPropagation();
  if(_fe.ctx){_fe.ctx.remove();}
  if(!_fe.sel.has(name)){_fe.sel.clear();_fe.sel.add(name);}
  const m=document.createElement('div');m.className='fe-ctx';
  let items='';
  if(kind==='directory'){
    items+=`<div onclick="_feDbl('${_feEsc(name)}','directory')">&#128193; Open</div>`;
    items+=`<div onclick="_feZipDir('${_feEsc(name)}')" title="Download directory as zip">&#128230; Download as zip</div>`;
  } else {
    items+=`<div onclick="_fePreview('${_feEsc(name)}')">&#128065; Preview</div>`;
    items+=`<div onclick="_feDl('${_feEsc(name)}')">&#11015; Download</div>`;
    items+=`<div onclick="_feCopyToStore('${_feEsc(name)}')">&#128230; Copy to FileStore</div>`;
  }
  items+=`<hr>`;
  const selCount=_fe.sel.size;
  items+=`<div onclick="_feCopySelected()">&#128203; Copy${selCount>1?' ('+selCount+')':''}</div>`;
  items+=`<div onclick="_feCutSelected()">&#9986; Cut${selCount>1?' ('+selCount+')':''}</div>`;
  if(_fe.clip)items+=`<div onclick="_fePaste()">&#128203; Paste here (${_fe.clip.items.length})</div>`;
  items+=`<hr>`;
  items+=`<div onclick="_feRenameStart('${_feEsc(name)}')">&#9998; Rename</div>`;
  items+=`<div onclick="_feDel('${_feEsc(name)}')">&#128465; Delete</div>`;
  items+=`<hr>`;
  items+=`<div onclick="_feNewFile()">&#128196; New file</div>`;
  items+=`<div onclick="_feNewDir()">&#128193; New folder</div>`;
  m.innerHTML=items;
  m.style.left=e.clientX+'px';m.style.top=e.clientY+'px';
  document.body.appendChild(m);_fe.ctx=m;
  const close=()=>{if(_fe.ctx){_fe.ctx.remove();_fe.ctx=null;}document.removeEventListener('click',close);};
  setTimeout(()=>document.addEventListener('click',close),0);
}

function _fePath(name){return _fe.path==='.'?name:_fe.path+'/'+name;}

function _feCopySelected(){
  const names=[..._fe.sel];if(!names.length)return;
  _fe.clip={action:'copy',service:_fe.svc,basePath:_fe.path,items:names.map(n=>({name:n,path:_fePath(n)}))};
  _feStatus();
}
function _feCutSelected(){
  const names=[..._fe.sel];if(!names.length)return;
  _fe.clip={action:'cut',service:_fe.svc,basePath:_fe.path,items:names.map(n=>({name:n,path:_fePath(n)}))};
  _feRender();
}
function _feCopy(name){_fe.sel.clear();_fe.sel.add(name);_feCopySelected();}
function _feCut(name){_fe.sel.clear();_fe.sel.add(name);_feCutSelected();}

async function _fePaste(){
  if(!_fe.clip||!_fe.clip.items.length)return;
  for(const item of _fe.clip.items){
    let destName=item.name;
    // Same folder? Auto-rename to avoid overwrite
    if(_fe.clip.service===_fe.svc&&_fe.clip.basePath===_fe.path&&_fe.clip.action==='copy'){
      const dot=destName.lastIndexOf('.');
      destName=dot>0?destName.slice(0,dot)+' (copy)'+destName.slice(dot):destName+' (copy)';
    }
    const dest=_fePath(destName);
    // Server-side copy (no client round-trip for file content)
    const d=await _feApi('fs_copy',{source_service:_fe.clip.service,source_path:item.path,dest_service:_fe.svc,dest_path:dest});
    if(d.error){addMsg('error','Paste failed: '+d.error);return;}
    if(_fe.clip.action==='cut'){
      await _feApi('fs_delete',{service:_fe.clip.service,path:item.path});
    }
  }
  if(_fe.clip.action==='cut')_fe.clip=null;
  _feNav(_fe.path);
}

async function _feDel(name){
  if(!confirm('Delete "'+name+'"? This cannot be undone.'))return;
  await _feApi('fs_delete',{service:_fe.svc,path:_fePath(name)});
  _feNav(_fe.path);
}

async function _feDelSelected(){
  const names=[..._fe.sel];
  if(!names.length)return;
  const label=names.length===1?'"'+names[0]+'"':names.length+' items';
  if(!confirm('Delete '+label+'? This cannot be undone.'))return;
  for(const n of names)await _feApi('fs_delete',{service:_fe.svc,path:_fePath(n)});
  _feNav(_fe.path);
}

function _feRenameStart(name){
  const row=document.querySelector(`tr[data-name="${name}"] td:nth-child(2)`);
  if(!row)return;
  const inp=document.createElement('input');inp.className='fe-inline';inp.value=name;
  row.innerHTML='';row.appendChild(inp);inp.focus();inp.select();
  const finish=async()=>{
    const nv=inp.value.trim();
    if(nv&&nv!==name){
      const oldP=_fePath(name),newP=_fePath(nv);
      await _feApi('fs_rename',{service:_fe.svc,old_path:oldP,new_path:newP});
    }
    _feNav(_fe.path);
  };
  inp.onblur=finish;inp.onkeydown=e=>{if(e.key==='Enter')finish();if(e.key==='Escape')_feNav(_fe.path);};
}

async function _feNewFile(){
  const name=prompt('New file name:');if(!name)return;
  await _feApi('fs_write_file',{service:_fe.svc,path:_fePath(name),content:'',encoding:'utf-8'});
  _feNav(_fe.path);
}

async function _feNewDir(){
  const name=prompt('New folder name:');if(!name)return;
  await _feApi('fs_mkdir',{service:_fe.svc,path:_fePath(name)});
  _feNav(_fe.path);
}

async function _feDl(name){
  const d=await _feApi('fs_read_file',{service:_fe.svc,path:_fePath(name)});
  if(d.error){alert('Error: '+d.error);return;}
  let blob;
  if(d.encoding==='base64'){
    const bin=atob(d.content);const arr=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)arr[i]=bin.charCodeAt(i);
    blob=new Blob([arr]);
  } else {blob=new Blob([d.content],{type:'text/plain;charset=utf-8'});}
  const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;a.click();URL.revokeObjectURL(url);
}

function _feUpload(){
  const inp=document.createElement('input');inp.type='file';inp.multiple=true;
  inp.onchange=()=>{if(inp.files.length)_feUploadFiles(inp.files);};
  inp.click();
}

async function _feUploadFiles(files){
  const count=files.length;
  const status=document.getElementById('feCount');
  let done=0;
  for(const f of files){
    if(status)status.textContent=`Uploading ${++done}/${count}: ${f.name}`;
    const b64=await new Promise(r=>{const rd=new FileReader();rd.onload=()=>r(rd.result.split(',')[1]);rd.readAsDataURL(f);});
    await _feApi('fs_write_file',{service:_fe.svc,path:_fePath(f.name),content:b64,encoding:'base64'});
  }
  _feNav(_fe.path);
}

async function _feCopyToStore(name){
  const d=await _feApi('fs_copy_to_store',{service:_fe.svc,path:_fePath(name)});
  if(d.error){alert('Error: '+d.error);return;}
  alert('Stored as: '+d.filename+'\nURL: '+d.url);
}

async function _feZipDir(name){
  const dirPath=_fePath(name);
  const btn=event&&event.target;
  if(btn)btn.textContent='Zipping...';
  const d=await _feApi('fs_zip_dir',{service:_fe.svc,path:dirPath});
  if(btn)btn.textContent='\u{1F4E6} Download as zip';
  if(d.error){alert('Zip error: '+d.error);return;}
  // Trigger download via temporary anchor
  const a=document.createElement('a');
  a.href=d.url;
  a.download=d.filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

async function _fePreview(name){
  if(_fe.preview){_fe.preview.remove();_fe.preview=null;}
  const p=document.createElement('div');p.className='fe-preview-pane';
  p.innerHTML=`<div class="fe-ph"><span>${_feEsc(name)}</span><button class="btn" onclick="this.closest('.fe-preview-pane').remove();_fe.preview=null;">&#x2715;</button></div><div class="fe-loading">Loading...</div>`;
  document.body.appendChild(p);_fe.preview=p;
  const d=await _feApi('fs_read_file',{service:_fe.svc,path:_fePath(name)});
  if(d.error){p.querySelector('.fe-loading').textContent='Error: '+d.error;return;}
  const ext=name.split('.').pop().toLowerCase();
  const imgExts=['png','jpg','jpeg','gif','webp','svg','bmp','ico'];
  if(imgExts.includes(ext)&&d.encoding==='base64'){
    const mime=ext==='svg'?'image/svg+xml':'image/'+ext.replace('jpg','jpeg');
    p.innerHTML=`<div class="fe-ph"><span>${_feEsc(name)}</span><button class="btn" onclick="this.closest('.fe-preview-pane').remove();_fe.preview=null;">&#x2715;</button></div><img src="data:${mime};base64,${d.content}">`;
  } else {
    const text=d.encoding==='base64'?atob(d.content):d.content;
    p.innerHTML=`<div class="fe-ph"><span>${_feEsc(name)} (${_feFmtSz(d.size)})</span><button class="btn" onclick="this.closest('.fe-preview-pane').remove();_fe.preview=null;">&#x2715;</button></div><pre>${_feEsc(text.substring(0,50000))}</pre>`;
  }
}

async function _feSearch(q){
  if(!q){_feNav(_fe.path);return;}
  const tb=document.getElementById('feTbody');
  tb.innerHTML='<tr><td colspan=4 class="fe-loading">Searching...</td></tr>';
  const d=await _feApi('fs_search',{service:_fe.svc,path:_fe.path,pattern:'*'+q+'*'});
  if(d.error){tb.innerHTML=`<tr><td colspan=4 class="fe-empty">Error: ${d.error}</td></tr>`;return;}
  const results=(d.results||[]).slice(0,100);
  if(results.length===0){tb.innerHTML='<tr><td colspan=4 class="fe-empty">No matches</td></tr>';return;}
  tb.innerHTML=results.map(r=>`<tr class="fe-row" ondblclick="_feNavToFile('${_feEsc(r)}')"><td>&#128196;</td><td>${_feEsc(r)}</td><td></td><td></td></tr>`).join('');
  document.getElementById('feCount').textContent=results.length+' results';
}

function _feNavToFile(path){
  const parts=path.replace(/\\/g,'/').split('/');
  if(parts.length>1){_feNav(parts.slice(0,-1).join('/'));}
}

function _feSortBy(col){
  if(_fe.sort.col===col)_fe.sort.asc=!_fe.sort.asc;
  else{_fe.sort.col=col;_fe.sort.asc=true;}
  _feRender();
}

function _feRefresh(){_feNav(_fe.path);}

function _feKeys(e){
  if(!_fe.overlay)return;
  if(e.key==='Escape'){closeExplorer();e.preventDefault();}
  if(e.key==='Backspace'&&!['INPUT','TEXTAREA'].includes(document.activeElement.tagName)){
    e.preventDefault();
    if(_fe.path!=='.'&&_fe.path){const p=_fe.path.replace(/\\/g,'/').split('/');p.pop();_feNav(p.join('/')||'.');}
  }
  if(e.key==='Delete'&&_fe.sel.size>0){e.preventDefault();_feDelSelected();}
  if(e.key==='F2'){const s=[..._fe.sel];if(s.length===1)_feRenameStart(s[0]);}
  if(e.ctrlKey&&e.key==='c'&&_fe.sel.size>0){e.preventDefault();_feCopySelected();}
  if(e.ctrlKey&&e.key==='x'&&_fe.sel.size>0){e.preventDefault();_feCutSelected();}
  if(e.ctrlKey&&e.key==='v')_fePaste();
}

function _feFmtSz(b){if(!b||b===0)return '0 B';const u=['B','KB','MB','GB'];let i=0;let v=b;while(v>=1024&&i<3){v/=1024;i++;}return v.toFixed(i?1:0)+' '+u[i];}
function _feEsc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,"\\'");}
function _feIcon(n){const e=n.split('.').pop().toLowerCase();const m={js:'&#128220;',ts:'&#128220;',py:'&#128013;',rs:'&#9881;',go:'&#128029;',java:'&#9749;',html:'&#127760;',css:'&#127912;',json:'&#128203;',md:'&#128210;',txt:'&#128196;',pdf:'&#128213;',png:'&#127912;',jpg:'&#127912;',jpeg:'&#127912;',gif:'&#127912;',svg:'&#127912;',zip:'&#128230;',gz:'&#128230;',tar:'&#128230;'};return m[e]||'&#128196;';}

addMsg('system', t('welcome'));

// ── App initialization (runs last, after all modules loaded) ──
_msgObserver.observe(document.getElementById('messages'), { childList: true });
document.getElementById('input').focus();
updateActiveAgentBadge();

loadConversations().then(convs => {
  if (convs.length && !conversationId) {
    resumeConv(convs[0].conversation_id);
  }
});
