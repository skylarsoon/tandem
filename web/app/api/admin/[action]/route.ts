import { NextResponse } from 'next/server';
import { AdminError, callAdmin, cookieName, digest, newCredential, origin, passwordMatches, sameOrigin, secret, sessionHash } from '../../../../lib/server/admin';
export const runtime = 'nodejs';
export const maxDuration = 30;
const headers = { 'Cache-Control': 'no-store', Vary: 'Cookie', 'X-Content-Type-Options': 'nosniff' };
type Context = { params: Promise<{ action: string }> };
function failure(error: unknown) {
  return NextResponse.json({ error: error instanceof AdminError ? error.message : 'Something went wrong. Please try again.' }, { status: error instanceof AdminError ? error.status : 500, headers });
}
function withSession(data: object, value: string, maxAge = 28800) {
  const response = NextResponse.json(data, { headers });
  response.cookies.set(cookieName(), value, { httpOnly: true, secure: process.env.NODE_ENV === 'production', sameSite: 'strict', path: '/', maxAge });
  return response;
}
export async function GET(request: Request, context: Context) {
  try {
    if ((await context.params).action !== 'status') return NextResponse.json({ error: 'Not found' }, { status: 404, headers });
    if (request.headers.get('sec-fetch-site') === 'cross-site') throw new AdminError(403, 'Open the admin page to view your session.');
    const result = await callAdmin('admin_overview', { sessionHash: sessionHash(request) });
    return NextResponse.json(result, { headers });
  } catch (error) { return failure(error); }
}
export async function POST(request: Request, context: Context) {
  try {
    sameOrigin(request);
    if (Number(request.headers.get('content-length') || 0) > 4096) throw new AdminError(413, 'Request too large.');
    const raw = await request.text();
    if (raw.length > 4096) throw new AdminError(413, 'Request too large.');
    let body: any;
    try { body = JSON.parse(raw); } catch { throw new AdminError(400, 'Invalid request.'); }
    if (!body || Array.isArray(body) || typeof body !== 'object') throw new AdminError(400, 'Invalid request.');
    const { action } = await context.params;
    if (action === 'login') {
      const email = typeof body.email === 'string' ? body.email.trim().toLowerCase() : '';
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length > 254 || typeof body.password !== 'string' || body.password.length > 128 || !body.password)
        throw new AdminError(401, 'Email or password is incorrect.');
      // Vercel supplies forwarding metadata; account and global limits apply even if an IP changes.
      const ip = request.headers.get('x-vercel-forwarded-for') || request.headers.get('x-forwarded-for') || 'unknown';
      const result = await callAdmin('admin_login_credential', { email, clientHash: digest(ip.slice(0,256)) });
      if (result.limited) throw new AdminError(429, 'Too many sign-in attempts. Try again in 15 minutes.');
      if (!await passwordMatches(body.password, result.credential)) throw new AdminError(401, 'Email or password is incorrect.');
      const token = secret();
      const account = await callAdmin('create_admin_session', { email, version: result.credential.version, sessionHash: digest(token) });
      return withSession({ email: account.email, mustChangePassword: account.mustChangePassword }, token);
    }
    const hash = sessionHash(request);
    if (action === 'password') {
      const current = await callAdmin('admin_password_credential', { sessionHash: hash });
      if (!current.mustChangePassword && (typeof body.currentPassword !== 'string' || body.currentPassword.length > 128 || !await passwordMatches(body.currentPassword, current)))
        throw new AdminError(401, 'The current password is incorrect.');
      if (typeof body.password === 'string' && body.password.length <= 128 && await passwordMatches(body.password, current))
        throw new AdminError(400, 'Choose a different password from your current one.');
      const credential = await newCredential(body.password);
      await callAdmin('change_admin_password', { sessionHash: hash, ...credential });
      return NextResponse.json({ ok: true }, { headers });
    }
    if (action === 'logout') {
      try { await callAdmin('admin_action', { sessionHash: hash, action: 'logout', payloadJson: '{}' }); }
      catch (error) { if (!(error instanceof AdminError) || error.status !== 401) throw error; }
      return withSession({ ok: true }, '', 0);
    }
    if (action === 'open-app') {
      const token = secret();
      await callAdmin('admin_action', { sessionHash: hash, action: 'open_app', payloadJson: JSON.stringify({ tokenHash: digest(token) }) });
      return NextResponse.json({ token }, { headers });
    }
    if (action === 'invitations') {
      const token = secret();
      await callAdmin('admin_action', { sessionHash: hash, action: 'create_invitation', payloadJson: JSON.stringify({ tokenHash: digest(token), label: body.label, hours: body.hours }) });
      return NextResponse.json({ invitation: `${origin(request)}/invite#invite=${token}` }, { headers });
    }
    if (action === 'revoke-invitation' || action === 'access') {
      await callAdmin('admin_action', { sessionHash: hash, action: action === 'access' ? 'set_user_access' : 'revoke_invitation',
        payloadJson: JSON.stringify(action === 'access' ? { identity: body.identity, revoked: body.revoked } : { tokenHash: body.hash }) });
      return NextResponse.json({ ok: true }, { headers });
    }
    return NextResponse.json({ error: 'Not found' }, { status: 404, headers });
  } catch (error) { return failure(error); }
}
