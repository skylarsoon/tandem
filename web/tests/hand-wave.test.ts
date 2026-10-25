import { expect, it } from 'vitest';
import { HandWave } from '../lib/hand-wave';
function hand(x:number, open=true) {
  const points=Array.from({length:21},()=>({x,y:.6}));
  points[0]={x,y:.8}; points[9]={x,y:.6};
  for(const tip of [8,12,16,20]) { points[tip-2]={x,y:.55}; points[tip]={x,y:open?.35:.65}; }
  return points;
}
it('does not wave at a still hand, small jitter, a fist, or one sideways pass',()=>{
  for(const [positions,open] of [ [Array(15).fill(.5),true],[[.5,.51,.49,.51,.49,.5],true],[[.3,.4,.5,.6,.5,.4,.5,.6],false],[[.3,.4,.5,.6,.7],true] ] as [number[],boolean][]) {
    const wave=new HandWave(); expect(positions.some((x,i)=>wave.update([hand(x,open)],i*150))).toBe(false);
  }
});
it('waves once for an open-handed back-and-forth gesture, with a cooldown',()=>{
  const wave=new HandWave();const sweep=[.3,.4,.5,.6,.5,.4,.3,.4,.5,.6];
  expect(sweep.filter((x,i)=>wave.update([hand(x)],i*150))).toHaveLength(1);
  expect(sweep.some((x,i)=>wave.update([hand(x)],1500+i*150))).toBe(false);
  expect(sweep.filter((x,i)=>wave.update([hand(x)],5000+i*150))).toHaveLength(1);
});
it('does not combine separate glimpses of a hand into a wave',()=>{
  const wave=new HandWave(); [.3,.4,.5].forEach((x,i)=>wave.update([hand(x)],i*150));
  wave.update([],1000);expect(wave.update([hand(.3)],1200)).toBe(false);
});

it('recognizes wrist-fixed palm rocking, including small waves',()=>{
  const wave=new HandWave();
  const results=[-.3,-.1,.15,.35,.1,-.2].map((angle,i)=>{
    const points=hand(.5); const wrist=points[0];
    for(let j=1;j<points.length;j++) {
      const x=points[j].x-wrist.x,y=points[j].y-wrist.y;
      points[j]={x:wrist.x+x*Math.cos(angle)-y*Math.sin(angle),y:wrist.y+x*Math.sin(angle)+y*Math.cos(angle)};
    }
    return wave.update([points],i*130);
  });
  expect(results.filter(Boolean)).toHaveLength(1);
  expect(wave.status).toBe('waving');
});
it('explains when the camera sees no hand versus a closed hand',()=>{
  const wave=new HandWave(); wave.update([],0); expect(wave.status).toBe('missing');
  wave.update([hand(.5,false)],100); expect(wave.status).toBe('closed');
  wave.update([hand(.5)],200); expect(wave.status).toBe('ready');
});
