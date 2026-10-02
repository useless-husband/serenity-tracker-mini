#!/usr/bin/env python3
"""Serenity (@aleabitoreddit) 推文存檔查詢 + 跟單回測小工具。

資料來源：archive/ 是 GitHub yan-labs/serenity-aleabitoreddit 的推文存檔（第三方整理，非官方）。
股價來源：Yahoo Finance 公開圖表 API（還原權息收盤價）。只用 Python 內建模組，不用裝套件。

用法：
  python3 serenity.py update                 # 從 GitHub 拉最新推文存檔
  python3 serenity.py latest [N] [--full] [--en]
                                             # 最近 N 則推文（預設 10）
  python3 serenity.py tweet <推文ID> [--en]   # 印出某則推文全文（ID 就是連結最後那串數字）
  python3 serenity.py ticker AXTI [N] [--full] [--en]
                                             # 他提過 $AXTI 幾次、第一次/最後一次、最近 N 則
  python3 serenity.py search "InP" [N] [--full] [--en]
                                             # 用關鍵字搜推文
  python3 serenity.py top [N] [--days D]     # 存檔最後 D 天最常提的 N 檔（預設 15 檔、30 天）
  python3 serenity.py follow AXTI [--tweet 推文ID | --since YYYY-MM-DD] [--yahoo 代號]
                                             # 跟單回測：他提的時候買，到現在賺賠多少、中間最慘跌多少
                                             #   不加選項：第一次提到 vs 最後一次提到
                                             #   --tweet：從指定那則推文之後買（用來指定「明確表態」那則）
                                             #   --since：用那天美股收盤價買（不對應任何推文）
  python3 serenity.py scorecard [N] [--days D]
                                             # 存檔最後 D 天最常提的 N 檔，一次全部回測

推文預設翻成繁體中文顯示（用 Claude 的 haiku 模型翻，翻過的存在 live/zh.json，不會重翻）；
加 --en 改看英文原文（agent 自己讀時用這個，比較準也比較快）。
長推文預設截在 600 字；截掉的部分如果有提到你查的代號，會另外印出那一段。要看全文用 --full 或 tweet 指令。
「最近 D 天」都是從存檔最後一則往回算，不是從今天算。
"""
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
ARCHIVE = HERE / "archive"
TWEETS = ARCHIVE / "data" / "aleabitoreddit_tweets.json"
LIVE_TWEETS = HERE / "live" / "tweets.json"
ZH_CACHE = HERE / "live" / "zh.json"
TPE = ZoneInfo("Asia/Taipei")
NY = ZoneInfo("America/New_York")
TICKER_RE = re.compile(r"\$([A-Z]{1,6}(?:\.[A-Z]{1,2})?)(?![A-Za-z0-9])")

# 他常提、但不在美國掛牌的代號 → Yahoo 代號
YAHOO_MAP = {
    "SIVE": "SIVE.ST",  # Sivers Semiconductors（瑞典）
    "SOI": "SOI.PA",    # Soitec（法國）
    "IQE": "IQE.L",     # IQE（英國）
    "RPI": "RPI.L",     # Raspberry Pi（英國）
    "LPK": "LPK.DE",    # LPKF Laser（德國）
    "ETH": "ETH-USD",
    "BTC": "BTC-USD",
}
BENCHMARK = "QQQ"


def load_tweets():
    if not TWEETS.exists():
        sys.exit(f"找不到推文存檔：{TWEETS}\n請先執行：python3 serenity.py update")
    tweets = json.loads(TWEETS.read_text())
    # 合併 watch.py 即時抓到、GitHub 存檔還沒有的推文
    if LIVE_TWEETS.exists():
        ids = {t["id"] for t in tweets}
        tweets += [t for t in json.loads(LIVE_TWEETS.read_text()) if t["id"] not in ids]
    for t in tweets:
        t["_dt"] = datetime.fromisoformat(t["createdAtISO"].replace("Z", "+00:00"))
        t["_tickers"] = sorted(set(TICKER_RE.findall(t["text"])))
    tweets.sort(key=lambda t: t["_dt"])
    return tweets


def link(t):
    return t.get("sourceUrl") or f"https://x.com/aleabitoreddit/status/{t['id']}"


def find_claude():
    return shutil.which("claude") or next(
        (p for p in [Path.home() / ".local/bin/claude", Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")]
         if p.exists()), None)


def translate(tweets):
    """把推文翻成繁體中文，回傳 {推文ID: 中文}。翻過的存在 live/zh.json，同一則只翻一次。"""
    try:
        cache = json.loads(ZH_CACHE.read_text())
    except (FileNotFoundError, ValueError):
        cache = {}
    todo = [t for t in tweets if t["id"] not in cache and not t.get("subscriberOnly") and t["text"].strip()]
    if not todo:
        return cache
    claude = find_claude()
    if not claude:
        print("⚠️ 找不到 claude 指令，先顯示英文原文\n", file=sys.stderr)
        return cache
    print(f"（翻譯 {len(todo)} 則推文中，第一次比較慢，翻過的會存起來…）\n", file=sys.stderr)
    # 分小批、同時翻，比一大批快很多
    batches, cur, size = [], [], 0
    for t in todo:
        if cur and (len(cur) >= 3 or size + len(t["text"]) > 3000):
            batches.append(cur)
            cur, size = [], 0
        cur.append(t)
        size += len(t["text"])
    batches.append(cur)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for got in pool.map(lambda b: _translate_batch(claude, b), batches):
            cache.update(got)
    ZH_CACHE.parent.mkdir(exist_ok=True)
    ZH_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    return cache


def _translate_batch(claude, batch):
    payload = json.dumps({t["id"]: t["text"] for t in batch}, ensure_ascii=False)
    prompt = ("把下面 JSON 裡每則推文翻成台灣用語的繁體中文。$股票代號、數字、人名、英文公司名保持原樣，"
              "網址保留，簡體中文直接轉成繁體，語氣照原文，不要加解釋。"
              "只輸出一個 JSON 物件：key 是原本的推文 ID，value 是翻譯。\n\n" + payload)
    r = subprocess.run([str(claude), "-p", prompt, "--model", "haiku", "--strict-mcp-config",
                        "--no-session-persistence", "--output-format", "text",
                        "--system-prompt", "你是專業的財經翻譯，只輸出 JSON，不輸出其他文字。"],
                       capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
                       env={**os.environ, "MAX_THINKING_TOKENS": "0"})  # 翻譯不需要先思考，關掉快很多
    m = re.search(r"\{.*\}", r.stdout, re.S)
    try:
        got = json.loads(m.group(0)) if m else {}
    except ValueError:
        got = {}
    if not got:
        print(f"⚠️ 翻譯失敗，先顯示英文原文：{(r.stderr or r.stdout).strip()[:200]}\n", file=sys.stderr)
    return {k: v for k, v in got.items() if isinstance(v, str)}


def show(t, width=600, focus=None, full=False, zh=None):
    when = t["_dt"].astimezone(TPE).strftime("%Y-%m-%d %H:%M")
    kind = "回覆" if t.get("isReply") else ("引用" if t.get("isQuote") else "發文")
    translated = bool(zh and t["id"] in zh)
    text = re.sub(r"\s+", " ", zh[t["id"]] if translated else t["text"]).strip()
    extra = ""
    if not full and len(text) > width:
        # 被截掉的部分如果有提到要查的代號，另外印出那一段，才看得到他怎麼講
        pos = text.find(f"${focus}", width) if focus else -1
        if pos >= 0 and text.find(f"${focus}") >= width:
            extra = f"\n  ……（${focus} 出現在後面）…{text[max(width, pos - 250):pos + 350]}…"
        text = text[:width] + f"…（共 {len(text)} 字，全文：python3 serenity.py tweet {t['id']}）"
    views = (t.get("metrics") or {}).get("views")
    tag = "｜中譯" if translated else ""
    print(f"[{when} 台灣時間｜{kind}｜{views or '?'} 次瀏覽{tag}] {link(t)}")
    print(f"  {text}{extra}\n")


def archive_status(tweets):
    last = tweets[-1]["_dt"]
    age = datetime.now(timezone.utc) - last
    live = [t for t in tweets if t.get("source") == "live-watch"]
    print(f"存檔：共 {len(tweets)} 則，最新一則 {last.astimezone(TPE):%Y-%m-%d %H:%M}（台灣時間），距今 {age.days} 天")
    if live:
        gh_last = max(t["_dt"] for t in tweets if t.get("source") != "live-watch")
        print(f"　其中 {len(live)} 則是 watch.py 即時抓的（從 {live[0]['_dt'].astimezone(TPE):%m-%d} 起）；"
              f"GitHub 存檔只到 {gh_last.astimezone(TPE):%m-%d}，中間那段可能有漏，而且即時抓取收不到回覆別人的推文。")
    if age.days >= 2:
        print("⚠️ 最新一則已經是 2 天以前，更新的推文要另外上網查。")
    print()


# ---------- 股價 ----------

def yahoo_symbol(ticker, override=None):
    return override or YAHOO_MAP.get(ticker, ticker)


def fetch_prices(symbol, start):
    """回傳 [(日期, 收盤時間UTC, 還原收盤價)]，從 start 前幾天到今天。"""
    p1 = int((start - timedelta(days=7)).timestamp())
    p2 = int(datetime.now(timezone.utc).timestamp()) + 86400
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?period1={p1}&period2={p2}&interval=1d&includeAdjustedClose=true&events=div%2Csplits")
    # 用系統 curl：python.org 版 Python 在 Mac 上常缺 SSL 憑證
    r = subprocess.run(["curl", "-s", "-m", "20", "-A", "Mozilla/5.0", url], capture_output=True, text=True)
    try:
        data = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError(f"Yahoo 抓不到 {symbol}（curl 結束碼 {r.returncode}）")
    res = (data.get("chart") or {}).get("result")
    if not res or not res[0].get("timestamp"):
        raise RuntimeError(f"Yahoo 沒有 {symbol} 的股價（代號可能不對，可用 --yahoo 指定）")
    res = res[0]
    tz = ZoneInfo(res["meta"].get("exchangeTimezoneName") or "America/New_York")
    closes = (res["indicators"].get("adjclose") or [{}])[0].get("adjclose") \
        or res["indicators"]["quote"][0]["close"]
    out = []
    for ts, c in zip(res["timestamp"], closes):
        if c is None:
            continue
        day = datetime.fromtimestamp(ts, tz).date()
        # 用當地收盤時間判斷「推文發出後第一個能買到的收盤價」；加密貨幣視為當天 23:59
        close_hour = 23 if symbol.endswith("-USD") else 16
        close_at = datetime(day.year, day.month, day.day, close_hour, 0, tzinfo=tz)
        out.append((day, close_at.astimezone(timezone.utc), float(c)))
    return out, res["meta"].get("currency", "")


def backtest(symbol, entry_time):
    prices, cur = fetch_prices(symbol, entry_time)
    after = [p for p in prices if p[1] >= entry_time]
    if not after:
        raise RuntimeError(f"{symbol} 在 {entry_time:%Y-%m-%d} 之後還沒有收盤價")
    entry_day, _, entry = after[0]
    peak, peak_day, max_dd, dd_from, dd_to = entry, entry_day, 0.0, entry_day, entry_day
    for day, _, c in after:
        if c > peak:
            peak, peak_day = c, day
        if c / peak - 1 < max_dd:
            max_dd, dd_from, dd_to = c / peak - 1, peak_day, day
    hi_day, _, hi = max(after, key=lambda p: p[2])
    lo_day, _, lo = min(after, key=lambda p: p[2])
    last_day, _, last = after[-1]
    return {
        "symbol": symbol, "currency": cur, "entry_day": entry_day, "entry": entry,
        "last_day": last_day, "last": last, "ret": last / entry - 1,
        "best": hi / entry - 1, "best_day": hi_day, "worst": lo / entry - 1, "worst_day": lo_day,
        "max_dd": max_dd, "dd_from": dd_from, "dd_to": dd_to, "days": len(after) - 1,
    }


def pct(x):
    return f"{x * 100:+.1f}%"


# ---------- 指令 ----------

def cmd_update(_args):
    if not (ARCHIVE / ".git").exists():
        subprocess.run(["git", "clone", "--depth", "1",
                        "https://github.com/yan-labs/serenity-aleabitoreddit.git", str(ARCHIVE)], check=True)
    else:
        before = len(load_tweets())
        r = subprocess.run(["git", "-C", str(ARCHIVE), "pull", "--ff-only", "-q"], capture_output=True, text=True)
        if r.returncode != 0:
            print("⚠️ 更新失敗，先用舊的存檔：", r.stderr.strip()[:300])
        else:
            added = len(load_tweets()) - before
            upstream = subprocess.run(["git", "-C", str(ARCHIVE), "log", "-1", "--format=%ci"],
                                      capture_output=True, text=True).stdout.strip()
            if added > 0:
                print(f"✅ 拉到 {added} 則新推文")
            else:
                print(f"已經是 GitHub 上最新的版本，沒有新推文（上游最後更新：{upstream}）")
    archive_status(load_tweets())


def cmd_latest(args):
    opts, rest = parse_opts(args, full=False, en=False)
    n = int(rest[0]) if rest else 10
    tweets = load_tweets()
    archive_status(tweets)
    shown = tweets[-n:][::-1]
    zh = {} if opts["en"] else translate(shown)
    for t in shown:
        show(t, full=opts["full"], zh=zh)


def cmd_tweet(args):
    if not args:
        sys.exit("用法：python3 serenity.py tweet <推文ID>")
    opts, rest = parse_opts(args, en=False)
    tid = rest[0].rstrip("/").split("/")[-1].split("?")[0]
    for t in load_tweets():
        if t["id"] == tid:
            show(t, full=True, zh={} if opts["en"] else translate([t]))
            return
    print(f"存檔裡找不到推文 {tid}")


def cmd_ticker(args):
    if not args:
        sys.exit("用法：python3 serenity.py ticker AXTI [N]")
    opts, rest = parse_opts(args, full=False, en=False)
    tk = rest[0].upper().lstrip("$")
    n = int(rest[1]) if len(rest) > 1 else 8
    tweets = load_tweets()
    archive_status(tweets)
    hits = [t for t in tweets if tk in t["_tickers"]]
    if not hits:
        print(f"存檔裡沒有提過 ${tk}")
        return
    first, last = hits[0], hits[-1]
    recent30 = sum(1 for t in hits if t["_dt"] > tweets[-1]["_dt"] - timedelta(days=30))
    print(f"${tk}：共提過 {len(hits)} 次，最近 30 天 {recent30} 次")
    print(f"第一次：{first['_dt'].astimezone(TPE):%Y-%m-%d}　最後一次：{last['_dt'].astimezone(TPE):%Y-%m-%d}\n")
    recent = hits[-n:][::-1]
    zh = {} if opts["en"] else translate([first] + recent)
    print("── 第一次提到 ──")
    show(first, focus=tk, full=opts["full"], zh=zh)
    print(f"── 最近 {len(recent)} 則 ──")
    for t in recent:
        show(t, focus=tk, full=opts["full"], zh=zh)


def cmd_search(args):
    if not args:
        sys.exit('用法：python3 serenity.py search "關鍵字" [N]')
    opts, rest = parse_opts(args, full=False, en=False)
    q = rest[0].lower()
    n = int(rest[1]) if len(rest) > 1 else 10
    tweets = load_tweets()
    hits = [t for t in tweets if q in t["text"].lower()]
    print(f"「{rest[0]}」共 {len(hits)} 則，顯示最近 {min(n, len(hits))} 則\n")
    shown = hits[-n:][::-1]
    zh = {} if opts["en"] else translate(shown)
    for t in shown:
        show(t, full=opts["full"], zh=zh)


FLAGS = {"full", "en"}


def parse_opts(args, **defaults):
    opts, rest, i = dict(defaults), [], 0
    while i < len(args):
        if args[i][2:] in FLAGS and args[i].startswith("--"):
            opts[args[i][2:]] = True
            i += 1
        elif args[i].startswith("--") and i + 1 < len(args):
            opts[args[i][2:]] = args[i + 1]
            i += 2
        else:
            rest.append(args[i])
            i += 1
    return opts, rest


def top_tickers(tweets, n, days):
    since = tweets[-1]["_dt"] - timedelta(days=days)
    c = Counter(tk for t in tweets if t["_dt"] >= since for tk in t["_tickers"])
    return c.most_common(n)


def cmd_top(args):
    opts, rest = parse_opts(args, days="30")
    n, days = int(rest[0]) if rest else 15, int(opts["days"])
    tweets = load_tweets()
    archive_status(tweets)
    print(f"存檔最後 {days} 天最常提到的代號：")
    for tk, cnt in top_tickers(tweets, n, days):
        print(f"  ${tk:<8}{cnt:>4} 次")


def report(tk, label, when, override=None, bench=None, tweet=None):
    sym = yahoo_symbol(tk, override)
    r = backtest(sym, when)
    if tweet:
        print(f"【{label}】推文 {when.astimezone(TPE):%Y-%m-%d %H:%M} 台灣時間 {link(tweet)}")
        print(f"  → 最早能買的收盤日 {r['entry_day']}")
    else:
        print(f"【{label}】用 {r['entry_day']} 當天收盤價買進（這個日期不對應任何推文）")
    print(f"  買進 {r['entry']:.2f} {r['currency']} → {r['last_day']} {r['last']:.2f}　報酬 {pct(r['ret'])}（{r['days']} 個交易日）")
    print(f"  過程中：最高曾 {pct(r['best'])}（{r['best_day']}），最低曾 {pct(r['worst'])}（{r['worst_day']}）")
    print(f"  從高點最大回檔 {pct(r['max_dd'])}（{r['dd_from']} 高點 → {r['dd_to']} 低點）")
    if bench:
        b = backtest(BENCHMARK, when)
        print(f"  同期間買 {BENCHMARK}（那斯達克 100 ETF）：{pct(b['ret'])}，最大回檔 {pct(b['max_dd'])}")
    return r


def cmd_follow(args):
    opts, rest = parse_opts(args, since=None, yahoo=None, tweet=None)
    if not rest:
        sys.exit("用法：python3 serenity.py follow AXTI [--tweet 推文ID | --since YYYY-MM-DD] [--yahoo 代號]")
    tk = rest[0].upper().lstrip("$")
    tweets = load_tweets()
    archive_status(tweets)
    hits = [t for t in tweets if tk in t["_tickers"]]
    print(f"${tk} 跟單回測（Yahoo 代號 {yahoo_symbol(tk, opts['yahoo'])}，用還原權息收盤價；假設看到推文後用下一個收盤價買進）\n")
    try:
        if opts["tweet"]:
            tid = opts["tweet"].rstrip("/").split("/")[-1].split("?")[0]
            t = next((t for t in tweets if t["id"] == tid), None)
            if not t:
                print(f"存檔裡找不到推文 {tid}")
                return
            report(tk, "從指定推文之後買", t["_dt"], opts["yahoo"], bench=True, tweet=t)
        elif opts["since"]:
            d = datetime.fromisoformat(opts["since"]).replace(tzinfo=NY)
            report(tk, f"自訂日期 {opts['since']}", d, opts["yahoo"], bench=True)
        elif not hits:
            print(f"存檔裡沒有提過 ${tk}，可以用 --since 指定日期")
            return
        else:
            report(tk, f"第一次提到就跟（共提過 {len(hits)} 次）", hits[0]["_dt"], opts["yahoo"], bench=True, tweet=hits[0])
            print()
            report(tk, "最後一次提到才跟", hits[-1]["_dt"], opts["yahoo"], bench=True, tweet=hits[-1])
            print("\n提醒：「最後一次提到」可能只是順帶一提。先用 ticker 讀原文，"
                  "找到他最後一次明確表態那則，再用 --tweet 推文ID 重算。")
    except RuntimeError as e:
        print(f"⚠️ {e}")


def cmd_scorecard(args):
    opts, rest = parse_opts(args, days="30")
    n, days = int(rest[0]) if rest else 12, int(opts["days"])
    tweets = load_tweets()
    archive_status(tweets)
    print(f"存檔最後 {days} 天最常提的 {n} 檔｜「第一次提就買」與「最近一次提才買」到今天的報酬\n")
    print(f"{'代號':<8}{'提及':>5}  {'第一次提到':<11}{'到今天':>9}{'最大回檔':>9}   {'最近提到':<11}{'到今天':>9}")
    rows = []
    for tk, cnt in top_tickers(tweets, n, days):
        hits = [t for t in tweets if tk in t["_tickers"]]
        try:
            a = backtest(yahoo_symbol(tk), hits[0]["_dt"])
            b = backtest(yahoo_symbol(tk), hits[-1]["_dt"])
        except RuntimeError as e:
            print(f"${tk:<7} 跳過：{e}")
            continue
        rows.append((a["ret"], b["ret"]))
        print(f"${tk:<7}{len(hits):>5}  {hits[0]['_dt'].astimezone(TPE):%Y-%m-%d} {pct(a['ret']):>9}{pct(a['max_dd']):>9}   "
              f"{hits[-1]['_dt'].astimezone(TPE):%Y-%m-%d} {pct(b['ret']):>9}")
    if rows:
        avg_a = sum(r[0] for r in rows) / len(rows)
        avg_b = sum(r[1] for r in rows) / len(rows)
        win_b = sum(1 for r in rows if r[1] > 0)
        print(f"\n平均（每檔同金額）：第一次就跟 {pct(avg_a)}；最近才跟 {pct(avg_b)}，{win_b}/{len(rows)} 檔目前賺錢")
    print("「最大回檔」= 抱著的期間，從最高點跌下來最多跌了幾 %（會不會嚇到你賣掉，看這欄）。")
    print("注意：這張表挑的是他『最近還在講』的股票，本來就偏向贏家；他講過後來不再提的輸家不在表裡。")


COMMANDS = {
    "update": cmd_update, "latest": cmd_latest, "tweet": cmd_tweet, "ticker": cmd_ticker, "search": cmd_search,
    "top": cmd_top, "follow": cmd_follow, "scorecard": cmd_scorecard,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(0)
    COMMANDS[sys.argv[1]](sys.argv[2:])
