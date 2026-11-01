import { test, expect, type Page } from '@playwright/test';
import { buildSync } from 'esbuild';
import { readFileSync } from 'node:fs';
import path from 'node:path';
const built = buildSync({entryPoints:['e2e/fixtures/product.tsx'],bundle:true,write:false,outfile:'/private/tmp/tandem-product-fixture.js',format:'iife',platform:'browser',jsx:'automatic',define:{'process.env.NODE_ENV':'"development"'}}).outputFiles;
const js=built.find(f=>f.path.endsWith('.js'))!.text;
const css=readFileSync('app/globals.css','utf8')+readFileSync('app/product.css','utf8')+built.filter(f=>f.path.endsWith('.css')).map(f=>f.text).join('')+'.test-room-shell{height:calc(100dvh - 78px)}';
async function open(page:Page,screen='home'){
  await page.route('http://tandem.test/**',async route=>{
    const file=path.join(process.cwd(),'public',new URL(route.request().url()).pathname);
    try{await route.fulfill({body:readFileSync(file),contentType:file.endsWith('.svg')?'image/svg+xml':file.endsWith('.webp')?'image/webp':'image/png'});}catch{await route.fulfill({status:404,body:''});}
  });
  await page.route('http://tandem.test/', route=>route.fulfill({contentType:'text/html',body:`<html><head><base href="http://tandem.test/"><meta name="viewport" content="width=device-width,initial-scale=1"><style>${css}</style></head><body><div id="root"></div><script>${js.replaceAll('</script','<\\/script')}</script></body></html>`}));
  await page.goto('http://tandem.test/');
  await page.evaluate(screen=>window.productFixture.screen(screen),screen);
}

test('trip creation and joining are focused, accessible flows with actual reducer arguments',async({page})=>{
  await open(page); await page.getByRole('button',{name:'Start a trip'}).click();
  const dialog=page.getByRole('dialog'); await expect(dialog).toBeVisible();
  await expect(page.getByLabel('Your display name')).toHaveValue('Alex');
  await page.getByLabel('Trip name',{exact:true}).fill('A weekend with friends');
  await page.getByRole('button',{name:'Create trip',exact:true}).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByRole('heading',{name:'Meet your travel companion.'})).toBeVisible();
  expect(await page.evaluate(()=>window.productFixture.calls.find((c:any)=>c.method==='createRoom').args)).toEqual({goal:'A weekend with friends',displayName:'Alex'});
  await page.getByRole('button',{name:'Enter the group room →'}).click();
  await expect(page.getByRole('tab',{name:'Your agent'})).toBeVisible();
  await page.getByRole('button',{name:'← All your trips'}).click();
  await page.getByRole('button',{name:'Join with a code'}).click();
  await page.getByLabel('Invite code',{exact:true}).fill('FRIEND');
  await page.getByRole('button',{name:'Join trip',exact:true}).click();
  expect(await page.evaluate(()=>window.productFixture.calls.find((c:any)=>c.method==='joinRoom').args)).toEqual({inviteCode:'FRIEND',displayName:'Alex'});
});

test('private input, source activity, return itinerary and exact approval remain separate',async({page},info)=>{
  await page.setViewportSize({width:1440,height:900});await open(page,'room');
  await expect(page.getByText('My private reason stays with my agent.',{exact:true})).toBeVisible();
  const activity=page.getByRole('region',{name:'Live agent activity'});
  await expect(activity).toContainText('search in progress');
  await page.getByRole('tab',{name:'Agent discussion'}).click();
  await expect(page.getByText('My private reason stays with my agent.',{exact:true})).toHaveCount(0);
  await expect(page.getByText('Someone’s missing')).toHaveCount(0);
  await expect(page.locator('.room-companions-list>li')).toHaveCount(2);
  await activity.getByRole('button').click();await activity.getByText('Source details',{exact:true}).click();await expect(activity.getByRole('link',{name:'Open provider run'})).toHaveAttribute('href','https://console.apify.com/actors/runs/isolated-test-run');await expect(activity).toContainText('SFO → SAN');await expect(activity).toContainText('2027-06-04');await page.screenshot({path:info.outputPath('actual-search-details.png'),animations:'disabled'});
  await page.getByRole('tab',{name:'Your plan'}).click();
  await expect(activity).toBeVisible(); await activity.getByRole('button').click();
  await expect(page.getByText('2027-06-06',{exact:true}).first()).toBeVisible();
  await expect(page.getByText('Accommodation · Shared with the group')).toBeVisible();
  await expect(page.getByText('traveller 3')).toHaveCount(0);
  await page.getByRole('button',{name:'Accept exact terms'}).click();
  expect(await page.evaluate(()=>{const c=window.productFixture.calls.at(-1);return {method:c.method,version:c.args.version,digest:c.args.termsDigest};})).toEqual({method:'respondToProposal',version:2,digest:'displayed-terms-only'});
  await page.screenshot({path:info.outputPath('plan-desktop.png')});
});

test('limits respect legacy controls, tab keyboard navigation, disconnect privacy and mobile bounds',async({page},info)=>{
  await open(page,'room'); await page.getByRole('tab',{name:'My limits'}).click();
  await expect(page.getByRole('button',{name:'Set aside for this trip'})).toHaveCount(1);
  await page.getByRole('button',{name:'Set aside for this trip'}).click();
  expect(await page.evaluate(()=>window.productFixture.calls.at(-1).args.state)).toBe('set_aside');
  await page.getByRole('tab',{name:'My limits'}).focus();await page.keyboard.press('Home');
  await expect(page.getByRole('tab',{name:'Your agent'})).toBeFocused();
  await page.getByLabel('Message your agent').fill('Keep this draft');
  await page.getByRole('tab',{name:'Your plan'}).click();await page.getByRole('tab',{name:'Your agent'}).click();
  await expect(page.getByLabel('Message your agent')).toHaveValue('Keep this draft');
  for(const size of [{width:1280,height:720},{width:390,height:844}]){
    await page.setViewportSize(size);
    for(const name of ['Your agent','Agent discussion','Your plan','My limits']){
      await page.getByRole('tab',{name:new RegExp('^'+name)}).click();
      expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
      await expect(page.getByRole('region',{name:'Live agent activity'})).toBeVisible();
    }
  }
  await page.screenshot({path:info.outputPath('limits-mobile.png')});
  await page.evaluate(()=>window.productFixture.setConnected(false));
  await expect(page.getByText('A well-reviewed place, please.',{exact:false})).toHaveCount(0);
  await expect(page.getByRole('region',{name:'Live agent activity'})).toContainText('Connection lost');
});

test('profile name input keeps focus while typing and transitions to the next question',async({page},info)=>{
  await page.setViewportSize({width:1440,height:1000});await open(page,'profile');
  const name=page.locator('.profile-answer-field input').first();await name.fill('Alex');await name.pressSequentially('andra');
  await expect(name).toHaveValue('Alexandra');await expect(name).toBeFocused();
  await page.getByRole('button',{name:'Save and continue'}).click();
  await expect(page.getByText('QUESTION 2 OF 9')).toBeVisible();
  expect(await page.evaluate(()=>JSON.parse(window.productFixture.calls.at(-1).args.valueJson))).toBe('Alexandra');
  await page.screenshot({path:info.outputPath('profile-desktop.png')});
});

test('live discussion follows new turns, keeps history readable, and never invents waiting dialogue',async({page})=>{
 await open(page,'room');await page.getByRole('tab',{name:'Agent discussion'}).click();
 await expect(page.getByText('Following live updates',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Pause following'}).click();
 await page.evaluate(()=>window.productFixture.patch({room_messages:[{id:10n,roomId:1n,senderAgentId:12n,recipientAgentId:13n,utterance:'Could we check the later departure?',createdAt:{microsSinceUnixEpoch:BigInt(Date.now())*1000n}}]}));
 await expect(page.getByText('Could we check the later departure?',{exact:true})).toBeVisible();
 await expect(page.getByText('to Mediator',{exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Follow live · 1 new'})).toBeVisible();
 await page.getByRole('button',{name:'Follow live · 1 new'}).click();
 await expect(page.getByText('Following live updates',{exact:true})).toBeVisible();
});


test('trip links preserve the selected view, browser back and account-scoped access', async ({page})=>{
  await open(page);
  await page.getByRole('button').filter({has:page.getByRole('heading',{name:'A weekend by the coast',exact:true})}).click();
  await page.getByRole('tab',{name:'Agent discussion',exact:true}).click();
  await expect(page).toHaveURL(/trip=1&view=discussion/);
  await page.getByRole('tab',{name:/^Your plan/}).click();
  await page.goBack();
  await expect(page.getByRole('tab',{name:'Agent discussion',exact:true})).toHaveAttribute('aria-selected','true');
  await page.evaluate(()=>{history.replaceState(null,'','/?trip=999&view=plan');dispatchEvent(new PopStateEvent('popstate'));});
  await expect(page.getByRole('alert')).toContainText('This trip isn’t available');
  await expect(page.getByRole('tab',{name:/^Your plan/})).toHaveCount(0);
  await expect(page.getByRole('heading',{name:'Your trips 1',exact:true})).toBeVisible();
});

test('mobile conversation has reading space and the profile character never overlaps its heading', async({page},info)=>{
  await page.setViewportSize({width:390,height:844}); await open(page);
  await page.getByRole('button').filter({has:page.getByRole('heading',{name:'A weekend by the coast',exact:true})}).click();
  await page.getByRole('tab',{name:'Agent discussion',exact:true}).click();
  const feed=page.locator('.conversation-feed');
  await expect.poll(async ()=>(await feed.boundingBox())!.height).toBeGreaterThan(190);
  await page.getByRole('button',{name:/View activity/}).click();
  await expect(page.getByRole('region',{name:'Live agent activity'}).locator('strong').filter({hasText:'SFO → SAN'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  await page.screenshot({path:info.outputPath('mobile-discussion.png')});
  await page.evaluate(()=>window.productFixture.screen('profile'));
  const portrait=await page.locator('.profile-character-stage').boundingBox(),heading=await page.locator('.profile-companion-panel h1').boundingBox();
  expect(portrait!.x).toBeGreaterThanOrEqual(heading!.x+heading!.width);
  expect((await page.locator('.profile-companion-panel').boundingBox())!.width).toBe(390);
  await page.screenshot({path:info.outputPath('mobile-profile.png')});
});


test('laptop workspaces keep the transcript, controls and plan inside the viewport',async({page},info)=>{
  for(const size of [{width:1366,height:768},{width:1440,height:900},{width:1280,height:720}]){
    await page.setViewportSize(size); await open(page);
    if(size.height<=850){
      const trip=await page.locator('.trip-home-card').first().boundingBox();
      expect(trip!.y+trip!.height).toBeLessThanOrEqual(size.height);
    }
    await page.getByRole('button').filter({has:page.getByRole('heading',{name:'A weekend by the coast',exact:true})}).click();
    await page.getByRole('tab',{name:'Agent discussion',exact:true}).click();
    const box=await page.locator('.conversation-feed').boundingBox();
    expect(box!.height).toBeGreaterThan(180);
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
    const footer=await page.locator('.discussion-footer').boundingBox();
    expect(footer!.y+footer!.height).toBeLessThanOrEqual(size.height+1);
    await page.screenshot({path:info.outputPath(`laptop-discussion-${size.width}.png`),animations:'disabled'});
    await page.getByRole('tab',{name:/^Your plan/}).click();
    const plan=page.locator('.plan-view');
    expect((await plan.boundingBox())!.height).toBeGreaterThan(250);
    await page.getByRole('button',{name:'Accept exact terms'}).scrollIntoViewIfNeeded();
    await expect(page.getByRole('button',{name:'Accept exact terms'})).toBeVisible();
  }
});

test('travellers can privately talk to Pip without leaving live group discussion',async({page},info)=>{
  await page.setViewportSize({width:1366,height:768}); await open(page,'room');
  await page.getByRole('tab',{name:'Agent discussion',exact:true}).click();
  await page.getByRole('button',{name:'Talk to your Pip',exact:true}).click();
  await expect(page.getByRole('tab',{name:'Agent discussion',exact:true})).toHaveAttribute('aria-selected','true');
  await page.getByLabel('Message your agent').fill('What have the searches found so far?');
  await page.getByRole('button',{name:'Send message ↑'}).click();
  expect(await page.evaluate(()=>window.productFixture.calls.at(-1).method)).toBe('submitPrivateMessage');
  await expect(page.getByRole('log',{name:'Private conversation with your Pip'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
  const composer=await page.locator('.chat-composer').boundingBox();
  expect(composer!.y+composer!.height).toBeLessThanOrEqual(768);
  await page.screenshot({path:info.outputPath('laptop-live-private-conversation.png')});
  await page.getByRole('button',{name:'Close private conversation'}).click();
  await expect(page.getByRole('log',{name:'Private conversation with your Pip'})).toHaveCount(0);
  await expect(page.getByText('Following live updates',{exact:true})).toBeVisible();
});


test('private replies follow the latest message and preserve deliberate scrolling', async({page})=>{
  await open(page,'room');
  await page.evaluate(()=>window.productFixture.patch({my_private_messages:[
    {id:1n,roomId:1n,sender:'AGENT',body:'Earlier confirmed details. '.repeat(100),createdAt:1n},
    {id:2n,roomId:1n,sender:'HUMAN',body:'What is happening now?',createdAt:2n}
  ]}));
  await page.getByRole('tab',{name:'Agent discussion',exact:true}).click();
  await page.getByRole('button',{name:'Talk to your Pip',exact:true}).click();
  const log=page.getByRole('log',{name:'Private conversation with your Pip'});
  await expect.poll(()=>log.evaluate(n=>n.scrollHeight-n.scrollTop-n.clientHeight)).toBeLessThan(3);
  await log.evaluate(n=>{n.scrollTop=0;n.dispatchEvent(new Event('scroll',{bubbles:true}));});
  await page.evaluate(()=>window.productFixture.patch({my_private_messages:[
    {id:1n,roomId:1n,sender:'AGENT',body:'Earlier confirmed details. '.repeat(100),createdAt:1n},
    {id:2n,roomId:1n,sender:'HUMAN',body:'What is happening now?',createdAt:2n},
    {id:3n,roomId:1n,sender:'AGENT',body:'The first search returned options for comparison.',createdAt:3n}
  ]}));
  await expect(log.getByText('The first search returned options for comparison.')).toBeAttached();
  expect(await log.evaluate(n=>n.scrollTop)).toBe(0);
  await page.getByRole('button',{name:'Close private conversation'}).click();
  await page.getByRole('button',{name:'Talk to your Pip',exact:true}).click();
  await expect.poll(()=>log.evaluate(n=>n.scrollHeight-n.scrollTop-n.clientHeight)).toBeLessThan(3);
});
