import { createHash, randomBytes, scrypt, timingSafeEqual } from 'node:crypto';

export class AdminError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
export const digest = (value: string) => createHash('sha256').update(value).digest('hex');
export const secret = () => randomBytes(32).toString('base64url');
export const cookieName = () => process.env.NODE_ENV === 'production' ? '__Host-tandem-admin' : 'tandem-admin';
export function origin(request: Request) {
  if (process.env.TANDEM_APP_ORIGIN) return new URL(process.env.TANDEM_APP_ORIGIN).origin;
  if (process.env.NODE_ENV === 'production') return 'https://tandem-calhacks.vercel.app';
  const url = new URL(request.url);
  return `${url.protocol}//${request.headers.get('host') || url.host}`;
}
export function sameOrigin(request: Request) {
  if (request.headers.get('origin') !== origin(request) || request.headers.get('sec-fetch-site') === 'cross-site')
    throw new AdminError(403, 'Open this action from the Tandem website.');
}
export function sessionHash(request: Request, name = cookieName()) {
  const entry = (request.headers.get('cookie') || '').split(';').map(s => s.trim()).find(s => s.startsWith(`${name}=`));
  const token = entry?.slice(name.length + 1);
  if (!token || !/^[A-Za-z0-9_-]{43}$/.test(token)) throw new AdminError(401, 'Please sign in.');
  return digest(token);
}
export async function callAdmin(name: string, args: object): Promise<any> {
  const token = process.env.TANDEM_ADMIN_GATEWAY_TOKEN;
  const base = process.env.NEXT_PUBLIC_STDB_URL, database = process.env.NEXT_PUBLIC_STDB_DB;
  if (!token || !base || !database) throw new AdminError(503, 'Account sign-in is not configured yet.');
  const url = new URL(`/v1/database/${encodeURIComponent(database)}/call/${name}`, base.replace(/^ws/, 'http'));
  let response: Response;
  try { response = await fetch(url, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify(args), cache: 'no-store', signal: AbortSignal.timeout(15000) }); }
  catch { throw new AdminError(503, 'The account service is unavailable. Please try again.'); }
  if (!response.ok) throw new AdminError(503, 'The account service is unavailable. Please try again.');
  let result = await response.json();
  if (typeof result === 'string') result = JSON.parse(result);
  if (!result?.ok) {
    if (result?.code === 'NOT_AUTHORIZED') throw new AdminError(401, 'Your session has ended. Please sign in again.');
    if (result?.code === 'PASSWORD_CHANGE_REQUIRED') throw new AdminError(403, 'Change your temporary password first.');
    if (result?.code === 'ACCOUNT_LINK_REQUIRED') throw new AdminError(409, 'This account has multiple older profiles. Contact the project owner to connect them safely.');
    if (result?.code === 'ACCESS_SUSPENDED') throw new AdminError(403, 'Your app access is suspended. Contact an administrator to restore it.');
    if (result?.code === 'EMAIL_IN_USE') throw new AdminError(409, 'An account already uses this email. Sign in instead.');
    if (result?.code === 'INVITATION_INVALID') throw new AdminError(400, 'This invitation is invalid, expired, used, or revoked. Ask the team for a new link.');
    throw new AdminError(400, 'This change could not be completed. Refresh and try again.');
  }
  return result;
}
// Parameters are fixed for every credential; salts and digests are private DB data.
export function passwordHash(password: string, salt: string): Promise<Buffer> {
  return new Promise((resolve, reject) => scrypt(password, Buffer.from(salt, 'hex'), 64,
    { N: 131072, r: 8, p: 1, maxmem: 192 * 1024 * 1024 }, (error, key) => error ? reject(error) : resolve(key)));
}
export async function passwordMatches(password: string, credential: { passwordSalt: string; passwordHash: string } | null) {
  const salt = credential?.passwordSalt || '00'.repeat(32);
  const expected = Buffer.from(credential?.passwordHash || '00'.repeat(64), 'hex');
  const actual = await passwordHash(password, salt);
  return expected.length === actual.length && timingSafeEqual(expected, actual) && !!credential;
}
export async function newCredential(password: unknown) {
  if (typeof password !== 'string' || password.length < 14 || password.length > 128 || password.trim().length < 14)
    throw new AdminError(400, 'Choose a password or passphrase with 14–128 characters.');
  const passwordSalt = randomBytes(32).toString('hex');
  return { passwordSalt, passwordHash: (await passwordHash(password, passwordSalt)).toString('hex') };
}
