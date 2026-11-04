import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { POST } from '../app/api/waitlist/route';
const good={email:'Alex@Example.test',name:' Alex ',consent:true};
const request=(body:unknown,headers:Record<string,string>={})=>new Request('http://localhost:3003/api/waitlist',{method:'POST',headers:{origin:'http://localhost:3003','Content-Type':'application/json',...headers},body:JSON.stringify(body)});
beforeEach(()=>{vi.stubEnv('TANDEM_APP_ORIGIN','http://localhost:3003');vi.stubEnv('NEXT_PUBLIC_STDB_URL','https://database.test');vi.stubEnv('NEXT_PUBLIC_STDB_DB','test');vi.stubEnv('TANDEM_ADMIN_GATEWAY_TOKEN','private-gateway');});
afterEach(()=>{vi.restoreAllMocks();vi.unstubAllEnvs();});
it('rejects cross-origin submissions and malformed/oversized/unconsented requests before database access',async()=>{
 const fetch=vi.spyOn(globalThis,'fetch');
 expect((await POST(request(good,{origin:'https://foreign.test'}))).status).toBe(403);
 for(const bad of [null,[],{...good,email:'invalid'},{...good,consent:false},{...good,name:'a'.repeat(81)}]) expect((await POST(request(bad))).status).toBe(400);
 expect((await POST(request({...good,name:'a'.repeat(3000)}))).status).toBe(413);
 expect(fetch).not.toHaveBeenCalled();
});
it('saves normalized details through the server gateway without exposing a token or raw IP',async()=>{
 const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValue(Response.json(JSON.stringify({ok:true,accepted:true})));
 const response=await POST(request(good,{'x-vercel-forwarded-for':'203.0.113.10'}));
 expect(response.status).toBe(200);expect(await response.json()).toEqual({accepted:true});
 expect(response.headers.get('set-cookie')).toBeNull();expect(response.headers.get('cache-control')).toBe('no-store');
 const args=JSON.parse(String(fetch.mock.calls[0][1]?.body));expect(args.email).toBe('alex@example.test');expect(args.displayName).toBe('Alex');expect(args.clientHash).toMatch(/^[a-f0-9]{64}$/);expect(JSON.stringify(args)).not.toContain('203.0.113.10');
 expect(String(fetch.mock.calls[0][0])).toContain('/call/join_invitation_waitlist');
});
it('surfaces durable rate limits and service failures without reporting success',async()=>{
 const fetch=vi.spyOn(globalThis,'fetch').mockResolvedValueOnce(Response.json(JSON.stringify({ok:true,limited:true}))).mockRejectedValueOnce(new Error('private upstream details'));
 expect((await POST(request(good))).status).toBe(429);
 const failed=await POST(request(good));expect(failed.status).toBe(503);expect(await failed.text()).not.toContain('private upstream details');expect(fetch).toHaveBeenCalledTimes(2);
});
it('silently ignores honeypot submissions without creating entries',async()=>{
 const fetch=vi.spyOn(globalThis,'fetch');expect((await POST(request({...good,website:'spam'}))).status).toBe(200);expect(fetch).not.toHaveBeenCalled();
});
