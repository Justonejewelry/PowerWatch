#!/usr/bin/env python3
"""
Insider Signal Engine v1 — Chica-style, but for Wall Street.

Watches what people in power do with their own money:
  - Corporate insiders (SEC Form 4): officers, directors, 10% owners.
    Open-market PURCHASES (code P) are the core bullish signal — insiders
    sell for many reasons (taxes, diversification, planned 10b5-1 sales),
    but they buy with their own cash for basically one reason.
  - Cluster buying (several insiders at the same company buying in the
    same window) scores much higher than a lone buyer.

Data: SEC EDGAR daily filing index + Form 4 XML (official, free).
Prices: Yahoo Finance chart API (for the prediction log / later grading).

Outputs:
  data/signals-<date>.json   raw scored signals
  reports/report-<date>.md   human-readable ranked report
  predictions.csv            appended log of top calls (graded later)
"""
import argparse, csv, json, math, os, re, sys, threading, time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "PepperResearch/1.0 (mr.jsciaraffa@gmail.com)"}
SEC = "https://www.sec.gov"

# ---- polite rate limiting for SEC (fair access: max 10 req/s) ----
_rate_lock = threading.Lock()
_last_req = [0.0]
MIN_INTERVAL = 0.13  # ~7.5 req/s ceiling

def get(url, **kw):
    with _rate_lock:
        wait = MIN_INTERVAL - (time.time() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        _last_req[0] = time.time()
    r = requests.get(url, headers=UA, timeout=30, **kw)
    r.raise_for_status()
    return r

# ---------------------------------------------------------------- EDGAR ----
def quarter_path(d):
    return f"{d.year}/QTR{(d.month - 1) // 3 + 1}"

IDX_LINE = re.compile(
    r"^(\S+)\s+(.+?)\s+(\d{7,10})\s+(\d{8})\s+(edgar/data/\S+)$")

def fetch_form4_index(d):
    url = f"{SEC}/Archives/edgar/daily-index/{quarter_path(d)}/form.{d:%Y%m%d}.idx"
    text = get(url).text
    out = []
    for line in text.splitlines():
        m = IDX_LINE.match(line.strip())
        if m and m.group(1) == "4":
            out.append({"cik": m.group(3), "filed": m.group(4), "path": m.group(5)})
    return out

def txt(el, path):
    n = el.find(path)
    return (n.text or "").strip() if n is not None and n.text else ""

def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0

def parse_form4(xml_bytes):
    root = ET.fromstring(xml_bytes)
    symbol = txt(root, "./issuer/issuerTradingSymbol").upper()
    if symbol == "NONE" or not re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", symbol):
        return None  # funds / non-listed issuers have no tradeable ticker
    issuer = txt(root, "./issuer/issuerName")
    owner = txt(root, "./reportingOwner/reportingOwnerId/rptOwnerName")
    rel = root.find("./reportingOwner/reportingOwnerRelationship")
    is_dir = txt(rel, "isDirector") in ("1", "true") if rel is not None else False
    is_off = txt(rel, "isOfficer") in ("1", "true") if rel is not None else False
    is_10 = txt(rel, "isTenPercentOwner") in ("1", "true") if rel is not None else False
    title = txt(rel, "officerTitle") if rel is not None else ""
    footnotes = " ".join((f.text or "") for f in root.iter("footnote"))
    plan = "10b5-1" in footnotes
    txns = []
    for t in root.iter("nonDerivativeTransaction"):
        code = txt(t, "./transactionCoding/transactionCode")
        shares = num(txt(t, "./transactionAmounts/transactionShares/value"))
        price = num(txt(t, "./transactionAmounts/transactionPricePerShare/value"))
        ad = txt(t, "./transactionAmounts/transactionAcquiredDisposedCode/value")
        tdate = txt(t, "./transactionDate/value")
        if code:
            txns.append({"code": code, "shares": shares, "price": price,
                         "ad": ad, "date": tdate, "value": shares * price})
    if not symbol or not txns:
        return None
    return {"symbol": symbol, "issuer": issuer, "owner": owner,
            "is_director": is_dir, "is_officer": is_off, "is_10pct": is_10,
            "title": title, "plan10b51": plan, "txns": txns}

OWNERSHIP_DOC = re.compile(
    r"<ownershipDocument>.*?</ownershipDocument>", re.DOTALL)

def process_filing(f):
    """Fetch the complete-submission .txt and parse every ownershipDocument
    inside it (joint filings can carry more than one). Returns a list."""
    try:
        text = get(f"{SEC}/Archives/{f['path']}").text
    except Exception:
        return []
    records = []
    for seg in OWNERSHIP_DOC.findall(text):
        try:
            rec = parse_form4(seg.encode())
        except Exception:
            rec = None
        if rec:
            records.append(rec)
    return records

# ---------------------------------------------------------------- scoring ----
def role_points(rec):
    title = (rec["title"] or "").lower()
    if rec["is_officer"]:
        if "chief executive" in title or title.strip() in ("ceo",) or "president" in title and "vice" not in title:
            return 3.0, rec["title"] or "Officer"
        if "chief financial" in title or "cfo" in title:
            return 3.0, rec["title"] or "Officer"
        return 2.0, rec["title"] or "Officer"
    if rec["is_director"]:
        return 1.5, "Director"
    if rec["is_10pct"]:
        return 1.0, "10% owner"
    return 0.5, "Insider"

def score_issuers(records):
    by_sym = {}
    for rec in records:
        s = by_sym.setdefault(rec["symbol"], {
            "symbol": rec["symbol"], "issuer": rec["issuer"],
            "buyers": {}, "buy_value": 0.0, "sell_value": 0.0,
            "sell_value_plan": 0.0, "sellers": {}, "buy_txns": []})
        for t in rec["txns"]:
            if t["code"] == "P" and t["ad"] == "A":
                b = s["buyers"].setdefault(rec["owner"], {
                    "owner": rec["owner"], "value": 0.0,
                    "role_pts": 0.0, "role": ""})
                b["value"] += t["value"]
                pts, role = role_points(rec)
                b["role_pts"] = max(b["role_pts"], pts)
                b["role"] = role
                s["buy_value"] += t["value"]
                s["buy_txns"].append({"owner": rec["owner"], "role": role,
                                      "value": t["value"], "date": t["date"],
                                      "price": t["price"], "shares": t["shares"]})
            elif t["code"] == "S" and t["ad"] == "D":
                if rec["plan10b51"]:
                    s["sell_value_plan"] += t["value"]
                else:
                    s["sell_value"] += t["value"]
                    s["sellers"][rec["owner"]] = s["sellers"].get(rec["owner"], 0) + t["value"]
    signals = []
    for s in by_sym.values():
        if s["buy_value"] <= 0:
            continue
        score = 0.0
        for b in s["buyers"].values():
            score += b["role_pts"]
            if b["value"] >= 10_000:
                score += min(3.0, math.log10(b["value"] / 10_000) + 0.5)
        n_buyers = len(s["buyers"])
        if n_buyers == 2:
            score += 2.0
        elif n_buyers >= 3:
            score += 4.0
        mixed = s["sell_value"] > s["buy_value"]
        if mixed:
            score *= 0.4
        signals.append({**s, "n_buyers": n_buyers, "score": round(score, 2),
                        "mixed_selling": mixed,
                        "buyers": list(s["buyers"].values())})
    signals.sort(key=lambda x: x["score"], reverse=True)
    sellers = sorted(
        ({"symbol": k, "issuer": v["issuer"], "sell_value": v["sell_value"],
          "sellers": v["sellers"]}
         for k, v in by_sym.items() if v["sell_value"] >= 1_000_000),
        key=lambda x: x["sell_value"], reverse=True)
    return signals, sellers

# ---------------------------------------------------------------- prices ----
def yahoo_close_on(symbol, target):
    """Close on or just before target date (YYYY-MM-DD), else latest close."""
    try:
        r = requests.get(
            f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "6mo", "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        res = r.json()["chart"]["result"][0]
        ts = res["timestamp"]
        closes = res["indicators"]["quote"][0]["close"]
        pairs = [(date.fromtimestamp(t).isoformat(), c)
                 for t, c in zip(ts, closes) if c]
        if not pairs:
            return None, None
        eligible = [p for p in pairs if p[0] <= target]
        d, c = eligible[-1] if eligible else pairs[-1]
        return c, d
    except Exception:
        return None, None

# ---------------------------------------------------------------- site data ----
def yahoo_history(symbol):
    """Daily OHLC, last ~6 months: [[YYYY-MM-DD, open, high, low, close], ...]"""
    try:
        r = requests.get(
            f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "6mo", "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        res = r.json()["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
        out = []
        for t, o, h, l, c in zip(ts, q["open"], q["high"], q["low"], q["close"]):
            if c:
                out.append([date.fromtimestamp(t).isoformat(),
                            round(o or c, 4), round(h or c, 4),
                            round(l or c, 4), round(c, 4)])
        return out
    except Exception:
        return []

def close_on_or_after(hist, target):
    for row in hist:
        if row[0] >= target:
            return row[-1]
    return None

def rsi14(closes, period=14):
    """Wilder's RSI. None when there isn't enough history."""
    if len(closes) < period + 1:
        return None
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        ag = (ag * (period - 1) + g) / period
        al = (al * (period - 1) + l) / period
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 1)

def indicators(hist):
    """RSI(14), distance from the 50-day average, 1-month momentum."""
    closes = [row[-1] for row in hist]
    if len(closes) < 15:
        return {}
    out = {"rsi": rsi14(closes)}
    if len(closes) >= 50:
        sma50 = sum(closes[-50:]) / 50
        out["vs_sma50_pct"] = round((closes[-1] / sma50 - 1) * 100, 1)
    if len(closes) >= 22:
        out["mom_1m_pct"] = round((closes[-1] / closes[-22] - 1) * 100, 1)
    return out

def update_site_data(day, signals, sellers, stats, data_dir):
    os.makedirs(data_dir, exist_ok=True)
    day_s = str(day)
    top = []
    for i, s in enumerate(signals[:25]):
        entry = {"symbol": s["symbol"], "issuer": s["issuer"],
                 "score": s["score"], "buy_value": round(s["buy_value"]),
                 "n_buyers": s["n_buyers"], "mixed_selling": s["mixed_selling"],
                 "buyers": [{"owner": b["owner"], "role": b["role"],
                             "value": round(b["value"])}
                            for b in sorted(s["buyers"], key=lambda x: -x["value"])],
                 "sell_value": round(s["sell_value"])}
        if i < 15:
            hist = yahoo_history(s["symbol"])
            entry["history"] = hist[-60:]
            entry["indicators"] = indicators(hist)
        top.append(entry)
    latest = {"date": day_s, "stats": stats, "signals": top,
              "heavy_sellers": [
                  {"symbol": s["symbol"], "issuer": s["issuer"],
                   "sell_value": round(s["sell_value"]),
                   "sellers": {k: round(v) for k, v in s["sellers"].items()}}
                  for s in sellers[:15]]}
    with open(f"{data_dir}/latest.json", "w") as fh:
        json.dump(latest, fh, indent=1)

    pred_file = f"{data_dir}/predictions.json"
    preds = []
    if os.path.exists(pred_file):
        with open(pred_file) as fh:
            preds = json.load(fh)
    have = {(p["symbol"], p["signal_date"]) for p in preds}
    recent_syms = {p["symbol"] for p in preds
                   if (day - date.fromisoformat(p["signal_date"])).days < 14}
    for s in signals:
        if (s["score"] >= 5 and s["buy_value"] >= 25_000
                and not s["mixed_selling"]
                and (s["symbol"], day_s) not in have
                and s["symbol"] not in recent_syms):
            hist = yahoo_history(s["symbol"])
            entry_price, entry_date = None, None
            if hist:
                latest_txn = max((t["date"] for t in s["buy_txns"]), default=day_s)
                elig = [h for h in hist if h[0] <= latest_txn] or hist
                entry_date, entry_price = elig[-1][0], elig[-1][-1]
            preds.append({
                "signal_date": day_s, "symbol": s["symbol"],
                "issuer": s["issuer"], "score": s["score"],
                "buy_value": round(s["buy_value"]), "n_buyers": s["n_buyers"],
                "buyers": [{"owner": b["owner"], "role": b["role"],
                            "value": round(b["value"])} for b in s["buyers"]],
                "entry_price": entry_price, "entry_date": entry_date,
                "current_price": entry_price, "current_date": entry_date,
                "return_pct": 0.0, "days_held": 0,
                "grade_30d_pct": None, "grade_90d_pct": None,
                "history": hist[-120:] if hist else []})
    for p in preds:
        hist = yahoo_history(p["symbol"])
        if not hist:
            continue
        p["history"] = hist[-120:]
        p["current_date"], p["current_price"] = hist[-1][0], hist[-1][-1]
        if p["entry_price"]:
            p["return_pct"] = round(
                (p["current_price"] / p["entry_price"] - 1) * 100, 2)
        sig = date.fromisoformat(p["signal_date"])
        p["days_held"] = (date.fromisoformat(p["current_date"]) - sig).days
        if p["grade_30d_pct"] is None and p["days_held"] >= 30 and p["entry_price"]:
            c = close_on_or_after(hist, (sig + timedelta(days=30)).isoformat())
            if c:
                p["grade_30d_pct"] = round((c / p["entry_price"] - 1) * 100, 2)
        if p["grade_90d_pct"] is None and p["days_held"] >= 90 and p["entry_price"]:
            c = close_on_or_after(hist, (sig + timedelta(days=90)).isoformat())
            if c:
                p["grade_90d_pct"] = round((c / p["entry_price"] - 1) * 100, 2)
    preds.sort(key=lambda p: p["signal_date"], reverse=True)
    with open(pred_file, "w") as fh:
        json.dump(preds, fh, indent=1)
    return len(preds)

# ---------------------------------------------------------------- report ----
def money(v):
    if v >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v/1_000:.0f}K"
    return f"${v:,.0f}"

def build_report(day, signals, sellers, stats):
    lines = [f"# Insider Signal Report — {day}", "",
             f"Scanned **{stats['filings']}** Form 4 filings from SEC EDGAR "
             f"({stats['parsed']} parsed with trades). "
             f"**{stats['with_buys']}** companies had open-market insider buying.", ""]
    lines += ["## 🟢 Bullish — insiders buying with their own money", ""]
    shown = 0
    for s in signals:
        if s["buy_value"] < 25_000 and shown >= 10:
            continue
        if shown >= 15:
            break
        shown += 1
        who = "; ".join(
            f"{b['owner']} ({b['role']}, {money(b['value'])})"
            for b in sorted(s["buyers"], key=lambda x: -x["value"])[:4])
        flag = " ⚠️ heavy insider selling offsets this" if s["mixed_selling"] else ""
        lines.append(f"**{shown}. {s['symbol']}** — {s['issuer']} — score {s['score']}")
        lines.append(f"   - {s['n_buyers']} buyer(s), {money(s['buy_value'])} total bought. {who}{flag}")
    if shown == 0:
        lines.append("No qualifying insider buying found in this window.")
    lines += ["", "## 🔴 Heavy insider selling (non-10b5-1, $1M+)", ""]
    if sellers:
        for s in sellers[:10]:
            who = "; ".join(f"{k} ({money(v)})" for k, v in
                            sorted(s["sellers"].items(), key=lambda x: -x[1])[:3])
            lines.append(f"- **{s['symbol']}** — {s['issuer']} — {money(s['sell_value'])} sold. {who}")
    else:
        lines.append("None over $1M in this window.")
    lines += ["", "_Signals, not guarantees. Insider buying is one of the "
              "best-documented edges in markets, but position sizing and "
              "homework still apply. Predictions from this report are logged "
              "and graded at 30/90 days._"]
    return "\n".join(lines)

# ---------------------------------------------------------------- main ----
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="Filing date YYYY-MM-DD (default: latest available)")
    ap.add_argument("--max-filings", type=int, default=0)
    ap.add_argument("--site", action="store_true",
                    help="Write dashboard JSON (data/latest.json, data/predictions.json)")
    args = ap.parse_args()

    day = None
    if args.date:
        day = date.fromisoformat(args.date)
        filings = fetch_form4_index(day)
    else:
        probe = date.today()
        for _ in range(7):
            try:
                filings = fetch_form4_index(probe)
                day = probe
                break
            except Exception:
                probe -= timedelta(days=1)
        else:
            sys.exit("No EDGAR daily index found in the last 7 days.")
    if args.max_filings:
        filings = filings[:args.max_filings]
    print(f"{day}: {len(filings)} Form 4 filings to parse…", flush=True)

    records = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        for i, recs in enumerate(ex.map(process_filing, filings)):
            records.extend(recs)
            if (i + 1) % 250 == 0:
                print(f"  {i + 1}/{len(filings)} processed, {len(records)} with trades", flush=True)

    signals, sellers = score_issuers(records)
    stats = {"filings": len(filings), "parsed": len(records),
             "with_buys": len(signals)}

    if args.site:
        data_dir = os.path.join(os.path.dirname(BASE), "data")
        n = update_site_data(day, signals, sellers, stats, data_dir)
        print(f"Site data updated: latest.json + predictions.json ({n} tracked calls)")
        return

    os.makedirs(f"{BASE}/data", exist_ok=True)
    os.makedirs(f"{BASE}/reports", exist_ok=True)
    with open(f"{BASE}/data/signals-{day}.json", "w") as fh:
        json.dump({"date": str(day), "stats": stats, "signals": signals,
                   "heavy_sellers": sellers}, fh, indent=1, default=str)
    report = build_report(str(day), signals, sellers, stats)
    with open(f"{BASE}/reports/report-{day}.md", "w") as fh:
        fh.write(report)

    # ---- prediction log: top calls get graded later ----
    pred_path = f"{BASE}/predictions.csv"
    new_file = not os.path.exists(pred_path)
    with open(pred_path, "a", newline="") as fh:
        w = csv.writer(fh)
        if new_file:
            w.writerow(["signal_date", "symbol", "issuer", "score", "buy_value",
                        "n_buyers", "entry_price", "entry_price_date",
                        "grade_30d_pct", "grade_90d_pct"])
        for s in signals:
            if s["score"] >= 5 and s["buy_value"] >= 25_000 and not s["mixed_selling"]:
                latest_txn = max((t["date"] for t in s["buy_txns"]), default=str(day))
                price, pdate = yahoo_close_on(s["symbol"], latest_txn)
                w.writerow([day, s["symbol"], s["issuer"], s["score"],
                            round(s["buy_value"]), s["n_buyers"],
                            price or "", pdate or "", "", ""])
    print(report)
    print(f"\nSaved: data/signals-{day}.json, reports/report-{day}.md, predictions.csv")

if __name__ == "__main__":
    main()
