#!/usr/bin/env python3
"""Owner-only invitation management. Requires a signed-in SpacetimeDB CLI."""
import argparse, hashlib, json, os, secrets, subprocess
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument("--server",default="maincloud")
p.add_argument("--database",default="tandem-dev")
p.add_argument("--site",default="https://tandem-calhacks.vercel.app")
s=p.add_subparsers(dest="command",required=True)
c=s.add_parser("create");c.add_argument("--label",required=True);c.add_argument("--hours",type=int,default=168);c.add_argument("--out",type=Path)
v=s.add_parser("revoke");v.add_argument("--hash",required=True)
a=p.parse_args()
if a.command=="create":
    if not 1<=a.hours<=168: p.error("--hours must be 1 through 168")
    if len(a.label)>100: p.error("--label must be at most 100 characters")
    token=secrets.token_urlsafe(32); digest=hashlib.sha256(token.encode()).hexdigest()
    reducer="create_app_invitation"; values=[digest,a.label,a.hours*3600]
else:
    reducer="revoke_app_invitation";values=[a.hash]
result=subprocess.run(["spacetime","call","--server",a.server,"--no-config","-y",a.database,reducer,*map(json.dumps,values)],capture_output=True,text=True)
if result.returncode: raise SystemExit("Invitation operation failed. Check CLI owner authentication, database, and module deployment.\n"+result.stderr)
if a.command=="create":
    output=f"Invitation: {a.site.rstrip('/')}/invite#invite={token}\nRevoke hash: {digest}\nExpires in {a.hours} hours. Single use.\n"
    if a.out:
        fd=os.open(a.out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w") as f:f.write(output)
        print(f"Private invitation written to {a.out}")
    else:print(output)
else:print("Invitation revoked; any current access granted by it has been revoked too.")
