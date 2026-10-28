/** Mint a scoped token; never return or persist the user's Cartesia API key. */
export async function POST(request: Request) {
  const headers = { 'Cache-Control': 'no-store' };
  const url = new URL(request.url);
  // Next's internal URL can use localhost while the browser uses 127.0.0.1.
  // Compare the browser origin with the actual request Host, retaining CSRF protection.
  const origin = request.headers.get('origin');
  let sameOrigin = false;
  try { const source = new URL(origin || ''); sameOrigin = ['http:', 'https:'].includes(source.protocol) && source.host === (request.headers.get('host') || url.host); } catch {}
  if (!sameOrigin || request.headers.get('sec-fetch-site') === 'cross-site') return Response.json({ error: 'Open voice from this app.' }, { status: 403, headers });
  if (Number(request.headers.get('content-length') || 0) > 4096) return Response.json({ error: 'Request too large.' }, { status: 413, headers });
  const authorization = request.headers.get('authorization');
  if (!authorization?.startsWith('Bearer ') || authorization.length > 8192)
    return Response.json({ error: 'Accept your Tandem invitation before enabling voice.' }, { status: 401, headers });
  try {
    const base = process.env.NEXT_PUBLIC_STDB_URL;
    const database = process.env.NEXT_PUBLIC_STDB_DB;
    if (!base || !database) throw new Error('Access service is not configured');
    const endpoint = new URL(`/v1/database/${encodeURIComponent(database)}/sql`, base.replace(/^ws/, 'http'));
    const access = await fetch(endpoint, { method: 'POST', headers: { Authorization: authorization, 'Content-Type': 'text/plain' },
      body: 'SELECT allowed FROM my_app_access', cache: 'no-store', signal: AbortSignal.timeout(10000) });
    if (access.status === 401 || access.status === 403)
      return Response.json({ error: 'Accept your Tandem invitation before enabling voice.' }, { status: 403, headers });
    if (!access.ok) throw new Error('Access service unavailable');
    const result = await access.json();
    if (!Array.isArray(result) || result[0]?.rows?.[0]?.[0] !== true)
      return Response.json({ error: 'Accept your Tandem invitation before enabling voice.' }, { status: 403, headers });
  } catch {
    return Response.json({ error: 'We could not verify your access. Please try again.' }, { status: 503, headers });
  }
  try {
    const raw = await request.text();
    if (raw.length > 4096) return Response.json({ error: 'Request too large.' }, { status: 413, headers });
    const input = JSON.parse(raw);
    // Server credentials are available only to this local demo. A deployed
    // service must authenticate the caller before spending a shared API key.
    const local = process.env.NODE_ENV !== 'production' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
    const key = typeof input.key === 'string' && input.key.trim() ? input.key.trim() : local ? process.env.CARTESIA_API_KEY : undefined;
    if (!key) return Response.json({ error: 'Enter your Cartesia key to enable voice. It stays in memory for this session.' }, { status: 401, headers });
    const response = await fetch('https://api.cartesia.ai/access-token', {
      method: 'POST', headers: { Authorization: `Bearer ${key}`, 'Cartesia-Version': '2026-08-14', 'Content-Type': 'application/json' },
      body: JSON.stringify({ grants: { tts: true, stt: true }, expires_in: 600 }), signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) return Response.json({ error: response.status === 401 || response.status === 403 ? 'Cartesia could not authorize this key. Check its TTS and STT permissions.' : 'Cartesia is unavailable. Please try again.' }, { status: 502, headers });
    const result = await response.json();
    if (typeof result.token !== 'string') throw new Error('Missing token');
    return Response.json({ token: result.token, expiresAt: Date.now() + 540000 }, { headers });
  } catch { return Response.json({ error: 'Voice could not connect. Check your connection and try again.' }, { status: 502, headers }); }
}
