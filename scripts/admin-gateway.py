#!/usr/bin/env python3
"""Provision a dedicated server gateway identity; never use the database owner's token in Vercel."""
import argparse,json,os,subprocess,urllib.request
from pathlib import Path
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--url',default='https://maincloud.spacetimedb.com')
p.add_argument('--server',default='maincloud');p.add_argument('--database',default='tandem-dev');p.add_argument('--out',type=Path,required=True)
a=p.parse_args()
fd=os.open(a.out,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
try:
    with urllib.request.urlopen(urllib.request.Request(a.url.rstrip('/')+'/v1/identity',data=b'',method='POST'),timeout=30) as response:
        identity=json.load(response)
    with os.fdopen(fd,'w') as f:json.dump(identity,f)
    subprocess.run(['spacetime','call','--server',a.server,'--no-config','-y',a.database,'configure_admin_gateway',json.dumps({'__identity__':'0x'+identity['identity']}),'true'],check=True,capture_output=True)
    print(f'Gateway registered. Private credential saved to {a.out}. Set its token as TANDEM_ADMIN_GATEWAY_TOKEN on the server only.')
except Exception:
    raise SystemExit('Gateway provisioning failed. Inspect the private file and CLI owner configuration before retrying; credentials were not printed.')
