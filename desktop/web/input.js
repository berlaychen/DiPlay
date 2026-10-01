/* PlayPort-inspired interaction, independently implemented with immediate edges.
 * Fixed slots match the portable core's array-index HID contract. */
'use strict';
class DiPlayInput {
  constructor(canvas, send, available) {
    this.canvas=canvas;this.send=send;this.available=available;
    this.ids=[null,null];this.points=[0,1].map(()=>({x:0,y:0,down:false}));this.timer=null;
    this.down=e=>this.edge(e,true);this.up=e=>this.edge(e,false);
    this.move=e=>{
      const i=this.ids.indexOf(e.pointerId);if(i<0||!this.available())return;
      e.preventDefault();Object.assign(this.points[i],this.position(e));
      if(this.timer===null)this.timer=setTimeout(()=>{this.timer=null;this.flush();},25);
    };
    this.cancel=()=>this.release();
    this.visibility=()=>{if(document.hidden)this.release();};
    this.key=e=>{
      if(!this.available()||e.defaultPrevented||e.ctrlKey||e.altKey||e.metaKey||e.repeat)return;
      if(e.target?.closest?.('input,select,textarea,button,a,[contenteditable]')||document.querySelector('dialog[open]'))return;
      const keys={ArrowLeft:'left',ArrowRight:'right',ArrowUp:'up',ArrowDown:'down',Enter:'select',Backspace:'back',Home:'home',s:'siri'};
      if(keys[e.key]){e.preventDefault();send({op:'key',key:keys[e.key]});}
    };
    for(const [name,fn] of [['pointerdown',this.down],['pointerup',this.up],['pointercancel',this.up],['pointermove',this.move],['lostpointercapture',this.up]])canvas.addEventListener(name,fn);
    window.addEventListener('blur',this.cancel);window.addEventListener('keydown',this.key);
    document.addEventListener('visibilitychange',this.visibility);
  }
  position(e){
    const r=this.canvas.getBoundingClientRect(),ratio=this.canvas.width/this.canvas.height;
    const w=Math.min(r.width,r.height*ratio),h=Math.min(r.height,r.width/ratio);
    if(!Number.isFinite(w)||!Number.isFinite(h)||w<=0||h<=0)return{x:0,y:0};
    const clamp=x=>Math.max(0,Math.min(1,Number.isFinite(x)?x:0));
    return{x:clamp((e.clientX-r.left-(r.width-w)/2)/w),y:clamp((e.clientY-r.top-(r.height-h)/2)/h)};
  }
  unschedule(){if(this.timer!==null){clearTimeout(this.timer);this.timer=null;}}
  flush(){this.send({op:'touch',contacts:this.points.map(p=>({...p}))});}
  edge(e,down){
    if(!this.available())return;
    if(down&&e.pointerType==='mouse'&&e.button!==0)return;
    let i=this.ids.indexOf(e.pointerId);
    if(i<0){if(!down)return;i=this.ids.indexOf(null);if(i<0)return;this.ids[i]=e.pointerId;}
    e.preventDefault();this.unschedule();
    this.points[i]={...this.position(e),down};this.flush();
    if(down){try{this.canvas.setPointerCapture(e.pointerId);}catch{this.release();}}
    else{this.ids[i]=null;try{this.canvas.releasePointerCapture(e.pointerId);}catch{}}
  }
  release(){
    this.unschedule();const active=this.points.some(p=>p.down);
    this.ids=[null,null];this.points.forEach(p=>p.down=false);if(active)this.flush();
  }
  detach(){
    this.release();
    for(const [name,fn] of [['pointerdown',this.down],['pointerup',this.up],['pointercancel',this.up],['pointermove',this.move],['lostpointercapture',this.up]])this.canvas.removeEventListener(name,fn);
    window.removeEventListener('blur',this.cancel);window.removeEventListener('keydown',this.key);
    document.removeEventListener('visibilitychange',this.visibility);
  }
}
if(typeof module!=='undefined')module.exports={DiPlayInput};
