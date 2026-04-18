#!/usr/bin/env bash
# tp-kbd-backlight uninstaller.

set -euo pipefail

BIN="$HOME/.local/bin/tp_kbd_backlight.py"
UNIT="$HOME/.config/systemd/user/tp-kbd-backlight.service"
CONFIG_DIR="$HOME/.config/tp-kbd-backlight"
UDEV_RULE="/etc/udev/rules.d/90-thinkpad-kbd-backlight.rules"

echo "==> stopping + disabling service"
systemctl --user disable --now tp-kbd-backlight.service 2>/dev/null || true

echo "==> removing user files"
rm -f "$BIN" "$UNIT"
systemctl --user daemon-reload || true

if [ -d "$CONFIG_DIR" ]; then
    read -r -p "Remove config dir $CONFIG_DIR? [y/N] " ans
    case "$ans" in
        y|Y) rm -rf "$CONFIG_DIR" ;;
    esac
fi

if [ -f "$UDEV_RULE" ]; then
    read -r -p "Remove udev rule $UDEV_RULE (requires sudo)? [y/N] " ans
    case "$ans" in
        y|Y)
            sudo rm -f "$UDEV_RULE"
            sudo udevadm control --reload || true
            sudo udevadm trigger --subsystem-match=leds || true
            ;;
    esac
fi

echo "Done."
