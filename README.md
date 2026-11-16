# uvpn

Hands-free University of Utah VPN on Linux. One command logs in through CAS and Duo with no phone, starts the GlobalProtect tunnel, and a small watchdog keeps it up across Wi-Fi changes, suspend and reboots.

```
$ uvpn
[uvpn] CAS login
[uvpn] Duo: choosing passcode
[uvpn] authenticated as ad\u0000000, starting openconnect
```

It is built for the U's setup (`vpn.utah.edu`, GlobalProtect with SAML, CAS and Duo), but the same pattern works for any GlobalProtect portal behind SAML and Duo. See [Adapting it](#adapting-it-to-another-organization).

## How it works

```
uvpn-watch (every minute, systemd timer)
  └─ tunnel healthy? one ping through tun0, done
  └─ dead for 2 checks → uvpn
        ├─ keepassxc-cli  →  uNID, CIS password, TOTP code   (vault unlocked via GNOME keyring)
        ├─ headless Chromium (Playwright)
        │     vpn.utah.edu SAML → go.utah.edu CAS → Duo "Duo Mobile passcode"
        │     └─ captures the GlobalProtect prelogin-cookie
        └─ sudo uvpn-oc  →  openconnect --protocol=gp (background, HIP report included)
```

Duo normally only works with the Duo Mobile app. [unduo](https://unduo.me) registers an extra Duo device that produces standard TOTP codes, so the code can come from KeePassXC instead of your phone. Your phone keeps working as before.

### Files

| File | Role |
|---|---|
| `src/uvpn.py` | Login (CAS + Duo in headless Chromium) and connect |
| `src/bin/uvpn` | Launcher. Runs `uvpn.py` in the venv, one login at a time |
| `src/bin/uvpn-watch` | Watchdog. One ping when healthy, a full login only when the tunnel is really dead |
| `src/bin/2fa` | Prints the current TOTP code for any KeePassXC entry |
| `src/system/uvpn-oc.in` | Root-owned openconnect wrapper with fixed arguments |
| `src/system/sudoers.in` | Sudo rule allowing only that wrapper and `pkill -INT openconnect` |
| `src/system/uvpn-watch.*` | User service and timer that run the watchdog every minute |
| `install.sh` | Installs everything for the current user |

## Requirements

- Ubuntu or Debian (tested on Ubuntu 22.04), with a desktop session and GNOME keyring
- Python 3.9 or newer
- A Duo-protected uNID, and permission from your organization to use a third-party authenticator

## Setup

### 1. Install

```
git clone https://github.com/<you>/uvpn.git ~/Documents/projects/uvpn
cd ~/Documents/projects/uvpn
./install.sh
```

The installer asks for your sudo password once. It installs `keepassxc`, `libsecret-tools`, `openconnect` and `libnotify-bin`, creates `.venv` with Playwright and headless Chromium (about 130 MB), symlinks the commands into `~/.local/bin` and the units into `~/.config/systemd/user`, installs the root wrapper and sudo rule, and enables the timer. Rerunning it is safe.

### 2. Create the KeePassXC vault

1. Open KeePassXC, choose **Database → New Database**, and save it as `~/Passwords.kdbx` (or anywhere, then set `TWOFA_DB`).
2. Store the vault password in your login keyring so scripts can unlock it without a prompt.

   ```
   secret-tool store --label="KeePassXC 2FA vault" service keepassxc-2fa
   ```

   Type the vault's master password at the hidden prompt.

### 3. Get a TOTP secret for Duo with unduo

1. Go to [unduo.me](https://unduo.me), choose your school (or **Other**), and open the downloaded `UnDuo - .html` file.
2. Sign in to anything that uses your uNID. At the Duo prompt, choose **Other options → Manage devices** and approve as usual.
3. Choose **Add a device → Tablet → Android → I have Duo Mobile installed**. Right-click the QR code, choose **Copy Image**, and paste it into the unduo page.
4. Follow unduo's activation step. On the last step it shows a QR code and a **Setup key**.
5. Scan the QR code with your phone's authenticator too, so you keep a backup.

Keep your existing Duo device registered. Each Duo QR code works once, so if unduo says it expired, make a new one.

### 4. Add the KeePassXC entry

1. Create an entry titled exactly `UofU Duo`.
2. Set **Username** to your uNID and **Password** to your CIS password.
3. Right-click the entry, choose **TOTP → Set up TOTP**, paste unduo's setup key (spaces removed), keep the default settings (30 s, 6 digits, SHA-1), and save the vault with Ctrl+S.

Check it with `2fa "UofU Duo"`. The code should match your phone.

### 5. Test, then connect

```
uvpn test     # full CAS + Duo login, no tunnel
uvpn          # connect
```

The watchdog is already running, so from now on the VPN comes back by itself.

## Usage

| Command | What it does |
|---|---|
| `uvpn` or `uvpn up` | Log in and connect. Says so and exits if already connected |
| `uvpn down` | Disconnect and keep the watchdog off until the next `uvpn` |
| `uvpn test` | Log in only, to check credentials and the Duo flow |
| `2fa <entry>` | Print the current code for any KeePassXC entry. `2fa --list` lists entries |
| `UVPN_DEBUG=1 uvpn test` | Show the browser window to watch the login |
| `journalctl --user -u uvpn-watch -f` | Follow what the watchdog does |
| `systemctl --user disable --now uvpn-watch.timer` | Turn the watchdog off for good |

Settings come from environment variables. `TWOFA_DB` sets the vault path (default `~/Passwords.kdbx`) and `UVPN_ENTRY` sets the entry name (default `UofU Duo`).

## What the watchdog does

Every minute it runs these checks in order and stops at the first one that applies.

1. `uvpn down` was run. Do nothing.
2. A login is already running. Do nothing.
3. A ping to the U's DNS (155.98.58.10) through `tun0` works. Healthy, do nothing.
4. First failed check. Wait a minute, because openconnect often recovers from a short blip by itself.
5. Second failed check in a row. Stop the dead openconnect.
6. `vpn.utah.edu` is unreachable (offline, or a captive portal not yet accepted). Wait.
7. A login failed in the last 5 minutes. Wait, so repeated failures can't lock your Duo account.
8. Otherwise run `uvpn`. If it fails, show a desktop notification.

A healthy check costs about 0.4 s and 3.5 MB. Python and Chromium only start when a new login is needed. After a reboot the timer starts when you log in to the desktop, because the vault password lives in the login keyring.

On guest Wi-Fi with a captive portal (hotel, hospital, airport), accept the portal's terms in your browser and the VPN returns within a couple of minutes.

## Security notes

- **Secrets stay off the command line.** The vault password and the VPN cookie are passed over stdin, and the CIS password is typed into the page by the browser. Nothing secret appears in `ps` output or in logs.
- **The vault is only as safe as your desktop login.** Anything running as your user while you are logged in can ask the keyring for the vault password. This is the same trade-off as any password manager that auto-unlocks.
- **Passwordless sudo is narrow.** The sudo rule allows only `/usr/local/sbin/uvpn-oc`, a root-owned script that runs openconnect with fixed arguments and rejects any unexpected username, plus `pkill -INT openconnect`. Allowing raw `openconnect` would let any program running as you become root through `--script`.
- **The Duo secret is a second factor.** Anyone with it and your password can sign in as you. Keep it only in KeePassXC and your phone, and never paste it into chats, websites or dotfiles. If it leaks, delete that device in Duo's **Manage devices** page and run unduo again.
- **Policy.** Check that your organization allows third-party authenticators and automated logins before using this.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `put your uNID and CIS password in the "UofU Duo" KeePassXC entry` | Fill in the entry's Username and Password and save the vault |
| `secret-tool` or `keepassxc-cli` errors | Rerun step 2. Check the vault path (`TWOFA_DB`) and the entry title |
| `no VPN cookie: Duo rejected the passcode or the page changed` | Compare `2fa "UofU Duo"` with your phone, and check the system clock. Then run `UVPN_DEBUG=1 uvpn test` to watch where it stops |
| `uvpn: a login is already running` | Wait about 30 s and try again |
| `already connected` but nothing works | `uvpn down` then `uvpn` |
| `sudo: a password is required` | Rerun `./install.sh` to reinstall the sudo rule |
| `sudo: unable to resolve host <name>` | Harmless. Add `127.0.1.1 <hostname>` to `/etc/hosts` to silence it |
| Conflicts with the GlobalProtect GUI (`gpclient`) | Quit it and remove it from startup apps. Use only one client |

## Adapting it to another organization

- **Gateway.** Change `GATEWAY` in `src/uvpn.py` and the hostname at the end of `root/uvpn-oc.in`.
- **Login page.** The CAS selectors (`#username`, `#password`, `#submitBtn`) are in `saml_login()`. Run `UVPN_DEBUG=1 uvpn test` and inspect your login page to find yours.
- **Duo.** The Universal Prompt steps (**Other options → Duo Mobile passcode → Verify**) are standard and should work unchanged.
- **Health probe.** Change `155.98.58.10` in `src/bin/uvpn-watch` to any host that answers ping only through your VPN.
- **Interface.** The tunnel is pinned to `tun0` in `src/system/uvpn-oc.in`, and the watchdog pings through it.

## Uninstall

```
systemctl --user disable --now uvpn-watch.timer
rm ~/.config/systemd/user/uvpn-watch.{service,timer} ~/.local/bin/{uvpn,uvpn-watch,2fa}
sudo rm /usr/local/sbin/uvpn-oc /etc/sudoers.d/uvpn
rm -rf ~/.cache/uvpn
```

Then remove the extra device in Duo's **Manage devices** page.

## Credits

- [unduo](https://github.com/UnDuoMe/unduo.me) for turning a Duo activation into a standard TOTP secret
- [OpenConnect](https://www.infradead.org/openconnect/) for the GlobalProtect client
- [gp-saml-gui](https://github.com/dlenski/gp-saml-gui) for documenting the SAML prelogin-cookie flow
