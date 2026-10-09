# Original Airlock workflow and Vercel migration

Inspected upstream hackclub/airlock at commit `52cb3d6`. The original implementation does not install or launch code-server.

1. The reviewer signs in through Hack Club Auth. Airlock checks global admins, organization membership, its users list, or membership in the configured Slack channel.
2. The reviewer submits a GitHub URL. The UI streams the getSession response, blocks duplicate form submissions, and opens the returned session URL, with a manual link when popups are blocked.
3. The backend normalizes the URL and fetches the repository tree and README from GitHub. An AI pre-analysis chooses extra files to inspect and estimates project difficulty. Airlock fetches those files and asks the selected AI model for a Bash installer, review guide, tech stack, and summary.
4. The backend requests a Kasm session with the configured image and optional organization Kasm user, then polls until it is running.
5. The desktop image extends `kasmweb/ubuntu-noble-desktop:1.18.0`: XFCE, browser, file manager and terminal, plus C/C++ build tools, Wine/Winetricks, Python scientific/web tools, Node 22, Bun, npm tools, Rust, Go 1.23.4, Java 21/Maven/Gradle, and .NET 8.
6. The backend injects commands as root: gives kasm-user passwordless sudo, defines language-toolchain environment variables, clones the repository onto the Desktop, writes airlock_install.sh and REVIEW_GUIDE.html, and changes the desktop wallpaper.
7. The backend opens the HTML guide and GitHub URL in the desktop browser, opens the project in Thunar, opens an interactive terminal, and opens a second terminal that automatically executes the generated installer. Both terminals stay open for review.
8. It returns the Kasm session URL to the reviewer. The manual says sessions must not exceed one hour; the repository does not configure that expiration in the Kasm API request itself.

The Vercel implementation retains steps 1–3 and 5–7. Only provisioning and browser transport are replaced: the custom desktop image boots in a Hack Club Vercel Sandbox, the desktop startup is invoked explicitly because Vercel ignores Docker ENTRYPOINT/CMD, and the authenticated KasmVNC HTTP/WebSocket stream is served through a published sandbox port. A launch link sets a temporary session cookie so the reviewer can open the desktop directly. Vercel enforces the one-hour lifetime. Failed starts are stopped, and the Airlock UI supports closing the current session.

The desktop appearance change is a Verlock wallpaper. The Kasm desktop and desktop applications remain; code-server is not used. The image is repacked into smaller filesystem layers because the upstream Kasm image has a layer larger than VCR's 2 GB limit. Tool packages come from Ubuntu where equivalent (ripgrep, bat, fd), and Hetzner-specific apt mirror changes are omitted. These are build/hosting adaptations.

Kasm's external broker, organization-specific Kasm account IDs, and broker-managed sharing/profile policies are not part of the VM recreation. Existing Airlock organization fields stay compatible, but Kasm user IDs are unused by the Vercel provider. The control plane now runs as native Vercel FastAPI functions at verlock.hackclub.dev, with a new OAuth client and private Vercel Blob access lists/session ownership. DeepSeek V4.1 Flash through OpenRouter replaces the original Hack Club AI proxy. Vercel AI Gateway and other OpenAI-compatible endpoints remain configurable.

The current admin dashboard supersedes the original organization system with Running sandboxes and People with access. Access is now a single persistent member/admin list. Existing user and organization roles are migrated once; subsequent edits are managed directly in the dashboard.
