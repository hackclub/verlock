"""Use the same pinned vc CLI locally and in Vercel's Python function."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def node_command():
    bundled = ROOT / 'runtime/node/bin/node'
    return str(bundled) if bundled.exists() else 'node'

def vc_command():
    if os.getenv('VERCEL'):
        return [node_command(), str(ROOT / 'node_modules/vc-runtime/dist/vc.js')]
    return ['vc']
