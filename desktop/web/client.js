'use strict';
const $=id=>document.getElementById(id),canvas=$('screen'),context=canvas.getContext('2d',{alpha:false});
let ws,decoder,configuration,waitingKey=true,videoCount=0,audioContext,playback,micStream,micNode,micSource,micMute,micId=null;
let codecEpoch=0;
function diagnostic(text){$('diagnostic').textContent=text;}
function send(value){if(ws?.readyState===WebSocket.OPEN)ws.send(JSON.stringify(value));}
function stopMicrophone(){micSource?.disconnect();micNode?.disconnect();micMute?.disconnect();micStream?.getTracks().forEach(t=>t.stop());micStream=micSource=micNode=micMute=null;micId=null;}
async function enableAudio(){
  if(!audioContext){
    audioContext=new AudioContext({sampleRate:48000,latencyHint:'interactive'});
    if(audioContext.sampleRate!==48000)throw Error('This preview requires a 48 kHz browser AudioContext');
    await audioContext.audioWorklet.addModule('/audio-worklet.js');
    playback=new AudioWorkletNode(audioContext,'diplay-playback',{outputChannelCount:[2]});playback.connect(audioContext.destination);
  }
  await audioContext.resume();$('audio').textContent='Sound enabled';
}
async function startMicrophone(id){
  if(!$('mic').checked)return;
  stopMicrophone();micId=id;
  try{
    await enableAudio();
    const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true},video:false});
    if(micId!==id){stream.getTracks().forEach(t=>t.stop());return;}
    micStream=stream;micSource=audioContext.createMediaStreamSource(stream);
    micNode=new AudioWorkletNode(audioContext,'diplay-capture');
    micNode.port.onmessage=({data})=>{
      if(ws?.readyState!==WebSocket.OPEN||ws.bufferedAmount>32768||micId!==id)return;
      const packet=new Uint8Array(4+data.byteLength);new DataView(packet.buffer).setUint32(0,id,false);packet.set(new Uint8Array(data),4);ws.send(packet);
    };
    micSource.connect(micNode);micMute=audioContext.createGain();micMute.gain.value=0;micNode.connect(micMute).connect(audioContext.destination);
  }catch(e){stopMicrophone();diagnostic('Microphone: '+e.message);}
}
async function configure(m){
  const epoch=++codecEpoch;
  configuration=m;waitingKey=true;videoCount=0;
  decoder?.close();decoder=null;
  if(!isSecureContext||!('VideoDecoder'in window)){diagnostic('WebCodecs requires a supported browser on localhost or HTTPS');return;}
  const options={codec:m.codec,optimizeForLatency:true,hardwareAcceleration:'prefer-hardware'};
  const support=await VideoDecoder.isConfigSupported(options);
  if(epoch!==codecEpoch)return;
  if(!support.supported){diagnostic('H.264 WebCodecs decoder unavailable on this browser');return;}
  decoder=new VideoDecoder({output:frame=>{
    if(canvas.width!==frame.displayWidth)canvas.width=frame.displayWidth;
    if(canvas.height!==frame.displayHeight)canvas.height=frame.displayHeight;
    context.drawImage(frame,0,0);frame.close();$('placeholder').hidden=true;$('counter').textContent=(++videoCount)+' frames';
    if(videoCount===1)send({op:'rendered'});
  },error:error=>{diagnostic('Decoder: '+error.message);waitingKey=true;send({op:'keyframe'});}});
  decoder.configure(options);send({op:'keyframe'});diagnostic(m.codec+' / WebCodecs (hardware use depends on browser/driver)');
}
function receive(m,data){
  if(m.event==='authorized'){$('login').hidden=true;return;}
  if(m.event==='status'){$('state').textContent=m.state;return;}
  if(m.event==='video_config'){configure(m).catch(e=>diagnostic(e.message));return;}
  if(m.event==='video'&&data&&decoder?.state==='configured'){
    if(decoder.decodeQueueSize>4){waitingKey=true;decoder.reset();decoder.configure({codec:configuration.codec,optimizeForLatency:true,hardwareAcceleration:'prefer-hardware'});send({op:'keyframe'});}
    if(waitingKey&&!m.key)return;
    if(m.key)waitingKey=false;
    try{decoder.decode(new EncodedVideoChunk({type:m.key?'key':'delta',timestamp:m.time_us,data}));}catch(e){waitingKey=true;send({op:'keyframe'});diagnostic(e.message);}
  }else if(m.event==='pcm'&&data&&playback){
    const copy=data.slice().buffer;playback.port.postMessage({op:'pcm',id:m.id,data:copy},[copy]);
  }else if(m.event==='audio_stop'){playback?.port.postMessage({op:'stop',id:m.id});
  }else if(m.event==='mic_start'){startMicrophone(m.id);
  }else if(m.event==='mic_stop'){if(m.id===micId)stopMicrophone();
  }else if(m.event==='resync'){waitingKey=true;send({op:'keyframe'});
  }else if(m.event==='video_stop'){context.clearRect(0,0,canvas.width,canvas.height);$('placeholder').hidden=false;
  }else if(m.event==='error'||m.event==='fatal'||m.event==='diagnostic'){diagnostic((m.component||m.event)+': '+m.message);}
}
$('login').querySelector('form').onsubmit=event=>{
  event.preventDefault();$('error').textContent='';ws?.close();
  ws=new WebSocket((location.protocol==='https:'?'wss://':'ws://')+location.host+'/ws');ws.binaryType='arraybuffer';
  const socket=ws;
  ws.onopen=()=>socket.send(JSON.stringify({op:'auth',token:$('token').value.trim()}));
  ws.onmessage=event=>{
    if(socket!==ws)return;
    try{if(typeof event.data==='string'){receive(JSON.parse(event.data));return;}
      const bytes=new Uint8Array(event.data),size=new DataView(bytes.buffer).getUint32(0,false);
      if(size>4096||4+size>bytes.length)throw Error('Malformed media header');
      receive(JSON.parse(new TextDecoder().decode(bytes.subarray(4,4+size))),bytes.subarray(4+size));
    }catch(e){diagnostic('Stream: '+e.message);}
  };
  ws.onclose=event=>{if(socket!==ws)return;$('state').textContent='Disconnected';$('login').hidden=false;$('error').textContent=event.reason||'Receiver disconnected';stopMicrophone();playback?.port.postMessage({op:'reset'});decoder?.close();decoder=null;codecEpoch++;};
};
$('audio').onclick=()=>enableAudio().catch(e=>diagnostic(e.message));
$('mic').onchange=()=>{if(!$('mic').checked)stopMicrophone();};
$('fullscreen').onclick=()=>{if(document.fullscreenElement)document.exitFullscreen();else document.documentElement.requestFullscreen().catch(e=>diagnostic(e.message));};
$('reconnect').onclick=()=>send({op:'reconnect'});
for(const button of document.querySelectorAll('[data-key]'))button.onclick=()=>send({op:'key',key:button.dataset.key});
let dragging=false,lastPoint={x:0,y:0},lastMove=0;
function contact(event,down){
  const rect=canvas.getBoundingClientRect(),ratio=canvas.width/canvas.height;
  let width=rect.width,height=width/ratio;if(height>rect.height){height=rect.height;width=height*ratio;}
  const x=(event.clientX-rect.left-(rect.width-width)/2)/width,y=(event.clientY-rect.top-(rect.height-height)/2)/height;
  lastPoint={x:Math.max(0,Math.min(1,x)),y:Math.max(0,Math.min(1,y))};
  send({op:'touch',contacts:[{...lastPoint,down}]});
}
canvas.onpointerdown=event=>{if(dragging)return;dragging=true;canvas.setPointerCapture(event.pointerId);contact(event,true);};
canvas.onpointermove=event=>{if(dragging&&performance.now()-lastMove>25){lastMove=performance.now();contact(event,true);}};
canvas.onpointerup=event=>{if(dragging){contact(event,false);dragging=false;}};
canvas.onpointercancel=()=>{send({op:'touch',contacts:[{...lastPoint,down:false}]});dragging=false;};
window.addEventListener('blur',()=>{if(dragging){send({op:'touch',contacts:[{...lastPoint,down:false}]});dragging=false;}});
