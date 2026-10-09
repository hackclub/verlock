"""Airlock's CLI-backed Sandbox provider. Every call is pinned to Hack Club."""
import asyncio
import logging
import os
from pathlib import Path
import re
import secrets
import tempfile
import uuid
import tarfile
from contextlib import asynccontextmanager
from launch_timing import launch_stage
from runtime_tools import vc_command
from request_identity import current_oidc_token

TEAM = "hackclub"
PROJECT = "verlock"
TIMEOUT = "1h"
logger = logging.getLogger(__name__)


async def vc(*args):
    # Never inherit an OIDC scope or accept scope/project overrides from a request.
    env = dict(os.environ)
    env["VERCEL_TELEMETRY_DISABLED"] = "1"
    if os.getenv("VERCEL") and current_oidc_token():
        env["VERCEL_OIDC_TOKEN"] = current_oidc_token()
    if not os.getenv("VERCEL"):
        env.pop("VERCEL_OIDC_TOKEN", None)
    cli_cwd = None
    if os.getenv("VERCEL"):
        # Only /tmp is writable in the function; locally, keep the saved vc login.
        env["XDG_CONFIG_HOME"] = "/tmp/verlock-vc"
        env["XDG_DATA_HOME"] = "/tmp/verlock-vc-data"
        env["XDG_CACHE_HOME"] = "/tmp/verlock-vc-cache"
        cli_cwd = "/tmp/verlock-vc-working"
        Path(cli_cwd).mkdir(exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        *vc_command(), "sandbox", args[0], "--scope", TEAM, "--project", PROJECT, *args[1:],
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env, cwd=cli_cwd,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout={"create": 120, "copy": 60, "exec": 180, "stop": 30}.get(args[0], 120))
    except BaseException:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()
        raise
    if proc.returncode:
        # CLI errors may contain arguments or credentials; keep them out of the stream.
        detail = (stdout + stderr).decode()
        for value in (current_oidc_token(), env.get("VERCEL_AUTH_TOKEN"), env.get("VERCEL_TOKEN")):
            if value:
                detail = detail.replace(value, "[redacted]")
        detail = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[redacted]", detail)
        logger.error("Vercel Sandbox %s failed (exit %s): %s", args[0], proc.returncode, detail[-2000:])
        diagnostic = detail.lower()
        if args[0] == "create" and ("image_not_ready" in diagnostic or "image is not ready" in diagnostic):
            raise RuntimeError("The desktop image is still being prepared by Vercel. Retry when VCR reports Ready.")
        raise RuntimeError(f"Vercel Sandbox {args[0]} failed; check the operation and CLI access")
    return (stdout + stderr).decode()


async def stop_session(name):
    if not re.fullmatch(r"airlock-[a-f0-9]{32}", name):
        raise ValueError("Invalid Airlock sandbox name")
    await vc("stop", name)


async def list_running_sessions():
    """Read the pinned CLI's table, following cursors including empty pages."""
    sessions = []
    cursor = None
    visited = set()
    while True:
        args = ["list", "--limit", "50"]
        if cursor:
            args += ["--cursor", cursor]
        output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", await vc(*args))
        for line in output.splitlines():
            columns = re.split(r"\s{2,}", line.strip())
            if len(columns) >= 8 and columns[1] == 'running':
                sessions.append(dict(zip(('name', 'status', 'created', 'region', 'memory', 'vcpus', 'image', 'expires'), columns)))
        next_cursor = re.search(r"More results:.* --cursor (\S+)", output)
        if not next_cursor:
            break
        cursor = next_cursor[1]
        if cursor in visited:
            raise RuntimeError('Vercel Sandbox pagination repeated a cursor')
        visited.add(cursor)
    return sessions


async def run_with_files(name, files, script, args, sentinel):
    """Send files as one archive, then run a sandbox script that must print its sentinel."""
    stage = script.removesuffix(".sh").replace("-", "_")
    with tempfile.TemporaryDirectory(prefix="airlock-") as tmp:
        archive_path = Path(tmp, "session-files.tar")
        with tarfile.open(archive_path, "w") as archive:
            for filename, content in files.items():
                path = Path(tmp, filename)
                path.write_text(content)
                path.chmod(0o600)
                archive.add(path, arcname=filename)
        archive_path.chmod(0o600)
        with launch_stage(stage + "_transfer", name):
            await vc("copy", str(archive_path), f"{name}:/vercel/sandbox/session-files.tar")
    with launch_stage(stage, name):
        output = await vc("exec", name, "--sudo", "--", "bash", "-c",
            f'tar -xf /vercel/sandbox/session-files.tar -C /vercel/sandbox; rm /vercel/sandbox/session-files.tar; exec bash /vercel/sandbox/{script} "$@"',
            "airlock", *args)
    for line in output.splitlines():
        if re.fullmatch(r"AIRLOCK_TIMING stage=[a-z_]+ seconds=[0-9]+", line):
            logger.info("launch_timing id=%s %s", name, line)
    if sentinel not in output:
        raise RuntimeError("Desktop startup failed; inspect its startup logs")


def repo_args(repo_url):
    return repo_url, repo_url.rsplit("/", 1)[-1].removesuffix(".git")


@asynccontextmanager
async def reserve_session(repo_url):
    """Create and start the desktop in the background; retain cleanup until delivery."""
    if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", repo_url):
        raise ValueError("A normalized public GitHub repository URL is required")
    name = "airlock-" + uuid.uuid4().hex
    access_token = secrets.token_urlsafe(32)

    async def create():
        with launch_stage("vm_create", name):
            output = await vc(
                "create", "--name", name, "--image", "airlock-desktop:v1",
                "--timeout", TIMEOUT, "--vcpus", "2", "--publish-port", "8080",
                "--non-persistent", "--network-policy", "allow-all", "--tag", "app=airlock",
            )
        match = re.search(r"https://[a-zA-Z0-9-]+\.vercel\.run", output)
        if not match:
            raise RuntimeError("Vercel did not return a desktop workspace URL")
        files = {"desktop-password": secrets.token_urlsafe(24), "desktop-token": access_token}
        for filename in ("bootstrap.sh", "desktop-start.sh", "nginx.conf"):
            files[filename] = Path(__file__).with_name("sandbox").joinpath(filename).read_text()
        await run_with_files(name, files, "bootstrap.sh", repo_args(repo_url), "AIRLOCK_DESKTOP_READY")
        return match[0]

    reservation = {"name": name, "token": access_token, "task": asyncio.create_task(create()), "delivered": False}
    try:
        yield reservation
    finally:
        if not reservation["delivered"]:
            reservation["task"].cancel()
            await asyncio.gather(reservation["task"], return_exceptions=True)
            # Creation may have reached Vercel before cancellation or a CLI error.
            try:
                await asyncio.shield(stop_session(name))
            except Exception:
                logger.exception("Could not clean up sandbox %s; one-hour timeout remains", name)


async def create_session(repo_url, install_script, help_html, reservation=None):
    """Finish a reserved workspace, or provision directly for the prototype."""
    if reservation is None:
        async with reserve_session(repo_url) as reserved:
            yield "[*] Creating a one-hour Vercel Sandbox in Hack Club...\n"
            async for message in create_session(repo_url, install_script, help_html, reserved):
                if isinstance(message, dict):
                    reserved["delivered"] = True
                yield message
        return
    name = reservation["name"]
    if not reservation["task"].done():
        yield "[*] Waiting for the Kasm desktop and repository clone...\n"
    url = await reservation["task"]
    with launch_stage("desktop_setup", name):
        yield "[*] Adding the installer and review guide to the desktop...\n"
        files = {"airlock_install.sh": install_script, "REVIEW_GUIDE.html": help_html,
                 "open-session.sh": Path(__file__).with_name("sandbox").joinpath("open-session.sh").read_text()}
        await run_with_files(name, files, "open-session.sh", repo_args(repo_url), "AIRLOCK_SESSION_READY")
        yield "[*] The review guide, GitHub page, file manager, and both terminals are open. The installer runs automatically.\n"
        yield "[*] This workspace expires after one hour. Download anything you need before closing it.\n"
        yield {"name": name, "url": f"{url}/launch/{reservation['token']}"}
