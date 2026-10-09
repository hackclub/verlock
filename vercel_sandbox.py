"""Airlock's Sandbox provider on the native Vercel SDK. Every call is pinned to Hack Club."""
import asyncio
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from vercel.oidc.utils import get_vercel_cli_token
from vercel.sandbox import (NetworkPolicy, SandboxClient, SandboxCredentials, SandboxResources,
                            SandboxServiceOptions)
from launch_timing import launch_stage
from request_identity import current_oidc_token

TEAM_ID = "team_gUyibHqOWrQfv3PDfEUpB45J"
PROJECT_ID = "prj_x3fSVJUlIHILwV5jqM9J2fH3Wu4P"
TIMEOUT_SECONDS = 3600
SANDBOX_DIR = "/vercel/sandbox"
logger = logging.getLogger(__name__)


async def credentials():
    if os.getenv("VERCEL"):
        token = current_oidc_token() or os.getenv("VERCEL_AUTH_TOKEN")
    else:
        token = os.getenv("VERCEL_AUTH_TOKEN") or get_vercel_cli_token()
    if not token:
        raise RuntimeError("Vercel Sandbox credentials are unavailable; run `vc login` locally")
    return SandboxCredentials(token=token, team_id=TEAM_ID, project_id=PROJECT_ID)


class DesktopError(RuntimeError):
    """A remote startup script failed; its message is safe to show."""


def new_client():
    return SandboxClient.create(options=SandboxServiceOptions(credentials_factory=credentials))


@asynccontextmanager
async def sandbox_errors(operation, seconds):
    """Bound each SDK call and keep error details, which may echo credentials, out of the stream."""
    try:
        async with asyncio.timeout(seconds):
            yield
    except DesktopError:
        raise
    except Exception as error:
        detail = str(error)
        for value in (current_oidc_token(), os.getenv("VERCEL_AUTH_TOKEN")):
            if value:
                detail = detail.replace(value, "[redacted]")
        detail = re.sub(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", "[redacted]", detail)
        logger.error("Vercel Sandbox %s failed (%s): %s", operation, type(error).__name__, detail[-2000:])
        if operation == "create" and ("image_not_ready" in detail.lower() or "image is not ready" in detail.lower()):
            raise RuntimeError("The desktop image is still being prepared by Vercel. Retry when VCR reports Ready.") from None
        raise RuntimeError(f"Vercel Sandbox {operation} failed; check the operation and Sandbox access") from None


@asynccontextmanager
async def sandbox_client(operation, seconds):
    client = new_client()
    try:
        async with sandbox_errors(operation, seconds):
            yield client
    finally:
        await client.aclose()


async def stop_session(name):
    if not re.fullmatch(r"airlock-[a-f0-9]{32}", name):
        raise ValueError("Invalid Airlock sandbox name")
    async with sandbox_client("stop", 30) as client:
        await (await client.get_sandbox(name=name)).stop()


def relative_time(milliseconds):
    seconds = int(milliseconds / 1000 - datetime.now(timezone.utc).timestamp())
    minutes = max(abs(seconds) // 60, 1)
    amount = f"{minutes // 60} hours" if minutes >= 120 else f"{minutes} minutes"
    return f"in {amount}" if seconds > 0 else f"{amount} ago"


async def list_running_sessions():
    """List running sandboxes, following the SDK's pagination."""
    sessions = []
    async with sandbox_client("list", 60) as client:
        async for sandbox in client.query_sandboxes(page_size=50):
            if sandbox.status != "running":
                continue
            limit = sandbox.execution_time_limit
            sessions.append({
                "name": sandbox.name, "status": sandbox.status, "region": sandbox.region,
                "memory": f"{sandbox.memory:,} MB", "vcpus": str(sandbox.vcpus), "image": sandbox.image,
                "created": relative_time(sandbox.created_at),
                "expires": relative_time(sandbox.created_at + limit.total_seconds() * 1000) if limit else "",
            })
    return sessions


async def run_with_files(sandbox, files, script, args, sentinel):
    """Upload files in one request, then run a sandbox script that must print its sentinel."""
    stage = script.removesuffix(".sh").replace("-", "_")
    with launch_stage(stage + "_transfer", sandbox.name):
        async with sandbox.fs.batch(cwd=SANDBOX_DIR) as batch:
            for filename, content in files.items():
                batch.write_text(filename, content, mode=0o600)
    with launch_stage(stage, sandbox.name):
        result = await sandbox.run_process("bash", [f"{SANDBOX_DIR}/{script}", *args],
                                           sudo=True, capture_output=True)
    output = (result.stdout or "") + (result.stderr or "")
    for line in output.splitlines():
        if re.fullmatch(r"AIRLOCK_TIMING stage=[a-z_]+ seconds=[0-9.]+", line):
            logger.info("launch_timing id=%s %s", sandbox.name, line)
    if result.returncode or sentinel not in output:
        logger.error("Sandbox %s %s exited %s: %s", sandbox.name, script, result.returncode, output[-2000:])
        raise DesktopError("Desktop startup failed; inspect its startup logs")


def repo_args(repo_url):
    return repo_url, repo_url.rsplit("/", 1)[-1].removesuffix(".git")


@asynccontextmanager
async def reserve_session(repo_url):
    """Create and start the desktop in the background; retain cleanup until delivery."""
    if not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", repo_url):
        raise ValueError("A normalized public GitHub repository URL is required")
    name = "airlock-" + uuid.uuid4().hex
    access_token = secrets.token_urlsafe(32)
    client = new_client()

    async def create():
        with launch_stage("vm_create", name):
            async with sandbox_errors("create", 120):
                sandbox = await client.create_sandbox(
                    name=name, image="airlock-desktop:v1", ports=[8080],
                    execution_time_limit=TIMEOUT_SECONDS, resources=SandboxResources(vcpus=2),
                    persistent=False, network_policy=NetworkPolicy.allow_all(), tags={"app": "airlock"},
                )
        url = next((route.url for route in sandbox.routes if route.port == 8080), "")
        if not re.fullmatch(r"https://[a-zA-Z0-9-]+\.vercel\.run", url):
            raise RuntimeError("Vercel did not return a desktop workspace URL")
        files = {"desktop-password": secrets.token_urlsafe(24), "desktop-token": access_token}
        for filename in ("bootstrap.sh", "desktop-start.sh", "nginx.conf"):
            files[filename] = Path(__file__).with_name("sandbox").joinpath(filename).read_text()
        async with sandbox_errors("bootstrap", 180):
            await run_with_files(sandbox, files, "bootstrap.sh", repo_args(repo_url), "AIRLOCK_DESKTOP_READY")
        return sandbox, url

    reservation = {"name": name, "token": access_token, "task": asyncio.create_task(create()), "delivered": False}
    try:
        yield reservation
    finally:
        if not reservation["delivered"]:
            reservation["task"].cancel()
            await asyncio.gather(reservation["task"], return_exceptions=True)
            # Creation may have reached Vercel before cancellation or an API error.
            try:
                await asyncio.shield(stop_session(name))
            except Exception:
                logger.exception("Could not clean up sandbox %s; one-hour timeout remains", name)
        await asyncio.shield(client.aclose())


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
    sandbox, url = await reservation["task"]
    with launch_stage("desktop_setup", name):
        yield "[*] Adding the installer and review guide to the desktop...\n"
        files = {"airlock_install.sh": install_script, "REVIEW_GUIDE.html": help_html,
                 "open-session.sh": Path(__file__).with_name("sandbox").joinpath("open-session.sh").read_text()}
        async with sandbox_errors("open_session", 120):
            await run_with_files(sandbox, files, "open-session.sh", repo_args(repo_url), "AIRLOCK_SESSION_READY")
        yield "[*] The review guide, GitHub page, file manager, and both terminals are open. The installer runs automatically.\n"
        yield "[*] This workspace expires after one hour. Download anything you need before closing it.\n"
        yield {"name": name, "url": f"{url}/launch/{reservation['token']}"}
