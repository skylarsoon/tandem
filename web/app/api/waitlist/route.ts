import { createHmac } from 'node:crypto';
import { NextResponse } from 'next/server';
import { AdminError, callAdmin, sameOrigin } from '../../../lib/server/admin';
export const runtime = 'nodejs';
export const maxDuration = 30;
const headers = { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' };
export async function POST(request: Request) {
  try {
    sameOrigin(request);
    if (Number(request.headers.get('content-length') || 0) > 2048) throw new AdminError(413, 'Request too large.');
    const raw = await request.text();
    if (raw.length > 2048) throw new AdminError(413, 'Request too large.');
    let body;
    try { body = JSON.parse(raw); } catch { throw new AdminError(400, 'Please check your details.'); }
    if (!body || typeof body !== 'object' || Array.isArray(body)) throw new AdminError(400, 'Please check your details.');
    const email = typeof body.email === 'string' ? body.email.trim().toLowerCase() : '';
    const name = typeof body.name === 'string' ? body.name.trim() : '';
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length > 254 || name.length > 80 || /[\x00-\x1f\x7f]/.test(name) || body.consent !== true)
      throw new AdminError(400, 'Enter a valid email and agree to be contacted about your invitation.');
    if (body.website) return NextResponse.json({ accepted: true }, { headers });
    const key = process.env.TANDEM_ADMIN_GATEWAY_TOKEN;
    if (!key) throw new AdminError(503, 'The waitlist is unavailable. Please try again later.');
    const ip = (request.headers.get('x-vercel-forwarded-for') || request.headers.get('x-forwarded-for') || 'unknown').split(',')[0].trim().slice(0,256);
    const clientHash = createHmac('sha256', key).update(`waitlist:${ip}`).digest('hex');
    const result = await callAdmin('join_invitation_waitlist', { email, displayName: name, clientHash });
    if (result.limited) throw new AdminError(429, 'Too many requests. Please try again in an hour.');
    if (result.unavailable) throw new AdminError(503, 'The waitlist is full right now. Please try again later.');
    return NextResponse.json({ accepted: true }, { headers });
  } catch (error) {
    return NextResponse.json({ error: error instanceof AdminError ? error.message : 'Could not save your request. Please try again.' },
      { status: error instanceof AdminError ? error.status : 500, headers });
  }
}
