import {test,expect} from '@playwright/test';
import {build} from 'esbuild';
let script:string;
test.beforeAll(async()=>{
  const output=await build({stdin:{resolveDir:process.cwd(),loader:'tsx',contents:`
    import {StrictMode} from 'react';
    import {createRoot} from 'react-dom/client';
    import {InvitationGate} from './components/invitation-gate';
    const listeners=new Set(); let state={status:'ready',tables:{my_app_access:[{allowed:false}]}};
    const client={session:{subscribe:fn=>{listeners.add(fn);return()=>listeners.delete(fn)},getSnapshot:()=>state},connection:{reducers:{}}};
    window.gateFixture={setStatus(status){state={...state,status};listeners.forEach(fn=>fn());}};
    createRoot(document.getElementById('root')).render(<StrictMode><InvitationGate client={client}><p>Private trip content</p></InvitationGate></StrictMode>);
  `},bundle:true,write:false,outfile:'/private/tmp/tandem-gate-fixture.js',format:'iife',platform:'browser',jsx:'automatic',define:{'process.env.NODE_ENV':'"development"'},plugins:[{name:'isolated-account-restore',setup(b){b.onResolve({filter:/account-app$/},()=>({path:'restore',namespace:'test'}));b.onLoad({filter:/.*/,namespace:'test'},()=>({contents:`export function restoreSignedInAccount(){return new Promise((resolve,reject)=>{window.gateFixture.finish=()=>resolve(false);window.gateFixture.fail=()=>reject(new Error('Please sign in again.'));});}`,loader:'js'}));}}]});
  script=output.outputFiles.find(f=>f.path.endsWith('.js'))!.text;
});
for(const failed of [false,true])test(`restoration leaves loading after connection changes (${failed?'failure':'signed out'})`,async({page})=>{
  await page.route('http://gate.test/',r=>r.fulfill({contentType:'text/html',body:`<div id="root"></div><script>${script.replaceAll('</script','<\\/script')}</script>`}));
  await page.goto('http://gate.test/');
  await expect(page.getByText('Opening your space…')).toBeVisible();
  await page.evaluate(()=>window.gateFixture.setStatus('synchronizing'));
  await page.evaluate(()=>window.gateFixture.setStatus('ready'));
  await page.evaluate(failed=>failed?window.gateFixture.fail():window.gateFixture.finish(),failed);
  await expect(page.getByRole('heading',{name:'Good company. By invitation.'})).toBeVisible();
  await expect(page.getByText('Private trip content')).toHaveCount(0);
  if(failed)await expect(page.getByRole('alert')).toHaveText('Please sign in again.');
});
declare global {interface Window {gateFixture:any}}
