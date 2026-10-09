"""Private Vercel Blob storage, with local JSON files for development."""
import json
import os
import subprocess
from threading import RLock
from pathlib import Path
from runtime_tools import ROOT, node_command
from request_identity import current_oidc_token

def blob_configured():
    return bool(os.getenv('BLOB_STORE_ID') or os.getenv('BLOB_READ_WRITE_TOKEN'))

def blob(operation, pathname, value=None, etag=None):
    env = dict(os.environ)
    # Blob OIDC auth needs the current request's token, passed only to this subprocess.
    if os.getenv('VERCEL') and current_oidc_token():
        env['VERCEL_OIDC_TOKEN'] = current_oidc_token()
    result = subprocess.run([node_command(), str(ROOT / 'scripts/blob.mjs')], input=json.dumps({'operation':operation,'pathname':pathname,'value':value,'etag':etag}), text=True, capture_output=True, timeout=30, env=env)
    if result.returncode:
        raise RuntimeError('Private state storage failed')
    return json.loads(result.stdout)

def load_state(filename, default):
    if blob_configured():
        value = blob('get', 'state/' + filename)
        return default if value is None else value
    if os.getenv('VERCEL'):
        raise RuntimeError('Private state storage is not configured')
    path = ROOT / filename
    return json.loads(path.read_text()) if path.exists() else default

def save_state(filename, value):
    if blob_configured():
        blob('put', 'state/' + filename, value)
    elif os.getenv('VERCEL'):
        raise RuntimeError('Private state storage is not configured')
    else:
        path = ROOT / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2))


_local_state_lock = RLock()


def update_state(filename, default, change):
    """Retry conditional Blob writes so concurrent role changes cannot overwrite."""
    if blob_configured():
        for _ in range(5):
            snapshot = blob('get_version', 'state/' + filename)
            current = snapshot['value'] if snapshot['value'] is not None else default
            updated = change(current)
            if updated == current or blob('compare_put', 'state/' + filename, updated, snapshot['etag']):
                return updated
        raise RuntimeError('Access changed concurrently; please retry')
    with _local_state_lock:
        current = load_state(filename, default)
        updated = change(current)
        if updated != current:
            save_state(filename, updated)
        return updated
