/* Branching photo-in-photo camera. Coordinates are rebased near the lens so
 * a cyclic graph can be explored indefinitely without floating-point drift. */
(() => {
  'use strict';
  const mix=(a,b,t)=>a+(b-a)*t;
  class EsperCamera {
    constructor(canvas,world,onFrame) {
      this.canvas=canvas;this.paint=canvas.getContext('2d');this.world=world;
      this.onFrame=onFrame;this.path=[world.root];this.depth=0;this.loaded=false;
      this.width=this.height=1;this.images={};
      for(const [id,scene] of Object.entries(world.scenes)){
        const image=new Image();image.src=scene.image;this.images[id]=image;
      }
      this.ready=Promise.all(Object.values(this.images).map(image=>image.decode())).then(()=>{this.loaded=true;this.resize();});
      this.observer=new ResizeObserver(()=>this.resize());this.observer.observe(canvas.parentElement);
    }
    canonicalPath(target,from=this.world.root) {
      const queue=[[from]],seen=new Set();
      while(queue.length){
        const path=queue.shift(),id=path[path.length-1];
        if(id===target)return path;
        if(seen.has(id))continue;seen.add(id);
        for(const portal of this.world.scenes[id].portals)queue.push([...path,portal.target]);
      }
      throw new Error('Unreachable photo: '+target);
    }
    extend(path,count=1) {
      const out=[...path];
      for(let n=0;n<count;n++)out.push(this.world.scenes[out[out.length-1]].portals[0].target);
      return out;
    }
    static commonPrefix(a,b) {
      let length=0;while(length<Math.min(a.length,b.length)&&a[length]===b[length])length++;
      return length;
    }
    resize() {
      const box=this.canvas.parentElement.getBoundingClientRect();
      this.width=Math.max(1,box.width);this.height=Math.max(1,box.height);
      const dpr=Math.min(devicePixelRatio||1,2);
      this.canvas.width=Math.round(this.width*dpr);this.canvas.height=Math.round(this.height*dpr);
      this.paint.setTransform(dpr,0,0,dpr,0,0);this.draw(this.depth,this.path);
    }
    draw(depth,path=this.path) {
      this.depth=Math.max(0,depth);this.path=path;
      if(!this.loaded)return;
      const base=Math.floor(this.depth),fraction=this.depth-base;
      const route=this.extend(path,Math.max(0,base+7-path.length));
      const start=Math.max(0,base-2),nodes=[];
      const make=(id,box)=>({...box,id,crop:{x:0,y:0,w:1,h:1}});
      const child=(parent,portal)=>{
        const p=portal.rect,c=parent.crop;
        return make(portal.target,{x:parent.x+(p[0]-c.x)/c.w*parent.w,
          y:parent.y+(p[1]-c.y)/c.h*parent.h,w:p[2]/c.w*parent.w,h:p[3]/c.h*parent.h});
      };
      for(let i=start;i<=base+1;i++){
        if(i===start)nodes.push(make(route[i],{x:0,y:0,w:1536,h:1024}));
        else {
          const parent=nodes[nodes.length-1];
          const portal=this.world.scenes[parent.id].portals.find(p=>p.target===route[i]);
          if(!portal)throw new Error('Broken photo route: '+parent.id+' to '+route[i]);
          nodes.push(child(parent,portal));
        }
      }
      const focus=nodes[base-start],next=nodes[base-start+1];
      // Keep all of a child's photograph inside its frame. As the lens enters,
      // restore its natural aspect ratio instead of cropping away its portals.
      const goal=node=>{
        const image=this.images[node.id];
        const scale=(this.width<=720?Math.min:Math.max)(this.width/image.naturalWidth,this.height/image.naturalHeight)*1.02;
        const centerY=this.width>720&&image.naturalWidth===image.naturalHeight?.45:.5;
        return {x:node.x+node.w/2,y:node.y+node.h*centerY,ux:node.w/(image.naturalWidth*scale),uy:node.h/(image.naturalHeight*scale)};
      };
      const from=goal(focus),to=goal(next);
      const ux=Math.exp(mix(Math.log(from.ux),Math.log(to.ux),fraction));
      const uy=Math.exp(mix(Math.log(from.uy),Math.log(to.uy),fraction));
      const progress=(a,b,u)=>Math.abs(a-b)<1e-12?fraction:(a-u)/(a-b);
      const pose={x:mix(from.x,to.x,progress(from.ux,to.ux,ux)),y:mix(from.y,to.y,progress(from.uy,to.uy,uy)),ux,uy};
      const project=node=>({x:(node.x-pose.x)/ux+this.width/2,y:(node.y-pose.y)/uy+this.height/2,w:node.w/ux,h:node.h/uy});
      const ctx=this.paint;ctx.fillStyle='#081316';ctx.fillRect(0,0,this.width,this.height);
      let budget=110,drawn=0;
      const draw=(node,levels,pathIndex)=>{
        if(!budget--||levels<0)return;
        const b=project(node),c=node.crop,image=this.images[node.id];
        const left=Math.max(0,b.x),top=Math.max(0,b.y),right=Math.min(this.width,b.x+b.w),bottom=Math.min(this.height,b.y+b.h);
        if(right<=left||bottom<=top||b.w<.65||b.h<.65)return;
        ctx.drawImage(image,(c.x+(left-b.x)/b.w*c.w)*image.naturalWidth,
          (c.y+(top-b.y)/b.h*c.h)*image.naturalHeight,
          (right-left)/b.w*c.w*image.naturalWidth,(bottom-top)/b.h*c.h*image.naturalHeight,
          left,top,right-left,bottom-top);drawn++;
        const portals=[...this.world.scenes[node.id].portals];
        // Prioritize the camera route when a hub has many small descendants.
        portals.sort((a,b)=>Number(b.target===route[pathIndex+1])-Number(a.target===route[pathIndex+1]));
        for(const portal of portals)draw(child(node,portal),levels-1,portal.target===route[pathIndex+1]?pathIndex+1:-100);
      };
      draw(nodes[0],8,start);
      const portals=this.world.scenes[focus.id].portals.map(portal=>({...portal,...project(child(focus,portal))}));
      this.state={depth:this.depth,scene:focus.id,fraction,pose,portals,width:this.width,height:this.height,drawn,path:[...path]};
      this.onFrame?.(this.state);
    }
    destroy(){this.observer.disconnect();}
  }
  window.EsperCamera=EsperCamera;
})();
