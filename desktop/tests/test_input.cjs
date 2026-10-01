const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
function harness(){
  const sent=[],timers=new Map(),listeners=new Map();let next=1,enabled=true;
  const surface={width:960,height:540,rect:{left:0,top:0,width:1000,height:600},
    getBoundingClientRect(){return this.rect;},setPointerCapture(){},releasePointerCapture(){},
    addEventListener(n,f){listeners.set(n,f);},removeEventListener(n){listeners.delete(n);}};
  const win={addEventListener(n,f){listeners.set(n,f);},removeEventListener(n){listeners.delete(n);}};
  const doc={hidden:false,querySelector(){return null;},...win};
  const box=vm.createContext({module:{exports:{}},document:doc,window:win,
    setTimeout(fn,ms){assert.equal(ms,25);const id=next++;timers.set(id,fn);return id;},clearTimeout(id){timers.delete(id);}});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/input.js'),'utf8'),box);
  const input=new box.module.exports.DiPlayInput(surface,v=>sent.push(JSON.parse(JSON.stringify(v))),()=>enabled);
  function event(name,id=1,x=500,y=300){listeners.get(name)?.({pointerId:id,clientX:x,clientY:y,pointerType:'touch',preventDefault(){}});}
  return{input,sent,timers,listeners,surface,event,enable(v){enabled=v;},flush(){for(const [id,fn]of [...timers]){timers.delete(id);fn();}}};
}
test('tap inside one animation frame cannot disappear',()=>{
 const h=harness();h.event('pointerdown');h.event('pointerup');h.flush();
 assert.deepEqual(h.sent.map(m=>m.contacts[0].down),[true,false]);
});
test('fixed HID slots survive lift, third pointer ignored',()=>{
 const h=harness();h.event('pointerdown',1);h.event('pointerdown',2);h.event('pointerdown',3);h.event('pointerup',1);
 assert.equal(h.sent.length,3);assert.deepEqual(h.sent.at(-1).contacts.map(p=>p.down),[false,true]);
});
test('move storms schedule one update; release sends newest position immediately',()=>{
 const h=harness();h.event('pointerdown');for(let i=0;i<1000;i++)h.event('pointermove',1,i,300);
 assert.equal(h.timers.size,1);assert.equal(h.sent.length,1);h.event('pointerup',1,900,300);
 assert.equal(h.timers.size,0);assert.equal(h.sent.length,2);assert.equal(h.sent.at(-1).contacts[0].x,.9);
});
test('detach cancels pending work and releases both contacts',()=>{
 const h=harness();h.event('pointerdown',1);h.event('pointerdown',2);h.event('pointermove',1);h.input.detach();h.flush();
 assert.equal(h.timers.size,0);assert.equal(h.listeners.size,0);assert.ok(h.sent.at(-1).contacts.every(c=>!c.down));
});
test('blur and disabled sessions cannot leave stale presses',()=>{
 const h=harness();h.event('pointerdown');h.listeners.get('blur')();h.enable(false);h.event('pointerdown',2);
 assert.equal(h.sent.length,2);assert.ok(h.sent.at(-1).contacts.every(c=>!c.down));
});
test('keyboard shortcuts never escape inputs, buttons or modifier combinations',()=>{
 const h=harness(),key=h.listeners.get('keydown');
 for(const props of [{ctrlKey:true},{altKey:true},{metaKey:true},{repeat:true},{target:{closest:()=>true}}])key({key:'ArrowLeft',...props});
 assert.equal(h.sent.length,0);key({key:'ArrowLeft',preventDefault(){}});assert.equal(h.sent[0].key,'left');
});
test('coordinates account for letterboxing and clamp edge drags',()=>{
 const h=harness();h.surface.rect={left:100,top:50,width:400,height:400};h.event('pointerdown',1,300,250);
 assert.deepEqual(h.sent[0].contacts[0],{x:.5,y:.5,down:true});h.event('pointerup',1,900,0);
 assert.deepEqual(h.sent.at(-1).contacts[0],{x:1,y:0,down:false});
});
