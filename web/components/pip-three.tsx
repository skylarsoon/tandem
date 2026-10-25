"use client";

import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';
import type { Gaze } from '../lib/face-presence';
import { PipCompanion } from './pip-companion';

/** A volumetric recreation of Pip. Yaw rotates the complete model; no roll/lean. */
export function PipThree(props: { active: boolean; gaze?: Gaze; waveToken: number; angle: number | null }) {
  const host = useRef<HTMLDivElement>(null);
  const input = useRef(props);
  useEffect(() => { input.current = props; }, [props]);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const node = element;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true }); }
    catch { setFailed(true); return; }
    renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    renderer.setClearColor(0x000000, 0);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.1;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    node.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(-1.65, 1.65, 3.2, -.1, .1, 30);
    camera.position.set(0, 2.8, 9); camera.lookAt(0, 1.45, 0);
    // Frustum is centred around camera target.
    camera.top=1.65; camera.bottom=-1.65; camera.updateProjectionMatrix();
    const ambient = new THREE.HemisphereLight('#fff6ef', '#9380b1', 2.3); scene.add(ambient);
    const key = new THREE.DirectionalLight('#fff8f0', 3.8); key.position.set(-3,5,5); key.castShadow=true;
    key.shadow.mapSize.set(1024,1024); key.shadow.camera.left=-2; key.shadow.camera.right=2; key.shadow.camera.top=4; key.shadow.camera.bottom=-2;
    key.shadow.normalBias=.035; key.shadow.radius=4; scene.add(key);
    const rim = new THREE.DirectionalLight('#e4dfff', 2); rim.position.set(3,3,-3); scene.add(rim);
    const root = new THREE.Group(); scene.add(root);
    const clay = new THREE.MeshStandardMaterial({color:'#b6a0d3',roughness:.57});
    const leafMat = new THREE.MeshStandardMaterial({color:'#86ac69',roughness:.52});
    const eyeMat = new THREE.MeshPhysicalMaterial({color:'#332127',roughness:.2,clearcoat:1,clearcoatRoughness:.12});
    const lipMat = new THREE.MeshStandardMaterial({color:'#745047',roughness:.47});
    const blushMat = new THREE.MeshStandardMaterial({color:'#e9a69e',roughness:1});
    const sphere = new THREE.SphereGeometry(1,48,32);
    const ellipsoid = (parent: THREE.Object3D, mat: THREE.Material, pos: number[], scale: number[]) => {
      const mesh = new THREE.Mesh(sphere,mat); mesh.position.set(pos[0],pos[1],pos[2]); mesh.scale.set(scale[0],scale[1],scale[2]); mesh.castShadow=true; mesh.receiveShadow=true; parent.add(mesh); return mesh;
    };
    // Pear-shaped continuous torso, with an actual back and sides.
    const profile = new THREE.SplineCurve([
      [0,.26],[.38,.28],[.64,.43],[.76,.7],[.74,1.05],[.69,1.4],[.68,1.8],[.63,2.1],[.48,2.36],[.25,2.51],[0,2.55],
    ].map(([x,y]) => new THREE.Vector2(x,y)));
    const bodyGeo = new THREE.LatheGeometry(profile.getPoints(70),80);
    const body = new THREE.Mesh(bodyGeo,clay); body.scale.z=.84; body.castShadow=true; body.receiveShadow=true; root.add(body);
    for (const side of [-1,1]) {
      ellipsoid(root,clay,[side*.29,.21,.08],[.18,.22,.23]);
      const leaf=ellipsoid(root,leafMat,[side*.16,2.75,0],[.14,.28,.1]); leaf.rotation.z=-side*.48;
    }
    const arms = [-1,1].map(side => {
      const joint=new THREE.Group(); joint.position.set(side*.64,1.32,0); root.add(joint);
      const arm=ellipsoid(joint,clay,[side*.13,-.25,0],[.16,.37,.17]); arm.rotation.z=side*.27;
      return joint;
    });
    const eyes = [-1,1].map(side => {
      const eye=ellipsoid(root,eyeMat,[side*.245,1.87,.534],[.068,.082,.052]);
      ellipsoid(eye,new THREE.MeshBasicMaterial({color:'#fff7ee'}),[-.22,.3,.88],[.22,.18,.08]);
      return eye;
    });
    for (const side of [-1,1]) ellipsoid(root,blushMat,[side*.38,1.71,.487],[.105,.058,.016]);
    const smileCurve=new THREE.QuadraticBezierCurve3(new THREE.Vector3(-.12,1.76,.568),new THREE.Vector3(0,1.64,.592),new THREE.Vector3(.12,1.76,.568));
    const smileGeo=new THREE.TubeGeometry(smileCurve,24,.014,8,false); const smile=new THREE.Mesh(smileGeo,lipMat); root.add(smile);
    const floorGeo=new THREE.CircleGeometry(1,64);
    const floorMat=new THREE.MeshBasicMaterial({color:'#645274',transparent:true,opacity:.018,depthWrite:false});
    for(let i=0;i<12;i++) {
      const shadow=new THREE.Mesh(floorGeo,floorMat); shadow.rotation.x=-Math.PI/2;
      shadow.position.y=-.014+i*.0001; const size=.32+i*.045;
      shadow.scale.set(size,size*.65,1); scene.add(shadow);
    }
    const resize = () => { const size=node.clientWidth; if(size>0)renderer.setSize(size,size,false); };
    const observer=new ResizeObserver(resize); observer.observe(node); resize();
    const reduced=matchMedia('(prefers-reduced-motion: reduce)');
    let visible=true, frame=0, previous=0, yaw=0, lastWave=input.current.waveToken, waveAt=-Infinity, nextBlink=performance.now()+2300, blinkAt=-Infinity;
    const visibility=new IntersectionObserver(entries => {visible=entries[0]?.isIntersecting??true; restart();}); visibility.observe(node);
    const contextLost=(e: Event)=>{e.preventDefault(); cancelAnimationFrame(frame); setFailed(true);}; renderer.domElement.addEventListener('webglcontextlost',contextLost);
    function tick(now: number) {
      const p=input.current, dt=Math.min((now-(previous||now))/1000,.05); previous=now;
      const manual=p.angle!==null;
      const target=manual ? p.angle!*Math.PI/180 : p.active && p.gaze?.present ? THREE.MathUtils.clamp(p.gaze.x*1.7,-1,1)*Math.PI/2 : 0;
      yaw = reduced.matches || !p.active ? (manual?target:0) : THREE.MathUtils.damp(yaw,target,7,dt);
      root.rotation.set(0,yaw,0); // Always upright: pitch and roll remain exactly zero.
      const animate=p.active && !reduced.matches;
      if (p.waveToken!==lastWave) { lastWave=p.waveToken; if(animate)waveAt=now; }
      const waveTime=(now-waveAt)/1000;
      const envelope=waveTime<0 || waveTime>2.2 ? 0 : Math.min(1,waveTime/.45,(2.2-waveTime)/.45);
      arms[1].rotation.z=animate && envelope>0 ? envelope*(2.2+Math.sin(waveTime*14)*.16) : 0;
      if(animate && now>nextBlink){blinkAt=now;nextBlink=now+3000+Math.random()*2600;}
      const blink=animate && now-blinkAt<180 ? Math.max(.08,Math.abs((now-blinkAt)/90-1)) : 1;
      eyes.forEach(eye=>{eye.scale.y=.082*blink;});
      node.dataset.yaw=(yaw*180/Math.PI).toFixed(1); node.dataset.roll='0'; node.dataset.arm=arms[1].rotation.z.toFixed(3); node.dataset.wave=envelope>0&&animate?'true':'false';
      renderer.render(scene,camera);
      if(visible&&!document.hidden)frame=requestAnimationFrame(tick);
    }
    function restart(){cancelAnimationFrame(frame); previous=0; if(visible&&!document.hidden)frame=requestAnimationFrame(tick);}
    document.addEventListener('visibilitychange',restart); reduced.addEventListener('change',restart); restart();
    return () => {
      cancelAnimationFrame(frame); observer.disconnect(); visibility.disconnect();
      document.removeEventListener('visibilitychange',restart); reduced.removeEventListener('change',restart);
      renderer.domElement.removeEventListener('webglcontextlost',contextLost);
      const geometries=new Set<THREE.BufferGeometry>(), materials=new Set<THREE.Material>();
      scene.traverse(object=>{if(object instanceof THREE.Mesh){geometries.add(object.geometry);const mats=Array.isArray(object.material)?object.material:[object.material];mats.forEach(m=>materials.add(m));}});
      geometries.forEach(g=>g.dispose());materials.forEach(m=>m.dispose());renderer.dispose();renderer.domElement.remove();
    };
  }, []);
  return <div className="agent-avatar agent-avatar--hero pip-three-shell">
    <div ref={host} className="pip-three" role="img" aria-label="Pip, an upright rotatable 3D companion" hidden={failed}/>
    {failed && <><PipCompanion active={props.active} gaze={props.gaze} waveToken={props.waveToken}/><span className="vision-sr-only" role="status">3D is unavailable in this browser. Showing the original artwork.</span></>}
  </div>;
}
