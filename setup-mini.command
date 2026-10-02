#!/bin/bash
# Serenity 自動快報：在 Mac mini 上安裝（使用者 2026-09-28 要求建立）
# 用法：打開「終端機」，輸入  bash ~/Downloads/SerenityTracker-mini/setup-mini.command  按 Enter
set -e

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${SERENITY_DEST:-$HOME/SerenityTracker}"
LABEL="${SERENITY_LABEL:-com.serenity.watch}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

say() { printf "\n\033[1m%s\033[0m\n" "$1"; }

say "【1/7】檢查開發者工具（git、python3 要用）"
if ! xcode-select -p >/dev/null 2>&1; then
  echo "還沒裝，現在會跳出安裝視窗，請按「安裝」。"
  xcode-select --install || true
  echo "裝完之後，請再執行一次這個安裝程式。"
  exit 1
fi
PY="$(command -v python3)"
"$PY" -c "import zoneinfo" || { echo "python3 版本太舊（需要 3.9 以上）"; exit 1; }
echo "OK：$PY"

say "【2/7】複製程式到 $DEST"
mkdir -p "$DEST/live"
cp "$SRC/serenity.py" "$SRC/watch.py" "$DEST/"
if [ ! -d "$DEST/archive/.git" ]; then
  git clone --depth 1 https://github.com/yan-labs/serenity-aleabitoreddit.git "$DEST/archive"
else
  git -C "$DEST/archive" pull --ff-only -q || true
fi
echo "OK"

say "【3/7】安裝 serenity agent"
mkdir -p "$HOME/.claude/agents"
if [ -z "$SERENITY_SKIP_AGENT" ]; then
  [ -f "$HOME/.claude/agents/serenity.md" ] && cp "$HOME/.claude/agents/serenity.md" "$HOME/.claude/agents/serenity.md.bak"
  cp "$SRC/serenity-agent.md" "$HOME/.claude/agents/serenity.md"
fi
echo "OK"

say "【4/7】檢查 Claude Code"
CLAUDE="$(command -v claude || true)"
[ -z "$CLAUDE" ] && [ -x "$HOME/.local/bin/claude" ] && CLAUDE="$HOME/.local/bin/claude"
if [ -z "$CLAUDE" ]; then
  read -r -p "這台還沒裝 Claude Code，要現在安裝嗎？(y/n) " yn
  if [ "$yn" = "y" ]; then
    curl -fsSL https://claude.ai/install.sh | bash
    CLAUDE="$HOME/.local/bin/claude"
  else
    echo "沒有 Claude Code 就不能自動分析。裝好之後再執行一次這個安裝程式。"
    exit 1
  fi
fi
echo "測試 Claude 能不能用（約 10 秒）…"
if ! (cd "$DEST" && "$CLAUDE" -p "只回覆 OK 兩個字" --model sonnet --no-session-persistence 2>/dev/null | grep -q OK); then
  echo ""
  echo "Claude 還沒登入。請在終端機輸入  claude  按 Enter，照畫面登入你的帳號，"
  echo "登入完輸入 /exit 離開，再執行一次這個安裝程式。"
  exit 1
fi
echo "OK：$CLAUDE"

say "【5/7】快報要傳到哪裡？"
if [ -n "${SERENITY_IMESSAGE+x}" ]; then
  TO="$SERENITY_IMESSAGE"
else
  echo "輸入你 iPhone 的電話號碼（例如 +886912345678）或 Apple ID 的 Email，"
  echo "這台 Mac mini 的「訊息」App 要先登入 iMessage。"
  read -r -p "直接按 Enter = 只在 Mac mini 上跳通知：" TO
fi
"$PY" - "$DEST/config.json" "$TO" <<'EOF'
import json, sys
path, to = sys.argv[1], sys.argv[2].strip()
try:
    cfg = json.load(open(path))
except Exception:
    cfg = {}
cfg.update({"analyze": True, "imessage_to": to, "model": cfg.get("model", "sonnet"),
            "max_analyses_per_day": cfg.get("max_analyses_per_day", 12),
            "quiet_hours": cfg.get("quiet_hours", [0, 8])})
json.dump(cfg, open(path, "w"), ensure_ascii=False, indent=1)
print("設定存在", path, "→", to or "只跳 Mac 通知")
EOF

say "【6/7】先記下目前的推文（避免一裝好就把舊推文全部傳給你）"
(cd "$DEST" && "$PY" watch.py --quiet)

say "【7/7】設定每 10 分鐘自動檢查"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key><string>$LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>$PY</string>
		<string>$DEST/watch.py</string>
	</array>
	<key>WorkingDirectory</key><string>$DEST</string>
	<key>EnvironmentVariables</key>
	<dict>
		<key>PATH</key><string>$(dirname "$CLAUDE"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
	</dict>
	<key>StartInterval</key><integer>600</integer>
	<key>RunAtLoad</key><false/>
	<key>StandardOutPath</key><string>$DEST/live/watch.log</string>
	<key>StandardErrorPath</key><string>$DEST/live/watch.log</string>
</dict>
</plist>
EOF
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"

echo ""
echo "現在用背景排程跑一次測試：拿他最近 2 則推文分析，再傳給你。"
echo "⚠️ 如果螢幕跳出「python3 想要控制『訊息』」或通知權限的視窗，請按「允許」。"
touch "$DEST/live/test_request"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
for i in $(seq 1 24); do
  sleep 5
  grep -q "已用 iMessage 傳出\|已跳 Mac 通知\|分析失敗" "$DEST/live/watch.log" 2>/dev/null && break
done
tail -n 4 "$DEST/live/watch.log"

say "安裝完成 ✅"
cat <<EOF
- 每 10 分鐘檢查一次；半夜 0～8 點的新推文會累積到 8 點再一次傳。
- 手機沒收到測試訊息 → 看上面最後幾行紀錄，或打開 $DEST/reports/ 看快報全文。
- 看紀錄：    tail -n 30 $DEST/live/watch.log
- 改設定：    open -e $DEST/config.json   （改完不用重開，下次檢查就生效）
- 停用並移除：bash "$SRC/uninstall-mini.command"
EOF
