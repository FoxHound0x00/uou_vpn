"""Headless U of U VPN login: CAS (uNID + password) -> Duo passcode -> openconnect.

Credentials come from the KeePassXC entry "UofU Duo" (UserName, Password, TOTP),
unlocked with the vault password stored in the GNOME keyring.

  uvpn [up]   connect          uvpn test   login only, don't connect
  uvpn down   disconnect and keep the watchdog off until the next `uvpn`
"""
import base64
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

GATEWAY = "vpn.utah.edu"
DB = os.path.expanduser(os.environ.get("TWOFA_DB", "~/Passwords.kdbx"))
ENTRY = os.environ.get("UVPN_ENTRY", "UofU Duo")
DEBUG = os.environ.get("UVPN_DEBUG")
OFF = Path.home() / ".cache/uvpn/off"  # present after `uvpn down`: watchdog stays quiet


def kp(*args):
    pw = subprocess.run(["secret-tool", "lookup", "service", "keepassxc-2fa"],
                        stdout=subprocess.PIPE, text=True, check=True).stdout
    return subprocess.run(["keepassxc-cli", "show", "-q", *args, DB, ENTRY],
                          input=pw, stdout=subprocess.PIPE, text=True, check=True).stdout.rstrip("\n")


def log(msg):
    print(f"[uvpn] {msg}", file=sys.stderr)


def saml_login():
    user, _, password = kp("-a", "UserName", "-a", "Password").partition("\n")
    if not user or not password:
        sys.exit(f'[uvpn] put your uNID and CIS password in the "{ENTRY}" KeePassXC entry')

    req = urllib.request.Request(
        f"https://{GATEWAY}/ssl-vpn/prelogin.esp?tmp=tmp&clientVer=4100&clientos=Windows", data=b"")
    xml = urllib.request.urlopen(req, timeout=20).read().decode()
    m = re.search(r"<saml-request>(.*?)</saml-request>", xml, re.S)
    if not m:
        sys.exit("[uvpn] VPN portal did not offer a SAML login")

    result = {}

    def grab(resp):
        if "prelogin-cookie" in resp.headers:
            result.update(resp.headers)

    with sync_playwright() as p:
        page = p.chromium.launch(headless=not DEBUG).new_page()
        page.on("response", grab)
        page.set_content(base64.b64decode(m.group(1)).decode())  # auto-submits the SAML request

        log("CAS login")
        page.wait_for_selector("#username", timeout=30000)
        page.fill("#username", user)
        page.fill("#password", password)
        page.click("#submitBtn")

        log("Duo: choosing passcode")
        page.get_by_text("Other options").first.click(timeout=45000)
        page.get_by_text("Duo Mobile passcode").first.click(timeout=20000)
        page.locator("input[name='passcode-input'], #passcode-input").first.fill(kp("-t"))
        page.get_by_role("button", name=re.compile("verify", re.I)).first.click()

        for _ in range(60):  # wait for the cookie; answer "Is this your device?" if asked
            if result:
                break
            trust = page.get_by_role("button", name=re.compile(r"^yes, ", re.I))
            if trust.count():
                trust.first.click()
            page.wait_for_timeout(500)

    if not result:
        sys.exit("[uvpn] no VPN cookie: Duo rejected the passcode or the page changed "
                 "(rerun with UVPN_DEBUG=1 to watch)")
    return result.get("saml-username", user), result["prelogin-cookie"]


def main():
    arg = sys.argv[1:]
    if arg == ["down"]:
        OFF.parent.mkdir(parents=True, exist_ok=True)
        OFF.touch()
        subprocess.run(["sudo", "pkill", "-INT", "openconnect"])
        return
    if arg not in ([], ["up"], ["test"]):
        sys.exit("usage: uvpn [up|down|test]")
    testing = arg == ["test"]
    if not testing:
        OFF.unlink(missing_ok=True)
        if subprocess.run(["pgrep", "-x", "openconnect"], stdout=subprocess.DEVNULL).returncode == 0:
            log("already connected (run `uvpn down` first to reconnect)")
            return

    user, cookie = saml_login()
    if testing:
        log(f"login OK as {user}, got VPN cookie (not connecting)")
        return
    if OFF.exists():
        sys.exit("[uvpn] `uvpn down` ran during login, not connecting")
    log(f"authenticated as {user}, starting openconnect")
    subprocess.run(["sudo", "/usr/local/sbin/uvpn-oc", user], input=cookie + "\n", text=True, check=True)


if __name__ == "__main__":
    main()
