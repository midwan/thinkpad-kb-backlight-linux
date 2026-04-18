# tp-kbd-backlight (Linux)

Keeps the ThinkPad keyboard backlight **on while you're using the laptop**,
off after an idle timeout, and restores it on any keyboard / mouse / trackpad
activity. Linux/Ubuntu companion to [ThinkPadKbBacklight for Windows](https://github.com/midwan/thinkpad-kb-backlight).

## Requirements

- Ubuntu 22.04+ (or any distro shipping GNOME 42+, systemd user units, and
  `python3-dbus` / `python3-gi`)
- **GNOME session** (X11 or Wayland). Idle detection goes through the
  `org.gnome.Mutter.IdleMonitor` DBus service that GNOME ships.
- ThinkPad with the `thinkpad_acpi` kernel module (almost every recent
  ThinkPad). The LED device `/sys/class/leds/tpacpi::kbd_backlight` is what
  this tool drives.

> If you run KDE / Sway / Hyprland / etc., this version will not work as-is;
> the idle source is GNOME-specific. Open an issue if you need it ported.

## Install

```bash
git clone https://github.com/midwan/thinkpad-kb-backlight-linux.git
cd thinkpad-kb-backlight-linux
./install.sh
```

The installer:

1. Checks for `python3-dbus`, `python3-gi`, `brightnessctl`; offers to
   `apt install` anything missing.
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
  "RestorePreviousLevel": true
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

- **Idle detection**: GNOME Mutter's `org.gnome.Mutter.IdleMonitor` DBus
  service. `AddIdleWatch(ms)` fires `WatchFired` after `ms` of no input;
  `AddUserActiveWatch()` fires once when input resumes. This sees Wayland
  input, unlike `xprintidle` / `xss`.
- **Backlight control**: write to
  `/sys/class/leds/tpacpi::kbd_backlight/brightness` when the permissions
  allow; otherwise `brightnessctl --device=tpacpi::kbd_backlight set N`.
- **Service lifecycle**: systemd user unit tied to `graphical-session.target`
  so it only runs when you're logged in with a graphical session.

## License

GPL-3.0. See [LICENSE](LICENSE).
