'use client';

import { useEffect, useRef } from 'react';

// A decorative, low-resolution water surface. All rendering stays on the GPU;
// pointer positions are ephemeral and never leave the page.
const fragment = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif
uniform vec2 resolution;
uniform float time;
uniform vec4 ripples[12];
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1,311.7))) * 43758.5453); }
float noise(vec2 p) {
  vec2 i=floor(p), f=fract(p); f=f*f*(3.0-2.0*f);
  return mix(mix(hash(i),hash(i+vec2(1.,0.)),f.x),mix(hash(i+vec2(0.,1.)),hash(i+vec2(1.)),f.x),f.y);
}
void main() {
  vec2 uv=gl_FragCoord.xy/resolution;
  vec2 p=gl_FragCoord.xy/min(resolution.x,resolution.y)*5.0;
  float t=time*.12;
  vec2 displacement=vec2(0.);
  float crest=0.;
  // Each contact continues expanding after the pointer moves on. Overlapping
  // waves bend the light beneath them, rather than drawing a cursor halo.
  for(int i=0;i<12;i++) {
    float age=time-ripples[i].z;
    if(age>=0. && age<3.8) {
      vec2 delta=(uv-ripples[i].xy)*resolution/min(resolution.x,resolution.y);
      float d=length(delta);
      float radius=.025+age*.17;
      float width=.045+age*.018;
      float envelope=exp(-pow((d-radius)/width,2.))*exp(-age*.85)*ripples[i].w;
      float phase=(d-radius)*100.;
      float slope=cos(phase)*envelope;
      displacement+=delta/max(d,.001)*slope*.010;
      crest+=slope;
    }
  }
  vec2 q=p+displacement*5.;
  for(int i=0;i<3;i++) {
    float k=float(i)+1.;
    q += vec2(sin(q.y*1.25+t*k),cos(q.x*1.1-t*k))*.36;
  }
  float n=noise(q*1.3+t*.15);
  float light=pow(1.0-abs(sin(q.x*2.4+sin(q.y*2.1)+n*2.)),14.);
  float light2=pow(1.0-abs(sin(q.y*2.5+cos(q.x*1.7)-n)),18.);
  float depth=noise(p*.65+vec2(t*.12,-t*.1));
  vec3 water=mix(vec3(.76,.87,.83),vec3(.91,.95,.88),depth);
  water += (light*.065+light2*.045);
  water += clamp(crest,-1.,1.)*vec3(.025,.032,.035);
  // Broad, quiet light behind typography, with visible caustics around it.
  water=mix(water,vec3(.97,.97,.92),.22);
  gl_FragColor=vec4(water,1.);
}`;

export function WaterBackground({ active, className }: { active: boolean; className: string }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const running = useRef(active);
  useEffect(() => { running.current = active; }, [active]);
  useEffect(() => {
    const element = canvas.current;
    if (!element) return;
    const gl = element.getContext('webgl', { alpha: false, antialias: false, depth: false, powerPreference: 'low-power' });
    if (!gl) return; // The CSS water gradient is also the no-WebGL fallback.
    const shaders: WebGLShader[] = [];
    const compile = (type: number, source: string) => {
      const shader = gl.createShader(type);
      if (!shader) return null;
      shaders.push(shader); gl.shaderSource(shader, source); gl.compileShader(shader);
      return gl.getShaderParameter(shader, gl.COMPILE_STATUS) ? shader : null;
    };
    const vertex = compile(gl.VERTEX_SHADER, 'attribute vec2 position; void main(){gl_Position=vec4(position,0.,1.);}');
    const pixel = compile(gl.FRAGMENT_SHADER, fragment);
    const program = gl.createProgram();
    const dispose = () => { shaders.forEach(shader => gl.deleteShader(shader)); if (program) gl.deleteProgram(program); };
    if (!vertex || !pixel || !program) { dispose(); return; }
    gl.attachShader(program, vertex); gl.attachShader(program, pixel); gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) { dispose(); return; }
    gl.useProgram(program);
    const buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]),gl.STATIC_DRAW);
    const position=gl.getAttribLocation(program,'position'); gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position,2,gl.FLOAT,false,0,0);
    const size=gl.getUniformLocation(program,'resolution'), clock=gl.getUniformLocation(program,'time'), touch=gl.getUniformLocation(program,'ripples[0]');
    let frame=0, last=0, elapsed=0, dirty=true, pointerAt=-10, nextRipple=0;
    let previous: { x: number; y: number; at: number } | null = null;
    const ripples=new Float32Array(12*4);
    for(let i=0;i<12;i++) ripples[i*4+2]=-10;
    const emit=(x:number,y:number,strength:number)=>{
      ripples.set([x/innerWidth,1-y/innerHeight,elapsed,strength],nextRipple*4);
      nextRipple=(nextRipple+1)%12;
    };
    const resize=()=>{
      // Cap shader work independently of device pixel ratio / Retina resolution.
      const scale=Math.min(1,1200/innerWidth);
      element.width=Math.round(innerWidth*scale); element.height=Math.round(innerHeight*scale);
      gl.viewport(0,0,element.width,element.height); dirty=true;
    };
    const move=(event: PointerEvent)=>{
      if (!running.current) return;
      const now=performance.now();
      const distance=previous ? Math.hypot(event.clientX-previous.x,event.clientY-previous.y) : 0;
      if(event.type==='pointerdown') {
        emit(event.clientX,event.clientY,.75);
      } else {
        if(elapsed-pointerAt<.12 || (previous && distance<12)) return;
        const speed=previous ? distance/Math.max(now-previous.at,16) : .3;
        const strength=Math.min(.65,.3+speed*.12);
        if(previous && distance>45 && now-previous.at<200) {
          emit((previous.x+event.clientX)/2,(previous.y+event.clientY)/2,strength*.7);
        }
        emit(event.clientX,event.clientY,strength);
      }
      pointerAt=elapsed; previous={x:event.clientX,y:event.clientY,at:now};
    };
    const draw=(now:number)=>{
      frame=requestAnimationFrame(draw);
      if(now-last<40) return; // At most 25 fps for a slow decorative surface.
      const delta=Math.min((now-last)/1000,.08); last=now;
      if(!running.current && !dirty) return;
      if(running.current) elapsed+=delta;
      gl.uniform2f(size,element.width,element.height); gl.uniform1f(clock,elapsed); gl.uniform4fv(touch,ripples);
      gl.drawArrays(gl.TRIANGLES,0,6); element.style.opacity='1'; dirty=false;
    };
    const lost=(event:Event)=>{event.preventDefault();cancelAnimationFrame(frame);element.style.opacity='0';};
    resize(); frame=requestAnimationFrame(draw);
    window.addEventListener('resize',resize); window.addEventListener('pointermove',move,{passive:true}); window.addEventListener('pointerdown',move,{passive:true});
    element.addEventListener('webglcontextlost',lost);
    return()=>{cancelAnimationFrame(frame);window.removeEventListener('resize',resize);window.removeEventListener('pointermove',move);window.removeEventListener('pointerdown',move);element.removeEventListener('webglcontextlost',lost);gl.deleteBuffer(buffer);dispose();};
  },[]);
  return <canvas ref={canvas} className={className} aria-hidden="true" />;
}
