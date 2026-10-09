"""Deploy only Hack Club/verlock and explicitly advance its public domain."""
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent.parent
TEAM_ID = 'team_gUyibHqOWrQfv3PDfEUpB45J'
PROJECT_ID = 'prj_x3fSVJUlIHILwV5jqM9J2fH3Wu4P'
env = {**os.environ, 'VERCEL_ORG_ID':TEAM_ID, 'VERCEL_PROJECT_ID':PROJECT_ID}
result = subprocess.run(['vc','deploy','--prod','--yes','--scope','hackclub'],cwd=ROOT,env=env,text=True,capture_output=True)
print(result.stderr, end='')
if result.returncode:
    print(result.stdout, end='')
    raise SystemExit(result.returncode)
try:
    url = json.loads(result.stdout)['deployment']['url']
except (ValueError, KeyError):
    urls = re.findall(r'https://verlock-[a-z0-9]+-hackclub\.vercel\.app', result.stdout + result.stderr)
    if not urls:
        raise RuntimeError('Could not identify the completed deployment')
    url = urls[-1]
subprocess.run(['vc','alias','set',url,'verlock.hackclub.dev','--scope','hackclub'],cwd=ROOT,env=env,check=True)
print('Live: https://verlock.hackclub.dev')
