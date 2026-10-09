"""Bundle a verified Linux Node binary for the CLI in Vercel's Python runtime."""
from pathlib import Path
import hashlib
import tarfile
import urllib.request
import subprocess
import shutil

version = 'v24.4.1'
archive = f'node-{version}-linux-x64.tar.xz'
base = f'https://nodejs.org/dist/{version}/'
checksums = urllib.request.urlopen(base + 'SHASUMS256.txt').read().decode()
expected = next(line.split()[0] for line in checksums.splitlines() if line.endswith('  ' + archive))
data = urllib.request.urlopen(base + archive).read()
if hashlib.sha256(data).hexdigest() != expected:
    raise RuntimeError('Node checksum mismatch')
root = Path('runtime'); root.mkdir(exist_ok=True)
path = root / archive; path.write_bytes(data)
with tarfile.open(path) as package:
    package.extractall(root, filter='data')
shutil.rmtree(root / 'node', ignore_errors=True)
(root / f'node-{version}-linux-x64').rename(root / 'node')
path.unlink()
for folder in ('include', 'share', 'lib'):
    shutil.rmtree(root / 'node' / folder)
shutil.rmtree('node_modules', ignore_errors=True)
subprocess.run(['bun','install','--frozen-lockfile','--production','--no-optional','--ignore-scripts','--linker','hoisted'], check=True)

subprocess.run(['strip', str(root / 'node/bin/node')], check=True)
# Framework builders are unused: this deployed CLI only invokes sandbox.
modules = Path('node_modules')
for item in (modules / '@vercel').iterdir():
    if item.name.startswith('vc-native-') or item.name in {'go', 'python', 'next', 'node', 'ruby', 'rust', 'remix-builder', 'redwood', 'static-build', 'fun', 'container', 'hydrogen', 'gatsby-plugin-vercel-builder'}:
        shutil.rmtree(item, ignore_errors=True)
for name in ('typescript', 'esbuild', '@esbuild', '@swc', '@img', 'sharp', '@rolldown', '@oxc-transform', '@oxc-parser'):
    shutil.rmtree(modules / name, ignore_errors=True)
for entry in modules.rglob('*'):
    if entry.is_file() and (entry.name.endswith(('.map', '.d.ts', '.d.mts')) or entry.suffix == '.md'):
        entry.unlink()
print('Runtime bundle bytes:', sum(p.stat().st_size for folder in (root, modules) for p in folder.rglob('*') if p.is_file()))

for folder in (root / 'node/bin', modules / '.bin'):
    for entry in folder.iterdir():
        if entry.is_symlink() and not entry.exists():
            entry.unlink()
