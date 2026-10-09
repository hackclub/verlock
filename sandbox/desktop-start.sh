#!/usr/bin/env bash
set -euo pipefail
source /opt/airlock/image-env.sh
export HOME=/home/kasm-user USER=kasm-user DISPLAY=:1
export STARTUPDIR=/dockerstartup
export VNC_PW
VNC_PW=$(cat /vercel/sandbox/desktop-password)
export VNC_RESOLUTION=1920x1080 VNC_COL_DEPTH=24 NO_VNC_PORT=6901
export KASM_VNC_PATH=/usr/share/kasmvnc
export MAX_FRAME_RATE=60
export START_XFCE4=1 START_DE=xfce4-session
export START_PULSEAUDIO=1
export KASMVNC_AUTO_RECOVER=true
cd "$HOME"
sed 's/hostname -i/echo 127.0.0.1/g' /dockerstartup/vnc_startup.sh > /tmp/airlock-vnc-startup.sh
exec bash /tmp/airlock-vnc-startup.sh --wait
