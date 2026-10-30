import { afterEach, describe, expect, it, vi } from 'vitest';
import { GET, POST } from '../app/api/account/[action]/route';
import { newCredential } from '../lib/server/admin';
const base='http://localhost:3003/api/account/';
const context=(action:string)=>({params:Promise.resolve({action})});
function request(action:string,body:object,headers:Record<string,string>={}) {
  return new Request(base+action,{method:'POST',headers:{origin:'http://localhost:3003','Content-Type':'application/json',...headers},body:JSON.stringify(body)});
}
function setup(){vi.stubEnv('NODE_ENV','test');vi.stubEnv('TANDEM_APP_ORIGIN','http://localhost:3003');vi.stubEnv('NEXT_PUBLIC_STDB_URL','https://db.test');vi.stubEnv('NEXT_PUBLIC_STDB_DB','test');vi.stubEnv('TANDEM_ADMIN_GATEWAY_TOKEN','private-gateway');}
const reply=(data:object)=>Response.json(JSON.stringify({ok:true,...data}));
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllEnvs();});
describe('traveller HTTP authentication boundaries',()=>{
  it('rejects cross-site registration before contacting the database',async()=>{
    setup();const fetch=vi.spyOn(globalThis,'fetch');
    const r=await POST(request('register',{email:'a@example.test',password:'Long passphrase for tests'},{origin:'https://evil.test'}),context('register'));
    expect(r.status).toBe(403);expect(fetch).not.toHaveBeenCalled();
  });
  it('does not accept an admin cookie or participant JWT as a traveller session',async()=>{
    setup();const fetch=vi.spyOn(globalThis,'fetch');
    const r=await GET(new Request(base+'status',{headers:{cookie:'tandem-admin='+'a'.repeat(43),Authorization:'Bearer participant'}}),context('status'));
    expect(r.status).toBe(401);expect(fetch).not.toHaveBeenCalled();
  });
  it('enforces the rate limit before password hashing or registration mutation',async()=>{
    setup();const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValue(reply({limited:true}));
    const r=await POST(request('register',{email:'a@example.test',password:'Long passphrase for tests'}),context('register'));
    expect(r.status).toBe(429);expect(fetch).toHaveBeenCalledTimes(1);expect(r.headers.get('set-cookie')).toBeNull();
  });
  it('does not overwrite an existing account or consume its invitation',async()=>{
    setup();const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValue(reply({limited:false,occupied:true}));
    const r=await POST(request('register',{email:'a@example.test',password:'Long passphrase for tests',invitation:'a'.repeat(43)}),context('register'));
    expect(r.status).toBe(409);expect(fetch).toHaveBeenCalledTimes(1);
  });
  it('returns only a one-use registration handoff and protected session cookie',async()=>{
    setup();const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(reply({limited:false,occupied:false})).mockResolvedValueOnce(reply({}));
    const password='Long passphrase for tests',invitation='a'.repeat(43);
    const r=await POST(request('register',{email:'A@Example.test',password,invitation}),context('register'));
    expect(r.status).toBe(200);const body=await r.json();expect(Object.keys(body)).toEqual(['token']);expect(body.token).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(r.headers.get('set-cookie')).toContain('tandem-user=');expect(r.headers.get('set-cookie')).toContain('HttpOnly');expect(r.headers.get('set-cookie')).toContain('SameSite=strict');
    const forwarded=JSON.parse(String(fetch.mock.calls[1][1]?.body));
    expect(forwarded.email).toBe('a@example.test');expect(forwarded.passwordHash).toMatch(/^[a-f0-9]{128}$/);expect(forwarded.invitationHash).toMatch(/^[a-f0-9]{64}$/);
    expect(JSON.stringify(forwarded)).not.toContain(password);expect(JSON.stringify(forwarded)).not.toContain(invitation);expect(JSON.stringify(body)).not.toContain(forwarded.passwordHash);
  });
  it('rejects short passwords without creating registration intent',async()=>{
    setup();const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValue(reply({limited:false,occupied:false}));
    const r=await POST(request('register',{email:'a@example.test',password:'short'}),context('register'));
    expect(r.status).toBe(400);expect(fetch).toHaveBeenCalledTimes(1);
  });
  it('returns a generic login failure for unknown accounts',async()=>{
    setup();vi.spyOn(globalThis,'fetch').mockResolvedValue(reply({limited:false,credential:null}));
    const r=await POST(request('login',{email:'unknown@example.test',password:'Wrong passphrase for tests'}),context('login'));
    expect(r.status).toBe(401);expect((await r.json()).error).toBe('Email or password is incorrect.');
  });
  it('uses a production Secure host-only cookie and never returns password hashes',async()=>{
    setup();vi.stubEnv('NODE_ENV','production');const credential=await newCredential('Long passphrase for tests');
    vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(reply({limited:false,credential:{...credential,version:1}})).mockResolvedValueOnce(reply({email:'a@example.test',mustChangePassword:false}));
    const r=await POST(request('login',{email:'a@example.test',password:'Long passphrase for tests'}),context('login'));
    expect(r.status).toBe(200);expect(r.headers.get('set-cookie')).toContain('__Host-tandem-user=');expect(r.headers.get('set-cookie')).toContain('Secure');expect(r.headers.get('cache-control')).toBe('no-store');
    expect(await r.text()).not.toContain(credential.passwordHash);
  });
  it('requires the current password for a normal password change',async()=>{
    setup();const credential=await newCredential('Original test passphrase');const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(reply({...credential,email:'a@example.test',mustChangePassword:false})).mockResolvedValueOnce(reply({limited:false}));
    const r=await POST(request('password',{currentPassword:'Incorrect password',password:'New test passphrase here'},{cookie:'tandem-user='+'a'.repeat(43)}),context('password'));
    expect(r.status).toBe(401);expect(fetch).toHaveBeenCalledTimes(2);
  });
  it('cannot issue an app handoff without a traveller session',async()=>{
    setup();const fetch=vi.spyOn(globalThis,'fetch');const r=await POST(request('open-app',{}),context('open-app'));expect(r.status).toBe(401);expect(fetch).not.toHaveBeenCalled();
  });
});
