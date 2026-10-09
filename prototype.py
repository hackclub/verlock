"""Launch a Hack Club desktop workspace without the OAuth/AI control plane."""
import argparse
import asyncio
from pathlib import Path

from vercel_sandbox import create_session


async def launch(args):
    script = args.installer.read_text() if args.installer else "#!/bin/bash\necho 'Desktop installer started automatically. Inspect and run this project.'\n"
    guide = args.guide.read_text() if args.guide else "<h1>Airlock review workspace</h1><p>Inspect the repository on the desktop and use the open terminal to run it.</p>"
    async for message in create_session(args.repo, script, guide):
        if isinstance(message, dict):
            print(f"[*] Sandbox: {message['name']}", flush=True)
            print(f"[SUCCESS] Session: {message['url']}", flush=True)
        else:
            print(message, end="", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", help="Public https://github.com/owner/repo URL")
    parser.add_argument("--installer", type=Path, help="Optional installer to make available to the reviewer")
    parser.add_argument("--guide", type=Path, help="Optional HTML review guide")
    asyncio.run(launch(parser.parse_args()))
