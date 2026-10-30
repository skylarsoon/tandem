import { NextResponse } from 'next/server';
import { AdminError, callAdmin, digest, newCredential, passwordMatches, sameOrigin, secret, sessionHash } from '../../../../lib/server/admin';
export const runtime = 'nodejs';
export const maxDuration = 30;
const headers = { 'Cache-Control': 'no-store', Vary: 'Cookie', 'X-Content-Type-Options': 'nosniff' };
const cookie = () => process.env.NODE_ENV === 'production' ? '__Host-tandem-user' : 'tandem-user';
type Context = { params: Promise<{ action: string }> };
function failure(error: unknown) {
  return NextResponse.json({ error: error instanceof AdminError ? error.message : 'Something went wrong. Please try again.' }, { status: error instanceof AdminError ? error.status : 500, headers });
}
function signed(data: object, token: string, maxAge = 28800) {
  const response = NextResponse.json(data, { headers });
  response.cookies.set(cookie(), token, { httpOnly: true, secure: process.env.NODE_ENV === 'production', sameSite: 'strict', path: '/', maxAge });
  return response;
}
async function rateCredential(request: Request, email: string) {
  const ip = request.headers.get('x-vercel-forwarded-for') || request.headers.get('x-forwarded-for') || 'unknown';
  const result = await callAdmin('traveller_login_credential', { email, clientHash: digest(ip.slice(0,256)) });
  if (result.limited) throw new AdminError(429, 'Too many attempts. Try again in 15 minutes.');
  return result;
}
export async function GET(request: Request, context: Context) {
  try {
    if ((await context.params).action !== 'status') return NextResponse.json({ error: 'Not found' }, { status: 404, headers });
    if (request.headers.get('sec-fetch-site') === 'cross-site') throw new AdminError(403, 'Open Tandem to view your account.');
    const result = await callAdmin('traveller_account_status', { sessionHash: sessionHash(request, cookie()) });
    return NextResponse.json({ email: result.email, mustChangePassword: result.mustChangePassword }, { headers });
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
    if (action === 'login' || action === 'register') {
      const email = typeof body.email === 'string' ? body.email.trim().toLowerCase() : '';
      if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length > 254 || typeof body.password !== 'string' || !body.password || body.password.length > 128)
        throw new AdminError(400, 'Enter a valid email and password.');
      const result = await rateCredential(request, email), session = secret();
      if (action === 'register') {
        if (result.occupied) throw new AdminError(409, 'An account already uses this email. Sign in instead. Administrators use Admin sign-in.');
        if (body.invitation && (typeof body.invitation !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(body.invitation))) throw new AdminError(400, 'Open your invitation link or enter its code.');
        const credential = await newCredential(body.password), token = secret();
        await callAdmin('prepare_traveller_registration', { email, ...credential, invitationHash: body.invitation ? digest(body.invitation) : '', tokenHash: digest(token), sessionHash: digest(session) });
        // The cookie only becomes valid after atomic registration by the signed browser.
        return signed({ token }, session);
      }
      if (!await passwordMatches(body.password, result.credential)) throw new AdminError(401, 'Email or password is incorrect.');
      const account = await callAdmin('create_traveller_session', { email, version: result.credential.version, sessionHash: digest(session) });
      return signed({ email: account.email, mustChangePassword: account.mustChangePassword }, session);
    }
    const hash = sessionHash(request, cookie());
    if (action === 'password') {
      const current = await callAdmin('traveller_password_credential', { sessionHash: hash });
      await rateCredential(request, current.email);
      if (!current.mustChangePassword && (typeof body.currentPassword !== 'string' || body.currentPassword.length > 128 || !await passwordMatches(body.currentPassword, current))) throw new AdminError(401, 'The current password is incorrect.');
      if (typeof body.password === 'string' && body.password.length <= 128 && await passwordMatches(body.password, current)) throw new AdminError(400, 'Choose a different password from your current one.');
      await callAdmin('change_traveller_password', { sessionHash: hash, ...await newCredential(body.password) });
      return NextResponse.json({ ok: true }, { headers });
    }
    if (action === 'logout') {
      try { await callAdmin('traveller_account_action', { sessionHash: hash, action: 'logout', tokenHash: '' }); }
      catch (error) { if (!(error instanceof AdminError) || error.status !== 401) throw error; }
      return signed({ ok: true }, '', 0);
    }
    if (action === 'open-app') {
      const token = secret();
      await callAdmin('traveller_account_action', { sessionHash: hash, action: 'open_app', tokenHash: digest(token) });
      return NextResponse.json({ token }, { headers });
    }
    return NextResponse.json({ error: 'Not found' }, { status: 404, headers });
  } catch (error) { return failure(error); }
}
