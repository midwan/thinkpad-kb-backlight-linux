#!/usr/bin/env python3
"""
tp-kbd-backlight: idle-off + wake-on-activity for the ThinkPad keyboard
backlight on Linux.

Idle detection uses the GNOME Mutter IdleMonitor DBus interface
(org.gnome.Mutter.IdleMonitor on /org/gnome/Mutter/IdleMonitor/Core),
which reflects real input activity under both X11 and Wayland on GNOME.

Backlight control tries, in order:
  1) Direct write to /sys/class/leds/tpacpi::kbd_backlight/brightness
  2) `brightnessctl --device=tpacpi::kbd_backlight set N`

Config: ~/.config/tp-kbd-backlight/config.json
"""

import argparse
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


CONFIG_DIR = Path.home() / ".config" / "tp-kbd-backlight"
CONFIG_PATH = CONFIG_DIR / "config.json"
LED_DIR = Path("/sys/class/leds/tpacpi::kbd_backlight")

DEFAULT_CONFIG = {
    "TimeoutSeconds": 30,
    "OnLevel": 2,
    "OffLevel": 0,
    "Paused": False,
    "RestorePreviousLevel": True,
}


def load_config():
    if not CONFIG_PATH.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH) as f:
            raw = json.load(f)
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(raw or {})
        cfg["TimeoutSeconds"] = max(3, int(cfg["TimeoutSeconds"]))
        cfg["OnLevel"] = max(0, min(2, int(cfg["OnLevel"])))
        cfg["OffLevel"] = max(0, min(2, int(cfg["OffLevel"])))
        cfg["Paused"] = bool(cfg["Paused"])
        cfg["RestorePreviousLevel"] = bool(cfg["RestorePreviousLevel"])
        return cfg
    except Exception as e:
        print(f"warn: could not parse {CONFIG_PATH}: {e}; using defaults",
              file=sys.stderr)
        return dict(DEFAULT_CONFIG)


def save_config(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")


def read_level():
    try:
        with open(LED_DIR / "brightness") as f:
            return int(f.read().strip())
    except Exception:
        return None


def read_max_level():
    try:
        with open(LED_DIR / "max_brightness") as f:
            return int(f.read().strip())
    except Exception:
        return None


def set_level(level):
    """Returns (ok, method, error)."""
    try:
        with open(LED_DIR / "brightness", "w") as f:
            f.write(str(level))
        return True, "sysfs", None
    except Exception as sysfs_err:
        pass

    if shutil.which("brightnessctl"):
        try:
            r = subprocess.run(
                ["brightnessctl", "--device=tpacpi::kbd_backlight",
                 "set", str(level)],
                capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                return True, "brightnessctl", None
            return False, "brightnessctl", (r.stderr or r.stdout).strip()
        except Exception as e:
            return False, "brightnessctl", f"{e}; sysfs: {sysfs_err}"

    return False, None, f"sysfs: {sysfs_err}; brightnessctl not installed"


# ---------------- diagnostics ----------------

def _safe_read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception as e:
        return f"<error: {e}>"


def _cmd(*args):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=5)
        out = (r.stdout or "").strip()
        err = (r.stderr or "").strip()
        return out if out else (err if err else "<no output>")
    except FileNotFoundError:
        return f"<{args[0]} not installed>"
    except Exception as e:
        return f"<error: {e}>"


def run_diagnose(cycle=True):
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    desktop = Path.home() / "Desktop"
    out_dir = desktop if desktop.is_dir() else Path.home()
    out_path = out_dir / f"tp-kbd-backlight-diagnostic-{ts}.txt"

    lines = []
    def add(s=""):
        lines.append(s)

    add("tp-kbd-backlight diagnostic report")
    add(f"generated: {datetime.now().isoformat(timespec='seconds')}")
    add("=" * 60)

    add("\n[system]")
    add(f"python     : {sys.version.split()[0]}")
    add(f"platform   : {platform.platform()}")
    add(f"kernel     : {_safe_read('/proc/version')[:200]}")
    try:
        with open("/etc/os-release") as f:
            add("os-release :")
            for line in f:
                add(f"  {line.rstrip()}")
    except Exception as e:
        add(f"os-release : <error: {e}>")

    add("\n[hardware]")
    for f in ("sys_vendor", "product_name", "product_version",
              "bios_vendor", "bios_version", "bios_date"):
        add(f"  {f:17s}: {_safe_read(f'/sys/class/dmi/id/{f}')}")

    add("\n[session]")
    for var in ("XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "DESKTOP_SESSION",
                "WAYLAND_DISPLAY", "DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
        add(f"  {var:27s}: {os.environ.get(var, '<unset>')}")

    add("\n[led sysfs]")
    add(f"  path             : {LED_DIR}")
    add(f"  exists           : {LED_DIR.is_dir()}")
    if LED_DIR.is_dir():
        brightness = LED_DIR / "brightness"
        try:
            st = brightness.stat()
            import stat as _s
            add(f"  brightness mode  : {oct(_s.S_IMODE(st.st_mode))}")
            add(f"  brightness owner : uid={st.st_uid} gid={st.st_gid}")
        except Exception as e:
            add(f"  brightness stat  : <error: {e}>")
        add(f"  current          : {_safe_read(brightness)}")
        add(f"  max              : {_safe_read(LED_DIR / 'max_brightness')}")
        add(f"  writable (euid)  : {os.access(brightness, os.W_OK)}")
    add("\n  all leds:")
    try:
        for entry in sorted(Path("/sys/class/leds").iterdir()):
            add(f"    {entry.name}")
    except Exception as e:
        add(f"    <error: {e}>")

    add("\n[tools]")
    add(f"  brightnessctl : {shutil.which('brightnessctl') or '<not found>'}")
    if shutil.which("brightnessctl"):
        add(f"  version       : {_cmd('brightnessctl', '--version').splitlines()[0] if _cmd('brightnessctl', '--version') else ''}")
        add(f"  info (kbd)    :")
        for line in _cmd("brightnessctl", "--device=tpacpi::kbd_backlight",
                         "info").splitlines():
            add(f"    {line}")
    add(f"  in video grp  : {'video' in _cmd('id', '-Gn').split()}")
    add(f"  groups        : {_cmd('id', '-Gn')}")

    add("\n[dbus: mutter idlemonitor]")
    try:
        import dbus
        bus = dbus.SessionBus()
        try:
            proxy = bus.get_object("org.gnome.Mutter.IdleMonitor",
                                   "/org/gnome/Mutter/IdleMonitor/Core")
            iface = dbus.Interface(proxy, "org.gnome.Mutter.IdleMonitor")
            idle_ms = int(iface.GetIdletime())
            add(f"  reachable   : yes")
            add(f"  idle_time_ms: {idle_ms}")
        except dbus.DBusException as e:
            add(f"  reachable   : NO ({e.get_dbus_name()}: {e.get_dbus_message()})")
    except ImportError as e:
        add(f"  dbus import : FAILED ({e}) — install python3-dbus")

    add("\n[cycle test]")
    if cycle:
        before = read_level()
        add(f"  before level : {before}")
        for lvl in (0, 1, 2, 0):
            ok, method, err = set_level(lvl)
            add(f"  set {lvl} -> {'OK' if ok else 'FAIL'} via {method}"
                + (f" ({err})" if err else ""))
            time.sleep(1.5)
        if before is not None:
            set_level(before)
            add(f"  restored     : {before}")
    else:
        add("  (skipped)")

    add("\n[config]")
    add(f"  path : {CONFIG_PATH}")
    add(f"  exists: {CONFIG_PATH.exists()}")
    if CONFIG_PATH.exists():
        add(f"  content:")
        for line in _safe_read(CONFIG_PATH).splitlines():
            add(f"    {line}")

    out_path.write_text("\n".join(lines) + "\n")
    print(f"diagnostic report: {out_path}")
    return out_path


# ---------------- daemon ----------------

def run_daemon():
    try:
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
    except ImportError as e:
        print(f"ERROR: missing dependency: {e}", file=sys.stderr)
        print("install with: sudo apt install python3-dbus python3-gi",
              file=sys.stderr)
        sys.exit(2)

    cfg = load_config()
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()

    try:
        proxy = bus.get_object("org.gnome.Mutter.IdleMonitor",
                               "/org/gnome/Mutter/IdleMonitor/Core")
        idle = dbus.Interface(proxy, "org.gnome.Mutter.IdleMonitor")
    except dbus.DBusException as e:
        print(f"ERROR: GNOME Mutter IdleMonitor unavailable: "
              f"{e.get_dbus_name()}: {e.get_dbus_message()}", file=sys.stderr)
        print("this daemon currently requires a GNOME session.",
              file=sys.stderr)
        sys.exit(3)

    state = {
        "cfg": cfg,
        "last_on_level": cfg["OnLevel"],
        "idle_watch_id": None,
        "active_watch_id": None,
    }

    cur = read_level()
    if cur is not None and cur >= 1:
        state["last_on_level"] = cur

    def wake_level():
        c = state["cfg"]
        return state["last_on_level"] if c["RestorePreviousLevel"] else c["OnLevel"]

    def log(msg):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

    def arm_idle_watch():
        if state["cfg"]["Paused"]:
            return
        ms = state["cfg"]["TimeoutSeconds"] * 1000
        try:
            state["idle_watch_id"] = int(idle.AddIdleWatch(dbus.UInt64(ms)))
            log(f"armed idle watch id={state['idle_watch_id']} ({ms}ms)")
        except dbus.DBusException as e:
            log(f"AddIdleWatch failed: {e}")

    def arm_active_watch():
        try:
            state["active_watch_id"] = int(idle.AddUserActiveWatch())
            log(f"armed active watch id={state['active_watch_id']}")
        except dbus.DBusException as e:
            log(f"AddUserActiveWatch failed: {e}")

    def remove_watch(key):
        wid = state[key]
        if wid is not None:
            try:
                idle.RemoveWatch(dbus.UInt32(wid))
            except Exception:
                pass
            state[key] = None

    def on_idle_fired():
        c = state["cfg"]
        if c["RestorePreviousLevel"]:
            cur = read_level()
            if cur is not None and cur >= 1:
                state["last_on_level"] = cur
        ok, method, err = set_level(c["OffLevel"])
        log(f"idle -> off ({method}{', err='+err if err else ''})")
        arm_active_watch()

    def on_active_fired():
        lvl = wake_level()
        ok, method, err = set_level(lvl)
        log(f"active -> {lvl} ({method}{', err='+err if err else ''})")
        arm_idle_watch()

    def on_watch_fired(wid):
        wid = int(wid)
        if wid == state["idle_watch_id"]:
            state["idle_watch_id"] = None
            on_idle_fired()
        elif wid == state["active_watch_id"]:
            state["active_watch_id"] = None
            on_active_fired()

    idle.connect_to_signal("WatchFired", on_watch_fired)

    if not cfg["Paused"]:
        cur = read_level()
        if cur is None or cur == 0:
            set_level(wake_level())

    arm_idle_watch()

    loop = GLib.MainLoop()

    def shutdown(*_):
        log("shutting down")
        remove_watch("idle_watch_id")
        remove_watch("active_watch_id")
        loop.quit()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGHUP, lambda *_: reload_cfg())

    def reload_cfg():
        log("SIGHUP: reloading config")
        new = load_config()
        state["cfg"] = new
        remove_watch("idle_watch_id")
        remove_watch("active_watch_id")
        if not new["Paused"]:
            set_level(wake_level())
            arm_idle_watch()

    log(f"daemon started; timeout={cfg['TimeoutSeconds']}s "
        f"on={cfg['OnLevel']} off={cfg['OffLevel']} "
        f"paused={cfg['Paused']} restore={cfg['RestorePreviousLevel']}")
    loop.run()


def main():
    ap = argparse.ArgumentParser(prog="tp-kbd-backlight",
                                 description=__doc__)
    ap.add_argument("-d", "--diagnose", action="store_true",
                    help="write diagnostic report to Desktop and exit")
    ap.add_argument("--no-cycle", action="store_true",
                    help="with --diagnose, skip the backlight cycle test")
    ap.add_argument("--set", type=int, metavar="N",
                    help="set level (0..max) and exit")
    ap.add_argument("--get", action="store_true",
                    help="print current level and exit")
    args = ap.parse_args()

    if args.get:
        lvl = read_level()
        print(lvl if lvl is not None else "")
        return 0 if lvl is not None else 1

    if args.set is not None:
        ok, method, err = set_level(args.set)
        if ok:
            print(f"ok ({method})")
            return 0
        print(f"FAIL: {err}", file=sys.stderr)
        return 1

    if args.diagnose:
        run_diagnose(cycle=not args.no_cycle)
        return 0

    run_daemon()
    return 0


if __name__ == "__main__":
    sys.exit(main())
