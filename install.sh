#!/usr/bin/env bash
# Install uvpn for the current user (Ubuntu/Debian). Safe to rerun.
# Commands and units are symlinked to this repo, so `git pull` updates them.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
REPO=$PWD

echo "== system packages"
sudo apt-get install -y keepassxc libsecret-tools openconnect libnotify-bin

echo "== python venv + headless chromium"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r src/requirements.txt
.venv/bin/python -m playwright install chromium

echo "== commands -> ~/.local/bin"
mkdir -p ~/.local/bin ~/.config/systemd/user
ln -sf "$REPO"/src/bin/{uvpn,uvpn-watch,2fa} ~/.local/bin/

echo "== root wrapper + sudoers rule"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
sed "s/@USER@/$USER/g" src/system/uvpn-oc.in > "$tmp/uvpn-oc"
sed "s/@USER@/$USER/g" src/system/sudoers.in > "$tmp/sudoers"
/usr/sbin/visudo -cf "$tmp/sudoers"
sudo install -m 755 -o root -g root "$tmp/uvpn-oc" /usr/local/sbin/uvpn-oc
sudo install -m 440 -o root -g root "$tmp/sudoers" /etc/sudoers.d/uvpn

echo "== watchdog timer"
ln -sf "$REPO"/src/system/uvpn-watch.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now uvpn-watch.timer

echo "done. Next: set up the KeePassXC vault (README, step 2), then run: uvpn test"
