import { afterEach, describe, expect, it, vi } from 'vitest';
import { passwordMatches, newCredential } from '../lib/server/admin';
import { GET, POST } from '../app/api/admin/[action]/route';
const url = 'http://localhost:3003/api/admin/';
const context = (action: string) => ({ params: Promise.resolve({ action }) });
function request(action: string, body: object, headers: Record<string,string> = {}) {
  return new Request(url + action, { method:'POST', headers:{ origin:'http://localhost:3003', 'Content-Type':'application/json', ...headers }, body:JSON.stringify(body) });
}
function configured() {
  vi.stubEnv('NODE_ENV','test');vi.stubEnv('NEXT_PUBLIC_STDB_URL','https://database.test');vi.stubEnv('NEXT_PUBLIC_STDB_DB','test');vi.stubEnv('TANDEM_ADMIN_GATEWAY_TOKEN','test-gateway');
}
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllEnvs();});
describe('admin password and API boundaries',()=>{
  it('salts scrypt credentials and rejects wrong passwords',async()=>{
    const a=await newCredential('A long private passphrase 2026'), b=await newCredential('A long private passphrase 2026');
    expect(a.passwordSalt).not.toBe(b.passwordSalt);expect(a.passwordHash).not.toBe(b.passwordHash);
    expect(await passwordMatches('A long private passphrase 2026',a)).toBe(true);
    expect(await passwordMatches('Wrong passphrase',a)).toBe(false);
    await expect(newCredential('short')).rejects.toThrow('14–128');
  });
  it('blocks cross-origin admin mutations before calling the database',async()=>{
    const fetch=vi.spyOn(globalThis,'fetch');
    const r=await POST(request('login',{email:'test@example.test',password:'password'},{origin:'https://evil.test'}),context('login'));
    expect(r.status).toBe(403);expect(fetch).not.toHaveBeenCalled();
  });
  it('does not treat a participant identity or email as an admin session',async()=>{
    const fetch=vi.spyOn(globalThis,'fetch');
    const r=await GET(new Request(url+'status',{headers:{authorization:'Bearer participant-token'}}),context('status'));
    expect(r.status).toBe(401);expect(fetch).not.toHaveBeenCalled();
  });
  it('honors persistent rate limits without creating a session',async()=>{
    configured();const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValue(Response.json(JSON.stringify({ok:true,limited:true})));
    const r=await POST(request('login',{email:'test@example.test',password:'not-secret-test'}),context('login'));
    expect(r.status).toBe(429);expect(fetch).toHaveBeenCalledTimes(1);expect(r.headers.get('set-cookie')).toBeNull();
  });
  it('never returns password hashes or gateway credentials on successful login',async()=>{
    configured();const credential=await newCredential('A correct test passphrase');
    const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(Response.json(JSON.stringify({ok:true,limited:false,credential:{...credential,version:1}}))).mockResolvedValueOnce(Response.json(JSON.stringify({ok:true,email:'test@example.test',mustChangePassword:true})));
    const r=await POST(request('login',{email:'test@example.test',password:'A correct test passphrase'}),context('login'));
    expect(r.status).toBe(200);const text=await r.text();expect(text).not.toContain(credential.passwordHash);expect(text).not.toContain('test-gateway');
    expect(r.headers.get('set-cookie')).toContain('HttpOnly');expect(r.headers.get('set-cookie')).toContain('SameSite=strict');expect(r.headers.get('cache-control')).toBe('no-store');
    expect(String(fetch.mock.calls[1][1]?.body)).not.toContain('A correct test passphrase');
  });
  it('gives the same error for unknown email and wrong password',async()=>{
    configured();vi.spyOn(globalThis,'fetch').mockResolvedValue(Response.json(JSON.stringify({ok:true,limited:false,credential:null})));
    const r=await POST(request('login',{email:'unknown@example.test',password:'test-password'}),context('login'));
    expect(r.status).toBe(401);expect((await r.json()).error).toBe('Email or password is incorrect.');
  });
  it('fails closed if the admin gateway is not configured',async()=>{
    configured();vi.stubEnv('TANDEM_ADMIN_GATEWAY_TOKEN','');const fetch=vi.spyOn(globalThis,'fetch');
    const r=await POST(request('login',{email:'test@example.test',password:'test-password'}),context('login'));
    expect(r.status).toBe(503);expect(fetch).not.toHaveBeenCalled();
  });
});
