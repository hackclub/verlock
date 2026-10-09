#!/usr/bin/env bash
set -euo pipefail
repo_url=$1
repo_name=$2
export HOME=/home/kasm-user USER=kasm-user DISPLAY=:1
export KASM_VNC_PATH=/usr/share/kasmvnc
# KasmVNC and xauth require a resolvable hostname; /etc/hosts is read-only.
hostname localhost
# Vercel boots the image filesystem without its Docker entrypoint.
if [[ ! -f "$HOME/.bashrc" ]]; then
    cp -a /home/kasm-default-profile/. "$HOME/"
fi
mkdir -p "$HOME/Desktop" "$HOME/Uploads" "$HOME/Downloads" /tmp/.X11-unix /var/run/pulse
chown kasm-user:kasm-user /var/run/pulse
chmod 1777 /tmp/.X11-unix
ln -sfn "$HOME/Uploads" "$HOME/Desktop/Uploads"
ln -sfn "$HOME/Downloads" "$HOME/Desktop/Downloads"
chmod 600 /vercel/sandbox/desktop-password /vercel/sandbox/desktop-token
chown kasm-user:kasm-user /vercel/sandbox/desktop-password
cat > /etc/profile.d/airlock_env.sh <<'ENV'
export STARTUPDIR=/dockerstartup
export BUN_INSTALL=/usr/local
export RUSTUP_HOME=/opt/rust
export CARGO_HOME=/opt/rust
export PATH="/opt/rust/bin:/usr/local/go/bin:$PATH"
ENV
chown -R kasm-user:kasm-user "$HOME"
# Old images still need uv; overlap that download and cloning with XFCE startup.
prepare_uv() {
    local started=$SECONDS
    if ! command -v uv > /dev/null; then
        curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
    fi
    echo "AIRLOCK_TIMING stage=uv_prepare seconds=$((SECONDS - started))"
}
clone_repository() {
    local started=$SECONDS
    sudo -H -u kasm-user git clone -- "$repo_url" "$HOME/Desktop/$repo_name"
    echo "AIRLOCK_TIMING stage=git_clone seconds=$((SECONDS - started))"
}
prepare_uv &
uv_pid=$!
clone_repository &
clone_pid=$!
trap 'kill "$uv_pid" "$clone_pid" 2>/dev/null || true' EXIT
desktop_started=$SECONDS
# The upstream startup script keeps the desktop, audio, and transfer services alive.
chmod 755 /vercel/sandbox/desktop-start.sh
nohup sudo -H -u kasm-user /vercel/sandbox/desktop-start.sh > /tmp/airlock-desktop.log 2>&1 < /dev/null &
python3 - <<'CONFIG'
from pathlib import Path
import base64
password = Path('/vercel/sandbox/desktop-password').read_text()
token = Path('/vercel/sandbox/desktop-token').read_text()
auth = base64.b64encode(('kasm_user:' + password).encode()).decode()
config = Path('/vercel/sandbox/nginx.conf').read_text().replace('__SESSION_TOKEN__', token).replace('__BASIC_AUTH__', auth)
path = Path('/tmp/airlock-nginx.conf')
path.write_text(config)
path.chmod(0o600)
CONFIG
nginx -c /tmp/airlock-nginx.conf
for i in $(seq 1 90); do
    if sudo -H -u kasm-user env DISPLAY=:1 xdpyinfo > /dev/null 2>&1 && pgrep -u kasm-user -x xfwm4 > /dev/null; then
        break
    fi
    if [[ "$i" == 90 ]]; then
        echo 'Desktop failed to start; inspect /tmp/airlock-desktop.log' >&2
        exit 1
    fi
    sleep 1
 done
echo "AIRLOCK_TIMING stage=xfce_start seconds=$((SECONDS - desktop_started))"
pkill -u kasm-user zenity || true
# The only intentional desktop appearance change is the wallpaper.
wallpaper_started=$SECONDS
for i in $(seq 1 30); do
    properties=$(sudo -H -u kasm-user env DISPLAY=:1 xfconf-query -c xfce4-desktop -l 2>/dev/null || true)
    if [[ "$properties" == *last-image* ]]; then
        while IFS= read -r property; do
            if [[ "$property" == */last-image ]]; then
                sudo -H -u kasm-user env DISPLAY=:1 xfconf-query -c xfce4-desktop -p "$property" -s /usr/share/backgrounds/bg_default.png
            fi
        done <<< "$properties"
        break
    fi
    sleep 1
 done
echo "AIRLOCK_TIMING stage=wallpaper seconds=$((SECONDS - wallpaper_started))"
# Verify authenticated upstream HTTP, rather than just an open TCP port.
vnc_started=$SECONDS
for i in $(seq 1 60); do
    status=$(curl -k -sS -u "kasm_user:$(cat /vercel/sandbox/desktop-password)" -o /dev/null -w '%{http_code}' https://localhost:6901/ || true)
    if [[ "$status" == 200 ]]; then
        echo "AIRLOCK_TIMING stage=vnc_ready seconds=$((SECONDS - vnc_started))"
        wait "$clone_pid"
        wait "$uv_pid"
        trap - EXIT
        echo AIRLOCK_DESKTOP_READY
        exit 0
    fi
    sleep 1
 done
echo 'KasmVNC did not become ready' >&2
exit 1
