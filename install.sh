#!/usr/bin/env bash
# Install / remove the Clawd widget integration for KDE Plasma 6 (Wayland).
#   ./install.sh                     install launcher, KWin rule, keyboard fix and shortcut
#   ./install.sh --code-dir ~/dev    folder Claude Code starts in (default: home)
#   ./install.sh --remove            undo everything
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
APP_ID="clawd-widget"
DESKTOP="$HOME/.local/share/applications/$APP_ID.desktop"
XKB="$HOME/.config/xkb"
SHORTCUT="Meta+Shift+F23"   # what the Copilot key sends
# Qt key code for Meta+Shift+F23: Meta 0x10000000 + Shift 0x02000000 + F23 0x01000046
SHORTCUT_CODE=$((0x10000000 + 0x02000000 + 0x01000046))
ACTION_ID="['$APP_ID.desktop', '_launch', 'Clawd', 'Clawd starten']"

MODE=install
CODE_DIR="$HOME"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --remove) MODE=remove ;;
        --code-dir)
            [[ $# -ge 2 && -n "$2" ]] || { echo "--code-dir braucht einen Ordner" >&2; exit 1; }
            CODE_DIR="$(realpath -m "${2/#\~/$HOME}")"; shift ;;
        -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
        *) echo "Unbekannte Option: $1" >&2; exit 1 ;;
    esac
    shift
done

# ---------- helpers ----------
kw() { kwriteconfig6 --file kwinrulesrc "$@"; }
# quote one argument for a .desktop Exec line (Desktop Entry spec: escape " ` $ \
# inside quotes, backslashes once more for the string layer, % as %%)
desktop_quote() {
    python3 -I -c '
import sys
s = sys.argv[1].replace("\\", "\\\\\\\\")
for c in "\"`$":
    s = s.replace(c, "\\\\" + c)
print("\"" + s.replace("%", "%%") + "\"")' "$1"
}
accel() { gdbus call --session --dest org.kde.kglobalaccel --object-path /kglobalaccel \
          --method "org.kde.KGlobalAccel.$1" "${@:2}" >/dev/null; }
kwin_reconfigure() {
    gdbus call --session --dest org.kde.KWin --object-path /KWin \
        --method org.kde.KWin.reconfigure >/dev/null
}
delete_rule_group() {
    local f="$HOME/.config/kwinrulesrc"
    [[ -f "$f" ]] || return 0
    awk -v g="[$APP_ID]" '/^\[/{skip=($0==g)} !skip' "$f" >"$f.tmp" && mv "$f.tmp" "$f"
}
remove_rule_from_list() {
    local rules
    rules="$(kreadconfig6 --file kwinrulesrc --group General --key rules)"
    rules="$(tr ',' '\n' <<<"$rules" | grep -vx "$APP_ID" | paste -sd, || true)"
    kw --group General --key rules "$rules"
    kw --group General --key count "$(tr ',' '\n' <<<"$rules" | grep -c . || true)"
}
xkb_options_without_clawd() {
    kreadconfig6 --file kxkbrc --group Layout --key Options | tr ',' '\n' | grep -vx "clawd:f23" | paste -sd, || true
}
set_xkb_options() {
    if [[ -n "$1" ]]; then
        kwriteconfig6 --file kxkbrc --group Layout --key Options "$1"
    else
        kwriteconfig6 --file kxkbrc --group Layout --key Options --delete
    fi
}

# ---------- requirements ----------
missing=()
for cmd in python3 konsole kwriteconfig6 kreadconfig6 kscreen-doctor gdbus; do
    command -v "$cmd" >/dev/null || missing+=("$cmd")
done
python3 -c "import PySide6" 2>/dev/null || missing+=("PySide6 (Fedora: sudo dnf install python3-pyside6)")
if [[ ${#missing[@]} -gt 0 && "$MODE" == install ]]; then
    printf 'Es fehlt:\n'; printf '  - %s\n' "${missing[@]}"; exit 1
fi
if [[ "${XDG_SESSION_TYPE:-}" != "wayland" || "${XDG_CURRENT_DESKTOP:-}" != *KDE* ]]; then
    echo "Hinweis: Clawd ist für KDE Plasma 6 unter Wayland gebaut (gefunden: ${XDG_CURRENT_DESKTOP:-?} / ${XDG_SESSION_TYPE:-?})." >&2
fi

# ---------- remove ----------
if [[ "$MODE" == remove ]]; then
    accel unregister "$APP_ID.desktop" "_launch" || true
    kwriteconfig6 --file kglobalshortcutsrc --group services --group "$APP_ID.desktop" --key _launch --delete
    rm -f "$DESKTOP"
    remove_rule_from_list
    delete_rule_group
    kwin_reconfigure
    rm -f "$XKB/symbols/clawd"
    grep -q "clawd:f23" "$XKB/rules/evdev" 2>/dev/null && rm -f "$XKB/rules/evdev"
    set_xkb_options "$(xkb_options_without_clawd)"
    echo "Clawd entfernt. Einmal ab- und wieder anmelden, um das Tastaturlayout zurückzusetzen."
    exit 0
fi

# ---------- window position: bottom right of the primary screen, above a bottom panel ----------
read -r WIN_W WIN_H < <(sed -nE 's/^WIN_W, WIN_H = ([0-9]+), ([0-9]+).*/\1 \2/p' "$DIR/clawd_widget.py")
read -r SCR_X SCR_Y SCR_W SCR_H < <(kscreen-doctor -j | python3 -I -c '
import json, sys
outs = [o for o in json.load(sys.stdin)["outputs"] if o.get("enabled")]
o = min(outs, key=lambda o: o.get("priority") or 99)      # primary screen
mode = next(m for m in o["modes"] if m["id"] == o["currentModeId"])["size"]
w, h = mode["width"], mode["height"]
if o.get("rotation") in (2, 8):                             # portrait
    w, h = h, w
s = o.get("scale") or 1
print(o["pos"]["x"], o["pos"]["y"], round(w / s), round(h / s))')
# height of a bottom panel that is always visible (+ gap if floating), 0 if none
PANEL_H="$(gdbus call --session --dest org.kde.plasmashell --object-path /PlasmaShell \
    --method org.kde.PlasmaShell.evaluateScript \
    'var h = 0; panels().forEach(function (p) { if (p.location == "bottom" && p.hiding == "none") h = Math.max(h, p.height + (p.floating ? 16 : 0)); }); print(h);' \
    2>/dev/null | grep -oE '[0-9]+' | head -1 || true)"
PANEL_H="${PANEL_H:-0}"
POS="$((SCR_X + SCR_W - WIN_W)),$((SCR_Y + SCR_H - PANEL_H - WIN_H))"

# ---------- 1. launcher ----------
mkdir -p "$(dirname "$DESKTOP")"
cat >"$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=Clawd
Comment=Claude Code, Claude Desktop oder Claude im Browser starten
Exec=env $(desktop_quote "CLAWD_CODE_DIR=$CODE_DIR") $(desktop_quote "$(command -v python3)") $(desktop_quote "$DIR/clawd_widget.py")
Icon=$DIR/assets/idle/0001.png
StartupNotify=false
Terminal=false
EOF

# ---------- 2. KWin window rule (keep above, fixed position, hidden from taskbar/alt+tab) ----------
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
kwin_reconfigure

# ---------- 3. keyboard ----------
# The Copilot key sends Meta+Shift+F23, but the default layout turns Shift+F23 into
# XF86Assistant (unknown to Qt/KDE). Keep it F23 on every level.
mkdir -p "$XKB/symbols" "$XKB/rules"
cat >"$XKB/symbols/clawd" <<'EOF'
// Copilot key: keep F23 even with Shift held (instead of XF86Assistant)
xkb_symbols "f23" {
    key <FK23> { symbols[Group1] = [ F23, F23 ] };
};
EOF
if [[ -f "$XKB/rules/evdev" ]] && ! grep -q "clawd:f23" "$XKB/rules/evdev"; then
    echo "WARNUNG: $XKB/rules/evdev existiert schon und stammt nicht von Clawd." >&2
    echo "         Füge die Option clawd:f23 dort selbst hinzu (siehe README)." >&2
else
    # system rules first, so clawd(f23) comes after inet(evdev) and wins
    printf '! include %%S/evdev\n\n! option = symbols\n  clawd:f23 = +clawd(f23)\n' >"$XKB/rules/evdev"
fi
set_xkb_options "$(xkb_options_without_clawd | sed 's/.\+/&,/')clawd:f23"
kwriteconfig6 --file kxkbrc --group Layout --key ResetOldOptions true

# ---------- 4. global shortcut ----------
# flags 2|4 = set as active shortcut, don't autoload a previously saved one
accel doRegister "$ACTION_ID"
accel setShortcut "$ACTION_ID" "[$SHORTCUT_CODE]" 6
# also persist it; a shortcut set over DBus did not survive a re-login
kwriteconfig6 --file kglobalshortcutsrc --group services --group "$APP_ID.desktop" --key _launch "$SHORTCUT"

cat <<EOF
Clawd installiert.
  Kürzel:        $SHORTCUT (Copilot-Taste)
  Position:      $POS (Bildschirm ${SCR_W}x${SCR_H}, Taskleiste ${PANEL_H} px)
  Claude Code:   startet in $CODE_DIR
Melde dich einmal ab und wieder an, damit die Copilot-Taste erkannt wird.
EOF
