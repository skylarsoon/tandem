/* Deterministic Canvas animation. Shared by the player and the MP4 renderer. */
(function (root) {
  'use strict';
  const W = 1920, H = 1080, DURATION = 48;
  const C = { bg:'#080c15', grid:'#172335', white:'#edf1f7', muted:'#98a9bf', dim:'#46576d', blue:'#64b5f6', teal:'#62ddc5', gold:'#f2c96d', red:'#f28d8d', purple:'#bda4ed' };
  const chapters = ['The shared plan','The wake-up','The state graph','Personal agents','The feedback loop','Human review'];
  const clamp = x => Math.max(0, Math.min(1, x));
  const ease = x => { x=clamp(x); return x*x*(3-2*x); };
  const mix = (a,b,t) => a+(b-a)*t;
  const frac = x => x-Math.floor(x);
  let g;
  function text(s,x,y,size=28,color=C.white,align='center',font='sans',alpha=1) {
    g.save(); g.globalAlpha*=alpha; g.fillStyle=color; g.textAlign=align; g.textBaseline='middle';
    g.font=`${font==='math'?'italic ':''}${size}px ${font==='sans'?'Arial, sans-serif':'Georgia, serif'}`;
    g.fillText(s,x,y); g.restore();
  }
  function line(points,color=C.dim,width=2,alpha=1,dash=[]) {
    g.save(); g.globalAlpha*=alpha; g.strokeStyle=color; g.lineWidth=width; g.setLineDash(dash);
    g.beginPath(); points.forEach((p,i)=>i?g.lineTo(...p):g.moveTo(...p)); g.stroke();g.restore();
  }
  function circle(x,y,r,color,width=2,fill=null,alpha=1) {
    g.save();g.globalAlpha*=alpha;g.beginPath();g.arc(x,y,r,0,Math.PI*2);
    if(fill){g.fillStyle=fill;g.fill();} if(width){g.lineWidth=width;g.strokeStyle=color;g.stroke();}g.restore();
  }
  function glow(x,y,color,r=7) {
    circle(x,y,r*3.4,color,0,color,.07);circle(x,y,r*1.8,color,0,color,.16);circle(x,y,r,color,0,color);
    circle(x-1,y-1,r*.32,C.white,0,C.white,.85);
  }
  function arrow(points,color=C.dim,width=2,alpha=1) {
    line(points,color,width,alpha);const b=points.at(-1),a=points.at(-2),v=Math.atan2(b[1]-a[1],b[0]-a[0]);
    line([[b[0]-13*Math.cos(v-.45),b[1]-13*Math.sin(v-.45)],b,[b[0]-13*Math.cos(v+.45),b[1]-13*Math.sin(v+.45)]],color,width,alpha);
  }
  function atPath(points,t) {
    const ds=points.slice(1).map((b,i)=>Math.hypot(b[0]-points[i][0],b[1]-points[i][1]));
    let d=clamp(t)*ds.reduce((a,b)=>a+b,0);
    for(let i=0;i<ds.length;i++){if(d<=ds[i]||i===ds.length-1)return [mix(points[i][0],points[i+1][0],d/ds[i]),mix(points[i][1],points[i+1][1],d/ds[i])];d-=ds[i];}
  }
  function signal(points,t,color=C.teal) {
    for(let k=6;k>=0;k--){const p=atPath(points,clamp(t-k*.012));circle(...p,Math.max(2,6-k*.6),color,0,color,(1-k/7)*.6);}
    glow(...atPath(points,t),color,6);
  }
  function pathStroke(points,t,color=C.teal,width=3) {
    const n=80,ps=[];for(let i=0;i<=n;i++)ps.push(atPath(points,t*i/n));line(ps,color,width);
  }
  function roundBox(x,y,w,h,color=C.dim,fill=C.bg,r=16,width=2) {
    g.save();g.beginPath();g.roundRect(x,y,w,h,r);if(fill){g.fillStyle=fill;g.fill();}g.lineWidth=width;g.strokeStyle=color;g.stroke();g.restore();
  }
  function title(k,heading,sub) {
    text(String(k+1).padStart(2,'0')+' / '+chapters[k].toUpperCase(),100,161,21,C.teal,'left');
    text(heading,100,234,63,C.white,'left','serif');
    text(sub,100,305,27,C.muted,'left');
  }
  function footer(a,b) {
    line([[100,883],[1820,883]],C.dim,1,.6);
    text(a,960,932,32,C.white,'center','serif');
    if(b)text(b,960,983,23,C.muted);
  }
  function node(x,y,label,sub,color=C.blue,active=0,r=54,symbol='') {
    circle(x,y,r+10,color,1,null,.15+active*.5);
    circle(x,y,r,color,2.5,C.bg); if(active)circle(x,y,r-6,color,0,color,active*.13);
    if(symbol)text(symbol,x,y,36,color,'center','math');
    else {circle(x,y,12,color,2,null);circle(x,y,4,color,0,color);}
    if(active>0.05)glow(x+r*Math.cos(-Math.PI/2+active*2*Math.PI),y+r*Math.sin(-Math.PI/2+active*2*Math.PI),color,4);
    text(label,x,y+r+38,29,C.white);if(sub)text(sub,x,y+r+73,20,C.muted);
  }
  function badge(s,x,y,color=C.teal,w=240){roundBox(x-w/2,y-22,w,44,color,null,22,1);text(s,x,y,20,color);}
  function overview(t) {
    title(0,'Many minds. One shared plan.','Tandem gives every person an agent — and the group a path to agreement.');
    const cx=960,cy=598, positions=[[560,484],[1360,484],[960,777]],colors=[C.blue,C.teal,C.gold];
    positions.forEach((p,i)=>{
      const theta=Math.atan2(p[1]-cy,p[0]-cx),end=[cx+96*Math.cos(theta),cy+96*Math.sin(theta)];
      line([p,end],colors[i],2,.45);
      signal([p,end],frac(t*.21+i*.33),colors[i]);
      node(...p,'Personal agent '+String.fromCharCode(65+i),'',colors[i],.35+.2*Math.sin(t*.9+i),45,String.fromCharCode(65+i));
    });
    circle(cx,cy,100,C.white,2,C.bg);circle(cx,cy,111,C.teal,1,null,.45);
    text('Shared',cx,cy-15,32,C.white,'center','serif');text('plan',cx,cy+24,32,C.white,'center','serif');
    for(let i=0;i<26;i++){const a=i*Math.PI*2/26;circle(cx+245*Math.cos(a),cy+160*Math.sin(a),1.7,C.dim,0,C.dim,.7);}
    text('private preferences',330,714,27,C.blue,'center','math');text('shared possibilities',1575,714,27,C.teal,'center','math');
    footer('Coordinate the search. Preserve each person’s voice.','LangGraph orchestration  ·  personal agents  ·  human approval');
  }
  function wake(t) {
    title(1,'A durable signal wakes the graph.','SpacetimeDB → TypeScript dispatcher → Python worker → LangGraph');
    const xs=[310,740,1170,1600],y=565,cs=[C.blue,C.gold,C.teal,C.purple];
    for(let i=0;i<3;i++){
      const ps=[[xs[i]+68,y],[xs[i+1]-68,y]];arrow(ps,C.dim,2);signal(ps,frac(t*.42-i*.25),cs[i+1]);
      text(['pending job','POST /wake','claim + load'][i],(xs[i]+xs[i+1])/2,y-45,21,C.muted);
    }
    ['SpacetimeDB','Dispatcher','Python worker','StateGraph'].forEach((s,i)=>node(xs[i],y,s,['durable queue','subscription hint','lease + fencing token','checkpointed state'][i],cs[i],.3+.25*Math.sin(t*2-i),61,['Q','→','W','G'][i]));
    const ps=[[1170,720],[1170,792],[310,792],[310,720]];arrow(ps,C.blue,2,.65);signal(ps,frac(t*.18),C.blue);
    text('claim authoritative work • reconcile on restart',745,826,23,C.muted);
    footer('A notification starts the check. The durable job owns the work.','Leases prevent stale attempts from publishing.');
  }
  function graph(t) {
    title(2,'State moves. The graph decides.','A checkpointed coordinator routes each planning step through a typed state.');
    const xs=[240,600,960,1320,1680],y=560;
    const n=['Snapshot','Discover','Solve','Dialogue','Validate'];
    const sub=['load_snapshot','discover','solve','dialogue','validate_publish'];
    for(let i=0;i<4;i++){
      const ps=[[xs[i]+59,y],[xs[i+1]-59,y]];arrow(ps,C.dim,2);
      const progress=clamp((t-0.4-i*1.1)/.8);if(progress>0&&progress<1)signal(ps,progress,C.teal);
      if(progress===1)line(ps,C.teal,2,.55);
    }
    xs.forEach((x,i)=>node(x,y,n[i],sub[i],i===2?C.gold:C.blue,ease((t-i*1.1)/.8),51,['S','R','Σ','A','✓'][i]));
    const loop=[[1320,475],[1320,402],[600,402],[600,475]];arrow(loop,C.gold,2,.75);
    text('next_round → targeted research',960,374,23,C.gold);
    const owner=[[1320,713],[1320,775],[960,775]];arrow(owner,C.purple,2,.8);
    badge('await_owner',835,775,C.purple,230);text('interrupt / resume',835,825,21,C.muted);
    text('Sₜ₊₁ = node(Sₜ)',375,796,37,C.white,'center','math');
    footer('The graph carries state; conditional edges choose what happens next.','Core path shown; blocked or exhausted runs can end without a proposal.');
  }
  function agents(t) {
    title(3,'One private mind per person.','Personal graphs run in sequence. Only safe decisions and references return.');
    const ys=[464,610,756],colors=[C.blue,C.teal,C.gold];
    text('PRIVATE CONTEXT',353,375,20,C.muted);text('PERSONAL GRAPH',902,375,20,C.muted);text('ROOM-SAFE OUTPUT',1560,375,20,C.muted);
    line([[535,410],[535,830]],C.dim,1,.7,[5,9]);
    ys.forEach((y,i)=>{
      const c=colors[i],local=t-i*1.8,active=local>=0&&local<1.8;
      text(String.fromCharCode(65+i),155,y,34,c,'center','math');
      roundBox(220,y-45,267,90,c,null,12,1);
      text(['limits + preferences','needs + permissions','reasons + priorities'][i],353,y-12,23,C.white);
      for(let k=0;k<9;k++)circle(293+k*15,y+18,3,c,0,c,.7);
      const ps=[[490,y],[623,y]];arrow(ps,C.dim);if(active)signal(ps,frac(local*.7),c);
      roundBox(625,y-40,265,80,active?c:C.dim,C.bg,12);text('Decide + enforce',758,y,25,active?c:C.white);
      arrow([[890,y],[949,y]],C.dim,2);
      roundBox(950,y-40,300,80,active?c:C.dim,C.bg,12);text('Phrase + check + send',1100,y,24,active?c:C.white);
      arrow([[1250,y],[1390,y]],C.dim,2);if(active)signal([[1250,y],[1390,y]],frac(local*.7+.5),c);
      const completed=t>i*1.8+.9;
      roundBox(1392,y-40,340,80,completed?c:C.dim,C.bg,12,1.5);
      text(['EVALUATE','OBJECT','COUNTERPROPOSE'][i],1562,y-10,22,completed?c:C.muted);
      text('typed act + evidence refs',1562,y+20,18,C.muted);
      if(active)circle(155,y,36,c,1,null,.5);
    });
    footer('Private reasons inform the decision. Permitted facts shape the message.','Owner context stays ephemeral; private caps and reasons stay out of room voice.');
  }
  function feedback(t) {
    title(4,'A blocker becomes a new path.','Research what is missing. Ask the owner when a decision needs their input.');
    const left=[430,570],right=[1450,570];
    const top=[[1390,545],[1240,423],[670,423],[490,545]];
    const bottom=[[1390,595],[1220,748],[680,748],[490,595]];
    arrow(top,C.gold,2,.65);arrow(bottom,C.purple,2,.65);
    const direct=[[500,570],[1380,570]];arrow(direct,C.dim,2,.6);
    node(...left,'Discover → solve','new evidence, new draft',C.blue,.5,60,'Σ');
    node(...right,'Agent dialogue','typed decisions',C.teal,.5,60,'A');
    badge('targeted research',960,423,C.gold,300);
    badge('await_owner',960,748,C.purple,260);
    text('next_round',960,375,22,C.gold);text('checkpoint → interrupt → resume',960,808,24,C.purple);
    if(t<3.2){signal(top,ease((t-.3)/2.6),C.gold);text('OBJECT → actionable blocker',960,523,26,C.gold);}
    else if(t<5.5){
      signal(bottom,clamp((t-3.2)/2.1)*.5,C.purple);
      text('CLARIFY → owner question',960,523,26,C.purple);
      text('State held while the owner decides',960,620,23,C.muted);
      if(t>4.4){circle(960,748,43,C.purple,1,null,.35);text('Ⅱ',960,685,36,C.purple);}
    } else {signal(bottom,.5+clamp((t-5.5)/1.8)*.5,C.purple);text('Owner answers → resume + reassess',960,523,26,C.teal);}
    footer('A pause is a saved state, not a lost conversation.','Changed constraints or permissions supersede an outdated generation.');
  }
  function review(t) {
    title(5,'Agents propose. People decide.','Feasibility, evidence, and consent are separate steps.');
    // Schematic feasible region, deliberately without invented measured values.
    const ox=170,oy=740;
    arrow([[ox,oy],[620,oy]],C.dim,2);arrow([[ox,oy],[ox,410]],C.dim,2);
    for(let i=1;i<5;i++)line([[ox+i*85,oy],[ox+i*85,410]],C.grid,1);
    for(let i=1;i<4;i++)line([[ox,oy-i*80],[620,oy-i*80]],C.grid,1);
    const poly=[[248,671],[350,468],[518,513],[561,625],[405,702]];
    g.save();g.beginPath();poly.forEach((p,i)=>i?g.lineTo(...p):g.moveTo(...p));g.closePath();g.fillStyle=C.teal;g.globalAlpha*=.10;g.fill();g.restore();
    line([...poly,poly[0]],C.teal,2,.65);
    line([[217,699],[367,399]],C.blue,2,.8);line([[275,448],[605,537]],C.gold,2,.8);
    text('constraints',180,380,21,C.muted,'left');text('candidate space · schematic',405,792,21,C.muted);
    const p=[mix(293,426,ease(t/2.6)),mix(647,572,ease(t/2.6))];glow(...p,C.teal,8);
    text('feasible draft',p[0],p[1]+35,23,C.teal);
    arrow([[646,565],[751,565]],C.dim,2);signal([[646,565],[751,565]],clamp(t/2),C.teal);
    roundBox(770,449,410,235,C.teal,C.bg,18,2);
    text('Independent validation',975,489,29,C.white,'center','serif');
    ['Costs + coverage','Evidence freshness','Baggage + arrival overlap'].forEach((s,i)=>{
      text(t>1+i*.5?'✓':'·',810,544+i*43,25,t>1+i*.5?C.teal:C.dim,'left');text(s,850,544+i*43,23,C.muted,'left');
    });
    arrow([[1180,565],[1325,565]],C.dim,2);if(t>2)signal([[1180,565],[1325,565]],clamp((t-2)/1.2),C.gold);
    roundBox(1340,451,365,228,C.gold,C.bg,16,2);
    text('Immutable proposal',1522,497,30,C.white,'center','serif');
    badge('AWAITING_REVIEW',1522,561,C.gold,290);
    text('Human approval',1522,622,26,C.gold);
    [1440,1522,1604].forEach((x,i)=>{circle(x,754,27,[C.blue,C.teal,C.gold][i],2);text(String.fromCharCode(65+i),x,754,22,[C.blue,C.teal,C.gold][i],'center','math');});
    line([[1522,679],[1522,713]],C.dim,2);text('Each person decides',1522,814,23,C.muted);
    footer('A valid proposal is ready for review. Agreement belongs to people.','Final consent lives outside the coordinator graph.');
  }
  const scenes=[overview,wake,graph,agents,feedback,review];
  function draw(ctx,seconds,width=W,height=H) {
    g=ctx;const time=((seconds%DURATION)+DURATION)%DURATION;
    g.save();g.scale(width/W,height/H);g.fillStyle=C.bg;g.fillRect(0,0,W,H);
    for(let x=80;x<W;x+=80)line([[x,105],[x,864]],C.grid,.7,.28);
    for(let y=145;y<880;y+=80)line([[80,y],[1840,y]],C.grid,.7,.28);
    text('T A N D E M',100,65,25,C.white,'left');text('THE ORCHESTRATION',1818,65,19,C.muted,'right');
    line([[100,108],[1820,108]],C.dim,1,.7);
    const index=Math.floor(time/8),local=time-index*8,transition=.8;
    // Hold the exact opening composition on both sides of the loop boundary.
    if(index===0&&local<.4){overview(0);}
    else if(local>8-transition){
      const u=ease((local-(8-transition))/transition);
      g.save();g.globalAlpha=1-u;scenes[index](local);g.restore();
      g.save();g.globalAlpha=u;scenes[(index+1)%6](0);g.restore();
    }else scenes[index](index===0?local-.4:local);
    g.restore();
  }
  const api={draw,width:W,height:H,duration:DURATION,chapters};
  if(typeof module!=='undefined')module.exports=api;else root.TandemAnimation=api;
})(typeof window!=='undefined'?window:globalThis);
