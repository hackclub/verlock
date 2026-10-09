# Verlock: Airlock on Vercel Sandbox

Airlock's original desktop review workflow, hosted in Vercel Sandbox under **Hack Club (`hackclub`)**, project **`verlock`**. The reviewer gets the same Kasm/XFCE desktop, browser, Thunar, and two terminals, including automatic execution of the generated installer. The desktop has a different Verlock wallpaper. See [the traced original workflow](WORKFLOW.md).

## Build the desktop image

Docker must build `linux/amd64`, even on an ARM Mac. The image includes the original Wine and language/build toolchains and is large. A native amd64 builder is significantly faster than local emulation.

```sh
bun add --global vercel@59.11.7
vc login
vc vcr login docker --scope hackclub --project verlock
vc vcr build docker . airlock-desktop:v1 --scope hackclub --project verlock --push -- --file docker/Dockerfile.vercel
vc vcr image ls airlock-desktop --scope hackclub --project verlock
```

Wait until VCR has optimized the image before launching. The image is split into smaller filesystem layers to satisfy VCR's 2 GB compressed-layer limit. The original Kasm Dockerfile remains in `docker/Dockerfile`; the Vercel adaptation is `docker/Dockerfile.vercel`. Vercel does not invoke Docker ENTRYPOINT/CMD, so the provider explicitly starts the desktop after boot.

## Launch a desktop directly

This prototype uses your existing `vc` login and does not need OAuth or AI credentials:

```sh
uv sync --frozen
bun install --frozen-lockfile
uv run prototype.py https://github.com/hackclub/airlock
# Optional: --installer path/to/install.sh --guide path/to/guide.html
```

The launcher prints a session name and a launch URL. Opening the URL sets a temporary access cookie and opens the desktop directly. Treat the URL as a session credential. Unauthenticated access to the desktop port is denied. The KasmVNC username/password are generated internally, stay in the individual VM, and are forwarded only over the internal loopback proxy.

The repository is cloned at `/home/kasm-user/Desktop/<repo-name>`. The review guide and GitHub page open in the desktop browser, Thunar opens the project directory, and two XFCE terminals open. One terminal automatically runs `airlock_install.sh`; the other starts an interactive shell. Both remain open. Review native Linux GUI apps or Windows programs with Wine as in the original image.

## Run the Airlock control plane

```sh
cp .env.example .env
# Configure the new OAuth client and a local session-signing secret.
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --workers 1
```

Register the control plane's `/auth` callback URL with Hack Club Auth. It retains Hack Club login, GitHub analysis, configurable AI providers, and the streamed session UI. The backend rechecks authorization before creating a VM. Access uses a single member/admin allowlist.

Production is hosted natively at **https://verlock.hackclub.dev** with Vercel FastAPI functions in `iad1`. The canonical access list and session ownership live in the private `verlock-state` Blob store, so they survive function restarts. Local development uses ignored `access.json` and session JSON files. Legacy users and organization roles are flattened once into the canonical list, preserving admins and deduplicating Slack IDs. The new Auth application requests only `openid slack_id` and uses `https://verlock.hackclub.dev/auth`. Credentials are Vercel secrets. Vercel's per-request OIDC header authenticates Sandbox, Gateway, and Blob operations (Blob through `BLOB_STORE_ID`); it is passed only to local CLI and Blob helper subprocesses, never into review VMs.

Deploy with `bun run deploy`. This runs the pinned project deployment through `vc`, then explicitly advances the `verlock.hackclub.dev` alias. The build uses `uv.lock` and `bun.lock`, bundles a verified Linux Node binary, and removes unused CLI builders, native alternative binaries, source maps, and typings to stay within the function size limit.

## AI provider

Production uses OpenRouter at `https://openrouter.ai/api/v1`, with `deepseek/deepseek-v4.1-flash` for both pre-analysis and installer/guide generation. Its API key is a Vercel secret. OpenRouter requests disable optional reasoning and request JSON output to reduce latency and preserve the installer/guide schema.

The configurable fallback is Vercel AI Gateway with `inclusionai/ling-3.1-flash`. Set `AI_BASE_URL=https://ai-gateway.vercel.sh/v1` and `AI_MODEL=inclusionai/ling-3.1-flash`; Gateway uses request-scoped OIDC in production, or `AI_GATEWAY_API_KEY` locally. Remove the OpenRouter `AI_API_KEY` when switching to Gateway.

For OpenRouter or another OpenAI-compatible provider, configure `AI_BASE_URL`, `AI_API_KEY`, and `AI_MODEL` in the project environment. For example, OpenRouter uses `https://openrouter.ai/api/v1`. Provider secrets stay in the control plane. Permanent HTTP rejections are reported without automatic retries.

## Launch efficiency

After GitHub validation, VM creation runs alongside project inspection and installer/guide generation. Simple root JavaScript/Python projects use their manifests directly, avoiding the inspection model call; complex layouts fall back to AI inspection. Selected source files share an HTTP connection pool and already-read manifests are reused. OpenRouter inspection requests prefer low latency, while installer/guide requests prefer throughput.

Each session copies one small archive containing its generated installer, guide, and current startup adapters. Inside the VM, repository cloning and preparation of uv run alongside XFCE startup; application launch waits for all three. The Dockerfile includes pinned uv for the next image build, and existing images retain the startup installation fallback. Toolchains remain preinstalled in the private VCR image.

Review VMs are created on demand with no paid idle pool. The one-hour lifetime starts at VM creation, including the overlapping AI work. Failed analysis, startup errors, and interrupted launches stop the reserved VM. Ownership is saved before the launch URL is returned.

Server logs include credential-free `launch_timing` records for authorization, GitHub metadata/source fetches, inspection, AI generation, VM creation, archive transfer, bootstrap, and ownership save. `launch_link` associates the request ID with its sandbox name. Bootstrap adds clone, uv, XFCE, wallpaper, and VNC timings. These stages overlap: do not sum them to estimate total latency; use `stream_total` plus authorization. Stream progress reports chunks rather than claiming each streamed event is a token.

## Session lifetime

Every provider operation explicitly uses `--scope hackclub --project verlock`. Each review VM has 2 vCPUs/4 GB RAM, one published desktop port, Internet egress for installing dependencies, and a hard one-hour lifetime. It is non-persistent; download anything needed before closing. Failed provisioning attempts are stopped automatically. Close Session stops only the signed-in user's VM. Ownership persists across control-plane restarts. You can also stop a session with the CLI:

```sh
vc sandbox list --scope hackclub --project verlock
vc sandbox stop <airlock-name> --scope hackclub --project verlock
```

Kasm Workspaces' external broker, broker-managed sharing, and persistent profiles are replaced by the Vercel provider. The desktop review workflow is retained.

## Administration

Open https://verlock.hackclub.dev/admin as an admin. The dashboard lists running sandboxes for Hack Club/verlock, including start/expiration, resources, and recorded owner/repository. Refresh fetches the current Vercel CLI inventory.

People with access is one list with **Member** and **Admin** roles. Add a Slack member ID, choose a role, or change/remove an existing person. Members can start review sessions; admins can also manage access and view running sandboxes. People and sandbox owners show Slack names, avatars, and pronouns from the public [Cachet](https://cachet.hackclub.com/) profile cache, and the add form previews whose ID was entered; IDs Cachet cannot find are flagged. Changes persist immediately without redeploying. At least one admin must remain, and access is rechecked on the server even for already-signed-in users.

`ADMIN_USERS` only seeds the list on first initialization; subsequent role changes belong in the dashboard. Organization APIs and implicit Slack-channel access are retired. Access edits use conditional Blob writes to avoid overwriting concurrent changes.

## Browser validation

Use the Playwright MCP connected to Helium. Start the local control plane as above and exercise login visibility, session progress, duplicate submissions, HTTP failure feedback, popup fallback, and closing/retrying a session. Browser API fixtures can validate the UI without production OAuth/AI credentials; those fixtures do not prove live login or AI generation.

For the desktop, launch a real sandbox and use Helium to open its session link, confirm the KasmVNC stream and desktop applications, inspect the guide and terminal, and verify the replacement wallpaper. See [validation notes](VALIDATION.md).

References: [Vercel Sandbox custom images](https://vercel.com/docs/sandbox/concepts/images), [Sandbox CLI](https://vercel.com/docs/sandbox/cli-reference), [VCR](https://vercel.com/docs/container-registry), [Kasm desktop base](https://hub.docker.com/r/kasmweb/ubuntu-noble-desktop).
