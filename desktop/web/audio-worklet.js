/* Bounded, interleaved PCM ring buffers. No unbounded scheduling on the main thread. */
class Playback extends AudioWorkletProcessor {
  constructor() {
    super();this.streams=new Map();
    this.port.onmessage=({data:m})=>{
      if(m.op==='stop'){this.streams.delete(m.id);return;}
      if(m.op==='reset'){this.streams.clear();return;}
      if(m.op!=='pcm')return;
      if(!this.streams.has(m.id)){
        if(this.streams.size>=8)return;
        this.streams.set(m.id,{samples:new Float32Array(48000),read:0,size:0});
      }
      const s=this.streams.get(m.id),input=new Int16Array(m.data);
      if(input.length>s.samples.length)return;
      if(s.size+input.length>24000){s.read=0;s.size=0;}
      for(let i=0;i<input.length;i++)s.samples[(s.read+s.size+i)%s.samples.length]=input[i]/32768;
      s.size+=input.length;
    };
  }
  process(inputs,outputs){
    const out=outputs[0];
    for(const s of this.streams.values()){
      for(let i=0;i<out[0].length&&s.size>=2;i++){
        out[0][i]+=s.samples[s.read];s.read=(s.read+1)%s.samples.length;
        out[1][i]+=s.samples[s.read];s.read=(s.read+1)%s.samples.length;s.size-=2;
      }
    }
    for(const channel of out)for(let i=0;i<channel.length;i++)channel[i]=Math.max(-1,Math.min(1,channel[i]));
    return true;
  }
}
class Capture extends AudioWorkletProcessor {
  constructor(){super();this.samples=new Int16Array(960);this.offset=0;}
  process(inputs){
    const input=inputs[0]?.[0];if(!input)return true;
    for(const value of input){
      this.samples[this.offset++]=Math.round(Math.max(-1,Math.min(1,value))*32767);
      if(this.offset===this.samples.length){this.port.postMessage(this.samples.buffer,[this.samples.buffer]);this.samples=new Int16Array(960);this.offset=0;}
    }
    return true;
  }
}
registerProcessor('diplay-playback',Playback);registerProcessor('diplay-capture',Capture);
