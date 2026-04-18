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
    "IgnoreExternalDevices": False,
    "InternalDeviceMarkers": None,
}

# Linux input subsystem bus types (see <linux/input.h> / include/uapi/linux/input.h).
BUS_PCI = 0x01
BUS_ISA = 0x10
BUS_USB = 0x03
BUS_I8042 = 0x11
BUS_BLUETOOTH = 0x05
BUS_VIRTUAL = 0x06
BUS_HOST = 0x19
BUS_I2C = 0x18

BUS_NAMES = {
    BUS_PCI: "PCI",
    BUS_ISA: "ISA",
    BUS_USB: "USB",
    BUS_I8042: "I8042",
    BUS_BLUETOOTH: "BLUETOOTH",
    BUS_VIRTUAL: "VIRTUAL",
    BUS_HOST: "HOST",
    BUS_I2C: "I2C",
}

INTERNAL_BUSES = {BUS_I8042, BUS_I2C, BUS_HOST, BUS_ISA, BUS_PCI}
EXTERNAL_BUSES = {BUS_USB, BUS_BLUETOOTH}

# Default name-based markers for "internal" evdev devices on ThinkPads. These
# are substring-matched against the device name, case-insensitively, and act as
# an override on top of the bus classification (so a USB device with one of
# these strings in its name is still treated as internal, matching the Windows
# behaviour where a few Lenovo-branded HIDs show up on the USB bus).
DEFAULT_INTERNAL_MARKERS = [
    "TrackPoint",
    "TPPS/2",
    "AT Translated Set 2 keyboard",
    "ThinkPad",
    "Synaptics",
    "Elan",
]


def effective_markers(cfg):
    m = cfg.get("InternalDeviceMarkers") if cfg else None
    return m if m else DEFAULT_INTERNAL_MARKERS


def _evdev_has_input_caps(dev):
    try:
        from evdev import ecodes
    except ImportError:
        return False
    try:
        caps = dev.capabilities()
    except Exception:
        return False
    return bool(caps.get(ecodes.EV_KEY)
                or caps.get(ecodes.EV_REL)
                or caps.get(ecodes.EV_ABS))


def is_internal_evdev_device(dev, cfg):
    """True iff the device should reset the idle timer in IgnoreExternal mode."""
    name = (getattr(dev, "name", "") or "")
    lname = name.lower()
    for mk in effective_markers(cfg):
        if mk and mk.lower() in lname:
            return True
    try:
        bus = dev.info.bustype
    except Exception:
        bus = None
    if bus in INTERNAL_BUSES:
        return True
    return False


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
        cfg["IgnoreExternalDevices"] = bool(cfg.get("IgnoreExternalDevices", False))
        markers = cfg.get("InternalDeviceMarkers")
        if markers is not None and not isinstance(markers, list):
            markers = None
        cfg["InternalDeviceMarkers"] = markers
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
    sysfs_err = None
    try:
        with open(LED_DIR / "brightness", "w") as f:
            f.write(str(level))
        return True, "sysfs", None
    except Exception as e:
        sysfs_err = str(e)

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
        add("  info (kbd)    :")
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
            add("  reachable   : yes")
            add(f"  idle_time_ms: {idle_ms}")
        except dbus.DBusException as e:
            add(f"  reachable   : NO ({e.get_dbus_name()}: {e.get_dbus_message()})")
    except ImportError as e:
        add(f"  dbus import : FAILED ({e}) — install python3-dbus")

    add("\n[idle monitor mode]")
    try:
        _cfg_for_mode = load_config()
    except Exception:
        _cfg_for_mode = dict(DEFAULT_CONFIG)
    _ignore_ext = bool(_cfg_for_mode.get("IgnoreExternalDevices", False))
    add(f"  mode             : {'evdev (internal devices only)' if _ignore_ext else 'Mutter DBus (any input)'}")
    add(f"  markers          : {', '.join(effective_markers(_cfg_for_mode))}")
    add(f"  input group      : {'yes' if 'input' in _cmd('id', '-Gn').split() else 'NO (needed for evdev mode)'}")

    add("\n[evdev devices]")
    try:
        from evdev import InputDevice, list_devices, ecodes  # noqa: F401
    except ImportError as e:
        add(f"  python3-evdev not installed ({e})")
        add("  install with: sudo apt install python3-evdev")
    else:
        try:
            paths = sorted(list_devices())
        except Exception as e:
            paths = []
            add(f"  enumeration error: {e}")
        if not paths:
            add("  (no /dev/input/event* visible — check 'input' group membership)")
        for p in paths:
            try:
                d = InputDevice(p)
            except Exception as e:
                add(f"  {p:20s} <open failed: {e}>")
                continue
            try:
                bus = d.info.bustype
                vid = d.info.vendor
                pid = d.info.product
                caps = d.capabilities()
                has_keys = bool(caps.get(ecodes.EV_KEY))
                has_rel = bool(caps.get(ecodes.EV_REL))
                has_abs = bool(caps.get(ecodes.EV_ABS))
                kind = []
                if has_keys: kind.append("keys")
                if has_rel: kind.append("rel")
                if has_abs: kind.append("abs")
                if not kind: kind.append("none")
                cls = "INTERNAL" if is_internal_evdev_device(d, _cfg_for_mode) else "external"
                bus_s = BUS_NAMES.get(bus, f"bus=0x{bus:02X}")
                add(f"  {d.path:20s} [{bus_s:9s}] [{cls:8s}] "
                    f"[{'/'.join(kind):11s}] "
                    f"vid={vid:04x} pid={pid:04x} {d.name!r}")
            finally:
                try: d.close()
                except Exception: pass

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
        add("  content:")
        for line in _safe_read(CONFIG_PATH).splitlines():
            add(f"    {line}")

    out_path.write_text("\n".join(lines) + "\n")
    print(f"diagnostic report: {out_path}")
    return out_path


# ---------------- daemon ----------------

def run_daemon():
    cfg = load_config()
    if cfg["IgnoreExternalDevices"]:
        run_evdev_daemon(cfg)
    else:
        run_mutter_daemon(cfg)


def run_mutter_daemon(cfg):
    try:
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
    except ImportError as e:
        print(f"ERROR: missing dependency: {e}", file=sys.stderr)
        print("install with: sudo apt install python3-dbus python3-gi",
              file=sys.stderr)
        sys.exit(2)

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

    log(f"daemon started (mutter); timeout={cfg['TimeoutSeconds']}s "
        f"on={cfg['OnLevel']} off={cfg['OffLevel']} "
        f"paused={cfg['Paused']} restore={cfg['RestorePreviousLevel']} "
        f"ignore_external=False")
    loop.run()


def run_evdev_daemon(cfg):
    try:
        from evdev import InputDevice, list_devices, ecodes
    except ImportError as e:
        print(f"ERROR: python3-evdev not installed: {e}", file=sys.stderr)
        print("install with: sudo apt install python3-evdev", file=sys.stderr)
        sys.exit(2)

    import select

    def log(msg):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

    state = {
        "cfg": cfg,
        "last_on_level": cfg["OnLevel"],
        "off_since": None,
        "last_activity": time.monotonic(),
        "devices": [],
    }

    cur = read_level()
    if cur is not None and cur >= 1:
        state["last_on_level"] = cur

    def wake_level():
        c = state["cfg"]
        return state["last_on_level"] if c["RestorePreviousLevel"] else c["OnLevel"]

    def close_all():
        for d in state["devices"]:
            try:
                d.close()
            except Exception:
                pass
        state["devices"] = []

    def open_internal():
        close_all()
        devs = []
        try:
            paths = sorted(list_devices())
        except Exception as e:
            log(f"list_devices failed: {e}")
            return
        for path in paths:
            try:
                d = InputDevice(path)
            except PermissionError:
                log(f"skip {path}: permission denied (add user to 'input' group)")
                continue
            except Exception as e:
                log(f"skip {path}: {e}")
                continue
            if not _evdev_has_input_caps(d):
                d.close()
                continue
            if is_internal_evdev_device(d, state["cfg"]):
                bus_s = BUS_NAMES.get(d.info.bustype, f"0x{d.info.bustype:02X}")
                log(f"watching [{bus_s}] {d.path}: {d.name!r}")
                devs.append(d)
            else:
                d.close()
        state["devices"] = devs
        if not devs:
            log("WARN: no internal input devices matched — the daemon will "
                "never see activity. Run --diagnose and adjust "
                "InternalDeviceMarkers in config.json.")

    def on_activity():
        state["last_activity"] = time.monotonic()
        if state["off_since"] is not None:
            lvl = wake_level()
            _, method, err = set_level(lvl)
            log(f"active -> {lvl} ({method}{', err='+err if err else ''})")
            state["off_since"] = None

    def go_idle():
        c = state["cfg"]
        if c["RestorePreviousLevel"]:
            cur = read_level()
            if cur is not None and cur >= 1:
                state["last_on_level"] = cur
        _, method, err = set_level(c["OffLevel"])
        log(f"idle -> off ({method}{', err='+err if err else ''})")
        state["off_since"] = time.monotonic()

    def reload_cfg(*_):
        log("SIGHUP: reloading config")
        new = load_config()
        if new["IgnoreExternalDevices"] != state["cfg"]["IgnoreExternalDevices"]:
            log("IgnoreExternalDevices toggled — restart the service to switch "
                "idle-monitor backend")
        state["cfg"] = new
        open_internal()
        if not new["Paused"] and state["off_since"] is None:
            set_level(wake_level())
        state["last_activity"] = time.monotonic()

    stopping = {"flag": False}
    def shutdown(*_):
        log("shutting down")
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGHUP, reload_cfg)

    open_internal()

    if not state["cfg"]["Paused"]:
        cur = read_level()
        if cur is None or cur == 0:
            set_level(wake_level())

    log(f"daemon started (evdev); timeout={cfg['TimeoutSeconds']}s "
        f"on={cfg['OnLevel']} off={cfg['OffLevel']} "
        f"paused={cfg['Paused']} restore={cfg['RestorePreviousLevel']} "
        f"ignore_external=True watching={len(state['devices'])}")

    state["last_activity"] = time.monotonic()
    while not stopping["flag"]:
        c = state["cfg"]
        if c["Paused"]:
            # Drain any queued events so the kernel doesn't buffer forever.
            fds = [d.fd for d in state["devices"]]
            try:
                r, _, _ = select.select(fds, [], [], 1.0)
            except (InterruptedError, OSError):
                continue
            for d in list(state["devices"]):
                if d.fd in r:
                    try:
                        for _ in d.read():
                            pass
                    except OSError:
                        log(f"device gone: {d.path}")
                        try: d.close()
                        except Exception: pass
                        state["devices"].remove(d)
            continue

        now = time.monotonic()
        timeout = c["TimeoutSeconds"]
        if state["off_since"] is None:
            wait = max(0.1, timeout - (now - state["last_activity"]))
        else:
            wait = 5.0

        fds = [d.fd for d in state["devices"]]
        try:
            r, _, _ = select.select(fds, [], [], wait)
        except (InterruptedError, OSError):
            continue

        activity = False
        for d in list(state["devices"]):
            if d.fd not in r:
                continue
            try:
                for ev in d.read():
                    if ev.type == ecodes.EV_KEY and ev.value != 0:
                        activity = True
                    elif ev.type in (ecodes.EV_REL, ecodes.EV_ABS):
                        activity = True
            except OSError:
                log(f"device gone: {d.path}")
                try: d.close()
                except Exception: pass
                state["devices"].remove(d)

        if activity:
            on_activity()
        elif state["off_since"] is None \
                and (time.monotonic() - state["last_activity"]) >= timeout:
            go_idle()

    close_all()


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
