type Point = { x: number; y: number; z?: number };
type Sample = { x: number; tilt: number; time: number };
/** Recognizes lateral sweeps AND wrist-fixed palm rocking. No images retained. */
export class HandWave {
  private samples: Sample[] = [];
  private cooldown = 0;
  private lastSeen = 0;
  status: 'missing' | 'closed' | 'ready' | 'waving' = 'missing';
  update(hands: Point[][], now: number) {
    const hand = hands.find(points => points.length === 21 && [8,12,16,20].filter(tip => {
      const distance = (i: number) => Math.hypot(points[i].x-points[0].x, points[i].y-points[0].y);
      return distance(tip) > distance(tip-2) * 1.1;
    }).length >= 2);
    this.status = now<this.cooldown ? 'waving' : hand ? 'ready' : hands.length ? 'closed' : 'missing';
    if (!hand || now < this.cooldown) { if (now-this.lastSeen > 500) this.samples=[]; return false; }
    const x = (hand[0].x+hand[9].x)/2;
    // Palm angle captures a typical wave even with a stationary wrist.
    const tipX=(hand[8].x+hand[12].x+hand[16].x)/3;
    const tipY=(hand[8].y+hand[12].y+hand[16].y)/3;
    const tilt=Math.atan2(tipX-hand[0].x,hand[0].y-tipY);
    if (this.samples.length && (now-this.lastSeen > 500 || Math.abs(x-this.samples.at(-1)!.x) > .25)) this.samples=[];
    this.lastSeen=now;
    this.samples.push({x,tilt,time:now}); this.samples=this.samples.filter(p=>now-p.time<2200);
    const oscillates=(key:'x'|'tilt', step:number, range:number)=>{
      let anchor=this.samples[0][key], direction=0, reversals=0;
      for(const sample of this.samples) {
        const delta=sample[key]-anchor;
        if(Math.abs(delta)<step)continue;
        const next=Math.sign(delta);
        if(direction && next!==direction)reversals++;
        direction=next;anchor=sample[key];
      }
      const values=this.samples.map(p=>p[key]);
      return reversals>=1 && Math.max(...values)-Math.min(...values)>range;
    };
    if (this.samples.length>=4 && now-this.samples[0].time>=250
      && (oscillates('x',.025,.07) || oscillates('tilt',.16,.38))) {
      this.samples=[]; this.cooldown=now+2600; this.status='waving'; return true;
    }
    return false;
  }
}
