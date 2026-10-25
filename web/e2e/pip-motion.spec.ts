import { test, expect, type Page } from '@playwright/test';
import { buildSync } from 'esbuild';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const bundle = buildSync({ stdin: { contents: `
import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {AgentAvatar} from './components/agent-avatar';
import {PipThree} from './components/pip-three';
function App() {
  const [gaze, setGaze] = useState({x:0,y:0,present:true});
  const [three, setThree] = useState(false), [angle,setAngle] = useState(null), [wave,setWave] = useState(0);
  window.show3D=setThree; window.turnPip=setAngle; window.wavePip=()=>setWave(n=>n+1);
  const [active, setActive] = useState(true);
  window.movePip = setGaze; window.activatePip = setActive;
  return <div style={{width:280,margin:'50px auto'}}>{three ? <PipThree active={active} gaze={gaze} angle={angle} waveToken={wave}/> : <AgentAvatar variant="lavender" size="hero" animated={active} gaze={gaze}/>}</div>;
}
createRoot(document.getElementById('root')).render(<App/>);`, resolveDir: process.cwd(), loader: 'tsx' }, bundle: true, write: false, format: 'iife', jsx: 'automatic' }).outputFiles[0].text;
async function open(page: Page) {
  await page.route('http://pip.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.startsWith('/agents/')) await route.fulfill({ body: readFileSync(path.resolve('public', `.${url.pathname}`)), contentType: 'image/png' });
    else await route.fulfill({ contentType: 'text/html', body: `<style>body{background:#f3edff}${readFileSync('app/globals.css','utf8')}</style><div id="root"></div><script>${bundle}</script>` });
  });
  await page.goto('http://pip.test');
  await expect(page.locator('.pip-companion')).toBeVisible();
}
const move = (page: Page, x: number, y = 0) => page.evaluate(({x,y}) => (window as any).movePip({x,y,present:true}), {x,y});
const pose = (page: Page) => page.locator('.pip-look').evaluate(el => {
  const matrix = (el as SVGGraphicsElement).getScreenCTM()!;
  const leaf = new DOMPoint(633,150).matrixTransform(matrix);
  const foot = new DOMPoint(633,1147).matrixTransform(matrix);
  return {leaf:leaf.x,foot:foot.x,lean:leaf.x-foot.x};
});

test('artwork fallback shifts without leaning and stops when inactive', async ({page}, info) => {
  await open(page);
  await move(page,-.3); await page.waitForTimeout(900);
  const left = await pose(page);
  await page.screenshot({path:info.outputPath('left.png')});
  await move(page,.3); await page.waitForTimeout(900);
  const right = await pose(page);
  // Both feet and leaves move; moving eyes alone cannot satisfy these checks.
  expect(right.leaf-left.leaf).toBeGreaterThan(10);
  expect(right.foot-left.foot).toBeGreaterThan(10);
  expect(Math.abs(left.lean)).toBeLessThan(.1); expect(Math.abs(right.lean)).toBeLessThan(.1);
  await page.screenshot({path:info.outputPath('right.png')});
  await page.evaluate(() => (window as any).activatePip(false));
  await expect.poll(async () => Math.abs((await pose(page)).lean)).toBeLessThan(.1);
  expect(await page.locator('.pip-companion').evaluate(el => el.getAnimations({subtree:true}).length)).toBe(0);
});

test('reduced motion holds the entire character still', async ({page}) => {
  await page.emulateMedia({reducedMotion:'reduce'}); await open(page);
  const before = await pose(page); await move(page,.8,.5); await page.waitForTimeout(350);
  expect(await pose(page)).toEqual(before);
  expect(await page.locator('.pip-companion').evaluate(el => el.getAnimations({subtree:true}).length)).toBe(0);
});


test('3D turns through both sides and the back without leaning, and waves only on a gesture signal', async ({page}, info) => {
  await open(page); await page.evaluate(() => (window as any).show3D(true));
  const model=page.locator('.pip-three');
  await expect(model.locator('canvas')).toBeVisible();
  for (const angle of [-90,90,180,0]) {
    await page.evaluate(a => (window as any).turnPip(a), angle);
    await expect.poll(async()=>Math.abs(Number(await model.getAttribute('data-yaw'))-angle)).toBeLessThan(1);
    await expect(model).toHaveAttribute('data-roll','0');
    await expect(model).toHaveAttribute('data-wave','false');
    await expect(model).toHaveAttribute('data-arm','0.000');
  }
  await page.evaluate(() => { (window as any).turnPip(null); (window as any).movePip({x:1,y:0,present:true}); });
  await expect.poll(async()=>Number(await model.getAttribute('data-yaw'))).toBeGreaterThan(89);
  await page.evaluate(() => { (window as any).turnPip(0); (window as any).wavePip(); });
  await expect(model).toHaveAttribute('data-wave','true');
  await page.waitForTimeout(700);
  await page.screenshot({path:info.outputPath('gesture-wave.png')});
  await expect(model).toHaveAttribute('data-wave','false',{timeout:4000});
  await expect(model).toHaveAttribute('data-arm','0.000');
  await page.evaluate(() => (window as any).movePip({x:0,y:0,present:false}));
  await expect.poll(async()=>Math.abs(Number(await model.getAttribute('data-yaw')))).toBeLessThan(1);
  await expect(model).toHaveAttribute('data-wave','false');
});
