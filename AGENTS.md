# Working on Verlock

## Product and scope

Verlock recreates Hack Club Airlock's desktop code-review workflow on Vercel. Preserve the original reviewer experience: Hack Club login, a GitHub repository form, streamed AI analysis, a Kasm/XFCE desktop, the review guide and GitHub browser tabs, Thunar, and two terminals. One terminal automatically executes the generated installer; the other is interactive. The intentional desktop appearance change is the Verlock wallpaper.

All hosting and Sandbox operations belong to the **Hack Club Vercel team (`hackclub`)**, project **`verlock`**. Production is **https://verlock.hackclub.dev**. Use native Vercel services and the `vc` CLI for Sandbox management. Keep team/project scope fixed in server code; never accept scope overrides from a request.

The original desktop uses Kasm/XFCE. Do not replace it with code-server. Kasm Workspaces' external broker, persistent profiles, and broker-managed sharing are outside this implementation. Organization APIs and UI have been retired. Legacy organization records are read only during the one-time access-list migration.

## Stack

| Layer | Implementation | Main files |
| --- | --- | --- |
| Backend | Python 3.12 on Vercel, FastAPI/Starlette, Uvicorn locally, Pydantic models | `main.py`, `pyproject.toml`, `vercel.json` |
| Frontend | Static HTML, CSS, and browser JavaScript; existing Airlock UI | `static/index.html`, other files in `static/` |
| Login | Hack Club Auth OpenID Connect through Authlib; signed Starlette session cookie | `main.py` |
| Authorization | One persistent allowlist with member/admin roles | `main.py` |
| AI and GitHub | Async HTTPX calls to OpenAI-compatible chat completions and GitHub REST; Markdown guides rendered to HTML | `main.py`, `ai_errors.py` |
| Durable state | Private Vercel Blob through a small Node helper; JSON files for local development | `state_store.py`, `scripts/blob.mjs` |
| Sandbox provisioning | Async Python subprocesses invoking pinned `vc` Sandbox commands | `vercel_sandbox.py`, `runtime_tools.py` |
| Runtime identity | Request-scoped Vercel OIDC token stored in a Python ContextVar | `request_identity.py` |
| Desktop | Ubuntu 24.04, Kasm desktop 1.18.0, XFCE, KasmVNC, Chrome, Thunar, XFCE terminals | `docker/Dockerfile.vercel`, `sandbox/` |
| Desktop transport | nginx on published port 8080 proxies authenticated HTTP/WebSocket traffic to KasmVNC on loopback port 6901 | `sandbox/nginx.conf` |
| Build and deployment | `uv`, `bun`, a bundled Linux Node binary, Vercel CLI, native FastAPI functions | `build.py`, `package.json`, `scripts/deploy.py` |

The desktop retains the original C/C++ tools, Wine/Winetricks, Python scientific/web packages, Node 22, Bun, Rust, Go 1.23.4, Java 21/Maven/Gradle, and .NET 8. `uv` is installed by the startup adapter when absent. New Python and JavaScript dependency installation, including generated installers, must use **`uv` and `bun`**.

## Repository map and runtime flow

- `main.py` owns OAuth, authorization, admin APIs, GitHub analysis, AI generation, the streamed session endpoint, session ownership, and closing sessions. `access_control.py` owns the member/admin allowlist and one-time migration from legacy users, organization roles, and `ADMIN_USERS`.
- `ai_errors.py` converts upstream AI failures into useful messages and redacts credentials. Permanent HTTP 400/401/403/404 failures should not trigger automatic retries.
- `vercel_sandbox.py` creates an `airlock-<UUID hex>` session from private VCR image `airlock-desktop:v1`. Each VM has 2 vCPUs, 4 GB memory, a hard one-hour lifetime, non-persistent storage, and Internet egress for project setup.
- The provider copies one small tar archive containing the generated Bash installer, HTML guide, per-session credentials, and current startup adapters. Startup-adapter changes usually do not require rebuilding the image.
- `sandbox/bootstrap.sh` prepares the user environment, starts the desktop/proxy, clones the repository onto `/home/kasm-user/Desktop/<repo-name>`, writes `airlock_install.sh` and `REVIEW_GUIDE.html`, opens the applications, and sets the wallpaper.
- `sandbox/desktop-start.sh` explicitly supplies the Kasm/XFCE startup environment. Vercel custom images do not execute Docker ENTRYPOINT/CMD or preserve Docker ENV automatically; `/opt/airlock/image-env.sh` restores baked image settings.
- `sandbox/wallpaper.svg` is rasterized into the desktop image's background during image construction.
- Successful provisioning requires the remote `AIRLOCK_DESKTOP_READY` sentinel. A zero local `vc sandbox exec` exit code alone does not prove the remote bootstrap succeeded.
- Failed or interrupted provisioning attempts are stopped. Session ownership is persisted before returning success. The stop API checks the signed-in owner's Slack ID before stopping a VM.
- `prototype.py` launches a real desktop directly using the local saved `vc` login, with optional installer/guide files, bypassing OAuth and AI generation.

## AI configuration

Production is configured for **OpenRouter**, `https://openrouter.ai/api/v1`, model **`deepseek/deepseek-v4.1-flash`**. Both stack analysis and installer/guide generation use that model. OpenRouter requests disable optional reasoning and request JSON output for speed and predictable parsing. Prompts are sent as user messages.

The code's unset-environment defaults remain **Vercel AI Gateway**, `https://ai-gateway.vercel.sh/v1`, model **`inclusionai/ling-3.1-flash`**. Gateway authenticates with the current request's Vercel OIDC token in production, or `AI_GATEWAY_API_KEY` when supplied. A team model allowlist may need the requested model enabled.

Use `AI_BASE_URL`, `AI_MODEL`, and `AI_API_KEY` to select OpenRouter or another OpenAI-compatible provider. These are server settings; API keys must never reach the frontend or review desktop.

## State, authentication, and secrets

- Hack Club Auth requests only `openid slack_id`. The production callback is `https://verlock.hackclub.dev/auth`.
- `APP_SECRET`, `HACKCLUB_CLIENT_ID`, and `HACKCLUB_CLIENT_SECRET` configure the signed login session and OAuth client.
- `ADMIN_USERS` seeds admins only when `access.json` is first created. Afterwards, manage roles in `/admin`; environment changes do not override stored roles. Do not hardcode a person's admin access in application code. Keep at least one admin; both the UI and API enforce this. Roles are rechecked from storage, not trusted from stale login cookies.
- `BLOB_READ_WRITE_TOKEN` connects the private `verlock-state` store. The canonical `state/access.json` allowlist and session ownership use `state/` Blob paths. Access edits use ETag-based conditional writes and retry conflicts to protect concurrent edits and the last-admin rule. Production must fail when durable storage is missing rather than falling back to ephemeral files.
- Local development without Blob uses ignored `access.json` and `sessions/<name>.json`. Legacy `users.json` and `organizations.json` are read once to seed the canonical list; duplicate IDs are merged and admin roles take precedence. Private Blob reads bypass caching so ownership and access-list changes are current.
- `GITHUB_TOKEN` is optional for public repositories. `SLACK_BOT_TOKEN` is optional for Slack profiles. Channel membership no longer grants implicit access; all authorized people appear in the dashboard.
- Vercel runtime OIDC arrives in `x-vercel-oidc-token`. Keep it request-scoped; never write it into the process-wide environment shared by requests. Pass it only into the current CLI subprocess environment.
- Local Sandbox operations use the saved `vc` login. Production CLI configuration, data, cache, and working directories live under `/tmp` because the function filesystem is read-only elsewhere.
- Desktop launch links are session credentials. The link sets a Secure, HttpOnly, SameSite=Lax, one-hour cookie; requests without it receive 403. KasmVNC credentials are injected only into the loopback proxy inside that VM.
- Never commit or print secrets, launch tokens, `.env*`, registry credentials, or control-plane credentials. Never copy control-plane credentials into review VMs. Keep CLI errors redacted.

## Dependencies and local commands

Use `uv` for Python dependencies and execution, and `bun` for JavaScript dependencies and scripts. Commit the corresponding `uv.lock` and `bun.lock` changes when dependencies change. `requirements.txt` is a compatibility list; Vercel installs from `uv.lock`.

```sh
uv sync --frozen
bun install --frozen-lockfile
# Set local values described in .env.example; do not commit .env.
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

The package manifest pins Bun 1.3.14 and aliases Vercel CLI 59.11.7 as `vc-runtime`. Preserve cloud-compatible lockfile format when upgrading Bun; a newer local Bun may emit a lockfile the Vercel build cannot read.

## Build and deployment

`vercel.json` selects native FastAPI functions in `iad1`, entrypoint `main:app`, with a maximum function duration of 800 seconds. `build.py` downloads checksum-verified Linux Node 24.4.1, installs locked production dependencies with Bun, and removes unused builders, alternate native CLI binaries, source maps, and typings to fit the function bundle limit. Keep `@vercel/python-analysis`: the CLI loads it even for Sandbox operations. The Node runtime supports the CLI and Blob helper; there is no separate Node web server.

When deploying is part of the task, use:

```sh
bun run deploy
```

`scripts/deploy.py` pins the Hack Club team/project IDs, runs `vc deploy`, and explicitly updates the `verlock.hackclub.dev` alias. A plain `vc deploy --prod` previously advanced only `verlock.vercel.app`; do not assume the public custom domain moved with it.

Build desktop images as **linux/amd64**, preferably with a native amd64 builder for speed. The original Dockerfile is retained in `docker/Dockerfile`; use `docker/Dockerfile.vercel` for VCR. Filesystem layers are split because the upstream image exceeds VCR's compressed-layer limit. Wait until VCR reports the image Ready before launching.

Every Sandbox/VCR command must specify `--scope hackclub --project verlock`, for example:

```sh
vc sandbox list --scope hackclub --project verlock
vc sandbox stop <airlock-name> --scope hackclub --project verlock
vc vcr image ls airlock-desktop --scope hackclub --project verlock
```

Avoid paid idle pools and unnecessary image rebuilds. Keep GitHub metadata/source fetches concurrent, session transfers batched into one archive, and toolchains in the image. Stop task-created test VMs and temporary builders after use; preserve unrelated resources.

## Testing and evidence

**Use Playwright MCP connected to Helium for all testing.** Do not substitute a standalone Playwright runner, another browser, shell-based test runner, or curl checks. CLI commands remain appropriate for builds, deployments, diagnostics, and resource management.

Use browser-side API fixtures for local UI behavior: login visibility, duplicate submissions, progress, HTTP failures, blocked-popup fallback, and close/retry feedback. Fixtures do not prove live OAuth, AI generation, or Sandbox provisioning.

For changes affecting the real workflow, exercise the authorized path in Helium: login → GitHub analysis → AI installer/guide → Sandbox popup → connected desktop → close. Inspect the guide, file manager, terminals, generated files, and wallpaper. Verify unauthenticated desktop access is denied and only the owner can close a session. Use isolated local fixtures for role edits and migration checks. Verify admin-only APIs, stale-cookie rejection, duplicate submissions, last-admin protection, Sandbox pagination/empty/error states, and mobile layout. Confine real Blob concurrency checks to random temporary paths and delete them afterwards. Restore any live access-list changes.

Report exactly what was verified. A connected desktop and generated installer do not prove an arbitrary reviewed application runs successfully, especially when that application needs its own credentials. Record relevant results and limitations in `VALIDATION.md`. Read `WORKFLOW.md` for the original implementation trace and `README.md` for operational setup.

## Admin dashboard

`/admin` has two sections: Running sandboxes and People with access. Sandboxes are fetched on demand through scoped `vc sandbox list`, following pagination; rows show owner/repository when recorded, start time, expiration, and resources. Do not expose desktop launch tokens in this list. Access management supports adding people by Slack member ID, setting member/admin roles, and removing access. Members can launch sessions; admins also manage access and inspect running sandboxes. The browser enriches people, sandbox owners, and the add-person preview with public Slack profiles from Cachet (`https://cachet.hackclub.com/users/<id>`); treat responses without `type: "user"` as unknown IDs. Keep organization controls out of the UI and APIs.
