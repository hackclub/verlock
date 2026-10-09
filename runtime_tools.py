"""Use the bundled Node runtime for the Blob helper in Vercel's Python function."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def node_command():
    bundled = ROOT / 'runtime/node/bin/node'
    return str(bundled) if bundled.exists() else 'node'
