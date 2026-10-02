#!/bin/bash
# Serenity 自動快報：停用並移除
# 用法：bash ~/Downloads/SerenityTracker-mini/uninstall-mini.command

DEST="${SERENITY_DEST:-$HOME/SerenityTracker}"
LABEL="${SERENITY_LABEL:-com.serenity.watch}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && echo "已停止每 10 分鐘自動檢查" || echo "自動檢查本來就沒在跑"
rm -f "$PLIST"

if [ -n "$SERENITY_YES" ]; then
  yn=y
else
  read -r -p "要一起刪掉 $DEST（推文存檔、快報紀錄）嗎？(y/n) " yn
fi
[ "$yn" = "y" ] && rm -rf "$DEST" && echo "已刪除 $DEST"

if [ -z "$SERENITY_SKIP_AGENT" ]; then
  read -r -p "要一起移除 serenity agent 嗎？(y/n) " yn
  [ "$yn" = "y" ] && rm -f "$HOME/.claude/agents/serenity.md" && echo "已移除 serenity agent"
fi
echo "完成"
