#!/usr/bin/env bash
# Install / remove the Clawd widget integration for KDE Plasma 6 (Wayland).
#   ./install.sh           install launcher, KWin rule and shortcut
#   ./install.sh --remove  undo everything
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
APP_ID="clawd-widget"
DESKTOP="$HOME/.local/share/applications/$APP_ID.desktop"
SHORTCUT="Meta+Shift+F23"   # what the Copilot key usually sends
# Qt key code for Meta+Shift+F23: Meta 0x10000000 + Shift 0x02000000 + F23 0x01000052
SHORTCUT_CODE=$((0x10000000 + 0x02000000 + 0x01000052))

# window size from clawd_widget.py, placed bottom right above the panel
WIN_W=560
WIN_H=440
SCREEN_W=1746
SCREEN_H=982
PANEL_H=48
POS="$((SCREEN_W - WIN_W)),$((SCREEN_H - PANEL_H - WIN_H))"

kw() { kwriteconfig6 --file kwinrulesrc "$@"; }
delete_rule_group() {
    local f="$HOME/.config/kwinrulesrc"
    [[ -f "$f" ]] || return 0
    awk -v g="[$APP_ID]" '/^\[/{skip=($0==g)} !skip' "$f" >"$f.tmp" && mv "$f.tmp" "$f"
}
accel() { gdbus call --session --dest org.kde.kglobalaccel --object-path /kglobalaccel \
          --method "org.kde.KGlobalAccel.$1" "${@:2}" >/dev/null; }
ACTION_ID="['$APP_ID.desktop', '_launch', 'Clawd', 'Clawd starten']"

remove_rule_from_list() {
    local rules
    rules="$(kreadconfig6 --file kwinrulesrc --group General --key rules)"
    rules="$(tr ',' '\n' <<<"$rules" | grep -vx "$APP_ID" | paste -sd, || true)"
    kw --group General --key rules "$rules"
    kw --group General --key count "$(tr ',' '\n' <<<"$rules" | grep -c . || true)"
}

if [[ "${1:-}" == "--remove" ]]; then
    accel unregister "$APP_ID.desktop" "_launch" || true
    rm -f "$DESKTOP"
    remove_rule_from_list
    delete_rule_group
    gdbus call --session --dest org.kde.KWin --object-path /KWin --method org.kde.KWin.reconfigure >/dev/null
    echo "Clawd integration removed."
    exit 0
fi

# 1. launcher
mkdir -p "$(dirname "$DESKTOP")"
cat >"$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Clawd
Comment=Start Claude Code, Desktop or Browser
Exec=/usr/bin/python3 $DIR/clawd_widget.py
Icon=$DIR/assets/idle/0001.png
StartupNotify=false
Terminal=false
EOF

# 2. KWin window rule (keep above, fixed position, hidden from taskbar/alt+tab)
remove_rule_from_list
delete_rule_group
kw --group "$APP_ID" --key Description "Clawd widget"
kw --group "$APP_ID" --key wmclass "$APP_ID"
kw --group "$APP_ID" --key wmclassmatch 1
for key in above skiptaskbar skippager skipswitcher acceptfocus; do
    kw --group "$APP_ID" --key "$key" true
    kw --group "$APP_ID" --key "${key}rule" 2
done
kw --group "$APP_ID" --key position "$POS"
kw --group "$APP_ID" --key positionrule 2
kw --group "$APP_ID" --key fsplevel 0
kw --group "$APP_ID" --key fsplevelrule 2
rules="$(kreadconfig6 --file kwinrulesrc --group General --key rules)"
rules="${rules:+$rules,}$APP_ID"
kw --group General --key rules "$rules"
kw --group General --key count "$(tr ',' '\n' <<<"$rules" | grep -c .)"
gdbus call --session --dest org.kde.KWin --object-path /KWin --method org.kde.KWin.reconfigure >/dev/null

# 3. global shortcut via kglobalaccel (flag 2 = set as active shortcut and save)
accel doRegister "$ACTION_ID"
accel setShortcut "$ACTION_ID" "[$SHORTCUT_CODE]" 2

echo "Clawd installed: shortcut $SHORTCUT, window at $POS"
