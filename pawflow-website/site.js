// Fallback when the GitHub API is unreachable or rate-limited. Keep the
// version in sync with the latest release tag on a best-effort basis — the
// live fetch below overrides it on every page load.
const PAWFLOW_RELEASE = {
  version: '1.0.0-beta.264',
  repo: 'https://github.com/allcolor/PawFlow-Agents',
};

// Asset names as published on releases (dots, not spaces, in the desktop
// names). Used to build fallback URLs and to match live asset lists.
const releaseAssets = (version) => ({
  installer: `pawflow-install-${version}.zip`,
  mcpClientZip: `pawflow-mcp-client-${version}.zip`,
  mcpClientTar: `pawflow-mcp-client-${version}.tar.gz`,
  pawcodeLinuxTar: `pawcode-${version}-linux-x86_64.tar.gz`,
  pawcodeLinuxZip: `pawcode-${version}-linux-x86_64.zip`,
  pawcodeWindowsZip: `pawcode-${version}-win-x86_64.zip`,
  pawcodeDeb: `pawcode_${version}_amd64.deb`,
  relayCliLinuxTar: `pawflow-relay-cli-${version}-linux-x86_64.tar.gz`,
  relayCliLinuxZip: `pawflow-relay-cli-${version}-linux-x86_64.zip`,
  relayCliWindowsZip: `pawflow-relay-cli-${version}-win-x86_64.zip`,
  relayDesktopTar: `pawflow-relay-desktop-${version}.tar.gz`,
  relayDesktopDeb: `pawflow-relay-desktop_${version}_amd64.deb`,
  relayDesktopAppImage: `PawFlow.Relay.Desktop-${version}.AppImage`,
  relayDesktopWindows: `PawFlow.Relay.Desktop.Setup.${version}.exe`,
  relayDesktopWindowsZip: `PawFlow.Relay.Desktop-${version}-win.zip`,
  vscodeVsix: `pawflow-vscode-${version}.vsix`,
  androidApk: `pawflow-android-${version}-debug.apk`,
});

// Patterns to pick each download out of the live release asset list, so
// renamed or re-versioned assets keep resolving without a website deploy.
const ASSET_PATTERNS = {
  installer: /^pawflow-install-.*\.zip$/,
  mcpClientZip: /^pawflow-mcp-client-.*\.zip$/,
  mcpClientTar: /^pawflow-mcp-client-.*\.tar\.gz$/,
  pawcodeLinuxTar: /^pawcode-.*-linux-x86_64\.tar\.gz$/,
  pawcodeLinuxZip: /^pawcode-.*-linux-x86_64\.zip$/,
  pawcodeWindowsZip: /^pawcode-.*-win-x86_64\.zip$/,
  pawcodeDeb: /^pawcode_.*_amd64\.deb$/,
  relayCliLinuxTar: /^pawflow-relay-cli-.*-linux-x86_64\.tar\.gz$/,
  relayCliLinuxZip: /^pawflow-relay-cli-.*-linux-x86_64\.zip$/,
  relayCliWindowsZip: /^pawflow-relay-cli-.*-win-x86_64\.zip$/,
  relayDesktopTar: /^pawflow-relay-desktop-.*\.tar\.gz$/,
  relayDesktopDeb: /^pawflow-relay-desktop_.*_amd64\.deb$/,
  relayDesktopAppImage: /Relay[ .]Desktop-.*\.AppImage$/,
  relayDesktopWindows: /Relay[ .]Desktop[ .]Setup[ .].*\.exe$/,
  relayDesktopWindowsZip: /Relay[ .]Desktop-.*-win\.zip$/,
  vscodeVsix: /^pawflow-vscode-.*\.vsix$/,
  androidApk: /^pawflow-android-.*\.apk$/,
};

const release = {
  ...PAWFLOW_RELEASE,
  tagUrl: `${PAWFLOW_RELEASE.repo}/releases/tag/${PAWFLOW_RELEASE.version}`,
  assets: releaseAssets(PAWFLOW_RELEASE.version),
};

function releaseDownloadUrl(assetName) {
  return `${release.repo}/releases/download/${release.version}/${encodeURIComponent(assetName)}`;
}

function renderReleaseReferences() {
  document.querySelectorAll('[data-release-version]').forEach((node) => {
    node.textContent = release.version;
  });
  document.querySelectorAll('[data-release-url]').forEach((node) => {
    node.setAttribute('href', release.tagUrl);
  });
  document.querySelectorAll('[data-release-download]').forEach((node) => {
    const key = node.dataset.releaseDownload;
    const asset = release.assets[key];
    if (!asset) return;
    node.setAttribute('href', releaseDownloadUrl(asset));
    const nameNode = node.querySelector('[data-release-asset]');
    if (nameNode) nameNode.textContent = asset;
  });
  document.querySelectorAll('[data-install-command]').forEach((node) => {
    const version = release.version;
    const installer = release.assets.installer;
    node.innerHTML = `<code>PAWFLOW_VERSION="${version}"
curl -L -o "${installer}" \\
  "${releaseDownloadUrl(installer)}"
unzip "${installer}"
cd "pawflow-install-${version}"
bash scripts/install-pawflow.sh --port 19990 --pull-images</code>`;
  });
  document.querySelectorAll('[data-install-command-compact]').forEach((node) => {
    const installer = release.assets.installer;
    const dir = installer.replace(/\.zip$/, '');
    node.innerHTML = `<code>curl -L -O "${releaseDownloadUrl(installer)}" && unzip "${installer}" && cd "${dir}" && bash scripts/install-pawflow.sh --port 19990 --pull-images</code>`;
  });
}

renderReleaseReferences();

// Resolve the CURRENT release from the GitHub API and re-render. The static
// block above is only the offline/rate-limited fallback — hardcoded versions
// went stale and served 404 download links.
(async () => {
  try {
    const resp = await fetch(
      'https://api.github.com/repos/allcolor/PawFlow-Agents/releases/latest',
      { headers: { Accept: 'application/vnd.github+json' } });
    if (!resp.ok) return;
    const data = await resp.json();
    const tag = (data.tag_name || '').trim();
    if (!tag) return;
    const liveNames = (data.assets || []).map((a) => a.name);
    release.version = tag;
    release.tagUrl = data.html_url || `${release.repo}/releases/tag/${tag}`;
    const templated = releaseAssets(tag);
    const resolved = {};
    Object.keys(ASSET_PATTERNS).forEach((key) => {
      resolved[key] = liveNames.find((n) => ASSET_PATTERNS[key].test(n)) || templated[key];
    });
    release.assets = resolved;
    renderReleaseReferences();
  } catch (error) {
    // Offline or rate-limited: the fallback render stays in place.
  }
})();

/* PawFlow ESPER: one photographic journey across the complete public site. */
(() => {
  'use strict';
  const CHAPTERS = [
    ['index.html','Discover'],['product.html','The runtime'],['features.html','Capabilities'],
    ['relays.html','Real machines'],['flows.html','Flows'],['integrations.html','Connections'],
    ['use-cases.html','In practice'],['howtos.html','How-tos'],['docs.html','Documentation'],
    ['faq.html','Questions'],['quickstart.html','Install']
  ];
  const GUIDE_GROUPS = [{"id":"install","scene":"station","title":"Start with something useful.","copy":"Install the runtime, configure your first agent, and verify the route.","recipes":["agent-tool-selection","install-wizard","install-docker","server-update","first-agent","troubleshoot"]},{"id":"agents-interop","scene":"agents","title":"Give reasoning a durable home.","copy":"Models, routing, external agents, and shared context.","recipes":["published-mcp-client","acp-agent","managed-mcp-providers","agui-embed","delegated-vision","multi-llm-aggregator","fault-tolerant-llm","native-cli-plugins","provider-tmux"]},{"id":"clients","scene":"train","title":"Continue from anywhere.","copy":"One conversation across your browser, terminal, editor, and phone.","aliases":["channels"],"recipes":["pawcode-installer","pawcode-usage","vscode-plugin-installer","vscode-code-server","chat-views","telegram","android-app"]},{"id":"relays-workspaces","scene":"servers","title":"Reach your real machines.","copy":"Connect the files, tools, browsers, and desktops where work lives.","recipes":["desktop-novnc-audio","relay-desktop-installer","relay-cli-installer","server-relay","remote-relay","desktop-relay","relay-terminals"]},{"id":"identity","scene":"vault","title":"Keep access intentional.","copy":"Identity, secrets, encryption, and the context your agents retain.","aliases":["security-context"],"recipes":["oauth-provider","oauth-refresh-policy","rclone-filesystem","variables-secrets","encryption","webchat-editors","cognitive-routing","compact-summarizer","private-gateway","private-demo"]},{"id":"resources","scene":"resources","title":"Build a library that stays.","copy":"Reusable skills, tools, packages, prompts, and themes.","recipes":["pawflow-depots","skills","skill-loop","mcp-hooks-tools-prompts","pfp-packages","marketplace","themes"]},{"id":"flows","scene":"workshop","title":"Turn discoveries into routines.","copy":"Design with agents. Run repeatable work as durable flows.","recipes":["flows-explained","agent-flow-main","workflow-agents","workflow-proposals","tasks-plans","daily-digest"]},{"id":"media-voice","scene":"observatory","title":"Make something worth seeing.","copy":"Images, films, music, voice, and multimodal tools.","recipes":["media-service","comfyui","voice-service","realtime-voice"]}];
  let visitStack = [], settledPath = null;
  const baseURL = new URL('.', location.href);
  const mod = (n, count) => ((n % count) + count) % count;
  const byId = id => document.getElementById(id);
  const safeGet = (key, fallback) => { try { return localStorage.getItem(key) ?? fallback; } catch (_) { return fallback; } };
  const safeSet = (key, value) => { try { localStorage.setItem(key,value); } catch (_) {} };
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const mediaQuery = matchMedia('(prefers-reduced-motion: reduce)');
  let reduced = safeGet('pawflow-site-motion','') === 'reduce' || mediaQuery.matches;
  let steps = [], pages = [], current = 0, ordinal = 0, camera, anim = 0, busy = false;
  let content, shell, indexDialog, savedIndexFocus, activeToken = 0;
  let sound, indexReady = false;

  function extract(doc, file, chapter) {
    const main = doc.querySelector(file === 'howtos.html' ? '.howto-reader' : 'main');
    if (!main) throw new Error('Missing page content: ' + file);
    let nodes;
    if (file === 'howtos.html') {
      const intro=main.querySelector('.page-hero').cloneNode(true);
      intro.dataset.esperScene='archive';intro.dataset.esperDetail='0';intro.id='howtos-home';
      nodes=[intro];
      for(const group of GUIDE_GROUPS){
        const category=document.createElement('section');category.className='howto-section';
        category.id=group.id;category.dataset.esperScene=group.scene;category.dataset.esperDetail='0';
        category.dataset.esperAliases=(group.aliases||[]).join(' ');
        category.innerHTML='<p class="eyebrow">FIELD GUIDES / '+escape(group.id.replaceAll('-',' '))+'</p><h2>'+escape(group.title)+'</h2><p>'+escape(group.copy)+'</p><div class="esper-recipes"></div>';
        const recipes=group.recipes.map((id,i)=>{
          const source=main.querySelector('#'+CSS.escape(id));
          if(!source)throw new Error('Missing canonical recipe: '+id);
          const recipe=source.cloneNode(true);recipe.dataset.esperScene=group.scene;recipe.dataset.esperDetail=String(i+1);
          const link=document.createElement('a');link.href='howtos.html#'+id;
          link.innerHTML='<span>'+String(i+1).padStart(2,'0')+'</span><b>'+escape(recipe.querySelector('h2').textContent)+'</b><i>↗</i>';
          category.querySelector('.esper-recipes').append(link);
          return recipe;
        });
        nodes.push(category,...recipes);
      }
    } else if (file === 'faq.html') {
      nodes = [main.querySelector('.page-hero'), ...main.querySelectorAll('details'), main.lastElementChild];
    } else if (['features.html','docs.html','use-cases.html'].includes(file)) {
      nodes = [main.querySelector('.page-hero'),...main.querySelectorAll('article.doc-group')];
      const last = main.lastElementChild;
      if (last && !last.querySelector('article.doc-group')) nodes.push(last);
    } else {
      nodes = [...main.children].filter(node => node.tagName === 'SECTION');
    }
    const unique = [...new Set(nodes.filter(Boolean))];
    const records = unique.map((source, part) => {
      const node = source.cloneNode(true);
      const id = node.id || (part ? 'part-' + (part+1) : 'overview');
      node.id = id;
      node.removeAttribute('hidden');
      node.classList.add('esper-section');
      node.querySelectorAll('[data-reveal]').forEach(el => { el.removeAttribute('data-reveal'); el.classList.add('is-visible'); });
      if (node.matches('details')) node.open = true;
      const heading = node.querySelector('h1,h2,summary') || node;
      const title = heading.textContent.trim().replace(/\s+/g,' ');
      const aliases = [id,...node.querySelectorAll('[id]')].map(el => typeof el === 'string' ? el : el.id);
      aliases.push(...(node.dataset.esperAliases||'').split(' ').filter(Boolean));
      const homeScenes=['study','control','workshop','observatory','garden','archive','station'];
      const pageScenes=['study','control','agents','servers','workshop','garden','workshop','archive','archive','vault','station'];
      const sceneId=node.dataset.esperScene || (file==='index.html'?homeScenes[part]:pageScenes[chapter]);
      const detail=node.dataset.esperDetail!==undefined?Number(node.dataset.esperDetail):(file==='index.html'?0:part);
      return {file,chapter,part,id,title,node,aliases,sceneId,detail,url:file+'#'+encodeURIComponent(id)};
    });
    return {file,chapter,title:doc.title,records};
  }

  function createAudio() {
    const music = new Audio(new URL('assets/media/esper/ambient.mp3',baseURL));
    const forward = new Audio(new URL('assets/media/esper/zoom-in.mp3',baseURL));
    const backward = new Audio(new URL('assets/media/esper/zoom-out.mp3',baseURL));
    music.loop = true; music.preload = 'metadata'; music.setAttribute('playsinline','');
    forward.preload = backward.preload = 'none';
    let wanted = safeGet('pawflow-site-sound','on') !== 'off';
    let volume = Math.min(1,Math.max(0,Number(safeGet('pawflow-site-volume','0.85')) || 0));
    let ctx, stopTimer, failed = false, restore = null;
    try { restore = JSON.parse(sessionStorage.getItem('pawflow-site-sound-playback') || 'null'); } catch (_) {}
    const button = byId('esper-sound');
    const slider = byId('esper-volume');
    slider.value = String(Math.round(volume*100));
    function update() {
      const playing = wanted && !music.paused;
      button.setAttribute('aria-pressed',String(playing));
      button.querySelector('span').textContent = failed ? 'Audio unavailable' : playing ? 'Sound on' : wanted ? 'Start sound' : 'Sound off';
      button.classList.toggle('is-on',playing);
    }
    function applyVolume() {
      const videoPlaying = [...document.querySelectorAll('.esper-reader video')].some(v => !v.paused && !v.muted);
      music.volume = volume*(videoPlaying ? .065 : .28);
      forward.volume = backward.volume = volume*.36;
    }
    async function start() {
      if (!wanted || document.hidden) return;
      if (!ctx) { const AC = window.AudioContext || window.webkitAudioContext; if (AC) ctx = new AC(); }
      if (ctx?.state === 'suspended') ctx.resume().catch(()=>{});
      if (music.paused && !failed) { try { await music.play(); } catch (_) {} }
      update();
    }
    function stopEffects() { clearTimeout(stopTimer); forward.pause(); backward.pause(); }
    function click() {
      if (!wanted || !ctx || ctx.state !== 'running') return;
      const oscillator = ctx.createOscillator(), gain = ctx.createGain(), t = ctx.currentTime;
      oscillator.frequency.setValueAtTime(740,t); oscillator.frequency.exponentialRampToValueAtTime(440,t+.075);
      gain.gain.setValueAtTime(Math.max(.0001,volume*.018),t); gain.gain.exponentialRampToValueAtTime(.0001,t+.12);
      oscillator.connect(gain);gain.connect(ctx.destination);oscillator.start(t);oscillator.stop(t+.13);
    }
    function zoom(direction, duration) {
      stopEffects();
      if (!wanted || reduced) return;
      const audio = direction > 0 ? forward : backward;
      try { audio.currentTime = Math.max(0,7-duration/1000); } catch (_) {}
      audio.play().catch(()=>{});
      stopTimer = setTimeout(()=>audio.pause(),duration+30);
    }
    button.addEventListener('click',() => {
      if (wanted && music.paused) start();
      else { wanted = !wanted; if (wanted) start(); else {music.pause();stopEffects();} }
      safeSet('pawflow-site-sound',wanted?'on':'off');update();
    });
    slider.addEventListener('input',() => {volume=Number(slider.value)/100;safeSet('pawflow-site-volume',String(volume));applyVolume();});
    document.addEventListener('pointerdown', event => { if (!event.target.closest('#esper-sound')) start(); },{passive:true});
    document.addEventListener('keydown',start,{passive:true});
    music.addEventListener('playing',update);music.addEventListener('pause',update);
    music.addEventListener('error',()=>{failed=true;update();});
    music.addEventListener('loadedmetadata',()=>{
      if (restore && Number.isFinite(restore.position) && music.duration) music.currentTime=restore.position%music.duration;
      restore=null;
    });
    document.addEventListener('play',applyVolume,true);document.addEventListener('pause',applyVolume,true);
    document.addEventListener('volumechange',event=>{if(event.target.tagName==='VIDEO')applyVolume();},true);
    document.addEventListener('visibilitychange',()=>{
      if(document.hidden){music.pause();stopEffects();}
      else start();
    });
    window.addEventListener('pagehide',()=>{
      try {sessionStorage.setItem('pawflow-site-sound-playback',JSON.stringify({position:music.currentTime,savedAt:Date.now()}));} catch (_) {}
    });
    applyVolume();update();
    return {start,click,zoom,stopEffects,applyVolume,get state(){return {wanted,paused:music.paused,time:music.currentTime,duration:music.duration,loop:music.loop,volume:music.volume};}};
  }

  function mount() {
    shell = document.createElement('div');
    shell.className = 'esper-shell';
    shell.innerHTML = `
      <div class="esper-stage" aria-hidden="true"><canvas id="esper-photo"></canvas><div class="esper-vignette"></div><div class="esper-grain"></div></div>
      <header class="esper-header">
        <a class="esper-brand" href="index.html"><img src="assets/logo.png" alt=""><span>PAWFLOW<small>SELF-HOSTED INTELLIGENCE</small></span></a>
        <div class="esper-header-links"><a href="docs.html">Documentation</a><a href="quickstart.html">Install PawFlow ↗</a></div>
        <button id="esper-index-open" aria-haspopup="dialog" aria-controls="esper-index">All sections <span>☰</span></button>
      </header>
      <nav class="esper-chapters" aria-label="Website chapters">${CHAPTERS.map(([file,title],i)=>`<a href="${file}" title="${title}" aria-label="${title}"><span>${String(i+1).padStart(2,'0')}</span><b>${title}</b></a>`).join('')}</nav>
      <nav id="esper-portals" aria-label="Photographic destinations"></nav>
      <div class="esper-optics" aria-hidden="true"><div class="esper-scan"></div><i></i><i></i><i></i><div id="esper-command">TRACK. ENHANCE. EXPLORE.</div></div>
      <div class="esper-photo-caption" aria-hidden="true"><span id="esper-photo-number">FRAME / 001</span><strong id="esper-photo-title">A world within a photograph.</strong><small>SCROLL TO EXPLORE · CLICK THE FRAME TO ENTER</small></div>
      <main class="esper-reader" id="esper-content" tabindex="-1" aria-label="Section content"></main>
      <div class="esper-reader-heading"><span id="esper-chapter-name"></span><span id="esper-part-number"></span></div>
      <footer class="esper-controls">
        <div class="esper-travel"><button id="esper-junction" aria-label="Return to the photo junction">⌂ <span>Junction</span></button><button id="esper-back" aria-label="Zoom out to previous section">← <span>Pull back</span></button><button id="esper-next" aria-label="Zoom into next section"><span>Enhance</span> →</button></div>
        <div class="esper-readout" aria-hidden="true"><span>ZM <b id="esper-zm">1.00</b></span><span>NS <b id="esper-ns">0500</b></span><span>EW <b id="esper-ew">0500</b></span></div>
        <div class="esper-preferences"><button id="esper-motion" aria-pressed="false">Motion</button><button id="esper-sound" aria-pressed="false"><i aria-hidden="true">▥</i> <span>Start sound</span></button><label class="esper-volume"><span class="sr-only">Volume</span><input id="esper-volume" type="range" min="0" max="100" value="85"></label></div>
      </footer>
      <div id="esper-status" class="sr-only" role="status" aria-live="polite"></div>
      <dialog id="esper-index" aria-labelledby="esper-index-title">
        <header><div><p class="eyebrow">CHOOSE YOUR DESTINATION</p><h2 id="esper-index-title">A closer look.</h2></div><button id="esper-index-close" aria-label="Close section index">×</button></header>
        <label class="esper-search"><span class="sr-only">Find a section or recipe</span><input type="search" id="esper-search" placeholder="Find a section, a capability, a recipe…" autocomplete="off"></label>
        <div class="esper-index-results" id="esper-index-results"></div>
        <p class="esper-index-hint">Select a destination to zoom directly into it.</p>
      </dialog>`;
    document.querySelectorAll('body > main,body > header,body > footer,.zoom-story-links,.zoom-story-hint').forEach(el=>el.remove());
    document.body.prepend(shell);
    document.body.className = 'esper-active';
    content = byId('esper-content');
    indexDialog = byId('esper-index');
    sound = createAudio();
    const motionButton = byId('esper-motion');
    const updateMotion = () => {motionButton.setAttribute('aria-pressed',String(!reduced));motionButton.textContent=reduced?'Motion reduced':'Motion full';};
    motionButton.addEventListener('click',()=>{reduced=!reduced;safeSet('pawflow-site-motion',reduced?'reduce':'full');updateMotion();});
    mediaQuery.addEventListener('change',event=>{reduced=event.matches;updateMotion();});
    updateMotion();
    byId('esper-back').addEventListener('click',()=>move(-1));
    byId('esper-next').addEventListener('click',()=>move(1));
    byId('esper-junction').addEventListener('click',()=>{
      const route=settledPath||camera.path;
      let end=route.length-2;
      while(end>0&&window.ESPER_WORLD.scenes[route[end]].portals.length<2)end--;
      const path=route.slice(0,end+1),scene=path[path.length-1];
      const record=steps.find(r=>r.sceneId===scene&&r.detail===0)||steps[0];
      navigate(record.index,{path});
    });
    byId('esper-index-open').addEventListener('click',()=>{
      savedIndexFocus=document.activeElement;indexDialog.showModal();byId('esper-search').focus();
    });
    byId('esper-index-close').addEventListener('click',()=>indexDialog.close());
    indexDialog.addEventListener('click',event=>{if(event.target===indexDialog)indexDialog.close();});
    indexDialog.addEventListener('close',()=>savedIndexFocus?.focus({preventScroll:true}));
    byId('esper-search').addEventListener('input',renderIndex);
    renderIndex();
  }

  function renderIndex() {
    const query = byId('esper-search').value.toLowerCase().trim();
    const results = byId('esper-index-results');
    results.replaceChildren();
    let count = 0;
    pages.forEach(page => {
      const records = page.records.filter(record => (CHAPTERS[record.chapter][1]+' '+record.title+' '+record.id).toLowerCase().includes(query));
      if (!records.length) return;
      const group = document.createElement('section');
      group.innerHTML = '<h3>'+escape(CHAPTERS[page.chapter][1])+'</h3>';
      for (const record of records) {
        const a = document.createElement('a');a.href=record.url;
        a.innerHTML='<span>'+String(record.index+1).padStart(3,'0')+'</span><b>'+escape(record.title)+'</b><i>↗</i>';
        group.append(a);count++;
      }
      results.append(group);
    });
    if (!count) results.textContent='No matching section. Try another word.';
  }

  function findRoute(url) {
    const file = (window.ESPER_PREVIEW && url.searchParams.get('page')) || url.pathname.split('/').pop() || 'index.html';
    if (!CHAPTERS.some(([name])=>name===file)) return null;
    if (new URL('.',url).pathname !== baseURL.pathname || url.origin !== baseURL.origin) return null;
    let fragment;
    try {fragment=decodeURIComponent(url.hash.slice(1)) || url.searchParams.get('read') || '';} catch (_) {return null;}
    const page = pages.find(item=>item.file===file);
    if (!page) return null;
    return page.records.find(record=>record.aliases.includes(fragment)) || (!fragment ? page.records[0] : null);
  }

  function renderContent(record, fragment='') {
    content.querySelectorAll('video').forEach(video=>video.pause());
    content.replaceChildren(record.node);
    content.scrollTop=0;
    document.title=pages[record.chapter].title;
    document.body.dataset.page=record.file.replace('.html','');
    byId('esper-chapter-name').textContent=String(record.chapter+1).padStart(2,'0')+' / '+CHAPTERS[record.chapter][1];
    byId('esper-part-number').textContent=String(record.part+1).padStart(2,'0')+' / '+String(pages[record.chapter].records.length).padStart(2,'0');
    document.querySelectorAll('.esper-chapters a').forEach((a,index)=>{
      if(index===record.chapter)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');
    });
    byId('esper-next').title=steps[mod(record.index+1,steps.length)].title;
    byId('esper-back').title=steps[mod(record.index-1,steps.length)].title;

    if (typeof renderReleaseReferences === 'function') renderReleaseReferences();
    sound.applyVolume();
    if(fragment) {
      const target=content.querySelector('#'+CSS.escape(fragment));
      if(target && target!==record.node)target.scrollIntoView({block:'start'});
    }
  }

  function onCamera(state) {
    const host=byId('esper-portals'),stage=byId('esper-photo').parentElement.getBoundingClientRect();
    if(host.dataset.scene!==state.scene){
      host.replaceChildren();host.dataset.scene=state.scene;
      for(const portal of state.portals){
        const link=document.createElement('a');link.className='esper-portal';
        link.href=portal.href||'#';link.dataset.photoTarget=portal.target;
        link.dataset.caption=window.ESPER_WORLD.scenes[portal.target].shortLabel;
        link.innerHTML='<i></i><span>'+escape(portal.label)+' <b>↗</b></span>';
        link.setAttribute('aria-label','Zoom into '+portal.label);host.append(link);
      }
    }
    state.portals.forEach((p,i)=>{
      const el=host.children[i];el.style.left=(p.x+stage.left)+'px';el.style.top=(p.y+stage.top)+'px';
      el.style.width=Math.max(32,p.w)+'px';el.style.height=Math.max(32,p.h)+'px';
      const visible=p.x+p.w>0&&p.y+p.h>0&&p.x<state.width&&p.y<state.height;
      el.hidden=!visible;el.tabIndex=busy?-1:0;
    });
    shell.classList.toggle('is-junction',state.portals.length>1&&!busy);
    byId('esper-zm').textContent=state.depth<8?Math.pow(4,state.depth).toFixed(2)+'×':'10^'+(state.depth*Math.log10(4)).toFixed(2);
    byId('esper-ns').textContent=(500+state.fraction*100).toFixed(3);
    byId('esper-ew').textContent=(500-state.fraction*100).toFixed(3);
    byId('esper-photo-number').textContent='DEPTH / '+String(Math.floor(state.depth)).padStart(3,'0');
    byId('esper-photo-title').textContent=window.ESPER_WORLD.scenes[state.scene].label;
  }

  function canonicalRoute(record) {return camera.extend(camera.canonicalPath(record.sceneId),record.detail);}
  function navigate(index,options={}) {
    if(!indexReady)return;
    const record=steps[index];if(!record)return;
    const oldPath=[...(settledPath||camera.path)];
    const newPath=options.path||canonicalRoute(record);
    if(!options.instant&&!options.skipRemember&&settledPath)visitStack.push({index:current,path:oldPath});
    current=index;ordinal=Number.isFinite(options.ordinal)?options.ordinal:index;
    const token=++activeToken;cancelAnimationFrame(anim);
    const fromDepth=camera.depth,common=EsperCamera.commonPrefix(camera.path,newPath);
    const pivot=Math.max(0,common-1),oldRoute=[...camera.path];
    const needsReturn=common<Math.min(oldRoute.length,newPath.length);
    const outDistance=needsReturn?Math.max(0,fromDepth-pivot):0;
    const destination=newPath.length-1;
    const totalDistance=outDistance+(needsReturn?destination-pivot:Math.abs(destination-fromDepth));
    const split=needsReturn?Math.max(.25,Math.min(.65,outDistance/Math.max(1,totalDistance))):0;
    const ms=options.instant?0:reduced?120:Math.min(3200,1550+totalDistance*100);
    if(options.history!==false){
      const url=new URL(record.url,baseURL);if(options.fragment)url.hash=options.fragment;
      if(window.ESPER_PREVIEW){
        url.pathname=location.pathname;url.searchParams.set('page',record.file);
      }
      history.pushState({esper:index,ordinal,path:newPath},'',url);
    }
    if(indexDialog.open)indexDialog.close();
    busy=ms>0;shell.classList.toggle('is-travelling',busy);content.inert=busy;
    byId('esper-command').textContent=(needsReturn?'PULL BACK. REFRAME. ':'TRACK. ENHANCE. ')+record.title.toUpperCase();
    sound.zoom(needsReturn||destination<fromDepth?-1:1,ms);
    const ease=t=>t*t*(3-2*t),start=performance.now();let swapped=false;
    function tick(now){
      if(token!==activeToken)return;
      const p=ms?Math.min(1,(now-start)/ms):1;
      const t=Math.max(0,Math.min(1,(p-.10)/.78));
      if(reduced)camera.draw(p<.5?fromDepth:destination,p<.5?oldRoute:newPath);
      else if(needsReturn&&t<split)camera.draw(fromDepth+(pivot-fromDepth)*ease(t/split),oldRoute);
      else if(needsReturn)camera.draw(pivot+(destination-pivot)*ease((t-split)/(1-split)),newPath);
      else camera.draw(fromDepth+(destination-fromDepth)*ease(t),common===oldRoute.length?newPath:oldRoute);
      if(!swapped&&p>=.55){renderContent(record,options.fragment);swapped=true;}
      content.style.opacity=p<.55?String(Math.max(0,1-p/.16)):String(Math.max(0,(p-.82)/.18));
      if(p<1){anim=requestAnimationFrame(tick);return;}
      settledPath=[...newPath];busy=false;content.inert=false;content.style.opacity='';
      camera.draw(destination,newPath);shell.classList.remove('is-travelling');sound.stopEffects();
      byId('esper-status').textContent=CHAPTERS[record.chapter][1]+'. '+record.title;
      if(!options.instant){const heading=content.querySelector('h1,h2,summary');if(heading){heading.tabIndex=-1;heading.focus({preventScroll:true});}}
    }
    tick(start);
  }
  function move(direction) {
    if(busy)return;
    if(direction<0&&visitStack.length){
      const visit=visitStack.pop();navigate(visit.index,{path:visit.path,skipRemember:true});return;
    }
    const index=mod(current+direction,steps.length);
    let path=canonicalRoute(steps[index]);
    if(direction>0){
      const old=settledPath||camera.path;
      const from=old[old.length-1],target=path[path.length-1];
      let suffix=camera.canonicalPath(target,from).slice(1);
      if(!suffix.length){
        const first=window.ESPER_WORLD.scenes[from].portals[0].target;
        suffix=[first,...camera.canonicalPath(target,first).slice(1)];
      }
      path=[...old,...suffix];
    }
    navigate(index,{path,ordinal:ordinal+direction});
  }
  function isControl(target) {return target.closest('input,textarea,select,[contenteditable],video,pre,.pf-help-panel,.pf-help-launcher,dialog');}
  function canScroll(target, direction) {
    for(let el=target;el && el!==shell;el=el.parentElement){
      if(el.scrollHeight>el.clientHeight+2&&/(auto|scroll)/.test(getComputedStyle(el).overflowY)){
        if(direction>0?el.scrollTop+el.clientHeight<el.scrollHeight-2:el.scrollTop>2)return true;
      }
    }return false;
  }
  function bindNavigation() {
    document.addEventListener('click',event=>{
      const link=event.target.closest('a[href]');
      if(!link||event.defaultPrevented||event.button||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey||link.target==='_blank'||link.hasAttribute('download'))return;
      const url=new URL(link.getAttribute('href'),location.href);
      if(link.dataset.photoTarget){
        if(busy){event.preventDefault();return;}
        event.preventDefault();sound.click();
        const record=findRoute(url)||steps[mod(current+1,steps.length)];
        navigate(record.index,{path:[...(settledPath||camera.path),link.dataset.photoTarget]});
        return;
      }
      const record=findRoute(url);
      if(!record)return;
      event.preventDefault();sound.click();
      navigate(record.index,{fragment:decodeURIComponent(url.hash.slice(1))});
    });
    document.addEventListener('click',event=>{
      if(event.target.closest('button,summary'))sound.click();
      const button=event.target.closest('[data-copy]');
      if(!button)return;
      const target=content.querySelector(button.dataset.copy);
      if(target)navigator.clipboard?.writeText(target.textContent).then(()=>{button.textContent='Copied';}).catch(()=>{button.textContent='Select and copy';});
    });
    let lastWheel=0, accumulator=0, consumed=false, scrolling=false;
    window.addEventListener('wheel',event=>{
      if(!indexReady||event.ctrlKey||Math.abs(event.deltaX)>Math.abs(event.deltaY)||isControl(event.target)||indexDialog.open)return;
      const now=performance.now(),fresh=now-lastWheel>180;lastWheel=now;
      if(fresh){accumulator=0;consumed=false;scrolling=canScroll(event.target,Math.sign(event.deltaY));}
      if(scrolling)return;
      event.preventDefault();
      if(consumed||busy)return;
      const delta=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?innerHeight:1);
      accumulator+=delta;
      if(Math.abs(accumulator)>=45){consumed=true;move(Math.sign(accumulator));}
    },{passive:false});
    document.addEventListener('keydown',event=>{
      if(event.defaultPrevented||isControl(event.target)||indexDialog.open||event.metaKey||event.ctrlKey||event.altKey)return;
      if(['ArrowRight','PageDown','ArrowLeft','PageUp'].includes(event.key)){
        event.preventDefault();move(['ArrowRight','PageDown'].includes(event.key)?1:-1);
      }
    });
    let touch;
    shell.addEventListener('touchstart',event=>{
      if(event.touches.length!==1||isControl(event.target))return;
      const t=event.touches[0];touch={x:t.clientX,y:t.clientY,target:event.target,
        up:canScroll(event.target,1),down:canScroll(event.target,-1)};
    },{passive:true});
    shell.addEventListener('touchend',event=>{
      if(!touch||busy)return;
      const t=event.changedTouches[0],dx=t.clientX-touch.x,dy=t.clientY-touch.y;
      if(Math.abs(dy)>65&&Math.abs(dy)>Math.abs(dx)&&!(dy<0?touch.up:touch.down))move(dy<0?1:-1);
      touch=null;
    },{passive:true});
    window.addEventListener('popstate',event=>{
      const record=findRoute(new URL(location.href));
      if(record)navigate(record.index,{history:false,ordinal:event.state?.ordinal,path:event.state?.path,skipRemember:true,fragment:decodeURIComponent(location.hash.slice(1))});
    });
    window.addEventListener('hashchange',()=>{
      const record=findRoute(new URL(location.href));
      if(record&&record.index!==current)navigate(record.index,{history:false,fragment:decodeURIComponent(location.hash.slice(1))});
    });
  }

  async function boot() {
    const initialFile=location.pathname.split('/').pop() || 'index.html';
    pages=await Promise.all(CHAPTERS.map(async([file],chapter)=>{
      let doc;
      if(file===initialFile)doc=document;
      else {
        const embedded=window.ESPER_DOCUMENTS?.[file];
        if(embedded)doc=new DOMParser().parseFromString(embedded,'text/html');
        else {
          const response=await fetch(new URL(file,baseURL));
          if(!response.ok)throw new Error('Page unavailable: '+file);
          doc=new DOMParser().parseFromString(await response.text(),'text/html');
        }
      }
      return extract(doc,file,chapter);
    }));
    steps=pages.flatMap(page=>page.records);
    steps.forEach((record,index)=>{record.index=index;});
    const initial=findRoute(new URL(location.href)) || steps[0];
    mount();
    camera=new EsperCamera(byId('esper-photo'),window.ESPER_WORLD,onCamera);
    renderContent(initial);
    await camera.ready;
    indexReady=true;bindNavigation();
    current=initial.index;ordinal=initial.index;
    history.replaceState({esper:current,ordinal,path:canonicalRoute(initial)},'',location.href);
    navigate(current,{instant:true,history:false,ordinal,fragment:decodeURIComponent(location.hash.slice(1))});
    window.PawFlowEsper={navigate:(index)=>navigate(index),next:()=>move(1),previous:()=>move(-1),
      get state(){return {ready:indexReady,current,ordinal,busy,total:steps.length,reduced,camera:camera.state,audio:sound.state,
        section:steps[current].id,file:steps[current].file};},
      get sections(){return steps.map(({index,file,id,title,url})=>({index,file,id,title,url}));}};
  }
  boot().catch(error=>{
    console.error('PawFlow exploration could not start',error);
    if(shell){
      content.inert=false;shell.classList.remove('is-travelling');
      const note=document.createElement('p');note.className='esper-load-error';
      note.textContent='The photographic journey is unavailable. Use the section links to continue reading.';
      content.prepend(note);
      byId('esper-index-open').onclick=()=>indexDialog.showModal();
    }
  });
})();


// ── Help widget (talks to the web_help_bot flow: POST /api/help) ──────
// Same-origin endpoint, fronted by Caddy (keep the listener port private).
// Disable on a page with <body data-no-help>; override the path with
// <body data-help-endpoint="/api/help">.
(function initHelpWidget() {
  if (document.body.dataset.noHelp !== undefined) return;
  const ENDPOINT = document.body.dataset.helpEndpoint || '/api/help';
  const STATUS = {
    400: 'Please type a message first.',
    429: 'You are sending messages too fast. Please wait a moment.',
    503: 'The help bot is temporarily unavailable. Please try again later.',
    504: 'Sorry, this took too long. Please try again.',
  };

  const SVG_NS = 'http://www.w3.org/2000/svg';
  function icon(paths, size) {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    if (size) { svg.setAttribute('width', size); svg.setAttribute('height', size); }
    paths.forEach((d) => {
      const p = document.createElementNS(SVG_NS, 'path');
      p.setAttribute('d', d);
      svg.appendChild(p);
    });
    return svg;
  }

  const launcher = document.createElement('button');
  launcher.type = 'button';
  launcher.className = 'pf-help-launcher';
  launcher.setAttribute('aria-label', 'Open the PawFlow help chat');
  launcher.appendChild(icon(['M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z'], 18));
  launcher.appendChild(document.createTextNode('Ask PawFlow'));

  const panel = document.createElement('div');
  panel.className = 'pf-help-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'PawFlow help chat');
  panel.setAttribute('aria-modal', 'false');

  const head = document.createElement('div');
  head.className = 'pf-help-head';
  const dot = document.createElement('span');
  dot.className = 'pf-help-dot';
  const titles = document.createElement('div');
  titles.className = 'pf-help-titles';
  const h3 = document.createElement('h3');
  h3.textContent = 'PawFlow help';
  const sub = document.createElement('p');
  sub.textContent = 'Ask about install, flows, agents, tools.';
  titles.append(h3, sub);
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'pf-help-close';
  closeBtn.setAttribute('aria-label', 'Close help chat');
  closeBtn.appendChild(icon(['M18 6 6 18', 'M6 6l12 12'], 18));
  head.append(dot, titles, closeBtn);

  const log = document.createElement('div');
  log.className = 'pf-help-log';
  const intro = document.createElement('div');
  intro.className = 'pf-help-msg intro';
  intro.textContent = 'Hi! I am the PawFlow help bot. Ask me anything about running PawFlow.';
  log.appendChild(intro);

  const form = document.createElement('form');
  form.className = 'pf-help-form';
  const input = document.createElement('textarea');
  input.rows = 1;
  input.placeholder = 'Type your question...';
  input.setAttribute('aria-label', 'Your message');
  const send = document.createElement('button');
  send.type = 'submit';
  send.className = 'pf-help-send';
  send.textContent = 'Send';
  form.append(input, send);

  panel.append(head, log, form);
  document.body.append(launcher, panel);

  panel.addEventListener('wheel', (event) => {
    event.stopPropagation();
    const target = event.target;
    if (!target || typeof target.closest !== 'function') return;
    if (target.closest('.pf-help-log')) return;
    const textarea = target.closest('.pf-help-form textarea');
    if (textarea && textarea.scrollHeight > textarea.clientHeight) return;
    const delta = event.deltaMode === 1
      ? event.deltaY * 16
      : event.deltaMode === 2
        ? event.deltaY * log.clientHeight
        : event.deltaY;
    if (!delta) return;
    event.preventDefault();
    log.scrollTop += delta;
  }, { passive: false });

  let busy = false;
  function scrollDown() { log.scrollTop = log.scrollHeight; }
  function addMsg(role, text) {
    const el = document.createElement('div');
    el.className = 'pf-help-msg ' + role;
    el.textContent = text;
    log.appendChild(el);
    scrollDown();
    return el;
  }
  function showTyping() {
    const t = document.createElement('div');
    t.className = 'pf-help-typing';
    t.append(document.createElement('span'), document.createElement('span'), document.createElement('span'));
    log.appendChild(t);
    scrollDown();
    return t;
  }

  function open() {
    panel.classList.add('is-open');
    launcher.classList.add('is-hidden');
    pinFloating();
    setTimeout(() => input.focus(), 50);
  }
  function close() {
    panel.classList.remove('is-open');
    launcher.classList.remove('is-hidden');
    launcher.focus();
  }
  launcher.addEventListener('click', open);
  closeBtn.addEventListener('click', close);
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && panel.classList.contains('is-open')) close();
  });

  // ── Floating-window behaviour: drag by the header; resize via the CSS
  // grip (bottom-right). Only on wider viewports — on phones the panel stays
  // full-screen (see the max-width: 520px media query).
  const FLOAT_MIN_VW = 520;
  function floatable() { return window.innerWidth > FLOAT_MIN_VW; }
  function pinFloating() {
    // Switch from the default right/bottom anchoring to left/top so dragging
    // and the resize grip both behave like a normal window. Done once.
    if (!floatable() || panel.dataset.pinned) return;
    const r = panel.getBoundingClientRect();
    panel.style.left = r.left + 'px';
    panel.style.top = r.top + 'px';
    panel.style.right = 'auto';
    panel.style.bottom = 'auto';
    panel.dataset.pinned = '1';
  }
  let drag = null;
  head.addEventListener('pointerdown', (e) => {
    if (!floatable() || e.target.closest('.pf-help-close')) return;
    pinFloating();
    const r = panel.getBoundingClientRect();
    drag = { dx: e.clientX - r.left, dy: e.clientY - r.top };
    head.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  head.addEventListener('pointermove', (e) => {
    if (!drag) return;
    const maxL = window.innerWidth - panel.offsetWidth;
    const maxT = window.innerHeight - panel.offsetHeight;
    panel.style.left = Math.max(0, Math.min(maxL, e.clientX - drag.dx)) + 'px';
    panel.style.top = Math.max(0, Math.min(maxT, e.clientY - drag.dy)) + 'px';
  });
  function endDrag(e) {
    if (!drag) return;
    drag = null;
    try { head.releasePointerCapture(e.pointerId); } catch (_) {}
  }
  head.addEventListener('pointerup', endDrag);
  head.addEventListener('pointercancel', endDrag);

  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 120) + 'px';
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  async function ask(text) {
    busy = true;
    send.disabled = true;
    const typing = showTyping();
    try {
      const resp = await fetch(ENDPOINT, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ message: text }),
      });
      let data = {};
      try { data = await resp.json(); } catch (err) { data = {}; }
      typing.remove();
      if (resp.ok) {
        addMsg('bot', (data.response || '').trim() || 'No response.');
      } else {
        addMsg('error', data.error || STATUS[resp.status] || ('Something went wrong (' + resp.status + ').'));
      }
    } catch (err) {
      typing.remove();
      addMsg('error', 'Network error. Please check your connection and try again.');
    } finally {
      busy = false;
      send.disabled = false;
      input.focus();
    }
  }

  form.addEventListener('submit', (e) => {
    e.preventDefault();
    if (busy) return;
    const text = input.value.trim();
    if (!text) return;
    addMsg('user', text);
    input.value = '';
    input.style.height = 'auto';
    ask(text);
  });
})();
