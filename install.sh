#!/usr/bin/env bash
# tp-kbd-backlight installer (Ubuntu / GNOME).
# Installs to ~/.local/bin and ~/.config/systemd/user, then enables the unit.
# Does NOT require sudo unless you also want the udev rule for sysfs writes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
UNIT_DIR="$HOME/.config/systemd/user"
UDEV_RULE="/etc/udev/rules.d/90-thinkpad-kbd-backlight.rules"

echo "==> tp-kbd-backlight installer"
echo

# --- sanity: python + gi + dbus + evdev -----------------------------------
need_pkgs=()
python3 -c "import gi" 2>/dev/null || need_pkgs+=(python3-gi)
python3 -c "import dbus" 2>/dev/null || need_pkgs+=(python3-dbus)
python3 -c "import evdev" 2>/dev/null || need_pkgs+=(python3-evdev)
if ! command -v brightnessctl >/dev/null 2>&1; then
    need_pkgs+=(brightnessctl)
fi
if [ "${#need_pkgs[@]}" -gt 0 ]; then
    echo "Missing packages: ${need_pkgs[*]}"
    echo "Install with: sudo apt install ${need_pkgs[*]}"
    read -r -p "Run it now? [y/N] " ans
    case "$ans" in
        y|Y) sudo apt install -y "${need_pkgs[@]}" ;;
        *) echo "OK — install those and re-run this script."; exit 1 ;;
    esac
fi

# --- sanity: LED exists ---------------------------------------------------
if [ ! -d /sys/class/leds/tpacpi::kbd_backlight ]; then
    echo "WARN: /sys/class/leds/tpacpi::kbd_backlight not found."
    echo "      This machine may not expose the ThinkPad keyboard backlight"
    echo "      via the thinkpad_acpi driver. Install will continue, but the"
    echo "      daemon will not be able to change the backlight."
fi

# --- sanity: GNOME session ------------------------------------------------
xdg_desktop="${XDG_CURRENT_DESKTOP:-}"
if ! echo "$xdg_desktop" | grep -qi gnome; then
    echo "WARN: XDG_CURRENT_DESKTOP='$xdg_desktop' — this daemon uses the GNOME"
    echo "      Mutter IdleMonitor DBus interface. It will likely fail to start"
    echo "      under non-GNOME sessions."
fi

# --- sanity: video + input group membership -------------------------------
# Writing to /sys/class/leds/tpacpi::kbd_backlight/brightness requires write
# access to the file. On Ubuntu it is root:video 0664, and both the direct
# sysfs path AND brightnessctl (via its own udev rule) rely on the caller
# being in the 'video' group. Without it, every backlight change fails.
missing_groups=()
if ! id -nG "$USER" | tr ' ' '\n' | grep -qx video; then
    missing_groups+=(video)
fi
# 'input' is only needed for the optional IgnoreExternalDevices mode (reads
# /dev/input/event*). Flag it alongside so users fix both in one go.
if ! id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
    missing_groups+=(input)
fi
if [ "${#missing_groups[@]}" -gt 0 ]; then
    echo
    echo "NOTE: you are not in these groups: ${missing_groups[*]}"
    echo "      - 'video' is REQUIRED for the daemon to change the keyboard"
    echo "        backlight (tpacpi::kbd_backlight is root:video 0664)."
    echo "      - 'input' is required only if you enable IgnoreExternalDevices"
    echo "        (the daemon reads /dev/input/event* directly)."
    read -r -p "Add $USER to ${missing_groups[*]} now via sudo usermod? [y/N] " ans
    case "$ans" in
        y|Y)
            sudo usermod -aG "$(IFS=,; echo "${missing_groups[*]}")" "$USER"
            echo "added. You MUST log out and back in (or reboot) for the new"
            echo "group membership to take effect before the daemon will work."
            ;;
        *)
            echo "OK — add them manually then log out/in:"
            echo "    sudo usermod -aG $(IFS=,; echo "${missing_groups[*]}") $USER"
            ;;
    esac
fi

# --- install binary -------------------------------------------------------
mkdir -p "$BIN_DIR"
install -m 0755 "$SCRIPT_DIR/tp_kbd_backlight.py" "$BIN_DIR/tp_kbd_backlight.py"
echo "installed: $BIN_DIR/tp_kbd_backlight.py"

# --- install systemd user unit --------------------------------------------
mkdir -p "$UNIT_DIR"
install -m 0644 "$SCRIPT_DIR/systemd/tp-kbd-backlight.service" \
    "$UNIT_DIR/tp-kbd-backlight.service"
echo "installed: $UNIT_DIR/tp-kbd-backlight.service"

# --- optional udev rule ---------------------------------------------------
# brightnessctl already handles permissions via its own udev rule when
# installed from apt, but a direct-sysfs rule is faster and removes the
# brightnessctl dependency at runtime. Offer it as optional.
if [ ! -f "$UDEV_RULE" ]; then
    echo
    echo "Optional: install a udev rule so your user can write"
    echo "  /sys/class/leds/tpacpi::kbd_backlight/brightness"
    echo "directly (no brightnessctl fork on every change). Requires sudo."
    read -r -p "Install udev rule? [y/N] " ans
    case "$ans" in
        y|Y)
            sudo tee "$UDEV_RULE" >/dev/null <<EOF
ACTION=="add|change", SUBSYSTEM=="leds", KERNEL=="tpacpi::kbd_backlight", \
    RUN+="/bin/chgrp video /sys/class/leds/%k/brightness", \
    RUN+="/bin/chmod g+w /sys/class/leds/%k/brightness"
EOF
            sudo udevadm control --reload
            sudo udevadm trigger --subsystem-match=leds
            echo "installed: $UDEV_RULE"
            ;;
    esac
fi

# --- enable + start --------------------------------------------------------
systemctl --user daemon-reload
systemctl --user enable --now tp-kbd-backlight.service
echo
echo "service status:"
systemctl --user --no-pager --lines=0 status tp-kbd-backlight.service || true
echo
echo "Done. Logs:    journalctl --user -u tp-kbd-backlight.service -f"
echo "     Config:   ~/.config/tp-kbd-backlight/config.json"
echo "     Uninstall: $SCRIPT_DIR/uninstall.sh"
