#!/usr/bin/env node
// Owner-only provisioning. The temporary password never appears in argv or logs.
import { parseArgs } from 'node:util';
import { randomBytes, scryptSync } from 'node:crypto';
import { openSync, writeFileSync, closeSync, unlinkSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
const { values } = parseArgs({ options: { email: { type:'string' }, out: { type:'string' }, server: { type:'string',default:'maincloud' }, database: { type:'string',default:'tandem-dev' }, site: { type:'string',default:'https://tandem-calhacks.vercel.app' }, disable: { type:'boolean',default:false } } });
if (!values.email || !values.out) throw new Error('Use --email ADDRESS --out PRIVATE_FILE; never commit that file. Existing accounts receive a password reset.');
const email=values.email.trim().toLowerCase();
if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length>254) throw new Error('Invalid email');
const fd=openSync(values.out,'wx',0o600);
try {
  const password=randomBytes(24).toString('base64url'), salt=randomBytes(32).toString('hex');
  const hash=scryptSync(password,Buffer.from(salt,'hex'),64,{N:131072,r:8,p:1,maxmem:192*1024*1024}).toString('hex');
  execFileSync(process.env.SPACETIME_BIN || 'spacetime',['call','--server',values.server,'--no-config','-y',values.database,'configure_admin_account',JSON.stringify(email),JSON.stringify(!values.disable),JSON.stringify(hash),JSON.stringify(salt)],{stdio:'pipe'});
  writeFileSync(fd,values.disable ? `Admin disabled: ${email}\n` : `Tandem admin\nLogin: ${values.site.replace(/\/$/,'')}/admin\nEmail: ${email}\nTemporary password: ${password}\n\nChange this password at first sign-in. Share this file privately with the account owner.\n`);
  console.log(`Private account details saved to ${values.out}`);
} catch { unlinkSync(values.out); throw new Error('Provisioning failed. Check the database owner login, email, and three-admin limit.'); }
finally { closeSync(fd); }
