#!/usr/bin/env bash
set -euo pipefail
repo_url=$1
repo_name=$2
HOME=/home/kasm-user
repo_dir="$HOME/Desktop/$repo_name"
cp /vercel/sandbox/airlock_install.sh /vercel/sandbox/REVIEW_GUIDE.html "$repo_dir/"
chown -R kasm-user:kasm-user "$repo_dir"
chmod +x "$repo_dir/airlock_install.sh"
sudo -H -u kasm-user env DISPLAY=:1 x-www-browser "file://$repo_dir/REVIEW_GUIDE.html" > /tmp/airlock-browser.log 2>&1 &
sudo -H -u kasm-user env DISPLAY=:1 x-www-browser "$repo_url" >> /tmp/airlock-browser.log 2>&1 &
sudo -H -u kasm-user env DISPLAY=:1 thunar "$repo_dir" > /tmp/airlock-thunar.log 2>&1 &
sudo -H -u kasm-user env DISPLAY=:1 xfce4-terminal --disable-server --working-directory="$repo_dir" -x bash -c 'source /etc/profile.d/airlock_env.sh; ls -lah; exec bash' > /tmp/airlock-terminal.log 2>&1 &
sudo -H -u kasm-user env DISPLAY=:1 xfce4-terminal --disable-server --working-directory="$repo_dir" -x bash -c 'source /etc/profile.d/airlock_env.sh; bash ./airlock_install.sh; exec bash' > /tmp/airlock-installer-terminal.log 2>&1 &
echo AIRLOCK_SESSION_READY
