#!/usr/bin/env node
// Owner-assisted recovery only. Does not create users or grant administrator roles.
import { parseArgs } from 'node:util';
import { randomBytes, scryptSync } from 'node:crypto';
import { openSync, writeFileSync, closeSync, unlinkSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
const { values } = parseArgs({ options: { email: { type:'string' }, out: { type:'string' }, server: { type:'string',default:'maincloud' }, database: { type:'string',default:'tandem-dev' } } });
if (!values.email || !values.out) throw new Error('Use --email ADDRESS --out PRIVATE_FILE. Verify the account owner before resetting.');
const email=values.email.trim().toLowerCase();
if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || email.length>254) throw new Error('Invalid email');
const fd=openSync(values.out,'wx',0o600);
try {
  const password=randomBytes(24).toString('base64url'), salt=randomBytes(32).toString('hex');
  const hash=scryptSync(password,Buffer.from(salt,'hex'),64,{N:131072,r:8,p:1,maxmem:192*1024*1024}).toString('hex');
  execFileSync(process.env.SPACETIME_BIN || 'spacetime',['call','--server',values.server,'--no-config','-y',values.database,'reset_traveller_password',JSON.stringify(email),JSON.stringify(hash),JSON.stringify(salt)],{stdio:'pipe'});
  writeFileSync(fd,`Tandem account recovery\nLogin: https://tandem-calhacks.vercel.app/account\nEmail: ${email}\nTemporary password: ${password}\n\nChange this password at first sign-in. Share privately with the verified account owner.\n`);
  console.log(`Private reset details saved to ${values.out}`);
} catch { unlinkSync(values.out); throw new Error('Reset failed. Check the database owner login and existing traveller email.'); }
finally { closeSync(fd); }
