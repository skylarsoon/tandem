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
  await activity.getByRole('button').click();await expect(activity).toContainText('isolated-test-run');
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
