import os
import json
import html
import base64
import asyncio
import httpx
import markdown
import logging
import uuid
import re
import tomllib
from launch_timing import launch_stage
from access_control import load_access, role_for, update_access
from vercel_sandbox import create_session, stop_session, reserve_session, list_running_sessions
from state_store import load_state, save_state
from ai_errors import AIRequestError, check_ai_response
from runtime_tools import vc_command
from request_identity import current_oidc_token, VercelIdentityMiddleware
from cachetools import TTLCache
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth
from typing import Literal, Optional
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Load environment variables
load_dotenv()

# Logging configuration
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
AI_BASE_URL = os.getenv("AI_BASE_URL", "https://ai-gateway.vercel.sh/v1").rstrip("/")
AI_MODEL = os.getenv("AI_MODEL", "inclusionai/ling-3.1-flash")

def ai_headers():
    custom = AI_BASE_URL != "https://ai-gateway.vercel.sh/v1"
    key = os.getenv("AI_API_KEY") if custom else (os.getenv("AI_GATEWAY_API_KEY") or current_oidc_token())
    if not key:
        raise RuntimeError("AI authentication is not configured")
    return {"Authorization": "Bearer " + key}
# Auth Configuration
HC_CLIENT_ID = os.getenv("HACKCLUB_CLIENT_ID")
HC_CLIENT_SECRET = os.getenv("HACKCLUB_CLIENT_SECRET")
APP_SECRET = os.getenv("APP_SECRET")

# Slack Configuration
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
slack_client = WebClient(token=SLACK_BOT_TOKEN)

# Caches
slack_profile_cache = TTLCache(maxsize=1000, ttl=600)  # 10 minutes cache

# Models
class User(BaseModel):
    slack_id: str = Field(pattern=r'^[UW][A-Z0-9]{5,31}$')
    role: Literal['member', 'admin'] = 'member'
    email: Optional[str] = None

class RoleUpdate(BaseModel):
    role: Literal['member', 'admin']

# Initialize FastAPI
app = FastAPI()
app.add_middleware(VercelIdentityMiddleware)
app.add_middleware(SessionMiddleware, secret_key=APP_SECRET)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize OAuth
oauth = OAuth()
oauth.register(
    name='hackclub',
    client_id=HC_CLIENT_ID,
    client_secret=HC_CLIENT_SECRET,
    server_metadata_url='https://auth.hackclub.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid slack_id'
    }
)

# --- Helper Functions ---

def get_slack_profile(slack_id: str):
    if not SLACK_BOT_TOKEN:
        return {"id": slack_id, "display_name": slack_id, "image": None}
    if slack_id in slack_profile_cache:
        return slack_profile_cache[slack_id]
    
    try:
        response = slack_client.users_info(user=slack_id)
        if response["ok"]:
            user = response["user"]
            profile = user.get("profile", {})
            data = {
                "id": user["id"],
                "display_name": profile.get("display_name") or profile.get("real_name") or user["name"],
                "image": profile.get("image_48"),
                "image_192": profile.get("image_192")
            }
            slack_profile_cache[slack_id] = data
            return data
    except Exception as e:
        logger.error(f"Error fetching Slack profile for {slack_id}: {e}")
    
    return {"id": slack_id, "display_name": slack_id, "image": None}

def is_authorized(slack_id):
    return role_for(slack_id) is not None

def normalize_github_url(url):
    url = url.strip()
    # Basic protocol fix
    if not url.startswith("http"):
        url = "https://" + url
    
    # Check domain
    if "github.com" not in url:
        raise ValueError("URL must be a GitHub URL (github.com)")

    from urllib.parse import urlparse
    parsed = urlparse(url)
    
    path_parts = [p for p in parsed.path.split('/') if p]
    
    if len(path_parts) < 2:
        raise ValueError("Invalid GitHub URL format. Expected github.com/owner/repo")
        
    owner = path_parts[0]
    repo = path_parts[1]
    
    normalized = f"https://github.com/{owner}/{repo}"
    
    warning = None
    if len(path_parts) > 2:
        warning = f"URL contained extra path segments ('/{'/'.join(path_parts[2:])}'). Normalized to {normalized}."
        
    return normalized, warning

async def get_github_context_data(repo_url):
    if not "github.com/" in repo_url:
        raise ValueError("Invalid repo url")

    # Ensure protocol
    if not repo_url.startswith("http"):
        repo_url = f"https://{repo_url}"

    parts = repo_url.rstrip("/").split("/")
    # Find where github.com is
    try:
        gh_index = parts.index("github.com")
        if len(parts) < gh_index + 3:
             raise ValueError("Invalid repo url structure")
        owner = parts[gh_index + 1]
        repo = parts[gh_index + 2]
    except ValueError:
        # Fallback if github.com not found directly in split (unlikely if check passed)
        raise ValueError("Invalid repo url structure")

    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    readme = ""
    files_list = []

    async with httpx.AsyncClient() as client:
        repo_response, readme_response, contents_response = await asyncio.gather(
            client.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers),
            client.get(f"https://api.github.com/repos/{owner}/{repo}/readme", headers=headers),
            client.get(f"https://api.github.com/repos/{owner}/{repo}/contents", headers=headers),
            return_exceptions=True,
        )
        # First check if the repo exists and is accessible
        try:
            r = repo_response
            if isinstance(r, Exception):
                raise r
            if r.status_code == 404:
                raise ValueError("Repository not found (404). Please check the URL.")
            elif r.status_code != 200:
                raise ValueError(f"Could not access repository (Status: {r.status_code})")
        except httpx.RequestError as e:
            raise ValueError(f"Network error accessing GitHub: {e}")

        try:
            r = readme_response
            if isinstance(r, Exception):
                raise r
            if r.status_code == 200:
                readme = base64.b64decode(r.json()['content']).decode('utf-8')[:4000]
        except: pass

        try:
            r = contents_response
            if isinstance(r, Exception):
                raise r
            if r.status_code == 200:
                files_data = r.json()
                if not files_data:
                     raise ValueError("Repository is empty.")
                     
                # Limit to top 300 files to prevent context explosion
                files_data = files_data[:300]
                files_list = [f"{i['type']}: {i['path']} (size: {i['size']})" for i in files_data]
                if len(r.json()) > 300:
                    files_list.append("... (truncated)")
            elif r.status_code == 404:
                 # Should have been caught by the first check, but double check contents endpoint
                 raise ValueError("Repository contents not found (empty or invalid).")
            else:
                 # If we can't get contents, we can't analyze it properly
                 raise ValueError(f"Could not list repository contents (Status: {r.status_code})")

        except ValueError as e:
            raise e
        except Exception as e:
             raise ValueError(f"Error listing repository files: {e}")

    return {"readme": readme, "files": "\n".join(files_list), "name": repo, "owner": owner, "repo": repo,
            "root_files": [item['path'] for item in files_data if item['type'] == 'file'],
            "root_dirs": [item['path'] for item in files_data if item['type'] == 'dir']}

async def fetch_github_file_content(owner, repo, path, client=None):
    if client is None:
        async with httpx.AsyncClient() as shared_client:
            return await fetch_github_file_content(owner, repo, path, shared_client)
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    try:
        r = await client.get(f"https://api.github.com/repos/{owner}/{repo}/contents/{path}", headers=headers)
        if r.status_code == 200:
            data = r.json()
            if 'content' in data:
                 return base64.b64decode(data['content']).decode('utf-8')
    except (httpx.HTTPError, ValueError, UnicodeError):
        pass
    return ""


async def inspect_common_project(context):
    """Use root manifests for simple JS/Python projects; defer complex layouts to AI."""
    paths = set(context['root_files'])
    if (paths & {'Dockerfile', 'docker-compose.yml', 'docker-compose.yaml', 'compose.yml',
                 'compose.yaml', 'pnpm-workspace.yaml', 'lerna.json', 'nx.json', 'turbo.json',
                 'setup.py', 'setup.cfg', 'Makefile', 'Cargo.toml', 'go.mod', 'pom.xml', 'build.gradle'}
            or set(context['root_dirs']) & {'apps', 'packages', 'frontend', 'backend', 'client', 'server'}):
        return None
    manifests = sorted(paths & {'package.json', 'pyproject.toml', 'requirements.txt'})
    if not manifests:
        return None
    async with httpx.AsyncClient() as client:
        contents = await asyncio.gather(*(fetch_github_file_content(
            context['owner'], context['repo'], path, client) for path in manifests))
    loaded = dict(zip(manifests, contents))
    if not all(contents):
        return None
    try:
        package = json.loads(loaded['package.json']) if 'package.json' in loaded else {}
        python = tomllib.loads(loaded['pyproject.toml']) if 'pyproject.toml' in loaded else {}
        if not isinstance(package, dict) or not isinstance(python, dict):
            return None
        if ('workspaces' in package or 'workspace' in python.get('tool', {}).get('uv', {})
                or 'path' in json.dumps(python.get('tool', {}).get('poetry', {}).get('dependencies', {}))):
            return None
        # Local packages can require manifests outside the root.
        if re.search(r'(?:file:|workspace:|link:)', loaded.get('package.json', '')):
            return None
        if re.search(r'^\s*(?:-e\b|\.|--requirement\b|--constraint\b|-r\b|-c\b)',
                     loaded.get('requirements.txt', ''), re.M):
            return None
    except (ValueError, TypeError, AttributeError):
        return None
    supporting = sorted(paths & {'main.py', 'app.py', 'manage.py', 'vite.config.js',
                                'vite.config.ts', 'next.config.js', 'next.config.mjs', 'next.config.ts'})
    return {"tech_stack": " / ".join(filter(None, ["JavaScript" if 'package.json' in paths else "",
             "Python" if paths & {'pyproject.toml', 'requirements.txt'} else ""])),
            "files_to_read": manifests + supporting, "contents": loaded}

async def pre_analyze_project(context):
    prompt = f"""
    Context:
    Repo Name: {context['name']}
    File Structure: {context['files']}
    README Content: {context['readme']}
    
    Task:
    Analyze the project structure and determine:
    1. Difficulty: "easy" or "hard".
       - Easy examples: Setting up some python dependencies, simple static site.
       - Hard examples: Setting up a complex website with a frontend and a backend and all of that.
    2. Tech Stack: A short string identifying the tech stack.
    3. Files to Read: A list of specific file paths (from the structure) that would provide critical context for writing an install script.
       - Select key files like package.json, requirements.txt, Dockerfile, main.py, etc. The system does not have docker so if the project requires docker, you should consider it hard and include the files docker uses on the list of files_to_read.
    
    Output Format (JSON only):
    {{
      "difficulty": "easy" | "hard",
      "tech_stack": "string",
      "files_to_read": ["path/to/file1", "path/to/file2"]
    }}
    """
    
    payload = {
        "model": AI_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.5
    }
    if AI_BASE_URL.startswith("https://openrouter.ai/"):
        payload["reasoning"] = {"enabled": False}
        payload["response_format"] = {"type": "json_object"}
        payload["provider"] = {"sort": "latency"}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(AI_BASE_URL + "/chat/completions",
                                  headers=ai_headers(), json=payload)
            await check_ai_response(r)
            content = r.json()['choices'][0]['message']['content']
            
            if "```json" in content:
                content = content.replace("```json", "").replace("```", "")
            
            return json.loads(content)
    except AIRequestError as e:
        if e.status in {400, 401, 403, 404}:
            raise
        logger.error(f"Pre-analysis failed: {e}")
        return {"difficulty": "easy", "tech_stack": "Unknown", "files_to_read": []}
    except Exception as e:
        logger.error(f"Pre-analysis failed: {e}")
        return {"difficulty": "easy", "tech_stack": "Unknown", "files_to_read": []}

async def analyze_with_ai_data(context, file_contents="", model_id=None):
    model_id = model_id or AI_MODEL
    # Retry transient streaming failures.
    max_retries = 2 if "flash" in model_id else 1
    
    prompt = f"""
    Context:
    Repo Name: {context['name']}
    File Structure: {context['files']}
    README Content: {context['readme']}
    Additional File Contents:
    {file_contents}

    System Environment:
    - OS: Ubuntu 24.04 (Noble), with an XFCE desktop streamed through KasmVNC
    - User: Sudo privileges available (no password required).
    - Python 3 is pre-installed.
    - The git repo is already cloned; the script runs from the repo root.
    - The system cannot run docker and has no systemd. If a project requires docker, you must run it manually.
    - The system has the following tooling installed:
    1. Core System & Build Tools
    Editors & Utilities: vim, nano, htop, jq, tree, curl, wget, git, unzip, zip, tar, gzip
    Build Essentials: build-essential, cmake, pkg-config, autoconf, automake, libtool
    Media: ffmpeg, imagemagick
    Databases (Clients): sqlite3, postgresql-client, default-mysql-client
    Dev Libraries: libssl-dev, zlib1g-dev, libffi-dev, uuid-dev, and various other headers (readline, sqlite3, etc.).
    2. Windows Compatibility (Wine)
    Wine: wine (64-bit), wine32 (32-bit architecture enabled), fonts-wine
    Utilities: winetricks, cabextract, zenity
    3. Python (Data Science & Web)
    Core: Python 3 (full), pip, venv
    Data Science: numpy, pandas, scipy, matplotlib, seaborn, scikit-learn
    Web Frameworks: flask, fastapi, uvicorn, django, requests
    Database/Cloud: boto3 (AWS), sqlalchemy, psycopg2-binary
    Tools: pytest, black, flake8, ipython, jupyterlab, beautifulsoup4, lxml, pyyaml, pillow, openpyxl
    4. JavaScript / TypeScript
    Runtimes: Node.js 22 (LTS), Bun
    Package Managers: npm (latest), yarn, pnpm
    Global Tools: typescript, ts-node, nodemon, eslint, prettier
    Framework CLIs: @angular/cli, react-scripts, express-generator
    5. Rust
    Language: Rust (installed via rustup)
    Cargo Tools: ripgrep (fast grep), bat (cat clone), fd-find (find clone)
    6. Go (Golang)
    Language: Go version 1.23.4
    7. Java
    JDK: OpenJDK 21
    Build Tools: maven, gradle
    8. .NET
    SDK: .NET 8.0 SDK
    
    You may use any of the tooling and install your own if needed. 
    Before using any tool, even if it's on the list, you must check if it's installed and if not the script must be able to handle it and install it.

    Goal:
    Create a production-grade automated installation script and a reviewer guide.

    Task 1: bash install script
    Write a Bash script to install and run the project. You must adhere to the following strict coding standards:
    1.  **Strict Mode & Safety:** Start with `set -euo pipefail` to ensure the script fails instantly on errors or undefined variables.
    2.  **Visual Logging:** Use the following function style for output (Green for INFO, Yellow for WARNING, Red for ERROR):
        - `print_status() {{ echo -e "\\033[0;32m[INFO]\\033[0m $1"; }}`
        - `print_error() {{ echo -e "\\033[0;31m[ERROR]\\033[0m $1"; }}`
    3.  **Error Handling:** Use a `trap` function to catch errors and print a helpful message before exiting.
    4.  **Apt Reliability:** Before running `apt-get install`, use a loop to check for and wait on `/var/lib/dpkg/lock` to ensure apt is not locked by background processes.
    5.  **Idempotency:** Do not blind install. Check if a package/tool exists using `command -v` before attempting to install it.
    6.  **Dependencies:** Use uv for Python and bun for JavaScript dependencies. For Python, use `uv sync` when a pyproject.toml exists, otherwise `uv venv venv` and `uv pip install --python venv/bin/python -r requirements.txt`. Keep dependencies isolated. For JavaScript, use `bun install` and `bun run`.
    7.  **Execution:** The script must handle all dependencies and end by running the project (or printing the command to run it if it is a service).

    Task 2: Markdown reviewer guide
    Create a Markdown guide.
    1.  **Header:** The exact command to execute the project (e.g., `./airlock_install.sh` or `source venv/bin/activate && python main.py`).
    2.  **Summary:** A concise technical summary of the project's purpose and the tech stack found in the file structure.
    3.  **Installer Logic:** A technical explanation of what the script does (e.g., "Checks apt locks, ensures Python 3.10+, creates a virtual environment, installs requirements.txt...").

    Task 3: Tech Stack
    A 1-line comma-separated list of the specific languages, frameworks, and critical tools detected.

    Task 4: Summary
    A 2-line summary. Line 1: What the repo does. Line 2: How the install script achieves the setup.

    Output Format:
    Return ONLY valid JSON with no markdown formatting.
    IMPORTANT: Ensure all strings are properly escaped (especially newlines and quotes) to be valid JSON.
    {{
      "script": "code string",
      "guide": "markdown string",
      "tech_stack": "plaintext string",
      "summary": "plaintext string"
    }}
    """
    
    last_exception = None

    for attempt in range(max_retries + 1):
        try:
            payload = {
                "model": model_id,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
                "stream": True
            }
            if AI_BASE_URL.startswith("https://openrouter.ai/"):
                payload["reasoning"] = {"enabled": False}
                payload["response_format"] = {"type": "json_object"}
                payload["provider"] = {"sort": "throughput"}

            full_content = ""
            start_time = asyncio.get_event_loop().time()
            last_update_time = start_time
            chunk_count = 0
            first_content = True
            
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", AI_BASE_URL + "/chat/completions",
                                      headers=ai_headers(), json=payload) as response:
                    await check_ai_response(response)
                    
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                            
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk['choices'][0]['delta'].get('content', '')
                            if delta and first_content:
                                logger.info("ai_first_content seconds=%.3f attempt=%s",
                                            asyncio.get_event_loop().time() - start_time, attempt + 1)
                                first_content = False
                            full_content += delta
                            chunk_count += 1
                            
                            current_time = asyncio.get_event_loop().time()
                            if current_time - last_update_time >= 2:
                                elapsed = current_time - start_time
                                chunks_per_second = chunk_count / elapsed if elapsed > 0 else 0
                                yield f"    [{int(elapsed)}s] Status: {chunk_count} chunks {chunks_per_second:.1f} chunks/s\n"
                                last_update_time = current_time
                                
                        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
                            continue

            # Process the full content
            content = full_content
            
            # Clean up markdown code blocks if present
            content = content.strip()
            if content.startswith("```json"):
                content = content[7:]
            if content.endswith("```"):
                content = content[:-3]
            content = content.strip()

            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                # JSON parsing failed, try to extract fields using regex as a fallback
                import re
                
                script_match = re.search(r'"script"\s*:\s*"(.*?)(?<!\\)"', content, re.DOTALL)
                guide_match = re.search(r'"guide"\s*:\s*"(.*?)(?<!\\)"', content, re.DOTALL)
                tech_stack_match = re.search(r'"tech_stack"\s*:\s*"(.*?)(?<!\\)"', content, re.DOTALL)
                summary_match = re.search(r'"summary"\s*:\s*"(.*?)(?<!\\)"', content, re.DOTALL)
                
                if script_match and guide_match and tech_stack_match and summary_match:
                     # Unescape the extracted strings
                     def unescape_json_string(s):
                         return s.replace('\\"', '"').replace('\\n', '\n').replace('\\t', '\t').replace('\\\\', '\\')

                     data = {
                         'script': unescape_json_string(script_match.group(1)),
                         'guide': unescape_json_string(guide_match.group(1)),
                         'tech_stack': unescape_json_string(tech_stack_match.group(1)),
                         'summary': unescape_json_string(summary_match.group(1))
                     }
                else:
                    logger.warning(f"AI Response content (failed to parse): {content[:500]}...")
                    raise

            script = data.get('script', '')
            guide = data.get('guide', '')
            tech_stack = data.get('tech_stack', '')
            summary = data.get('summary', '')

            html = f"<html><title>Airlock Manual</title><body style='font-family:sans-serif;padding:20px'><h1>Airlock Manual</h1><h2>AI Review Guide</h2>{markdown.markdown(guide)}<hr><h2>Vibecoded Install script</h2><pre><code>{script}</code></pre><p>You can run it with <code>bash ./airlock_install.sh</code></p><hr><h2>Airlock Info</h2><p>Airlock is a Hack Club tool for reviewing code in an ephemeral virtualized environment. Airlock sessions may not last longer than 1 hour. Please remember to close the Airlock session once you are done. You can use Airlock on airlock.hackclub.com. If you experience any issues, please contact @Carlos on Slack.</p></body></html>"

            yield {"type": "result", "data": (script, html, tech_stack, summary)}
            return
        
        except Exception as e:
            if isinstance(e, AIRequestError) and e.status in {400, 401, 403, 404}:
                raise
            last_exception = e
            logger.warning(f"AI Attempt {attempt+1}/{max_retries+1} failed: {e}")
            if attempt < max_retries:
                await asyncio.sleep(1) # Short backoff
                continue

    raise last_exception

# Session ownership survives Vercel function restarts in private Blob storage.

async def create_vercel_session_generator(repo_url, install_script, help_html, slack_id, reservation=None):
    async for message in create_session(repo_url, install_script, help_html, reservation):
        if isinstance(message, dict):
            try:
                with launch_stage("ownership_save", message["name"]):
                    await asyncio.to_thread(save_state, "sessions/" + message["name"] + ".json", {"owner": slack_id, "repo_url": repo_url})
            except BaseException:
                if reservation is None:
                    await asyncio.shield(stop_session(message["name"]))
                raise
            yield f"[*] Sandbox: {message['name']}\n"
            if reservation is not None:
                reservation['delivered'] = True
            yield f"[SUCCESS] Session: {message['url']}\n"
        else:
            yield message

# --- Dependencies ---

async def get_current_user(request: Request):
    user = request.session.get('user')
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user

async def get_admin_user(user: dict = Depends(get_current_user)):
    if await asyncio.to_thread(role_for, user['slack_id']) != 'admin':
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

# --- Routes ---

@app.get("/")
async def root():
    with open("static/index.html", "r") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content, status_code=200, headers={"Cache-Control": "public, max-age=600, s-maxage=60, stale-while-revalidate=3600"})

@app.get("/favicon.svg")
async def favicon():
    with open("static/icon-rounded.svg", "r") as f:
        svg_content = f.read()
    return Response(content=svg_content, media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=604800"})

@app.get("/admin")
async def admin_page():
    with open("static/admin.html", "r") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content, status_code=200, headers={"Cache-Control": "public, max-age=600, s-maxage=60, stale-while-revalidate=3600"})

@app.get("/login")
async def login(request: Request):
    if not HC_CLIENT_ID or not HC_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="Hack Club login is not configured")
    redirect_uri = request.url_for('auth_callback')
    return await oauth.hackclub.authorize_redirect(request, redirect_uri)

@app.get("/auth")
async def auth_callback(request: Request):
    try:
        token = await oauth.hackclub.authorize_access_token(request)
        user_info = token.get('userinfo')
        if not user_info:
             user_info = await oauth.hackclub.userinfo(token=token)

        # Fallback: If slack_id missing, try /api/v1/me
        if not user_info.get('slack_id'):
            access_token = token.get('access_token')
            async with httpx.AsyncClient() as client:
                r = await client.get("https://auth.hackclub.com/api/v1/me",
                                     headers={"Authorization": f"Bearer {access_token}"})
                if r.status_code == 200:
                    api_user = r.json()
                    if 'identity' in api_user and 'slack_id' in api_user['identity']:
                        user_info['slack_id'] = api_user['identity']['slack_id']
                        if 'name' not in user_info and 'name' in api_user['identity']:
                             user_info['name'] = api_user['identity'].get('name', 'User')
                        if 'email' not in user_info and 'primary_email' in api_user['identity']:
                             user_info['email'] = api_user['identity']['primary_email']

        slack_id = user_info.get('slack_id')
        if not slack_id:
             return HTMLResponse(
                 """
                 <html>
                 <head>
                    <title>Airlock | Access Denied</title>
                    <style>
                        body { display: flex; justify-content: center; align-items: center; min-height: 100vh; flex-direction: column; margin: 0; font-family: sans-serif; padding: 40px; text-align: center; }
                        .btn { display: inline-block; background: #ec3750; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; margin-top: 20px; }
                    </style>
                </head>
                <body>
                    <h1>Access Denied</h1>
                    <p style="margin: 0px;">We could not find a Slack ID associated with your profile.</p>
                    <p>Please connect your Slack account to your Hack Club account.</p>
                    <a href="https://auth.hackclub.com/" class="btn">Connect Slack on Hack Club Auth</a>
                </body>
                </html>
                 """,
                 status_code=403
             )

        role = await asyncio.to_thread(role_for, slack_id)
        if role is None:
            return HTMLResponse(
                f"""
                <html>
                <head>
                    <title>Verlock | Access Denied</title>
                    <style>
                        body {{ display: flex; justify-content: center; align-items: center; min-height: 100vh; flex-direction: column; margin: 0; font-family: sans-serif; padding: 40px; text-align: center; box-sizing: border-box; }}
                        code {{ font-size: 1.2em; background: #f1f1f1; padding: 4px 8px; border-radius: 4px; user-select: all; }}
                        .btn {{ display: inline-block; background: #ec3750; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; margin-top: 20px; }}
                    </style>
                </head>
                <body>
                    <h1>Access Denied</h1>
                    <p>You signed in with Slack member ID <code>{html.escape(str(slack_id))}</code></p>
                    <p>Ask a Verlock admin to add this ID to People with access.</p>
                    <a href="/logout" class="btn">Try another account</a>
                </body>
                </html>
                """,
                status_code=403,
            )
        user_info['is_admin'] = role == 'admin'
        user_info['role'] = role
        request.session['user'] = dict(user_info)

        return RedirectResponse(url='/')
    except Exception as e:
        logger.exception("Auth failed")
        return HTMLResponse(f"<h1>Auth Failed: {e}</h1>", status_code=400)

@app.get("/logout")
async def logout(request: Request):
    request.session.pop('user', None)
    return RedirectResponse(url='/')

@app.get("/api/v1/me")
async def me(user: dict = Depends(get_current_user)):
    user_data = dict(user)
    user_data["name"] = user_data.get("name") or user_data.get("email") or user_data["slack_id"]
    role = await asyncio.to_thread(role_for, user['slack_id'])
    if role is None:
        raise HTTPException(status_code=403, detail="Airlock access required")
    user_data.pop('organization', None)
    user_data.pop('is_org_admin', None)
    user_data['role'] = role
    user_data['is_admin'] = role == 'admin'
    return {"status": "authenticated", "user": user_data}

@app.get("/api/v1/getSession")
async def get_session(repo_url: str, user: dict = Depends(get_current_user)):
    launch_id = uuid.uuid4().hex
    with launch_stage("authorization", launch_id):
        if not await asyncio.to_thread(is_authorized, user['slack_id']):
            raise HTTPException(status_code=403, detail="Airlock access required")

    async def process_stream():
        with launch_stage("stream_total", launch_id):
            try:
                # URL Validation & Normalization
                try:
                    normalized_url, warning = normalize_github_url(repo_url)
                    if warning:
                        yield f"[!] {warning}\n"
                    repo_url_final = normalized_url
                except ValueError as ve:
                    yield f"[!] Error: {str(ve)}\n"
                    return

                yield f"[*] Fetching GitHub data for {repo_url_final}...\n"
                try:
                    with launch_stage("github_metadata", launch_id):
                        context = await get_github_context_data(repo_url_final)
                except Exception as e:
                    yield f"[!] Error fetching GitHub data: {e}\n"
                    return

                yield "[*] Creating the sandbox while analyzing the project...\n"
                async with reserve_session(repo_url_final) as reservation:
                    logger.info("launch_link id=%s sandbox=%s", launch_id, reservation['name'])
                    # Pre-analysis
                    yield f"[*] Inspecting the project... ({AI_MODEL})\n"
                    try:
                        with launch_stage("inspection", launch_id):
                            pre_analysis = await inspect_common_project(context)
                            if pre_analysis is None:
                                pre_analysis = await pre_analyze_project(context)
                            else:
                                yield "[*] Using project manifests for inspection.\n"
                        tech_stack = pre_analysis.get('tech_stack', 'Unknown')
                        difficulty = pre_analysis.get('difficulty', 'easy')
                        files_to_read = pre_analysis.get('files_to_read', [])
                        preloaded = pre_analysis.get('contents', {})

                        yield f"\n[*] Detected Tech Stack: {tech_stack}\n"
                    except Exception as e:
                        yield f"[!] Pre-analysis failed: {e}\n"
                        if isinstance(e, AIRequestError) and e.status in {400, 401, 403, 404}:
                            return
                        # Fallback defaults
                        difficulty = 'easy'
                        files_to_read = []
                        tech_stack = 'Unknown'
                        preloaded = {}

                    # Fetch extra files
                    additional_content = ""
                    if files_to_read:
                        yield f"[*] Reading: {', '.join(files_to_read)}...\n"
                        file_contents = []
                        max_total_chars = 4000
                        max_per_file = max_total_chars // len(files_to_read) if files_to_read else 4000

                        with launch_stage("github_sources", launch_id):
                            async with httpx.AsyncClient() as client:
                                missing = [path for path in files_to_read if path not in preloaded]
                                fetched = await asyncio.gather(*(fetch_github_file_content(
                                    context['owner'], context['repo'], path, client) for path in missing))
                            loaded = {**preloaded, **dict(zip(missing, fetched))}
                            contents = [loaded[path] for path in files_to_read]
                        for fpath, content in zip(files_to_read, contents):
                            if content:
                                truncated = content[:max_per_file]
                                file_contents.append(f"File: {fpath}\nContent:\n{truncated}\n")

                        additional_content = "\n".join(file_contents)

                    # Determine model
                    model_id = AI_MODEL

                    yield f"[*] Analyzing with AI... ({model_id})\n"

                    try:
                        script = ""
                        html = ""
                        final_tech_stack = ""
                        summary = ""

                        with launch_stage("ai_generation", launch_id):
                            async for chunk in analyze_with_ai_data(context, additional_content, model_id):
                                if isinstance(chunk, str):
                                    yield chunk
                                elif isinstance(chunk, dict) and chunk['type'] == 'result':
                                    script, html, final_tech_stack, summary = chunk['data']

                        yield f"\n[*] AI Summary: {summary}\n"
                    except Exception as e:
                        yield f"[!] AI Analysis failed: {e}\n"
                        return

                    async for msg in create_vercel_session_generator(repo_url_final, script, html, user['slack_id'], reservation):
                        yield msg

                    reservation["delivered"] = True

            except Exception as e:
                yield f"\n[ERROR] {e}\n"

    return StreamingResponse(process_stream(), media_type="text/plain", headers={"Cache-Control": "no-store"})

@app.post("/api/v1/sessions/{name}/stop")
async def close_sandbox(name: str, user: dict = Depends(get_current_user)):
    if not re.fullmatch(r'airlock-[a-f0-9]{32}', name):
        raise HTTPException(status_code=404, detail="Session not found")
    ownership = await asyncio.to_thread(load_state, 'sessions/' + name + '.json', {})
    if ownership.get('owner') != user['slack_id']:
        raise HTTPException(status_code=404, detail="Session not found")
    await stop_session(name)
    await asyncio.to_thread(save_state, "sessions/" + name + ".json", {})
    return {"status": "stopped"}

# --- Admin API ---

@app.get("/api/v1/admin/sandboxes")
async def running_sandboxes(response: Response, admin: dict = Depends(get_admin_user)):
    response.headers['Cache-Control'] = 'no-store'
    try:
        sandboxes = await list_running_sessions()
    except (RuntimeError, asyncio.TimeoutError):
        raise HTTPException(status_code=503, detail='Could not load running sandboxes. Please retry.')
    async def describe(sandbox):
        name = sandbox['name']
        ownership = {}
        if re.fullmatch(r'airlock-[a-f0-9]{32}', name):
            ownership = await asyncio.to_thread(load_state, 'sessions/' + name + '.json', {})
        return {**sandbox, 'owner': ownership.get('owner'), 'repo_url': ownership.get('repo_url')}
    return await asyncio.gather(*(describe(sandbox) for sandbox in sandboxes))

@app.get("/api/v1/admin/users")
async def list_users(response: Response, admin: dict = Depends(get_admin_user)):
    response.headers['Cache-Control'] = 'no-store'
    return await asyncio.to_thread(load_access)


def change_person(actor, slack_id, role=None, person=None, remove=False):
    def change(people):
        if not any(p['slack_id'] == actor and p['role'] == 'admin' for p in people):
            raise HTTPException(status_code=403, detail='Admin access required')
        existing = next((p for p in people if p['slack_id'] == slack_id), None)
        if person is not None and existing:
            raise HTTPException(status_code=409, detail='This person already has access')
        if person is None and existing is None:
            raise HTTPException(status_code=404, detail='Person not found')
        if existing and existing['role'] == 'admin' and (remove or role == 'member'):
            if sum(p['role'] == 'admin' for p in people) == 1:
                raise HTTPException(status_code=409, detail='Keep at least one admin')
        updated = [dict(p) for p in people if not (remove and p['slack_id'] == slack_id)]
        if person is not None:
            updated.append(person)
        elif not remove:
            next(p for p in updated if p['slack_id'] == slack_id)['role'] = role
        return updated
    return update_access(change)

@app.post("/api/v1/admin/users", status_code=201)
async def add_user(person: User, admin: dict = Depends(get_admin_user)):
    await asyncio.to_thread(change_person, admin['slack_id'], person.slack_id, person=person.model_dump(exclude_none=True))
    return {'status': 'added', 'user': person}

@app.patch("/api/v1/admin/users/{slack_id}")
async def set_role(slack_id: str, update: RoleUpdate, admin: dict = Depends(get_admin_user)):
    await asyncio.to_thread(change_person, admin['slack_id'], slack_id, role=update.role)
    return {'status': 'updated', 'role': update.role}

@app.delete("/api/v1/admin/users/{slack_id}")
async def delete_user(slack_id: str, admin: dict = Depends(get_admin_user)):
    await asyncio.to_thread(change_person, admin['slack_id'], slack_id, remove=True)
    return {'status': 'deleted'}

@app.get("/api/v1/slack/profile/{slack_id}")
async def get_profile(slack_id: str, response: Response, user: dict = Depends(get_current_user)):
    response.headers["Cache-Control"] = "private, max-age=86400"
    return get_slack_profile(slack_id)

@app.get('/api/health')
async def health():
    """Readiness for the native Vercel runtime, without exposing private state."""
    proc = await asyncio.create_subprocess_exec(*vc_command(), '--version', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, _ = await asyncio.wait_for(proc.communicate(), 15)
    if proc.returncode:
        raise HTTPException(status_code=503, detail='Sandbox CLI unavailable')
    await asyncio.to_thread(load_access)
    return {'status':'ok', 'sandbox_cli':stdout.decode().strip(), 'ai_model':AI_MODEL, 'login_configured':bool(HC_CLIENT_ID and HC_CLIENT_SECRET)}
