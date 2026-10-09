# Validation

## Desktop bootstrap during AI generation (2026-10-09, not deployed)

The reservation task now creates the VM and runs `sandbox/bootstrap.sh` (desktop, proxy, clone, wallpaper, VNC check) while the installer is generated. After generation, `sandbox/open-session.sh` adds the installer and guide and opens the applications. `vc()` now uses `/tmp` CLI directories only on Vercel; locally they hid the saved login and `prototype.py` hung on a device-login prompt.

`uv run python prototype.py https://github.com/3kh0/slick` returned both `AIRLOCK_DESKTOP_READY` and `AIRLOCK_SESSION_READY` and a launch link in about 16s; the VM was then stopped. The desktop was not opened in Helium, so the open applications, installer terminal, and the production timing gain remain unverified until deployment.

## Launch latency baseline: 3kh0/slick (2026-10-09)

Helium Playwright, production, signed in as an admin. The page was instrumented to timestamp streamed lines and capture the launch URL instead of opening a popup, so popup-blocker behavior was not observed.

- Click to launch link: 14.8s. Server `stream_total` 14.4s: authorization 0.36s, GitHub metadata 0.18s, manifest inspection 0.11s (no AI pre-analysis), AI generation 5.4s (first token 0.49s, `deepseek/deepseek-v4.1-flash`), VM create 6.4s in parallel, then desktop setup 7.8s: transfer 1.4s, bootstrap 6.0s (XFCE 3s, wallpaper wait 2s, clone 1s, uv 1s, VNC 0s), ownership save 0.4s.
- The critical path was VM creation followed by desktop setup; AI finished first. A prior `hackclub/verlock` launch took 26.0s with AI generation on the critical path (17.7s, first token 3.2s).
- Opening the launch URL: KasmVNC canvas after about 1.3s (13 resources, 300 KB). The desktop showed both Chrome tabs, Thunar, and two terminals.
- The test VM was closed through the owner stop API (200) and no longer appears in `vc sandbox list`.

## Admin dashboard (2026-10-09)

The dashboard at https://verlock.hackclub.dev/admin has two sections, Running sandboxes and People with access. Organization controls are absent from the page, and `/api/v1/admin/organizations` returns 404.

Helium Playwright, signed in as the remaining admin:

- The people list loaded one admin. That account's role and Remove controls are disabled, with the note that at least one admin is required.
- The first production load returned 503 for `/api/v1/admin/sandboxes` because `vc sandbox list --limit 100` is rejected (`limit` must be 50 or less). The list call now uses 50 and still follows cursors.
- After deploying that fix to https://verlock-23emhcqdo-hackclub.vercel.app and moving `verlock.hackclub.dev`, Refresh sandboxes returned one running workspace: name, owner, start, expiration, and resources. No desktop launch token is shown. A 390px-wide viewport has no horizontal overflow.

Adding people, role changes, duplicate submissions, member-only API denial, and Blob conflict retries were checked earlier against a local fixture. This pass did not change the live access list.

## Launch latency changes (2026-10-09)

Implemented background VM creation after GitHub validation, parallel clone/uv/XFCE preparation, manifest inspection for simple root JavaScript/Python projects with AI fallback, reused manifests and pooled source-file requests, OpenRouter latency/throughput routing, and credential-free stage timings. Login/session authorization and initial organization reads now run blocking state/Slack operations in worker threads. The Dockerfile adds pinned uv; the currently deployed image has not been rebuilt.

Static source review covered the reservation lifecycle: cancel and await unfinished creation before stopping an undelivered VM, retain cleanup on AI/source/bootstrap failure, require `AIRLOCK_DESKTOP_READY`, and persist ownership before sending the launch URL. Bootstrap waits for the clone and uv jobs before launching either terminal. Existing desktop applications, wallpaper, fixed Hack Club scope, and one-hour/non-persistent VM settings are retained.

No automated or browser checks were run for these changes: the required Playwright MCP connected to Helium is not exposed in this session. No production deployment, image build, or test VM was created. The earlier validation below describes the previous implementation and does not validate these changes. Actual latency savings, OpenRouter routing behavior, both manifest/fallback paths, failure/disconnect cleanup, desktop connectivity, and owner-only close still require Helium validation before release.

Vercel resources are scoped to Hack Club, project verlock. The desktop image was built natively in a disposable Vercel builder, then pushed using project-scoped registry credentials minted by `vc vcr login`. No registry or control-plane credentials are copied into review VMs.

Image: `airlock-desktop:v1`; index digest `sha256:91459af3e89030a2460dfade074b27b565ce2359c38b7c0c0d5575598f4cdaba`; amd64 manifest `sha256:2858175dd67574b4499cacc67f3dfa0afeea01070193f25c2e1b488e4b1319f2`. VCR accepted the 5.7 GB compressed image after its filesystem layers were split.

## Helium with Playwright MCP

Confirmed the MCP browser is launched from `/Applications/Helium.app/Contents/MacOS/Helium`.

Local control-plane UI checks passed using browser-side API fixtures:

- Signed-out visitors see Login with Hack Club.
- Signed-in reviewers see the repository form.
- A second click while provisioning does not create a second request.
- A failed session request displays an error.
- A blocked popup shows a working manual launch link.
- A failed Close Session displays an error and enables retry.
- Successful close hides the close button and stale launch link.

Production checks through Helium passed:

- New Hack Club OAuth client: authorization with openid/slack_id, callback to verlock.hackclub.dev, authenticated admin UI.
- Native FastAPI health, bundled vc version, and private Blob access.
- Private Blob user-list add/read/remove, restoring the original list; organization list read.
- AI Gateway authentication and Ling generation after the team model allowlist was enabled.
- OpenRouter DeepSeek V4.1 Flash authentication and JSON generation; production model configuration verified through health. Streaming generation reached approximately 110 tokens/sec.

A real Vercel Sandbox desktop was inspected through Helium: KasmVNC connected, review guide and GitHub browser tabs, Thunar, and two terminals. The automatic installer fixture wrote its proof file, which was read in the remote terminal. Node 22, Bun, Rust, Go, Java 21, and .NET 8 were checked in that terminal. The replacement wallpaper was visible. An independent browser context without the session cookie received HTTP 403; the launch cookie is Secure, HttpOnly, and SameSite=Lax.

Full production workflow passed at https://verlock.hackclub.dev: OAuth session → GitHub metadata and selected files → DeepSeek stack analysis → streamed installer/guide generation → native vc Sandbox create/copy/exec → authenticated KasmVNC desktop popup. The generated repository guide and installer were present in the remote desktop; uv 0.12.24 and Bun 1.4.2 were verified there. The original guide/GitHub tabs, Thunar, and both launch terminals were visible with the replacement background.

Close Session was exercised against that actual VM through the production UI after another deployment: HTTP 200 with status stopped, close button hidden, and desktop disconnected. An unknown session returned 404. Durable ownership survived the deployment. Production health confirms deepseek/deepseek-v4.1-flash.

The fixture proves automatic installer execution. The generated Airlock repository itself needs its own OAuth/Kasm/AI credentials; its application service was not verified as running inside the review desktop. No control-plane secrets were copied to the VM.

Native runtime fixes found during these checks: request-scoped OIDC forwarding; writable XDG CLI directories under /tmp; retaining vc's Python analysis support; explicit XFCE startup; resolvable VM hostname; relative nginx launch redirects. The runtime bundle is approximately 173 MB before Python dependencies.

## Earlier checks

Before the request to use Playwright MCP for testing, eight backend unit tests passed, along with Python/shell/JavaScript syntax checks and git diff --check. A native Docker desktop check confirmed the guide/browser, file manager, two terminal processes, and automatic installer execution. That check exposed Kasm's normally injected XFCE startup flag; the launcher now sets it explicitly and waits for xfwm4.
