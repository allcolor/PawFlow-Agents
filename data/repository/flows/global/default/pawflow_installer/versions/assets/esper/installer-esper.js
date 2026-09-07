/* ESPER presentation for the real bootstrap wizard. No installer values are stored here. */
(function () {
  'use strict';
  const root = '/install/assets/';
  const portals = [[.208,.239,.32,.489],[.302,.358,.164,.138],[.337,.404,.064,.075],
    [.154,.515,.175,.184],[.179,.315,.065,.075],[.065,.318,.193,.235],
    [.245,.564,.082,.11],[.304,.319,.203,.223]];
  const captions = [
    ['IDENTITY', 'It starts with you.', 'Create the account that will own this installation.'],
    ['CONNECTIONS', 'Choose who comes in.', 'Connect the identities you trust. Builtin sign-in is always available.'],
    ['PRIVATE GATEWAY', 'Make this world yours.', 'Replace the temporary key and set your permanent entrance.'],
    ['INTELLIGENCE', 'A place for thought.', 'Connect your models, credentials and conversation memory.'],
    ['REAL MACHINES', 'Reach beyond the screen.', 'Give your agents a workspace, or connect one later.'],
    ['VOICE', 'Give it a voice.', 'Add speech and listening when you are ready.'],
    ['FIRST CONTACT', 'Begin a conversation.', 'Choose the agents and workspace for your first exchange.'],
    ['READY', 'Bring it to life.', 'Review your choices, then start your PawFlow runtime.']
  ];
  const mix = (a,b,t) => a+(b-a)*t;
  window.createInstallerEsper = function (navigate) {
    const byId = id => document.getElementById(id);
    const view = byId('esper-view'), canvas = byId('esper-photo'), paint = canvas.getContext('2d');
    const form = byId('wizard'), portal = byId('esper-portal'), wrap = document.querySelector('.wrap');
    const motionQuery = matchMedia('(prefers-reduced-motion: reduce)');
    const get = (key, fallback) => {try {return localStorage.getItem(key) ?? fallback;} catch (_) {return fallback;}};
    const save = (key, value) => {try {localStorage.setItem(key,value);} catch (_) {}};
    let current = 0, depth = 0, width = 1, height = 1, ready = false, busy = false, locked = false;
    let reduced = get('pawflow-install-motion','') === 'reduce' || motionQuery.matches;
    let wanted = get('pawflow-install-sound','on') !== 'off';
    let volume = Math.max(0,Math.min(1,Number(get('pawflow-install-volume','.65')) || 0));
    let frame = 0, finishTransition = null, audioContext, effectTimer;
    const images = Array.from({length:9}, (_,i) => {
      const image = new Image(); image.src = root+'scene-0'+i+'.jpg'; return image;
    });
    const music = new Audio(root+'ambient.mp3');
    const zoomIn = new Audio(root+'zoom-in.mp3'), zoomOut = new Audio(root+'zoom-out.mp3');
    music.loop = true; music.preload = 'metadata';
    zoomIn.preload = zoomOut.preload = 'none';
    const loginOpen = () => !byId('vnc_dialog').hidden;
    function volumeChanged() {music.volume=volume*.3; zoomIn.volume=zoomOut.volume=volume*.38;}
    function soundLabel() {
      byId('esper-sound').textContent = !wanted ? 'Sound off' : music.paused ? 'Start sound' : 'Sound on';
      byId('esper-sound').setAttribute('aria-pressed',String(wanted&&!music.paused));
    }
    function stopEffects() {clearTimeout(effectTimer); zoomIn.pause(); zoomOut.pause();}
    function startMusic() {
      if (!wanted || document.hidden || loginOpen()) return;
      if (music.paused) music.play().catch(soundLabel);
    }
    function clickSound() {
      startMusic();
      if (!wanted || document.hidden || loginOpen()) return;
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!audioContext) audioContext = new AC();
      if (audioContext.state==='suspended') audioContext.resume().catch(()=>{});
      const oscillator=audioContext.createOscillator(), gain=audioContext.createGain(), t=audioContext.currentTime;
      oscillator.frequency.setValueAtTime(740,t); oscillator.frequency.exponentialRampToValueAtTime(360,t+.08);
      gain.gain.setValueAtTime(Math.max(.0001,volume*.025),t); gain.gain.exponentialRampToValueAtTime(.0001,t+.12);
      oscillator.connect(gain); gain.connect(audioContext.destination); oscillator.start(t); oscillator.stop(t+.13);
    }
    function zoomSound(direction, ms) {
      stopEffects();
      if (!wanted || reduced || document.hidden || loginOpen()) return;
      const audio = direction>0 ? zoomIn : zoomOut;
      try {audio.currentTime=Math.max(0,7-ms/1000);} catch (_) {}
      audio.play().catch(()=>{});
      effectTimer=setTimeout(()=>audio.pause(),ms+20);
    }
    byId('esper-sound').addEventListener('click',()=>{
      if (wanted && music.paused) startMusic();
      else {wanted=!wanted; if(wanted)startMusic(); else {music.pause();stopEffects();}}
      save('pawflow-install-sound',wanted?'on':'off'); soundLabel();
    });
    byId('esper-volume').value=String(Math.round(volume*100));
    byId('esper-volume').addEventListener('input',event=>{
      volume=Number(event.target.value)/100;save('pawflow-install-volume',String(volume));volumeChanged();
    });
    document.addEventListener('pointerdown',event=>{if(!event.target.closest('#esper-sound'))startMusic();},{passive:true});
    document.addEventListener('keydown',startMusic,{passive:true});
    document.addEventListener('click',event=>{if(event.target.closest('button')&&!event.target.closest('#esper-sound'))clickSound();});
    music.addEventListener('playing',soundLabel);music.addEventListener('pause',soundLabel);
    music.addEventListener('error',()=>{byId('esper-sound').textContent='Audio unavailable';});
    function visibilityChanged() {
      if(document.hidden||loginOpen()){music.pause();stopEffects(); if(document.hidden)finishTransition?.();}
      else startMusic();
    }
    document.addEventListener('visibilitychange',visibilityChanged);
    new MutationObserver(visibilityChanged).observe(byId('vnc_dialog'),{attributes:true,attributeFilter:['hidden']});
    window.addEventListener('pagehide',()=>{music.pause();stopEffects();finishTransition?.();});
    function motionLabel() {
      byId('esper-motion').textContent=reduced?'Motion reduced':'Motion full';
      byId('esper-motion').setAttribute('aria-pressed',String(!reduced));
    }
    byId('esper-motion').addEventListener('click',()=>{
      reduced=!reduced;save('pawflow-install-motion',reduced?'reduce':'full');motionLabel();
      if(reduced)finishTransition?.();
    });
    motionQuery.addEventListener('change',event=>{reduced=event.matches;motionLabel();if(reduced)finishTransition?.();});

    // Rebase at the preceding photograph: tiny frames never accumulate precision loss.
    function draw(value) {
      depth=value;
      if(!ready)return;
      const base=Math.min(7,Math.floor(value)), start=Math.max(0,base-1), fraction=value-base, nodes=[];
      for(let i=start;i<images.length;i++){
        if(i===start)nodes.push({x:0,y:0,w:images[i].naturalWidth,h:images[i].naturalHeight});
        else {
          const parent=nodes[nodes.length-1], rect=portals[i-1];
          nodes.push({x:parent.x+rect[0]*parent.w,y:parent.y+rect[1]*parent.h,w:rect[2]*parent.w,h:rect[3]*parent.h});
        }
      }
      const goal=(node,index)=>{
        const image=images[index], scale=(width<=720?Math.min:Math.max)(width/image.naturalWidth,height/image.naturalHeight);
        return {x:node.x+node.w/2,y:node.y+node.h/2,ux:node.w/(image.naturalWidth*scale),uy:node.h/(image.naturalHeight*scale)};
      };
      const from=goal(nodes[base-start],base), to=goal(nodes[base-start+1],base+1);
      const ux=Math.exp(mix(Math.log(from.ux),Math.log(to.ux),fraction));
      const uy=Math.exp(mix(Math.log(from.uy),Math.log(to.uy),fraction));
      const progress=(a,b,u)=>Math.abs(a-b)<1e-15?fraction:(a-u)/(a-b);
      const cx=mix(from.x,to.x,progress(from.ux,to.ux,ux)),cy=mix(from.y,to.y,progress(from.uy,to.uy,uy));
      const project=node=>({x:(node.x-cx)/ux+width/2,y:(node.y-cy)/uy+height/2,w:node.w/ux,h:node.h/uy});
      paint.fillStyle='#081417';paint.fillRect(0,0,width,height);paint.save();
      nodes.forEach((node,n)=>{
        const index=start+n, b=project(node), image=images[index];
        if(index===1 && start===0){
          paint.beginPath();paint.ellipse(b.x+b.w/2,b.y+b.h/2,b.w/2,b.h/2,0,0,Math.PI*2);paint.clip();
        }
        const left=Math.max(0,b.x),top=Math.max(0,b.y),right=Math.min(width,b.x+b.w),bottom=Math.min(height,b.y+b.h);
        if(right<=left||bottom<=top)return;
        paint.drawImage(image,(left-b.x)/b.w*image.naturalWidth,(top-b.y)/b.h*image.naturalHeight,
          (right-left)/b.w*image.naturalWidth,(bottom-top)/b.h*image.naturalHeight,left,top,right-left,bottom-top);
      });
      paint.restore();
      const target=project(nodes[base-start+1]);
      portal.hidden=current>=7;
      portal.style.left=target.x+'px';portal.style.top=target.y+'px';
      portal.style.width=Math.max(32,target.w)+'px';portal.style.height=Math.max(32,target.h)+'px';
      if(width>720&&!busy){
        const caption=document.querySelector('.esper-caption');
        caption.style.top='';caption.style.bottom='';
        const box=caption.getBoundingClientRect();
        if(current<7&&target.x<box.right&&target.x+target.w>box.left&&target.y<box.bottom&&target.y+target.h>box.top&&target.y>box.height+130){
          caption.style.top=(target.y-box.height-28)+'px';caption.style.bottom='auto';
        }
      } else if(width<=720){
        const caption=document.querySelector('.esper-caption');caption.style.top='';caption.style.bottom='';
      }
      byId('esper-depth').textContent=(value+1).toFixed(2)+' / 08';
      byId('esper-zoom').textContent=Math.pow(4,value).toFixed(value<2?2:0)+'×';
    }
    function resize() {
      width=Math.max(1,view.clientWidth);height=Math.max(1,view.clientHeight);
      const dpr=Math.min(devicePixelRatio||1,2);
      canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);
      paint?.setTransform(dpr,0,0,dpr,0,0);draw(depth);
    }
    function setStep(index) {
      current=index;
      const caption=captions[index];
      byId('esper-chapter').textContent=String(index+1).padStart(2,'0')+' / '+caption[0];
      byId('esper-title').textContent=caption[1];byId('esper-description').textContent=caption[2];
      byId('esper-portal-label').textContent='ENHANCE / NEXT STEP';
      portal.setAttribute('aria-label','Validate this step and zoom to the next');
      portal.hidden=index>=7||!ready;
      if(!busy)draw(index);
    }
    async function transition(from,to,change) {
      busy=true;form.inert=true;wrap.classList.add('is-travelling');portal.disabled=true;
      const screen=form.querySelector('.screen.active');
      const ms=reduced||!ready?0:Math.min(2800,1500+Math.abs(to-from)*130);
      zoomSound(to-from,ms);
      await new Promise(resolve=>{
        let changed=false,finished=false;
        const swap=()=>{if(!changed){screen.style.opacity='';change();changed=true;}};
        const finish=()=>{
          if(finished)return;finished=true;cancelAnimationFrame(frame);swap();draw(to);
          form.querySelector('.screen.active').style.opacity='';
          busy=false;form.inert=false;wrap.classList.remove('is-travelling');portal.disabled=locked;stopEffects();draw(to);
          finishTransition=null;
          const heading=form.querySelector('.screen.active h2');heading.tabIndex=-1;heading.focus({preventScroll:true});
          byId('esper-status').textContent='Step '+(to+1)+' of 8. '+heading.textContent;
          resolve();
        };
        finishTransition=finish;
        if(!ms){finish();return;}
        const start=performance.now();
        function tick(now){
          const p=Math.min(1,(now-start)/ms),t=Math.max(0,Math.min(1,(p-.12)/.73)),ease=t*t*(3-2*t);
          draw(mix(from,to,ease));
          if(p>=.55)swap();
          form.querySelector('.screen.active').style.opacity=String(p<.55?Math.max(0,1-p/.2):Math.max(0,(p-.82)/.18));
          if(p>=1)finish();else frame=requestAnimationFrame(tick);
        }
        frame=requestAnimationFrame(tick);
      });
    }
    const blocked=()=>busy||locked||loginOpen()||!!document.querySelector('.helper-pop');
    portal.addEventListener('click',()=>{if(!blocked())navigate(current+1);});
    let lastWheel=0, consumed=false, amount=0;
    window.addEventListener('wheel',event=>{
      if(event.ctrlKey||Math.abs(event.deltaX)>Math.abs(event.deltaY)||event.target.closest('#wizard,.steps,.installer-preferences,.helper-pop,.vnc-backdrop'))return;
      const now=performance.now();
      if(now-lastWheel>200){consumed=false;amount=0;}lastWheel=now;
      event.preventDefault();if(blocked()||consumed)return;
      amount+=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?height:1);
      if(Math.abs(amount)>50){consumed=true;navigate(current+Math.sign(amount));}
    },{passive:false});
    document.addEventListener('keydown',event=>{
      if(event.defaultPrevented||event.ctrlKey||event.metaKey||event.altKey||blocked()||event.target.closest('input,select,textarea,button,[contenteditable],.helper-pop,.vnc-backdrop'))return;
      if(['ArrowRight','ArrowLeft','PageDown','PageUp'].includes(event.key)){
        event.preventDefault();navigate(current+(['ArrowRight','PageDown'].includes(event.key)?1:-1));
      }
    });
    let touch=null;
    view.addEventListener('touchstart',event=>{touch=event.touches.length===1?{x:event.touches[0].clientX,y:event.touches[0].clientY}:null;},{passive:true});
    view.addEventListener('touchend',event=>{
      if(touch&&!blocked()){
        const dx=event.changedTouches[0].clientX-touch.x,dy=event.changedTouches[0].clientY-touch.y;
        if(Math.abs(dy)>60&&Math.abs(dy)>Math.abs(dx))navigate(current+(dy<0?1:-1));
      }touch=null;
    },{passive:true});
    volumeChanged();soundLabel();motionLabel();setStep(0);
    if(paint){
      new ResizeObserver(resize).observe(view);
      Promise.all(images.map(image=>image.decode())).then(()=>{ready=true;resize();setStep(current);}).catch(()=>{
        portal.hidden=true;byId('esper-media-note').textContent='Photographs unavailable. You can continue with the form.';
      });
    }
    return {setStep,transition,setLocked(value){locked=value;portal.disabled=value;},
      get state(){return {ready,busy,depth,step:current,reduced,locked,musicTime:music.currentTime,musicPaused:music.paused,wanted};}};
  };
})();
