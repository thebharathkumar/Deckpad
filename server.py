#!/usr/bin/env python3
"""Deckpad: turn a spare Android phone into a button pad for Claude Code.

Runs on the Mac. The phone opens a web page served from here; each button
press is turned into keystrokes sent to the app Claude runs in (Terminal,
iTerm, the Claude desktop app...). No Anthropic API, no credits, no
dependencies beyond the Python standard library.

Security model (single user, home network):
  * A random token is created on first run (.deck_token, chmod 600). The phone
    pairs once via /pair?t=TOKEN and gets an HttpOnly, SameSite=Strict cookie.
  * Only private-network / Tailscale clients are accepted.
  * Every POST needs the custom header X-Deck: 1 (blocks cross-site forms).
  * The phone can only trigger keystroke macros and type text. There is no
    endpoint that runs shell commands.
  * The hook endpoint (Claude -> phone notifications) only accepts 127.0.0.1.
  * Repeated bad tokens from an IP lock it out for 10 minutes.
"""

import hmac
import ipaddress
import json
import os
import queue
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
CONFIG_PATH = ROOT / "config.json"
DEFAULT_CONFIG_PATH = ROOT / "config.default.json"
TOKEN_PATH = ROOT / ".deck_token"
PORT = int(os.environ.get("DECK_PORT", "8765"))
DRY_RUN = os.environ.get("DECK_DRY_RUN") == "1"  # log keystrokes instead of sending

MAX_BODY = 64 * 1024
MAX_TEXT = 4000
APP_NAME_RE = re.compile(r"^[A-Za-z0-9 ._-]{1,60}$")

# macOS virtual key codes for the named keys a macro may use.
KEY_CODES = {
    "enter": 36, "return": 36, "tab": 48, "space": 49, "backspace": 51,
    "delete": 51, "esc": 53, "escape": 53, "left": 123, "right": 124,
    "down": 125, "up": 126,
}
MODIFIERS = {"shift": "shift down", "ctrl": "control down", "cmd": "command down",
             "alt": "option down", "option": "option down"}

# ---------------------------------------------------------------- token / auth

def load_token():
    if TOKEN_PATH.exists():
        return TOKEN_PATH.read_text().strip()
    token = secrets.token_urlsafe(24)
    TOKEN_PATH.write_text(token)
    os.chmod(TOKEN_PATH, 0o600)
    return token


TOKEN = load_token()
_failures = {}  # ip -> (count, first_failure_time)
_fail_lock = threading.Lock()


def locked_out(ip):
    with _fail_lock:
        count, since = _failures.get(ip, (0, 0))
        if time.time() - since > 600:
            _failures.pop(ip, None)
            return False
        return count >= 8


def record_failure(ip):
    with _fail_lock:
        count, since = _failures.get(ip, (0, time.time()))
        _failures[ip] = (count + 1, since)


def allowed_client(ip):
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if addr.version == 6 and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    tailscale = ipaddress.ip_network("100.64.0.0/10")
    return addr.is_private or addr.is_loopback or (addr.version == 4 and addr in tailscale)

# ---------------------------------------------------------------- config

_config_lock = threading.Lock()


def load_config():
    path = CONFIG_PATH if CONFIG_PATH.exists() else DEFAULT_CONFIG_PATH
    return json.loads(path.read_text())


def validate_config(cfg):
    if not isinstance(cfg, dict):
        raise ValueError("config must be an object")
    app = cfg.get("targetApp", "")
    if app and not APP_NAME_RE.match(app):
        raise ValueError("targetApp has invalid characters")
    buttons = cfg.get("buttons")
    if not isinstance(buttons, list) or len(buttons) > 60:
        raise ValueError("buttons must be a list (max 60)")
    seen = set()
    for b in buttons:
        if not isinstance(b, dict):
            raise ValueError("each button must be an object")
        for field in ("id", "label", "macro"):
            if not isinstance(b.get(field), str) or not b[field]:
                raise ValueError(f"button missing {field}")
        if b["id"] in seen:
            raise ValueError(f"duplicate id {b['id']}")
        seen.add(b["id"])
        if len(b["label"]) > 40 or len(b["macro"]) > 500:
            raise ValueError("label or macro too long")
        if not re.match(r"^[a-z-]{0,24}$", b.get("icon", "")):
            raise ValueError("icon name is invalid")
        parse_macro(b["macro"])  # raises on unknown keys
    return {"targetApp": app, "buttons": buttons}

# ---------------------------------------------------------------- keystrokes

TOKEN_RE = re.compile(r"\{([a-z+]+)\}")


def parse_macro(macro):
    """'/model opus{enter}' -> [('text','/model opus'), ('key',36,[])]"""
    steps, pos = [], 0
    for m in TOKEN_RE.finditer(macro):
        if m.start() > pos:
            steps.append(("text", macro[pos:m.start()]))
        parts = m.group(1).split("+")
        key, mods = parts[-1], parts[:-1]
        if key == "wait":
            steps.append(("wait",))
        elif key in KEY_CODES and all(x in MODIFIERS for x in mods):
            steps.append(("key", KEY_CODES[key], [MODIFIERS[x] for x in mods]))
        elif len(key) == 1 and mods and all(x in MODIFIERS for x in mods):
            steps.append(("char", key, [MODIFIERS[x] for x in mods]))  # e.g. {ctrl+c}
        else:
            raise ValueError(f"unknown key {{{m.group(1)}}}")
        pos = m.end()
    if pos < len(macro):
        steps.append(("text", macro[pos:]))
    return steps


_send_lock = threading.Lock()


def osascript(lines, args):
    # Text and app names are passed as argv, never spliced into the script.
    cmd = ["osascript"]
    for line in lines:
        cmd += ["-e", line]
    cmd += ["--"] + [str(a) for a in args]
    subprocess.run(cmd, check=True, capture_output=True, timeout=10)


def send_steps(steps, app):
    if DRY_RUN:
        print(f"[dry-run] -> {app or 'frontmost'}: {steps}", flush=True)
        return
    with _send_lock:
        if app:
            osascript(["on run argv", "tell application (item 1 of argv) to activate",
                       "end run"], [app])
            time.sleep(0.15)
        for step in steps:
            if step[0] == "text":
                osascript(["on run argv",
                           'tell application "System Events" to keystroke (item 1 of argv)',
                           "end run"], [step[1]])
            elif step[0] == "key":
                using = f" using {{{', '.join(step[2])}}}" if step[2] else ""
                osascript([f'tell application "System Events" to key code {step[1]}{using}'], [])
            elif step[0] == "char":
                using = f" using {{{', '.join(step[2])}}}"
                osascript(["on run argv",
                           f'tell application "System Events" to keystroke (item 1 of argv){using}',
                           "end run"], [step[1]])
            elif step[0] == "wait":
                time.sleep(0.4)
            time.sleep(0.05)

# ---------------------------------------------------------------- events (hooks -> phone)

_subscribers = set()
_sub_lock = threading.Lock()
_last_event = None


def publish(event):
    global _last_event
    _last_event = event
    with _sub_lock:
        for q in list(_subscribers):
            try:
                q.put_nowait(event)
            except queue.Full:
                pass


def summarize_hook(payload):
    name = payload.get("hook_event_name", "Event")
    project = Path(payload.get("cwd") or "").name or "Claude"
    if name == "Notification":
        msg = payload.get("message") or "Claude needs your attention"
        kind = "permission" if "permission" in msg.lower() else "attention"
    elif name == "Stop":
        msg, kind = "Finished — waiting for you", "done"
    elif name == "PreToolUse":
        msg, kind = f"Running {payload.get('tool_name', 'a tool')}", "working"
    elif name == "UserPromptSubmit":
        msg, kind = "Working…", "working"
    else:
        msg, kind = name, "info"
    return {"kind": kind, "message": str(msg)[:300], "project": project[:60],
            "time": int(time.time())}

# ---------------------------------------------------------------- http

CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
                 ".css": "text/css", ".json": "application/json",
                 ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml",
                 ".png": "image/png"}


class Handler(BaseHTTPRequestHandler):
    server_version = "Deckpad"
    sys_version = ""

    def log_message(self, fmt, *args):
        if "/api/events" not in (args[0] if args else ""):
            sys.stderr.write(f"{self.client_address[0]} {fmt % args}\n")

    # -- helpers
    def ip(self):
        return self.client_address[0]

    def send(self, code, body=b"", ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def cookie_token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return ""
        c = SimpleCookie()
        try:
            c.load(raw)
        except Exception:
            return ""
        return c["deck"].value if "deck" in c else ""

    def authed(self):
        return hmac.compare_digest(self.cookie_token().encode(), TOKEN.encode())

    def guard(self, need_auth=True):
        ip = self.ip()
        if not allowed_client(ip):
            self.send(403, {"error": "network not allowed"})
            return False
        if locked_out(ip):
            self.send(429, {"error": "too many attempts, wait 10 minutes"})
            return False
        if need_auth and not self.authed():
            if self.cookie_token():
                record_failure(ip)
            self.send(401, {"error": "not paired"})
            return False
        return True

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(length) or b"{}")

    # -- GET
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/pair":
            return self.pair(url)
        if url.path == "/api/events":
            return self.events()
        if url.path == "/api/config":
            if self.guard():
                with _config_lock:
                    self.send(200, load_config())
            return
        if url.path == "/defaults.json":
            if self.guard():
                self.send(200, DEFAULT_CONFIG_PATH.read_bytes())
            return
        if url.path in ("/", "/index.html"):
            if not self.guard(need_auth=False):
                return
            name = "index.html" if self.authed() else "locked.html"
            return self.static(name)
        # Static assets (css/js/icons) are not secret; the page itself is.
        if self.guard(need_auth=False):
            self.static(url.path.lstrip("/"))

    do_HEAD = do_GET

    def static(self, name):
        path = (STATIC / name).resolve()
        if STATIC not in path.parents or not path.is_file():
            return self.send(404, {"error": "not found"})
        self.send(200, path.read_bytes(), CONTENT_TYPES.get(path.suffix, "application/octet-stream"))

    def pair(self, url):
        if not self.guard(need_auth=False):
            return
        given = parse_qs(url.query).get("t", [""])[0]
        if not hmac.compare_digest(given.encode(), TOKEN.encode()):
            record_failure(self.ip())
            return self.send(401, "Wrong pairing link.", "text/plain")
        cookie = (f"deck={TOKEN}; HttpOnly; SameSite=Strict; Path=/; "
                  f"Max-Age={60 * 60 * 24 * 365}")
        self.send(303, b"", "text/plain", {"Set-Cookie": cookie, "Location": "/"})

    def events(self):
        if not self.guard():
            return
        q = queue.Queue(maxsize=50)
        with _sub_lock:
            _subscribers.add(q)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if _last_event:
                self.wfile.write(f"data: {json.dumps(_last_event)}\n\n".encode())
                self.wfile.flush()
            while True:
                try:
                    ev = q.get(timeout=20)
                    self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            with _sub_lock:
                _subscribers.discard(q)

    # -- POST / PUT
    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/hook":
            return self.hook()
        if not self.guard():
            return
        if self.headers.get("X-Deck") != "1":
            return self.send(403, {"error": "missing X-Deck header"})
        try:
            body = self.read_json()
            with _config_lock:
                cfg = load_config()
            if path == "/api/press":
                btn = next((b for b in cfg["buttons"] if b["id"] == body.get("id")), None)
                if not btn:
                    return self.send(404, {"error": "unknown button"})
                send_steps(parse_macro(btn["macro"]), cfg.get("targetApp"))
                return self.send(200, {"ok": True})
            if path == "/api/type":
                text = body.get("text", "")
                if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
                    return self.send(400, {"error": "text empty or too long"})
                # Typed text is sent literally; newlines become spaces so a
                # dictation can't submit half a prompt early.
                steps = [("text", " ".join(text.split()))]
                if body.get("enter", True):
                    steps.append(("key", KEY_CODES["enter"], []))
                send_steps(steps, cfg.get("targetApp"))
                return self.send(200, {"ok": True})
            if path == "/api/config":
                new = validate_config(body)
                with _config_lock:
                    tmp = CONFIG_PATH.with_suffix(".tmp")
                    tmp.write_text(json.dumps(new, indent=2))
                    tmp.replace(CONFIG_PATH)
                return self.send(200, {"ok": True})
            self.send(404, {"error": "not found"})
        except ValueError as e:
            self.send(400, {"error": str(e)})
        except subprocess.CalledProcessError as e:
            err = (e.stderr or b"").decode(errors="replace")
            hint = ("macOS blocked keystrokes: allow your terminal in System Settings → "
                    "Privacy & Security → Accessibility" if "not allowed" in err or "1002" in err
                    else err.strip()[:200])
            self.send(500, {"error": hint})

    def hook(self):
        ip = self.ip()
        if ip not in ("127.0.0.1", "::1"):
            return self.send(403, {"error": "local only"})
        if not hmac.compare_digest((self.headers.get("X-Deck-Token") or "").encode(),
                                   TOKEN.encode()):
            return self.send(401, {"error": "bad token"})
        try:
            publish(summarize_hook(self.read_json()))
        except ValueError:
            pass
        self.send(200, {"ok": True})


def lan_ip():
    try:
        out = subprocess.run(["ipconfig", "getifaddr", "en0"], capture_output=True,
                             text=True, timeout=3).stdout.strip()
        return out or "127.0.0.1"
    except Exception:
        return "127.0.0.1"


def find_adb():
    return shutil.which("adb") or next(
        (p for p in ("/opt/homebrew/bin/adb", str(Path.home() / "Library/Android/sdk/platform-tools/adb"))
         if Path(p).exists()), None)


def usb_watcher(adb):
    """Phone plugged in over USB -> tunnel the port and open the deck on it.

    `adb reverse` makes the phone's localhost:PORT reach this Mac, so the deck
    works without Wi-Fi, and Chrome treats localhost as secure (mic + wake lock).
    """
    ready = set()
    while True:
        try:
            out = subprocess.run([adb, "devices"], capture_output=True, text=True,
                                 timeout=10).stdout
            online = {ln.split()[0] for ln in out.splitlines()[1:]
                      if ln.strip().endswith("\tdevice")}
            for serial in online - ready:
                subprocess.run([adb, "-s", serial, "reverse", f"tcp:{PORT}", f"tcp:{PORT}"],
                               check=True, capture_output=True, timeout=10)
                subprocess.run([adb, "-s", serial, "shell", "am", "start", "-a",
                                "android.intent.action.VIEW", "-d",
                                f"http://localhost:{PORT}/pair?t={TOKEN}"],
                               capture_output=True, timeout=10)
                print(f"  USB phone connected ({serial}) — deck opened on it.", flush=True)
            ready = online  # unplugged phones drop out and get set up again on replug
        except Exception as e:
            print(f"  USB: {e}", flush=True)
        time.sleep(3)


HOOK_EVENTS = ("Notification", "Stop", "UserPromptSubmit")


def edit_hooks(install):
    """Add (or remove) Deckpad's hook in ~/.claude/settings.json, keeping a backup."""
    path = Path.home() / ".claude" / "settings.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    cmd = shlex.quote(str(ROOT / "hooks" / "deck-hook.sh"))
    hooks = data.setdefault("hooks", {})
    for event in HOOK_EVENTS:
        entries = [e for e in hooks.get(event, [])
                   if not any(h.get("command") == cmd for h in e.get("hooks", []))]
        if install:
            entries.append({"hooks": [{"type": "command", "command": cmd}]})
        if entries:
            hooks[event] = entries
        else:
            hooks.pop(event, None)
    if not hooks:
        data.pop("hooks")
    if path.exists():
        shutil.copy2(path, path.with_name("settings.json.deckpad-backup"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"{'Added' if install else 'Removed'} Deckpad hooks in {path}"
          + (" (backup: settings.json.deckpad-backup)" if path.with_name(
              "settings.json.deckpad-backup").exists() else ""))


def main():
    if "--setup" in sys.argv or "--uninstall" in sys.argv:
        return edit_hooks(install="--setup" in sys.argv)
    if "--link" in sys.argv:
        print(f"Wi-Fi: http://{lan_ip()}:{PORT}/pair?t={TOKEN}")
        print(f"USB:   http://localhost:{PORT}/pair?t={TOKEN}")
        return
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(DEFAULT_CONFIG_PATH.read_text())
    usb_only = os.environ.get("DECK_USB_ONLY") == "1"
    server = ThreadingHTTPServer(("127.0.0.1" if usb_only else "0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print("\n  Deckpad is running.")
    adb = find_adb()
    if adb:
        threading.Thread(target=usb_watcher, args=(adb,), daemon=True).start()
        print("  USB: plug in the phone (USB debugging on) and the deck opens by itself.")
    if not usb_only:
        print(f"  Wi-Fi: open once on the phone:\n\n"
              f"    http://{lan_ip()}:{PORT}/pair?t={TOKEN}\n")
        print("  Keep that link private — it is the password.")
    if DRY_RUN:
        print("  DRY RUN: keystrokes are printed, not sent.")
    print("  Ctrl+C to stop.\n", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
