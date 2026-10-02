#!/usr/bin/env python3
"""每隔幾分鐘看一次 Serenity 的 X 公開頁面，有新推文就存起來，交給 serenity agent 分析，再通知你。

- 不用登入：抓 x.com 公開個人頁拿推文 ID，再用 X 的嵌入推文網址（cdn.syndication.twimg.com）拿全文。
- 新推文存在 live/tweets.json，serenity.py 會自動把它跟 GitHub 存檔合併。
- config.json 的 analyze 打開時，會用 `claude -p --agent serenity` 寫中文快報，存到 reports/，
  有填 imessage_to 就用 iMessage 傳到手機，沒填就跳 Mac 通知。
- 由 launchd 定時執行（com.serenity.watch），紀錄在 live/watch.log。

用法：
  python3 watch.py            # 檢查一次
  python3 watch.py --quiet    # 檢查一次，只存檔，不分析也不通知（agent 手動查詢時用）
  python3 watch.py --test     # 拿最近 2 則推文試跑一次分析＋傳送，確認整條流程會通
"""
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

USER = "aleabitoreddit"
HERE = Path(__file__).resolve().parent
LIVE = HERE / "live"
LIVE_TWEETS = LIVE / "tweets.json"
PENDING = LIVE / "pending.json"
STATE = LIVE / "state.json"
REPORTS = HERE / "reports"
CONFIG = HERE / "config.json"
ARCHIVE_TWEETS = HERE / "archive" / "data" / "aleabitoreddit_tweets.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
TPE = ZoneInfo("Asia/Taipei")
TICKER_RE = re.compile(r"\$([A-Z]{1,6}(?:\.[A-Z]{1,2})?)(?![A-Za-z0-9])")

DEFAULT_CONFIG = {
    "analyze": False,             # 有新推文時要不要叫 serenity agent 分析
    "imessage_to": "",            # 快報要傳到哪（iPhone 電話號碼或 Apple ID）；空白 = 只跳 Mac 通知
    "model": "sonnet",            # 自動分析用的模型（sonnet 比較省用量）
    "max_analyses_per_day": 12,   # 一天最多分析幾次，超過就只傳原文
    "quiet_hours": [0, 8],        # 這段時間（台灣時間，幾點到幾點）先累積，時間到再一次傳
}


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))


def curl(url):
    r = subprocess.run(["curl", "-sL", "-m", "25", "-A", UA, url], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"curl 結束碼 {r.returncode}：{url}")
    return r.stdout


# ---------- 抓推文 ----------

def embed_token(tweet_id):
    # 和 X 官方嵌入元件一樣的算法：((id / 1e15) * π) 轉 36 進位，去掉 0 和小數點
    x = int(tweet_id) / 1e15 * math.pi
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    ip, fp, s = int(x), x - int(x), ""
    while ip:
        s, ip = digits[ip % 36] + s, ip // 36
    frac = ""
    for _ in range(12):
        fp *= 36
        frac += digits[int(fp)]
        fp -= int(fp)
    return re.sub(r"(0+|\.)", "", (s or "0") + "." + frac)


def profile_ids():
    page = curl(f"https://x.com/{USER}")
    ids = list(dict.fromkeys(re.findall(rf"/{USER}/status/(\d{{15,20}})", page)))
    if not ids:
        raise RuntimeError(f"個人頁抓到 {len(page)} 字，但找不到推文 ID（X 可能改版或擋了）")
    return ids


def fetch_tweet(tweet_id):
    d = json.loads(curl(f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&lang=en&token={embed_token(tweet_id)}"))
    created = datetime.fromtimestamp(((int(tweet_id) >> 22) + 1288834974657) / 1000, timezone.utc)
    base = {"id": tweet_id, "createdAtISO": created.isoformat(),
            "sourceUrl": f"https://x.com/{USER}/status/{tweet_id}", "source": "live-watch"}
    if d.get("__typename") == "TweetTombstone":
        return {**base, "text": "（付費訂閱者限定的推文，看不到內容）", "subscriberOnly": True}
    if (d.get("user") or {}).get("screen_name", "").lower() != USER:
        return None  # 別人的推文（例如他轉推的），不收
    text = (d.get("note_tweet") or {}).get("text") or d.get("text") or ""
    return {**base, "text": text, "isReply": bool(d.get("in_reply_to_screen_name")),
            "isQuote": "quoted_tweet" in d,
            "metrics": {"likes": d.get("favorite_count"), "replies": d.get("conversation_count")}}


def refresh_archive():
    """GitHub 存檔每 6 小時拉一次就好。"""
    git_dir = HERE / "archive" / ".git"
    stamp = git_dir / "FETCH_HEAD"
    if not git_dir.exists():
        return
    if stamp.exists() and datetime.now().timestamp() - stamp.stat().st_mtime < 6 * 3600:
        return
    r = subprocess.run(["git", "-C", str(HERE / "archive"), "pull", "--ff-only", "-q"], capture_output=True, text=True)
    log("GitHub 存檔已更新" if r.returncode == 0 else f"⚠️ GitHub 存檔更新失敗：{r.stderr.strip()[:200]}")


def check_new():
    live = load_json(LIVE_TWEETS, [])
    known = {t["id"] for t in live} | {t["id"] for t in load_json(ARCHIVE_TWEETS, [])}
    ids = profile_ids()
    new = []
    for tid in ids:
        if tid in known:
            continue
        try:
            t = fetch_tweet(tid)
        except Exception as e:
            log(f"⚠️ 推文 {tid} 抓不到：{e}")
            continue
        if t:
            new.append(t)
    if new:
        live.extend(new)
        live.sort(key=lambda t: t["createdAtISO"])
        save_json(LIVE_TWEETS, live)
        for t in sorted(new, key=lambda t: t["createdAtISO"]):
            tickers = " ".join("$" + x for x in sorted(set(TICKER_RE.findall(t["text"]))))
            log(f"新推文 {t['sourceUrl']} {tickers} {t['text'][:80]!r}")
    else:
        log(f"沒有新推文（公開頁 {len(ids)} 則都看過了）")
    return sorted(new, key=lambda t: t["createdAtISO"])


# ---------- 分析與傳送 ----------

def tpe_time(t):
    dt = datetime.fromisoformat(t["createdAtISO"].replace("Z", "+00:00"))
    return dt.astimezone(TPE).strftime("%m/%d %H:%M")


def raw_digest(tweets, reason=""):
    try:
        from serenity import translate  # 同資料夾的 serenity.py，翻成中文再傳
        zh = translate(tweets)
    except Exception as e:
        log(f"⚠️ 翻譯失敗，傳英文原文：{e}")
        zh = {}
    lines = [f"Serenity 新推文 {len(tweets)} 則{reason}"]
    for t in tweets:
        text = zh.get(t["id"]) or t["text"]
        lines.append(f"\n{tpe_time(t)}\n{re.sub(r'\s+', ' ', text)[:400]}\n{t['sourceUrl']}")
    return "\n".join(lines)


def find_claude():
    return shutil.which("claude") or next(
        (p for p in [Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")]
         if p.exists()), None)


def analyze(tweets, cfg):
    claude = find_claude()
    if not claude:
        raise RuntimeError("找不到 claude 指令（Claude Code 沒裝或不在 PATH）")
    listing = "\n\n".join(
        f"推文ID {t['id']}｜{tpe_time(t)} 台灣時間｜{t['sourceUrl']}\n{t['text']}" for t in tweets)
    prompt = f"""Serenity 剛發了下面 {len(tweets)} 則新推文。watch.py 已經抓好並存檔，不用再跑 update 或 watch.py。

請寫一份會用 iMessage 傳到手機的中文快報。這是字數有指定的情況：總長 500 字以內、純文字，
不要 Markdown 表格、不要 # 標題、不要粗體符號，也不要附來源清單。格式：
1. 第一行：一句話總結他這次在講什麼。
2. 每則一段：時間、中文重點 1～2 句、他的立場（明確持有／加碼／賣出／看多／看空／只是評論／閒聊）、提到的代號。
3. 只有他「明確表態」持有、買進或賣出的代號，才跑 `python3 serenity.py follow 代號`，
   用一行寫「他第一次提到至今報酬、期間從高點最大回檔」。閒聊、評論、新聞轉述都不要跑，也不要上網查。
4. 最後一行：對照使用者（跌 30% 會想賣、跟單上限 7,500 元玩樂金）的一句提醒，結尾加「（非投資建議）」。

{listing}"""
    r = subprocess.run(
        [str(claude), "-p", prompt, "--agent", "serenity", "--model", cfg["model"],
         "--allowedTools", "Bash(python3 serenity.py *)", "Read", "Grep", "--strict-mcp-config",
         "--no-session-persistence", "--output-format", "text"],
        cwd=HERE, capture_output=True, text=True, timeout=900, stdin=subprocess.DEVNULL)
    out = r.stdout.strip()
    if r.returncode != 0 or not out:
        raise RuntimeError(f"claude 結束碼 {r.returncode}：{(r.stderr or out)[:300]}")
    return out


def send_imessage(to, text):
    script = ['on run argv',
              'tell application "Messages"',
              'set svc to 1st account whose service type = iMessage',
              'send (item 1 of argv) to participant (item 2 of argv) of svc',
              'end tell', 'end run']
    args = ["osascript"] + sum([["-e", line] for line in script], []) + [text, to]
    r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"iMessage 傳送失敗：{r.stderr.strip()[:300]}")


def notify(title, body):
    script = f"display notification {json.dumps(body[:200])} with title {json.dumps(title)} sound name \"Glass\""
    subprocess.run(["osascript", "-e", script], capture_output=True)


def deliver(text, cfg, title="Serenity 快報"):
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"{datetime.now():%Y%m%d-%H%M%S}.txt"
    path.write_text(text)
    if cfg.get("imessage_to"):
        try:
            send_imessage(cfg["imessage_to"], text)
            log(f"已用 iMessage 傳出（{path.name}）")
            return
        except Exception as e:
            log(f"⚠️ {e}，改跳 Mac 通知")
    notify(title, text.splitlines()[0] if text else "")
    log(f"已跳 Mac 通知（全文在 reports/{path.name}）")


def process_pending(cfg, force=False):
    pending = load_json(PENDING, [])
    if not pending:
        return
    start, end = cfg["quiet_hours"]
    hour = datetime.now(TPE).hour
    if not force and start != end and (start <= hour < end if start < end else hour >= start or hour < end):
        log(f"安靜時段，先累積 {len(pending)} 則")
        return
    real = [t for t in pending if not t.get("subscriberOnly")]
    state = load_json(STATE, {})
    today = datetime.now(TPE).strftime("%Y-%m-%d")
    used = state.get("analyses", 0) if state.get("date") == today else 0
    if not cfg["analyze"] or not real:
        text = raw_digest(pending)
    elif used >= cfg["max_analyses_per_day"]:
        text = raw_digest(pending, f"（今天已分析 {used} 次，達上限，先傳原文）")
    else:
        try:
            text = analyze(real, cfg)
            save_json(STATE, {"date": today, "analyses": used + 1})
            locked = len(pending) - len(real)
            if locked:
                text += f"\n\n另有 {locked} 則付費訂閱者限定，看不到內容。"
        except Exception as e:
            log(f"⚠️ 分析失敗：{e}")
            text = raw_digest(pending, "（自動分析失敗，先傳原文）")
    deliver(text, cfg)
    save_json(PENDING, [])


def main():
    quiet = "--quiet" in sys.argv
    cfg = {**DEFAULT_CONFIG, **load_json(CONFIG, {})}
    LIVE.mkdir(exist_ok=True)

    # 安裝程式會放這個檔案再叫 launchd 跑一次，讓「控制訊息 App」的權限視窗在安裝當下跳出來
    test_flag = LIVE / "test_request"
    if test_flag.exists():
        test_flag.unlink()
        sys.argv.append("--test")

    if "--test" in sys.argv:
        sample = load_json(LIVE_TWEETS, [])[-2:]
        if not sample:
            sys.exit("live/tweets.json 還沒有推文，先跑一次 python3 watch.py --quiet")
        log(f"測試：拿最近 {len(sample)} 則試跑分析（{'有' if cfg['analyze'] else '沒有'}開分析）")
        save_json(PENDING, load_json(PENDING, []) + sample)
        process_pending(cfg, force=True)
        return

    refresh_archive()
    try:
        new = check_new()
    except Exception as e:
        log(f"⚠️ {e}")
        new = []
    if quiet:
        return
    if new:
        save_json(PENDING, load_json(PENDING, []) + new)
    process_pending(cfg)


if __name__ == "__main__":
    main()
