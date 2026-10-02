# Serenity Tracker mini（Serenity 自動快報，Mac mini 安裝包）

**English summary.** A tiny, self-contained installer that turns an always-on Mac (e.g. a Mac mini) into a Serenity ([@aleabitoreddit](https://x.com/aleabitoreddit)) watcher: every 10 minutes `watch.py` checks his public X page without logging in, and when a new tweet appears it asks a local Claude Code agent to write a short Traditional Chinese briefing and delivers it by iMessage (or a macOS notification). `serenity.py` is the same archive/backtest CLI as [serenity-tracker](https://github.com/useless-husband/serenity-tracker), minus the web UI. Not affiliated with Serenity. Not investment advice. Built with the help of Claude.

---

## 這是什麼

[serenity-tracker](https://github.com/useless-husband/serenity-tracker) 的精簡版，專門給一台 24 小時開著的 Mac（例如 Mac mini）用：

- 每 10 分鐘看一次 Serenity 的 X 公開頁，有新推文就存下來。
- 交給本機的 Claude Code agent（`serenity`）用繁體中文寫一段快報：他講了什麼、提到哪些股票、是持有／加碼／減碼還是只是閒聊、跟單回測結果。
- 用 iMessage 傳到你的 iPhone；沒填電話就改跳 Mac 通知。半夜 0～8 點的推文會累積到 8 點再一次傳。

不含網頁介面，只有命令列與背景排程。只用 Python 內建模組。

## 檔案

| 檔案 | 用途 |
|---|---|
| `setup-mini.command` | 一鍵安裝：檢查工具 → 複製程式到 `~/SerenityTracker` → 安裝 agent → 檢查 Claude Code → 問快報要傳到哪 → 設定 launchd 每 10 分鐘執行 → 跑一次測試 |
| `uninstall-mini.command` | 停用排程並（可選）刪除資料與 agent |
| `watch.py` | 抓新推文、呼叫 Claude 分析、iMessage／通知 |
| `serenity.py` | 推文查詢與跟單回測命令列工具（agent 會呼叫它） |
| `serenity-agent.example.md` | `serenity` agent 的設定範本，請複製成 `serenity-agent.md` 並填入自己的狀況（見下） |
| `使用說明.txt` | 給初學者看的白話安裝步驟 |

## 怎麼裝

Mac mini 上需要：macOS 13 以上、網路、「訊息」App 已登入 iMessage（要傳手機才需要）。

```bash
git clone https://github.com/useless-husband/serenity-tracker-mini.git ~/Downloads/SerenityTracker-mini
cd ~/Downloads/SerenityTracker-mini

# 1. 先準備 agent 設定檔：複製範本，打開把「使用者背景」那段改成自己的狀況
cp serenity-agent.example.md serenity-agent.md
open -e serenity-agent.md

# 2. 執行安裝程式，照畫面做
bash setup-mini.command
```

安裝程式會：

1. 檢查 `git`、`python3`（沒有會跳出 Xcode 命令列工具的安裝視窗）。
2. 把程式複製到 `~/SerenityTracker`，並 clone 第三方推文存檔到 `~/SerenityTracker/archive`。
3. 把 `serenity-agent.md` 裝到 `~/.claude/agents/serenity.md`。
4. 檢查 [Claude Code](https://claude.com/claude-code) 有沒有裝好、登入。
5. 問快報要傳到哪（iPhone 電話或 Apple ID Email；直接按 Enter = 只跳 Mac 通知），存到 `~/SerenityTracker/config.json`。
6. 先記下目前的推文，避免一裝好就把舊推文全部傳給你。
7. 設定 launchd（`com.serenity.watch`）每 600 秒執行一次 `watch.py`，並跑一次測試。

之後想改設定：`open -e ~/SerenityTracker/config.json`（`analyze`、`imessage_to`、`model`、`max_analyses_per_day`、`quiet_hours`），改完不用重開。看紀錄：`tail -n 30 ~/SerenityTracker/live/watch.log`。移除：`bash uninstall-mini.command`。

也可以不裝排程，直接在任何電腦上用命令列：`python3 serenity.py update`、`python3 serenity.py latest 10 --en`、`python3 serenity.py follow AAOI` 等，完整用法見 `serenity.py` 開頭說明。

## 為什麼 agent 檔要自己填

`serenity-agent.md` 裡有一段「使用者背景」（本金多少、跌多少會想賣），agent 每次下結論都會對照它。這是個人資料，所以 repo 只放去識別化的範本 `serenity-agent.example.md`；`serenity-agent.md` 已列在 `.gitignore`，你填好的版本只會留在自己的電腦。

## 資料來源

- 推文存檔：第三方整理的 [yan-labs/serenity-aleabitoreddit](https://github.com/yan-labs/serenity-aleabitoreddit)（非官方，安裝時自動 clone，不隨本 repo 散布）。
- 即時推文：x.com 公開個人頁與 X 官方嵌入推文端點，不登入、不用任何 cookie 或 token，所以只抓得到公開頁上給訪客看的幾則。
- 股價：Yahoo Finance 公開圖表 API。
- 分析與翻譯：本機 Claude Code CLI（`claude -p --agent serenity`）。

## 免責聲明

- **不是投資建議**。所有回測數字都來自公開資料，過去績效不代表未來。
- **與 Serenity 本人無關**，不是官方工具，未經其背書。他的官方管道只有 X 帳號 @aleabitoreddit；任何以他名義的跟單群組、App、私訊都應視為詐騙警訊。
- `setup-mini.command` 會在你的電腦上安裝 launchd 排程、寫入 `~/.claude/agents/`、並可能提示安裝 Claude Code；請先讀過腳本再執行。

## 關於開發

由 [useless-husband](https://github.com/useless-husband) 在 Claude（Anthropic 的 AI 助理，透過 Claude Code）協助下開發。程式碼以 [MIT License](LICENSE) 釋出。
