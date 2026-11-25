#!/bin/bash
# Maestro - cai dat cho macOS. Bam dup file nay (lan dau: chuot phai > Open).
APP_HOME="$HOME/Library/Application Support/Maestro"
mkdir -p "$APP_HOME/data"
LOG="$APP_HOME/install.log"
exec > >(tee -a "$LOG") 2>&1
echo ""
echo "==== $(date) ===="
echo "  Maestro __REL__ - cai dat (Mac)"
echo "  Khoang 5-15 phut. Khong tat cua so nay. Neu may hoi mat khau: nhap mat khau dang nhap Mac."
echo ""
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
have() { command -v "$1" >/dev/null 2>&1; }
line() { printf "  %-26s %s\n" "$1" "$2"; }
fail() { echo ""; echo "  LOI: $1"; echo "  Chi tiet: $LOG"; echo ""; read -n 1 -s -r -p "  Bam phim bat ky de dong..."; exit 1; }

# Homebrew (de cai Python, Node)
if have brew; then line "Homebrew" "da co"; else
  line "Homebrew" "dang cai... (nhap mat khau Mac khi duoc hoi)"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" </dev/tty
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$($b shellenv)"; done
  have brew || fail "Cai Homebrew chua xong. Kiem tra mang roi chay lai file nay."
fi
BREW_PY="$(brew --prefix)/bin/python3"
if [ -x "$BREW_PY" ]; then line "Python" "da co"; else line "Python" "dang cai..."; brew install python >/dev/null || fail "Cai Python chua xong."; fi
PY="$BREW_PY"; [ -x "$PY" ] || PY="$(command -v python3)"
[ -x "$PY" ] || fail "Khong tim thay Python."
if have node; then line "Node.js" "da co"; else line "Node.js" "dang cai..."; brew install node >/dev/null || fail "Cai Node.js chua xong."; fi

if have claude; then line "Claude Code" "da co"; else
  line "Claude Code" "dang cai..."; curl -fsSL https://claude.ai/install.sh | bash
  have claude || fail "Cai Claude Code chua xong. Kiem tra mang roi chay lai file nay."
fi
if have codex; then line "ChatGPT (Codex)" "da co"; else line "ChatGPT (Codex)" "dang cai..."; npm install -g @openai/codex >/dev/null 2>&1 || line "ChatGPT (Codex)" "loi (bo qua)"; fi
if have agy; then line "Gemini (Antigravity)" "da co"; else line "Gemini (Antigravity)" "dang cai..."; curl -fsSL https://antigravity.google/cli/install.sh | bash || line "Gemini (Antigravity)" "loi (bo qua)"; fi
line "Thu vien kiem tra file" "dang cai..."
"$PY" -m pip install --user --upgrade --quiet --disable-pip-version-check --break-system-packages pillow openpyxl python-docx python-pptx >/dev/null 2>&1 \
  || "$PY" -m pip install --user --upgrade --quiet pillow openpyxl python-docx python-pptx >/dev/null 2>&1

# Dung ban dang chay, ghi app
pkill -f "Application Support/Maestro" >/dev/null 2>&1
launchctl bootout "gui/$(id -u)/com.maestro.app" >/dev/null 2>&1
"$PY" - "$APP_HOME" <<'PYEOF'
import base64, sys, pathlib
h = pathlib.Path(sys.argv[1])
(h / "app.py").write_bytes(base64.b64decode("__APP_B64__"))
(h / "icon.png").write_bytes(base64.b64decode("__PNG_B64__"))
(h / "version.txt").write_text("__VER__")
for f in ("current.txt", "next.txt"):
    (h / f).unlink(missing_ok=True)
PYEOF
line "Ung dung" "xong"

# App Maestro trong Applications (mo lai bat cu luc nao; neu Maestro dang tat thi tu bat)
APPB="$HOME/Applications/Maestro.app"
rm -rf "$APPB"; mkdir -p "$APPB/Contents/MacOS" "$APPB/Contents/Resources"
cat > "$APPB/Contents/MacOS/Maestro" <<EOF
#!/bin/bash
export PATH="/opt/homebrew/bin:/usr/local/bin:\$HOME/.local/bin:\$PATH"
nohup "$PY" "$APP_HOME/app.py" >/dev/null 2>&1 &
EOF
chmod +x "$APPB/Contents/MacOS/Maestro"
cat > "$APPB/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleName</key><string>Maestro</string><key>CFBundleDisplayName</key><string>Maestro</string>
<key>CFBundleIdentifier</key><string>com.maestro.launcher</string><key>CFBundleExecutable</key><string>Maestro</string>
<key>CFBundleIconFile</key><string>Maestro</string><key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>__REL__</string><key>LSUIElement</key><true/>
</dict></plist>
EOF
ICS="$APP_HOME/Maestro.iconset"; rm -rf "$ICS"; mkdir -p "$ICS"
for s in 16 32 64 128 256 512; do
  sips -z $s $s "$APP_HOME/icon.png" --out "$ICS/icon_${s}x${s}.png" >/dev/null 2>&1
  d=$((s*2)); sips -z $d $d "$APP_HOME/icon.png" --out "$ICS/icon_${s}x${s}@2x.png" >/dev/null 2>&1
done
iconutil -c icns "$ICS" -o "$APPB/Contents/Resources/Maestro.icns" >/dev/null 2>&1; rm -rf "$ICS"
touch "$APPB"
line "App Maestro" "~/Applications/Maestro.app"

# Tu chay nen khi dang nhap may (tat/bat lai trong Cai dat cua Maestro)
AG="$HOME/Library/LaunchAgents/com.maestro.app.plist"; mkdir -p "$(dirname "$AG")"
cat > "$AG" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.maestro.app</string>
<key>ProgramArguments</key><array><string>$PY</string><string>$APP_HOME/app.py</string><string>--background</string></array>
<key>RunAtLoad</key><true/>
<key>EnvironmentVariables</key><dict><key>PATH</key><string>$PATH</string></dict>
<key>StandardOutPath</key><string>$APP_HOME/data/launchd.log</string><key>StandardErrorPath</key><string>$APP_HOME/data/launchd.log</string>
</dict></plist>
EOF
launchctl bootstrap "gui/$(id -u)" "$AG" >/dev/null 2>&1 || launchctl load -w "$AG" >/dev/null 2>&1
line "Tu mo khi dang nhap" "bat"

# Cho app len roi mo trinh duyet
URL=""
for i in $(seq 1 40); do
  for p in 80 8765; do
    if curl -s -m 1 "http://127.0.0.1:$p/icon.svg" | grep -q "<svg"; then
      URL="http://ai-orchestrator.localhost$([ $p = 80 ] || echo ":$p")/"; break 2
    fi
  done
  sleep 0.5
done
[ -n "$URL" ] || { "$APPB/Contents/MacOS/Maestro"; sleep 3; URL="http://ai-orchestrator.localhost/"; }
open "$URL"
echo ""
echo "  Xong. Trang Maestro da mo: $URL"
echo "  Mo lai: Launchpad / Applications > Maestro. Tat han: Cai dat > Thoat Maestro."
echo ""
sleep 2
