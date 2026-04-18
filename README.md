# tp-kbd-backlight (Linux)

Keeps the ThinkPad keyboard backlight **on while you're using the laptop**,
off after an idle timeout, and restores it on any keyboard / mouse / trackpad
activity. Linux/Ubuntu companion to [ThinkPadKbBacklight for Windows](https://github.com/midwan/thinkpad-kb-backlight).

## Requirements

- Ubuntu 22.04+ (or any distro shipping GNOME 42+, systemd user units, and
  `python3-dbus` / `python3-gi` / `python3-evdev`)
- **GNOME session** (X11 or Wayland) for the default idle source
  (`org.gnome.Mutter.IdleMonitor`). The optional `IgnoreExternalDevices`
  mode is desktop-agnostic and works anywhere.
- ThinkPad with the `thinkpad_acpi` kernel module (almost every recent
  ThinkPad). The LED device `/sys/class/leds/tpacpi::kbd_backlight` is what
  this tool drives.
- Membership in the `video` group (required — the daemon writes
  `/sys/class/leds/tpacpi::kbd_backlight/brightness`, which is
  `root:video 0664` on Ubuntu). Both the direct sysfs path and the
  `brightnessctl` fallback depend on this.
- Membership in the `input` group if you plan to use `IgnoreExternalDevices`
  (needed to read `/dev/input/event*`).

```bash
sudo usermod -aG video,input $USER
# log out and back in (or reboot)
```

> If you run KDE / Sway / Hyprland / etc., the default GNOME Mutter path will
> not work, but `IgnoreExternalDevices: true` uses evdev directly and has no
> desktop dependency.

## Install

```bash
git clone https://github.com/midwan/thinkpad-kb-backlight-linux.git
cd thinkpad-kb-backlight-linux
./install.sh
```

The installer:

1. Checks for `python3-dbus`, `python3-gi`, `python3-evdev`, `brightnessctl`;
   offers to `apt install` anything missing.
2. Drops `tp_kbd_backlight.py` into `~/.local/bin/`.
3. Drops `tp-kbd-backlight.service` into `~/.config/systemd/user/`.
4. Optionally installs a udev rule so your user (via the `video` group) can
   write `/sys/class/leds/tpacpi::kbd_backlight/brightness` directly — faster
   and removes the `brightnessctl` runtime dependency. If you skip this, the
   daemon falls back to shelling out to `brightnessctl` (which itself relies
   on its own udev rule from the Ubuntu package).
5. `systemctl --user enable --now tp-kbd-backlight.service`.

## Uninstall

```bash
./uninstall.sh
```

## Config

`~/.config/tp-kbd-backlight/config.json`:

```json
{
  "TimeoutSeconds": 30,
  "OnLevel": 2,
  "OffLevel": 0,
  "Paused": false,
  "RestorePreviousLevel": true,
  "IgnoreExternalDevices": false,
  "InternalDeviceMarkers": null
}
```

Levels on ThinkPad: `0` = off, `1` = low, `2` = high.

- `TimeoutSeconds` — idle time before the backlight drops to `OffLevel`.
- `OnLevel` — fallback wake level (only used when `RestorePreviousLevel` is
  false, or when the backlight was already off at startup).
- `RestorePreviousLevel` — if true (default), the daemon reads the current
  level right before turning off and restores exactly that on wake. Lets you
  dim via `Fn+Space` and have it stick across idle cycles.
- `Paused` — when true the daemon leaves the backlight alone.
- `IgnoreExternalDevices` — when `true`, only the built-in keyboard,
  TrackPoint, and touchpad reset the idle timer. External USB / Bluetooth
  mice and keyboards are ignored, so scrolling with an external mouse while
  reading will not wake the backlight. Mirrors the Windows option of the
  same name.
- `InternalDeviceMarkers` — list of case-insensitive substrings used to
  classify evdev device names as "internal" (and therefore activity-worthy)
  when `IgnoreExternalDevices` is `true`. `null` (default) means use the
  built-in list: `["TrackPoint", "TPPS/2", "AT Translated Set 2 keyboard",
  "ThinkPad", "Synaptics", "Elan"]`. A name-marker hit wins over bus
  classification, so a Lenovo-branded HID that happens to sit on the USB bus
  can still be treated as internal. Run `--diagnose` to see how each of your
  devices classifies, and extend the list if something is misclassified.

### How "internal" vs "external" is decided

With `IgnoreExternalDevices: true`, the daemon opens every `/dev/input/event*`
node, keeps the ones classified as internal, and ignores events from the
rest. Classification uses, in order:

1. **Name marker match** (case-insensitive substring against the evdev
   device name) — wins immediately.
2. **Bus type** (`EVIOCGID`):
   - Internal: `I8042`, `I2C`, `HOST`, `ISA`, `PCI`
     (built-in PS/2 keyboard, TrackPoint, I²C-HID touchpads, ACPI hotkeys)
   - External: `USB`, `BLUETOOTH`

Note: switching `IgnoreExternalDevices` on/off requires a `systemctl --user
restart tp-kbd-backlight.service` (the two modes use different idle sources —
Mutter DBus vs. direct evdev — and are picked at daemon start).

After editing, reload:

```bash
systemctl --user kill --signal=SIGHUP tp-kbd-backlight.service
```

or simply restart it:

```bash
systemctl --user restart tp-kbd-backlight.service
```

## Logs

```bash
journalctl --user -u tp-kbd-backlight.service -f
```

## Diagnostics

```bash
~/.local/bin/tp_kbd_backlight.py --diagnose
```

Writes `tp-kbd-backlight-diagnostic-YYYYMMDD-HHMMSS.txt` to your Desktop (or
`$HOME` if there's no Desktop dir). Includes DMI info, session type, LED
device permissions, `brightnessctl` status, Mutter IdleMonitor reachability,
and a 0→1→2→0 backlight cycle test. Attach this file if you open an issue.

One-off level commands:

```bash
~/.local/bin/tp_kbd_backlight.py --get        # print current level
~/.local/bin/tp_kbd_backlight.py --set 1      # set to low
```

## How it works

- **Idle detection**: two backends, picked by `IgnoreExternalDevices`.
  - **off** (default): GNOME Mutter's `org.gnome.Mutter.IdleMonitor` DBus
    service. `AddIdleWatch(ms)` fires `WatchFired` after `ms` of no input;
    `AddUserActiveWatch()` fires once when input resumes. Sees Wayland
    input, unlike `xprintidle` / `xss`.
  - **on**: direct `evdev` read of `/dev/input/event*`. The daemon opens
    only the devices classified as internal and runs its own idle timer
    using `time.monotonic()` — no desktop environment needed.
- **Backlight control**: write to
  `/sys/class/leds/tpacpi::kbd_backlight/brightness` when the permissions
  allow; otherwise `brightnessctl --device=tpacpi::kbd_backlight set N`.
- **Service lifecycle**: systemd user unit tied to `graphical-session.target`
  so it only runs when you're logged in with a graphical session.

## License

GPL-3.0. See [LICENSE](LICENSE).
