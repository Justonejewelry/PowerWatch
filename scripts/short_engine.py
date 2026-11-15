#!/usr/bin/env python3
"""
Short-Side Engine v2 — PowerWatch.

The mirror image of the insider-buy engine, built on the evidence in
~/workspace/research_notes/short-selling-signals-research-20261115-1625/report.md
and the pattern checks in
~/workspace/research_notes/github-short-tools-verify-20261115/verify.md
(StockIQ's squeeze-scanner framing — MIT, pattern reimplemented, no code
copied; TTM BB/KC compression is standard indicator math).

Design rules (from the research, not folklore):
  - Insider SELLING is a confirmation input, never a standalone trigger.
    Scored per report §1(d): code-S open-market sales only, 10b5-1 plan
    sales excluded, cluster >= 3 distinct insiders / 30 days.
  - Short interest (FINRA, twice monthly) is a real bearish signal at
    moderate levels and a squeeze warning at extremes (report §3).
  - Borrow fee (IBKR public file) is both signal and binding cost
    (report §7). FINRA does NOT publish borrow fees.
  - FTD / Reg SHO threshold-list status is a RISK flag, never bearish
    points (report §2).
  - Entry timing is a confirmed 50-day BREAKDOWN on an extended name;
    RSI/extension is context only. Never short above a rising 50-day
    (report §5).
  - SI + FTD + borrow fee + options are four views of ONE crowded-short
    state — their combined score contribution is capped (report §9).
  - 13F fund puts are worth at most +1 confirmation point, and the 13F
    dollar figure is underlying NOTIONAL, not premium paid (report §6).
  - Short calls are graded at 30/90 days NET of borrow-fee drag at the
    entry fee, with max adverse excursion published (report §9).

Data (all free, no keys):
  SEC EDGAR Form 4 (insider sells, rolling 30-day state),
  FINRA equity short-interest files (shrt{YYYYMMDD}.txt),
  IBKR public short-stock file via FTP (usa.txt; port 21 is blocked from
  some networks — degrades gracefully, runs from GitHub Actions),
  Nasdaq Reg SHO threshold lists (daily), Yahoo Finance chart API
  (prices/technicals), Nasdaq API (best-effort shares outstanding).

Outputs:
  data/shorts.json            candidates + squeeze watchlist + leg status
  data/short_predictions.json graded short-call tracker (net of fees)
  data/short_state.json       rolling sell window + borrow history
  data/short_book.json        $100 PAPER short book — max 3 open shorts,
                              daily picks from the board, rule-based exits
                              (see the paper-book section below)
"""
import argparse, ftplib, io, json, math, os, re, sys, threading, time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
DATA = os.path.join(ROOT, "data")
UA_SEC = {"User-Agent": "PepperResearch/1.0 (mr.jsciaraffa@gmail.com)"}
UA_WEB = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
SEC = "https://www.sec.gov"

# FINRA consolidated short-interest file. Pattern shrt{YYYYMMDD}.txt is the
# documented one (FINRA catalog "Historical files"); host confirmed to exist
# but this dev network gets S3 AccessDenied for every path, so treat fetch
# failure as a leg status, not a crash. Override with env var if FINRA moves.
FINRA_TEMPLATE = os.environ.get(
    "PW_FINRA_URL_TEMPLATE",
    "https://cdn.finra.org/equity/otcmarket/biweekly/shrt{date}.txt")

# ------------------------------------------------------------------ helpers --
_rate_lock = threading.Lock()
_last_req = [0.0]
MIN_INTERVAL = 0.13  # SEC fair access: stay under 10 req/s

def sec_get(url, **kw):
    with _rate_lock:
        wait = MIN_INTERVAL - (time.time() - _last_req[0])
        if wait > 0:
            time.sleep(wait)
        _last_req[0] = time.time()
    r = requests.get(url, headers=UA_SEC, timeout=30, **kw)
    r.raise_for_status()
    return r

def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0

def load_json(path, default):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return default

def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=1)

# ------------------------------------------------------------------ prices ---
def yahoo_history(symbol, rng="1y"):
    """Daily OHLCV ~1y: [[date, open, high, low, close, volume], ...]"""
    try:
        r = requests.get(
            f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol.replace('/', '-')}",
            params={"range": rng, "interval": "1d"},
            headers=UA_WEB, timeout=20)
        res = r.json()["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
        out = []
        for t, o, h, l, c, v in zip(ts, q["open"], q["high"], q["low"],
                                    q["close"], q.get("volume") or []):
            if c:
                out.append([date.fromtimestamp(t).isoformat(),
                            round(o or c, 4), round(h or c, 4),
                            round(l or c, 4), round(c, 4), v or 0])
        return out
    except Exception:
        return []

def rsi14(closes, period=14):
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

def rsi_series(closes, period=14):
    """RSI for every bar (None until warmed up)."""
    out = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    def _rsi(a, b):
        return 100.0 if b == 0 else 100 - 100 / (1 + a / b)
    out[period] = round(_rsi(ag, al), 1)
    for i in range(period, len(gains)):
        ag = (ag * (period - 1) + gains[i]) / period
        al = (al * (period - 1) + losses[i]) / period
        out[i + 1] = round(_rsi(ag, al), 1)
    return out

def sma(vals, n):
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n

def technicals(hist):
    """Trend/breakdown/squeeze context from daily bars (report §5)."""
    if not hist or len(hist) < 55:
        return {}
    closes = [r[4] for r in hist]
    vols = [r[5] for r in hist]
    last = closes[-1]
    s50 = sma(closes, 50)
    s50_prev = sum(closes[-55:-5]) / 50 if len(closes) >= 55 else None
    s200 = sma(closes, 200)
    rsi_now = rsi14(closes)
    rsis = rsi_series(closes)
    streak = 0
    for v in reversed(rsis):
        if v is not None and v >= 70:
            streak += 1
        elif v is not None:
            break
    ret = lambda n: round((last / closes[-1 - n] - 1) * 100, 1) if len(closes) > n else None
    ext_above_200 = (round((last / s200 - 1) * 100, 1) if s200 else None)
    extended = bool(
        (ext_above_200 is not None and ext_above_200 >= 20)
        or streak >= 5
        or (ret(63) is not None and ret(63) >= 50))
    sma50_rising = (s50 is not None and s50_prev is not None and s50 > s50_prev)
    below_50 = s50 is not None and last < s50
    prior20_low = min(closes[-21:-1]) if len(closes) >= 21 else None
    broke_20low = prior20_low is not None and last < prior20_low
    peak60 = max(closes[-60:])
    lower_high = last < peak60 * 0.97  # pulled back >=3% from the 60d peak
    breakdown = bool(below_50 and ((extended and lower_high and not sma50_rising)
                                   or broke_20low))
    avg_vol20 = sum(vols[-21:-1]) / 20 if len(vols) >= 21 else None
    vol_surge = bool(avg_vol20 and vols[-1] >= 1.5 * avg_vol20)
    # TTM-style volatility compression: Bollinger(20,2) inside Keltner(20,1.5*ATR)
    squeeze_on = None
    if len(closes) >= 20:
        mid = sma(closes, 20)
        sd = math.sqrt(sum((c - mid) ** 2 for c in closes[-20:]) / 20)
        trs = []
        for i in range(len(hist) - 19, len(hist)):
            h, l, pc = hist[i][2], hist[i][3], hist[i - 1][4]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        atr = sum(trs) / len(trs)
        squeeze_on = bool((mid + 2 * sd) < (mid + 1.5 * atr)
                          and (mid - 2 * sd) > (mid - 1.5 * atr))
    return {
        "price": round(last, 2),
        "sma50": round(s50, 2) if s50 else None,
        "sma50_rising": sma50_rising,
        "vs_sma50_pct": round((last / s50 - 1) * 100, 1) if s50 else None,
        "vs_sma200_pct": ext_above_200,
        "rsi": rsi_now, "rsi70_streak": streak,
        "mom_1m_pct": ret(21), "mom_3m_pct": ret(63), "mom_6m_pct": ret(126),
        "extended_context": extended,
        "breakdown": breakdown,
        "breakdown_volume_surge": vol_surge,
        "ttm_squeeze_on": squeeze_on,
    }

def nasdaq_shares_out(symbol):
    """Best-effort shares outstanding from the (keyless) Nasdaq API.
    Returns shares or None — float is genuinely hard to get free; a None
    here just means the %-of-float gates report 'unknown'."""
    try:
        r = requests.get(
            f"https://api.nasdaq.com/api/quote/{symbol}/info?assetclass=stocks",
            headers={**UA_WEB, "Accept": "application/json"}, timeout=15)
        blob = json.dumps(r.json()).lower()
        m = re.search(r"shares outstanding[^0-9]{0,40}([0-9][0-9,\.]*)\s*([mb])?", blob)
        if m:
            v = float(m.group(1).replace(",", ""))
            if m.group(2) == "b":
                v *= 1e9
            elif m.group(2) == "m":
                v *= 1e6
            return v
    except Exception:
        pass
    return None

# ------------------------------------------------------------- Form 4 sells --
def quarter_path(d):
    return f"{d.year}/QTR{(d.month - 1) // 3 + 1}"

IDX_LINE = re.compile(
    r"^(\S+)\s+(.+?)\s+(\d{7,10})\s+(\d{8})\s+(edgar/data/\S+)$")

def fetch_form4_index(d):
    url = f"{SEC}/Archives/edgar/daily-index/{quarter_path(d)}/form.{d:%Y%m%d}.idx"
    text = sec_get(url).text
    out = []
    for line in text.splitlines():
        m = IDX_LINE.match(line.strip())
        if m and m.group(1) == "4":
            out.append({"cik": m.group(3), "filed": m.group(4), "path": m.group(5)})
    return out

def _txt(el, path):
    n = el.find(path) if el is not None else None
    return (n.text or "").strip() if n is not None and n.text else ""

OWNERSHIP_DOC = re.compile(r"<ownershipDocument>.*?</ownershipDocument>", re.DOTALL)

def parse_form4_sells(xml_bytes):
    """Extract non-derivative SALES (code S, disposed) from one Form 4.
    Mirrors insider_engine.parse_form4 but also captures post-transaction
    shares owned (for the Scott & Xu %-of-holdings filter) and the 10b5-1
    checkbox element (post-Apr-2023 filings) as well as footnote mentions."""
    root = ET.fromstring(xml_bytes)
    symbol = _txt(root, "./issuer/issuerTradingSymbol").upper()
    if symbol == "NONE" or not re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", symbol):
        return []
    issuer = _txt(root, "./issuer/issuerName")
    owner = _txt(root, "./reportingOwner/reportingOwnerId/rptOwnerName")
    rel = root.find("./reportingOwner/reportingOwnerRelationship")
    is_dir = _txt(rel, "isDirector") in ("1", "true")
    is_off = _txt(rel, "isOfficer") in ("1", "true")
    is_10 = _txt(rel, "isTenPercentOwner") in ("1", "true")
    title = _txt(rel, "officerTitle")
    footnotes = " ".join((f.text or "") for f in root.iter("footnote"))
    aff = _txt(root, "./aff10b5One")
    plan = ("10b5-1" in footnotes) or aff in ("1", "true")
    t = (title or "").lower()
    is_ceo_cfo = bool(is_off and ("chief executive" in t or "chief financial" in t
                                  or t.strip() == "ceo" or "cfo" in t))
    role = title or ("Officer" if is_off else "Director" if is_dir
                     else "10% owner" if is_10 else "Insider")
    sells = []
    for txn in root.iter("nonDerivativeTransaction"):
        code = _txt(txn, "./transactionCoding/transactionCode")
        ad = _txt(txn, "./transactionAmounts/transactionAcquiredDisposedCode/value")
        if code != "S" or ad != "D":
            continue
        shares = num(_txt(txn, "./transactionAmounts/transactionShares/value"))
        price = num(_txt(txn, "./transactionAmounts/transactionPricePerShare/value"))
        tdate = _txt(txn, "./transactionDate/value")
        post = num(_txt(txn, "./postTransactionAmounts/sharesOwnedFollowingTransaction/value"))
        if shares <= 0 or not tdate:
            continue
        sells.append({
            "symbol": symbol, "issuer": issuer, "owner": owner, "role": role,
            "is_ceo_cfo": is_ceo_cfo, "date": tdate, "shares": shares,
            "price": price, "value": round(shares * price),
            "post_shares": post, "plan10b51": plan,
            "key": f"{symbol}|{owner}|{tdate}|{shares}|{price}"})
    return sells

def process_filing_sells(f):
    try:
        text = sec_get(f"{SEC}/Archives/{f['path']}").text
    except Exception:
        return []
    out = []
    for seg in OWNERSHIP_DOC.findall(text):
        try:
            out.extend(parse_form4_sells(seg.encode()))
        except Exception:
            continue
    return out

def scan_sells(days, max_filings=0):
    """Scan the most recent `days` filing days; returns (sells, info)."""
    sells, scanned_days, total_filings = [], [], 0
    probe = date.today()
    got = 0
    for _ in range(10):
        if got >= days:
            break
        try:
            filings = fetch_form4_index(probe)
        except Exception:
            probe -= timedelta(days=1)
            continue
        use = filings[:max_filings] if max_filings else filings
        total_filings += len(use)
        with ThreadPoolExecutor(max_workers=6) as ex:
            for recs in ex.map(process_filing_sells, use):
                sells.extend(recs)
        scanned_days.append(str(probe))
        got += 1
        probe -= timedelta(days=1)
    return sells, {"filing_days": scanned_days, "filings_scanned": total_filings,
                   "sell_txns_found": len(sells)}

def cluster_sells(state_sells, asof):
    """Group the rolling window into per-symbol clusters (report §1d)."""
    cutoff = (asof - timedelta(days=30)).isoformat()
    by_sym = {}
    for s in state_sells:
        if s["date"] < cutoff or s.get("plan10b51"):
            continue
        by_sym.setdefault(s["symbol"], []).append(s)
    clusters = {}
    for sym, rows in by_sym.items():
        owners = {}
        for r in rows:
            o = owners.setdefault(r["owner"], {"owner": r["owner"], "role": r["role"],
                                               "is_ceo_cfo": r["is_ceo_cfo"],
                                               "value": 0, "shares": 0,
                                               "post_shares": None, "last_date": ""})
            o["value"] += r["value"]
            o["shares"] += r["shares"]
            if r["date"] >= o["last_date"]:
                o["last_date"] = r["date"]
                o["post_shares"] = r["post_shares"]
        total_value = sum(o["value"] for o in owners.values())
        max_pct = None
        for o in owners.values():
            if o["post_shares"] is not None:
                denom = o["shares"] + o["post_shares"]
                if denom > 0:
                    pct_h = o["shares"] / denom * 100
                    max_pct = pct_h if max_pct is None else max(max_pct, pct_h)
        clusters[sym] = {
            "symbol": sym, "issuer": rows[0]["issuer"],
            "n_insiders": len(owners), "total_value": round(total_value),
            "max_pct_holdings_sold": round(max_pct, 1) if max_pct is not None else None,
            "any_ceo_cfo": any(o["is_ceo_cfo"] for o in owners.values()),
            "sellers": sorted(
                ({"owner": o["owner"], "role": o["role"], "value": round(o["value"])}
                 for o in owners.values()), key=lambda x: -x["value"])[:5],
            "first_date": min(r["date"] for r in rows),
            "last_date": max(r["date"] for r in rows)}
    return clusters

def insider_sell_score(cluster, tech):
    """Report §1(d) scoring. The +2 'opportunistic' (Cohen 3-year calendar
    test) is NOT computable without a 3-year per-insider history backfill,
    so it is always reported as unknown/0 — the >=6 bar below is therefore
    deliberately hard to clear, as the research intends."""
    if not cluster:
        return 0, {}
    det = {"cluster_ge3": cluster["n_insiders"] >= 3,
           "opportunistic": None,  # unknown — needs 3y history (report §1c)
           "big_pct_of_holdings": (cluster["max_pct_holdings_sold"] is not None
                                   and cluster["max_pct_holdings_sold"] >= 25),
           "value_ge_250k": cluster["total_value"] >= 250_000,
           "selling_into_strength": bool(tech and tech.get("mom_6m_pct") is not None
                                         and tech["mom_6m_pct"] >= 30),
           "ceo_cfo_selling": cluster["any_ceo_cfo"]}
    score = (2 if det["cluster_ge3"] else 0) \
        + (2 if det["big_pct_of_holdings"] else 0) \
        + (1 if det["value_ge_250k"] else 0) \
        + (1 if det["selling_into_strength"] else 0) \
        + (1 if det["ceo_cfo_selling"] else 0)
    return score, det

# ------------------------------------------------------------------- FINRA ---
def settlement_candidates(anchor=None, count=8):
    """Recent FINRA settlement dates (15th & month-end), newest first."""
    anchor = anchor or date.today()
    out = []
    y, m = anchor.year, anchor.month
    for _ in range(8):
        last = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
        for cand in (last, date(y, m, 15)):
            if cand <= anchor:
                out.append(cand)
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return sorted(set(out), reverse=True)[:count]

def fetch_finra_file(settle):
    url = FINRA_TEMPLATE.format(date=settle.strftime("%Y%m%d"))
    r = requests.get(url, headers=UA_WEB, timeout=60)
    r.raise_for_status()
    return r.text

def parse_finra(text):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return {}
    hdr = [h.strip().lower() for h in lines[0].split("|")]
    def col(*names):
        for i, h in enumerate(hdr):
            if any(n in h for n in names):
                return i
        return None
    i_sym = col("symbol")
    i_cur = col("current short", "current shares short", "shares short")
    i_prev = col("previous short", "previous shares short")
    i_adv = col("average daily share volume", "avg daily")
    i_dtc = col("days to cover", "days tocover")
    if i_sym is None or i_cur is None:
        return {}
    out = {}
    for ln in lines[1:]:
        p = ln.split("|")
        if len(p) <= max(i for i in (i_sym, i_cur) if i is not None):
            continue
        sym = p[i_sym].strip().upper()
        if not re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", sym):
            continue
        cur = num(p[i_cur]) if i_cur is not None else 0
        prev = num(p[i_prev]) if i_prev is not None and len(p) > i_prev else 0
        adv = num(p[i_adv]) if i_adv is not None and len(p) > i_adv else 0
        dtc = num(p[i_dtc]) if i_dtc is not None and len(p) > i_dtc else 0
        if dtc <= 0 and adv > 0:
            dtc = cur / adv
        out[sym] = {"shares_short": cur, "prev_shares_short": prev,
                    "pct_change": round((cur / prev - 1) * 100, 1) if prev > 0 else None,
                    "avg_daily_volume": adv, "days_to_cover": round(dtc, 2)}
    return out

def finra_leg():
    """Latest two settlement files. Returns (data_by_symbol, status_dict)
    where each symbol merges latest values + prior-file change if the
    in-file previous column is absent."""
    tried = []
    got = []
    for settle in settlement_candidates(count=8):
        if len(got) >= 2:
            break
        tried.append(str(settle))
        try:
            parsed = parse_finra(fetch_finra_file(settle))
        except Exception as e:
            tried[-1] += f" ({type(e).__name__})"
            continue
        if parsed:
            got.append((settle, parsed))
    if not got:
        return {}, {"status": "unavailable",
                    "note": "FINRA consolidated files unreachable from this network "
                            f"(tried settlement dates: {', '.join(tried)}). The file "
                            "pattern is shrt{YYYYMMDD}.txt per FINRA's catalog; "
                            "GitHub Actions egress may reach it.",
                    "settlement_latest": None, "settlement_prior": None,
                    "symbols": 0}
    latest_settle, latest = got[0]
    if len(got) > 1:
        prior_settle, prior = got[1]
        for sym, row in latest.items():
            if row.get("pct_change") is None and sym in prior:
                pv = prior[sym]["shares_short"]
                if pv > 0:
                    row["pct_change"] = round((row["shares_short"] / pv - 1) * 100, 1)
    else:
        prior_settle = None
    age_days = (date.today() - latest_settle).days
    return latest, {"status": "live" if age_days <= 21 else "stale",
                    "note": f"FINRA Rule 4560 twice-monthly data; settlement "
                            f"{latest_settle} published ~7 business days later, so "
                            f"positions are already ~{age_days} days old and can be "
                            f"up to ~4 weeks stale just before the next release.",
                    "settlement_latest": str(latest_settle),
                    "settlement_prior": str(prior_settle) if prior_settle else None,
                    "symbols": len(latest)}

# ------------------------------------------------------------- threshold -----
def threshold_leg():
    """Reg SHO threshold-list symbols (Nasdaq + NYSE files via Nasdaq Trader).
    Daily, T+1. Status is a squeeze/buy-in RISK flag only (report §2)."""
    syms = set()
    used = None
    for back in range(0, 6):
        d = date.today() - timedelta(days=back)
        ds = d.strftime("%Y%m%d")
        day_syms = set()
        ok_any = False
        for prefix in ("nasdaqth", "nyseth", "amexth"):
            try:
                r = requests.get(
                    f"https://www.nasdaqtrader.com/dynamic/symdir/regsho/{prefix}{ds}.txt",
                    headers=UA_WEB, timeout=20)
                if r.status_code != 200:
                    continue
                ok_any = True
                for ln in r.text.splitlines()[1:]:
                    p = ln.split("|")
                    if len(p) >= 4 and p[3].strip() == "Y":
                        day_syms.add(p[0].strip().upper())
            except Exception:
                continue
        if ok_any and day_syms:
            syms, used = day_syms, str(d)
            break
    if used is None:
        return set(), {"status": "unavailable", "date": None, "count": 0,
                       "note": "Nasdaq Trader threshold files unreachable."}
    return syms, {"status": "live", "date": used, "count": len(syms),
                  "note": "Reg SHO threshold list: >=10k shares failed AND >=0.5% of "
                          "shares outstanding for 5 consecutive settlement days. "
                          "Risk flag only — persistent fails historically precede "
                          "POSITIVE returns (report §2)."}

# ------------------------------------------------------------------ borrow ---
def borrow_leg(symbols, history):
    """IBKR public short-stock file (usa.txt) over FTP: user 'shortstock',
    no password. Pipe-delimited: #SYM|CUR|NAME|CON|ISIN|REBATERATE|FEERATE|
    AVAILABLE. Snapshot only — we archive per-symbol rows into
    short_state.borrow_history ourselves. FTP (port 21) is blocked from
    some networks (verified on this dev VM); failure is a status, and the
    leg runs for real from GitHub Actions."""
    today = str(date.today())
    try:
        ftp = ftplib.FTP("ftp3.interactivebrokers.com", timeout=25)
        ftp.login("shortstock", "")
        buf = []
        want = set(symbols)
        def _cb(line):
            if line.startswith("#SYM") or not line.startswith("#"):
                p = line.split("|")
                if len(p) >= 8 and p[0].strip().upper() in want:
                    buf.append(p)
        ftp.retrlines("RETR usa.txt", _cb)
        ftp.quit()
    except Exception as e:
        return {}, {"status": "unavailable",
                    "note": f"IBKR FTP fetch failed ({type(e).__name__}). Port 21 is "
                            "blocked from this network; the leg runs from GitHub "
                            "Actions. IBKR's file is a snapshot of IBKR's own "
                            "inventory, updated ~15 min, no history — PowerWatch "
                            "archives daily snapshots itself.",
                    "snapshot_date": None}
    out = {}
    for p in buf:
        sym = p[0].strip().upper()
        fee = num(p[6])
        avail = num(p[7])
        out[sym] = {"fee_pct": fee, "available": avail}
        hist = history.setdefault(sym, [])
        if not hist or hist[-1]["date"] != today:
            hist.append({"date": today, "fee_pct": fee, "available": avail})
        history[sym] = hist[-60:]
    return out, {"status": "live", "snapshot_date": today,
                 "note": "IBKR short-stock file (their inventory only, ~15-min "
                         "snapshots). Fee is annualized %; availability is shares "
                         "IBKR can locate right now.",
                 "symbols_matched": len(out)}

def borrow_rising(history, sym):
    """Fee >= 2x its level ~14 days ago (report §7: +1 and a cover warning)."""
    hist = history.get(sym) or []
    if len(hist) < 2:
        return False
    cur = hist[-1]["fee_pct"]
    past = [h for h in hist[:-1]
            if (date.today() - date.fromisoformat(h["date"])).days >= 10]
    if not past:
        past = hist[:-1]
    base = past[-1]["fee_pct"]
    return bool(base > 0 and cur >= 2 * base)

# ---------------------------------------------------------------- evidence ---
def fund_puts():
    """13F put positions from data/funds.json. The 13F 'value' is the value
    of the UNDERLYING shares (notional), not the premium paid — Burry's
    $912M PLTR puts cost ~$9.2M (report §6). New/increased puts earn the
    single +1 confirmation point; existing holdings are evidence only."""
    funds = load_json(os.path.join(DATA, "funds.json"), {})
    out = {}
    for fd in funds.get("funds", []):
        ch = fd.get("changes") or {}
        for h in ch.get("new") or []:
            if h.get("option") == "put" and h.get("symbol"):
                e = out.setdefault(h["symbol"], {"symbol": h["symbol"],
                                                 "name": h.get("name", ""),
                                                 "funds": [], "points": 1})
                e["points"] = 1  # a NEW put anywhere earns the point
                e["funds"].append({"fund": fd["name"], "kind": "new put position",
                                   "notional_value": h.get("value"),
                                   "pct_of_portfolio": h.get("pct")})
        for h in fd.get("top_holdings") or []:
            if h.get("option") == "put" and h.get("symbol"):
                e = out.setdefault(h["symbol"], {"symbol": h["symbol"],
                                                 "name": h.get("name", ""),
                                                 "funds": [], "points": 0})
                if not any(f["fund"] == fd["name"] for f in e["funds"]):
                    e["funds"].append({"fund": fd["name"],
                                       "kind": "existing put position (top holding)",
                                       "notional_value": h.get("value"),
                                       "pct_of_portfolio": h.get("pct")})
    return out

def congress_sells():
    c = load_json(os.path.join(DATA, "congress.json"), {})
    return {t["symbol"]: t for t in c.get("top_sells", []) if t.get("symbol")}

# ------------------------------------------------------------------ gates ----
def build_gates(c):
    """Hard risk gates (report §9). status: pass / block / unknown."""
    si, bo, tech = c.get("si") or {}, c.get("borrow") or {}, c.get("tech") or {}
    gates = []
    pct_f = si.get("pct_of_float")
    dtc = si.get("days_to_cover")
    flt = si.get("float_shares")
    if pct_f is not None and pct_f >= 30:
        gates.append({"gate": "crowding", "status": "block",
                      "detail": f"SI {pct_f}% of float >= 30% absolute bar"})
    elif dtc is not None and dtc >= 10:
        gates.append({"gate": "crowding", "status": "block",
                      "detail": f"days-to-cover {dtc} >= 10 absolute bar"})
    elif flt is not None and 0 < flt < 20_000_000:
        gates.append({"gate": "crowding", "status": "block",
                      "detail": f"float {flt/1e6:.0f}M shares < 20M absolute bar"})
    elif pct_f is not None and dtc is not None and pct_f >= 20 and dtc >= 5:
        gates.append({"gate": "crowding", "status": "block",
                      "detail": f"squeeze zone: SI {pct_f}% of float with DTC {dtc}"})
    elif pct_f is None and dtc is None:
        gates.append({"gate": "crowding", "status": "unknown",
                      "detail": "no FINRA short-interest data for this name yet"})
    else:
        gates.append({"gate": "crowding", "status": "pass",
                      "detail": "below squeeze-zone thresholds"})
    fee, avail = bo.get("fee_pct"), bo.get("available")
    if fee is not None and fee > 50:
        gates.append({"gate": "borrow", "status": "block",
                      "detail": f"borrow fee {fee}%/yr > 50% — hard no"})
    elif avail is not None and avail < 10_000:
        gates.append({"gate": "borrow", "status": "block",
                      "detail": f"only {avail:,.0f} shares available to borrow"})
    elif fee is not None and fee > 25:
        gates.append({"gate": "borrow", "status": "block",
                      "detail": f"borrow fee {fee}%/yr > 25% and no dated catalyst "
                                "on file — no new short (report §7)"})
    elif fee is None:
        gates.append({"gate": "borrow", "status": "unknown",
                      "detail": "borrow fee not available yet (IBKR leg pending)"})
    else:
        gates.append({"gate": "borrow", "status": "pass",
                      "detail": f"fee {fee}%/yr, {avail:,.0f} shares available"
                                if avail is not None else f"fee {fee}%/yr"})
    price = tech.get("price")
    if price is not None and price < 5:
        gates.append({"gate": "tradability", "status": "block",
                      "detail": f"price ${price} < $5 — outside the tradable short book"})
    elif tech.get("sma50_rising") and (tech.get("vs_sma50_pct") or -1) >= 0:
        gates.append({"gate": "trend", "status": "block",
                      "detail": "price above a RISING 50-day average — never short strength"})
    elif not tech:
        gates.append({"gate": "trend", "status": "unknown",
                      "detail": "no price history available"})
    else:
        gates.append({"gate": "trend", "status": "pass",
                      "detail": "not shorting into a rising trend"})
    gates.append({"gate": "catalyst", "status": "unknown",
                  "detail": "next earnings date not available from free sources — "
                            "verify manually; no naked short within 5 trading days "
                            "of earnings (report §9)"})
    return gates

# --------------------------------------------------------------- candidates --
def si_points(si):
    pts, det = 0, {}
    pct_f = si.get("pct_of_float")
    if pct_f is not None:
        if 5 <= pct_f < 10:
            pts += 1
        elif 10 <= pct_f <= 20:
            pts += 2
        det["si_pct_float"] = pct_f
    if si.get("pct_change") is not None and si["pct_change"] >= 20:
        pts += 1
        det["si_rising_fast"] = True
    return pts, det

def borrow_points(bo, rising):
    pts = 0
    fee = bo.get("fee_pct")
    if fee is not None:
        if 1 <= fee < 5:
            pts += 1
        elif 5 <= fee <= 10:
            pts += 2
    if rising:
        pts += 1
    return pts

def squeeze_score(c):
    """Squeeze-risk score for the AVOID/watch list (higher = more dangerous
    to be short). Crowding inputs are one state — capped (report §9)."""
    si, tech = c.get("si") or {}, c.get("tech") or {}
    score = 0
    det = []
    pct_f, dtc = si.get("pct_of_float"), si.get("days_to_cover")
    crowd = 0
    if pct_f is not None:
        crowd = 3 if pct_f >= 20 else 2 if pct_f >= 10 else 1 if pct_f >= 5 else 0
    if dtc is not None:
        crowd = max(crowd, 3 if dtc >= 10 else 2 if dtc >= 5 else 1 if dtc >= 3 else 0)
    score += crowd
    if crowd:
        det.append(f"crowded: SI {pct_f}% of float, DTC {dtc}")
    if crowd and tech.get("mom_1m_pct") is not None and tech["mom_1m_pct"] >= 10:
        score += 2
        det.append(f"price rising into crowding (+{tech['mom_1m_pct']}% last month)")
    if c.get("on_threshold_list"):
        score += 1
        det.append("on Reg SHO threshold list (settlement stress)")
    fee = (c.get("borrow") or {}).get("fee_pct")
    if fee is not None and fee > 25:
        score += 1
        det.append(f"borrow fee {fee}%/yr — lenders are charging squeeze prices")
    flt = si.get("float_shares")
    if flt is not None and 0 < flt < 20_000_000:
        score += 1
        det.append(f"tiny float ({flt/1e6:.0f}M shares)")
    if tech.get("ttm_squeeze_on"):
        det.append("TTM volatility compression active (coiled — breakout risk both ways)")
    return score, det

def assemble(symbols, ctx):
    out = []
    for sym in symbols:
        tech = ctx["tech"].get(sym, {})
        si = dict(ctx["finra"].get(sym, {}))
        if si:
            flt = ctx["floats"].get(sym)
            si["float_shares"] = flt
            si["pct_of_float"] = (round(si["shares_short"] / flt * 100, 1)
                                  if flt else None)
        bo = ctx["borrow"].get(sym, {})
        cluster = ctx["clusters"].get(sym)
        ins_score, ins_det = insider_sell_score(cluster, tech)
        s_pts, _ = si_points(si)
        b_pts = borrow_points(bo, borrow_rising(ctx["borrow_history"], sym))
        put = ctx["fund_puts"].get(sym)
        put_pts = put["points"] if put else 0
        # §9 double-counting cap: SI + borrow + threshold + fund-put points
        # are views of one crowded-short state — combined contribution <= 4.
        crowd_raw = s_pts + b_pts + put_pts + (1 if sym in ctx["threshold"] else 0)
        crowd_pts = min(4, crowd_raw)
        breakdown = bool(tech.get("breakdown"))
        score = ins_score + crowd_pts + (2 if breakdown else 0) \
            + (1 if breakdown and tech.get("breakdown_volume_surge") else 0)
        c = {"symbol": sym,
             "issuer": (cluster or {}).get("issuer")
                       or (put or {}).get("name") or "",
             "score": score,
             "insider": ({"score": ins_score, "detail": ins_det,
                          "cluster": cluster} if cluster else None),
             "si": si or None,
             "borrow": ({"fee_pct": bo.get("fee_pct"),
                         "available": bo.get("available"),
                         "rising": borrow_rising(ctx["borrow_history"], sym)}
                        if bo else None),
             "crowd_points": crowd_pts, "crowd_points_raw": crowd_raw,
             "fund_put": put,
             "congress_sells": ctx["congress"].get(sym),
             "on_threshold_list": sym in ctx["threshold"],
             "tech": tech or None}
        c["gates"] = build_gates(c)
        blocked = any(g["status"] == "block" for g in c["gates"])
        confirmed = ins_score >= 6 or crowd_pts >= 3
        if blocked:
            c["status"] = "blocked"
        elif breakdown and confirmed:
            c["status"] = "call"
        elif breakdown or ins_score >= 4 or crowd_pts >= 2:
            c["status"] = "watch"
        else:
            c["status"] = "evidence"
        sq, sq_det = squeeze_score(c)
        c["squeeze_score"] = sq
        c["squeeze_detail"] = sq_det
        out.append(c)
    out.sort(key=lambda x: (-x["score"], x["symbol"]))
    return out

# ----------------------------------------------------------------- tracker ---
def close_on_or_after(hist, target):
    for row in hist:
        if row[0] >= target:
            return row[4]
    return None

def update_short_tracker(candidates, borrow_now):
    path = os.path.join(DATA, "short_predictions.json")
    preds = load_json(path, [])
    today = str(date.today())
    have_recent = {p["symbol"] for p in preds
                   if (date.today() - date.fromisoformat(p["signal_date"])).days < 30}
    by_sym = {c["symbol"]: c for c in candidates}
    for c in candidates:
        if c["status"] != "call" or c["symbol"] in have_recent:
            continue
        tech = c.get("tech") or {}
        if not tech.get("price"):
            continue
        fee = (c.get("borrow") or {}).get("fee_pct")
        preds.append({
            "signal_date": today, "symbol": c["symbol"], "issuer": c["issuer"],
            "score": c["score"], "entry_price": tech["price"],
            "entry_date": today,
            "borrow_fee_at_entry_pct": fee,
            "thesis": {"insider_score": (c.get("insider") or {}).get("score", 0),
                       "crowd_points": c["crowd_points"],
                       "breakdown": True},
            "current_price": tech["price"], "current_date": today,
            "gross_return_pct": 0.0, "fee_drag_pct": 0.0, "net_return_pct": 0.0,
            "max_adverse_pct": 0.0, "days_held": 0,
            "grade_30d_pct": None, "grade_90d_pct": None,
            "status": "open", "exit_date": None, "exit_reason": None,
            "history": []})
    for p in preds:
        hist = yahoo_history(p["symbol"])
        if hist:
            p["history"] = [[r[0], r[4]] for r in hist[-120:]]
            p["current_date"], p["current_price"] = hist[-1][0], hist[-1][4]
        if not p.get("entry_price") or not p.get("current_price"):
            continue
        sig = date.fromisoformat(p["signal_date"])
        cur_d = date.fromisoformat(p["current_date"])
        p["days_held"] = (cur_d - sig).days
        gross = (p["entry_price"] - p["current_price"]) / p["entry_price"] * 100
        fee = p.get("borrow_fee_at_entry_pct")
        drag = (fee * p["days_held"] / 365) if fee is not None else 0.0
        p["gross_return_pct"] = round(gross, 2)
        p["fee_drag_pct"] = round(drag, 2)
        p["net_return_pct"] = round(gross - drag, 2)
        # max adverse excursion from closes since entry (report §9: publish it)
        closes = [(r[0], r[1]) for r in p["history"] if r[0] >= p["entry_date"]]
        if closes:
            worst = max(c for _, c in closes)
            p["max_adverse_pct"] = round((worst / p["entry_price"] - 1) * 100, 2)
        if p["status"] == "open":
            if gross <= -25:
                p["status"], p["exit_date"] = "closed_stop", p["current_date"]
                p["exit_reason"] = "hard stop: +25% adverse move (report §9)"
            elif p["days_held"] >= 90:
                p["status"], p["exit_date"] = "closed_time", p["current_date"]
                p["exit_reason"] = "90-day time stop (report §9)"
        for label, days in (("grade_30d_pct", 30), ("grade_90d_pct", 90)):
            if p[label] is None and p["days_held"] >= days:
                c0 = close_on_or_after(p["history"],
                                       (sig + timedelta(days=days)).isoformat())
                if c0:
                    g = (p["entry_price"] - c0) / p["entry_price"] * 100
                    if fee is not None:
                        g -= fee * days / 365
                    p[label] = round(g, 2)
    preds.sort(key=lambda p: p["signal_date"], reverse=True)
    save_json(path, preds)
    return preds

# ------------------------------------------------------------- paper book ----
# The $100 Short Book (Jay, 2026-11-15): "pick 3 stocks to short sell every
# day and also determine when to sell. Start with $100. Keep track on
# display." PAPER trading only — no real money, no broker. The point of the
# tiny stake is that every dollar of P&L stays visible.
#
# Accounting model (kept internally consistent, all fields stored in
# data/short_book.json):
#   - Start: cash = equity = $100.00, no positions.
#   - Entry RESERVES the notional: cash -= notional. A position's market
#     value while open is notional + unrealized net P&L, so equity is
#     unchanged by the act of entering.
#   - Notional per new position = current equity / 3 (the book holds at
#     most 3 positions), capped by available cash; fractional shares.
#   - Cover: cash += notional + net P&L, realized_pnl += net P&L, where
#     net P&L = (entry - exit) / entry * notional - accrued fee drag.
#   - Fee drag accrues daily at the ENTRY fee when a fee is known:
#     notional * (fee_pct/100) * days_held / 365. When the borrow leg is
#     unavailable the entry fee is null, fee_known is false and drag is 0 —
#     a fee is never fabricated.
#   - Equity = cash + sum(open positions' market values), recomputed
#     every run.
#
# Entry eligibility — ONLY the absolute safety bars (the book exists to
# make picks every day, so the trend/breakdown gate is NOT an entry rule;
# instead each pick is labeled tier "confirmed" when it has a breakdown,
# "aggressive" when it does not, and the tier is shown on the dashboard):
#   not on the Reg SHO threshold list, not on the squeeze watchlist,
#   float >= 20M shares where known, SI < 30% of float where known,
#   days-to-cover < 10 where known, borrow fee <= 50%/yr where known,
#   >= 10K shares available where known, price >= $2, price known.
#
# Exit rules — evaluated every run, in this priority order, full cover:
#   1. hard stop:      price >= entry * 1.20          "hard stop +20%"
#   2. fee spike:      current fee > 25%/yr or >= 2x entry fee
#                                                     "borrow fee spike"
#   3. trend reclaim:  price closes back above its 50-day SMA after having
#                      been below it                  "reclaimed 50-day — thesis dead"
#   4. take profit:    price <= entry * 0.75          "take profit −25%"
#   5. time stop:      held >= 90 days                "90-day time stop"
# Rule 3 needs the position to have actually been below the 50-day at some
# mark (tracked as below_sma50_seen, seeded from the entry mark): an
# "aggressive" pick entered above its 50-day is not instantly covered for
# sitting where it was knowingly entered — it must first fall below the
# average (thesis engages) and then reclaim it (thesis dead).
BOOK_PATH = os.path.join(DATA, "short_book.json")
BOOK_START_CAPITAL = 100.0
BOOK_MAX_POSITIONS = 3
BOOK_STOP_MULT = 1.20        # rule 1: +20% adverse
BOOK_TARGET_MULT = 0.75      # rule 4: -25% price move
BOOK_TIME_STOP_DAYS = 90     # rule 5
BOOK_FEE_SPIKE_PCT = 25.0    # rule 2

def _fmt_usd(v):
    if v >= 1_000_000:
        return f"${v / 1e6:.1f}M"
    if v >= 1_000:
        return f"${v / 1e3:.0f}K"
    return f"${v:.0f}"

def book_eligible(c, squeeze_syms):
    """Absolute safety bars only. Returns (ok, why_not). Unknown data
    (FINRA/borrow legs down) never blocks — the bars bite where known."""
    if c["symbol"] in squeeze_syms:
        return False, "on the squeeze watchlist"
    if c.get("on_threshold_list"):
        return False, "on the Reg SHO threshold list"
    si = c.get("si") or {}
    flt = si.get("float_shares")
    if flt is not None and flt < 20_000_000:
        return False, "float under 20M shares"
    if si.get("pct_of_float") is not None and si["pct_of_float"] >= 30:
        return False, "short interest >= 30% of float"
    if si.get("days_to_cover") is not None and si["days_to_cover"] >= 10:
        return False, "days-to-cover >= 10"
    bo = c.get("borrow") or {}
    if bo.get("fee_pct") is not None and bo["fee_pct"] > 50:
        return False, "borrow fee over 50%/yr"
    if bo.get("available") is not None and bo["available"] < 10_000:
        return False, "under 10K shares available to borrow"
    price = (c.get("tech") or {}).get("price")
    if price is None:
        return False, "no current price"
    if price < 2:
        return False, "price under $2"
    return True, ""

def pick_reason(c):
    """One line of the strongest evidence, for the dashboard pick cards."""
    bits = []
    cl = (c.get("insider") or {}).get("cluster")
    if cl:
        who = (f"{cl['n_insiders']} insider{'s' if cl['n_insiders'] != 1 else ''} "
               f"sold {_fmt_usd(cl['total_value'])}")
        if cl.get("max_pct_holdings_sold"):
            who += f" (up to {cl['max_pct_holdings_sold']}% of a holding)"
        bits.append(who)
    si = c.get("si") or {}
    if si.get("pct_of_float") is not None:
        bits.append(f"SI {si['pct_of_float']}% of float")
    elif si.get("shares_short"):
        bits.append(f"{si['shares_short'] / 1e6:.1f}M shares short")
    if si.get("days_to_cover"):
        bits.append(f"DTC {si['days_to_cover']}")
    if (c.get("tech") or {}).get("breakdown"):
        bits.append("broke the 50-day")
    bo = c.get("borrow") or {}
    if bo.get("fee_pct") is not None:
        bits.append(f"borrow {bo['fee_pct']}%/yr")
    if c.get("fund_put"):
        bits.append("a tracked fund opened puts")
    cong = c.get("congress_sells")
    if cong:
        bits.append(f"Congress selling ({cong.get('count', '?')} sales)")
    if not bits:
        bits.append(f"short-board score {c['score']}")
    return " · ".join(bits[:3])

def book_marks(sym):
    """Latest close, 50-day SMA and close series for one held symbol."""
    hist = yahoo_history(sym)
    if not hist:
        return {}
    closes = [r[4] for r in hist]
    return {"price": hist[-1][4], "date": hist[-1][0],
            "sma50": sma(closes, 50),
            "closes": [(r[0], r[4]) for r in hist]}

def book_equity(book):
    return round(book["cash"] + sum(
        p.get("market_value", p["notional"]) for p in book["positions"]), 2)

def update_short_book(candidates, ctx):
    today = str(date.today())
    book = load_json(BOOK_PATH, None)
    if not isinstance(book, dict) or "positions" not in book:
        book = {"version": 1, "paper": True,
                "starting_capital": BOOK_START_CAPITAL, "created": today,
                "cash": BOOK_START_CAPITAL, "realized_pnl": 0.0,
                "equity": BOOK_START_CAPITAL, "unrealized_pnl": 0.0,
                "total_return_pct": 0.0, "win_rate_pct": None,
                "positions": [], "closed": [], "picks_history": []}
    by_sym = {c["symbol"]: c for c in candidates}
    squeeze_syms = {c["symbol"] for c in candidates
                    if c.get("squeeze_score", 0) >= 4}
    exits_today = []

    # ---- 1) exits first, in rule-priority order ----
    still_open = []
    for pos in book["positions"]:
        m = book_marks(pos["symbol"])
        if not m:
            still_open.append(pos)  # no price today — cannot judge; hold
            continue
        price, entry = m["price"], pos["entry_price"]
        days = (date.today() - date.fromisoformat(pos["entry_date"])).days
        since = [cl for d, cl in m["closes"] if d >= pos["entry_date"]]
        if since:
            pos["max_adverse_pct"] = round(max(
                pos.get("max_adverse_pct") or 0.0,
                (max(since) / entry - 1) * 100), 2)
        if m.get("sma50") is not None and price < m["sma50"]:
            pos["below_sma50_seen"] = True
        fee_now = (by_sym.get(pos["symbol"], {}).get("borrow") or {}).get("fee_pct")
        if fee_now is None:
            fee_now = (ctx["borrow"].get(pos["symbol"]) or {}).get("fee_pct")
        entry_fee = pos.get("entry_fee_pct")
        reason = None
        if price >= entry * BOOK_STOP_MULT:
            reason = "hard stop +20%"
        elif fee_now is not None and (fee_now > BOOK_FEE_SPIKE_PCT
                                      or (entry_fee and fee_now >= 2 * entry_fee)):
            reason = "borrow fee spike"
        elif (m.get("sma50") is not None and price > m["sma50"]
              and pos.get("below_sma50_seen")):
            reason = "reclaimed 50-day — thesis dead"
        elif price <= entry * BOOK_TARGET_MULT:
            reason = "take profit −25%"
        elif days >= BOOK_TIME_STOP_DAYS:
            reason = "90-day time stop"
        drag = (pos["notional"] * entry_fee / 100 * days / 365) if entry_fee else 0.0
        if reason is None:
            gross = (entry - price) / entry * pos["notional"]
            pos.update({
                "current_price": price, "current_date": m["date"],
                "days_held": days, "accrued_fee_drag": round(drag, 2),
                "unrealized_pnl": round(gross - drag, 2),
                "unrealized_pct": round((gross - drag) / pos["notional"] * 100, 2)
                if pos["notional"] else 0.0})
            pos["market_value"] = round(pos["notional"] + pos["unrealized_pnl"], 2)
            still_open.append(pos)
            continue
        gross = (entry - price) / entry * pos["notional"]
        net = gross - drag
        book["cash"] = round(book["cash"] + pos["notional"] + net, 2)
        book["realized_pnl"] = round(book["realized_pnl"] + net, 2)
        rec = {k: pos.get(k) for k in (
            "symbol", "issuer", "entry_date", "entry_price", "shares",
            "notional", "tier", "score", "reason", "entry_fee_pct",
            "fee_known", "max_adverse_pct")}
        rec.update({"exit_date": m["date"], "exit_price": price,
                    "exit_reason": reason, "days_held": days,
                    "gross_pnl": round(gross, 2), "fee_drag": round(drag, 2),
                    "net_pnl": round(net, 2),
                    "return_pct": round(net / pos["notional"] * 100, 2)
                    if pos["notional"] else 0.0})
        book["closed"].append(rec)
        exits_today.append({"symbol": pos["symbol"], "reason": reason,
                            "net_pnl": rec["net_pnl"]})
    book["positions"] = still_open

    # ---- 2) fill free slots from today's ranked eligible candidates ----
    held = {p["symbol"] for p in book["positions"]}
    ranked = sorted(candidates, key=lambda x: (-x["score"], x["symbol"]))
    entered = []
    eligible_seen = 0
    cash_blocked = False
    for c in ranked:
        if len(book["positions"]) >= BOOK_MAX_POSITIONS:
            break
        if c["symbol"] in held:
            continue
        ok, _why = book_eligible(c, squeeze_syms)
        if not ok:
            continue
        eligible_seen += 1
        notional = round(min(book_equity(book) / BOOK_MAX_POSITIONS,
                             book["cash"]), 2)
        if notional <= 0:
            cash_blocked = True
            break
        tech = c.get("tech") or {}
        price = tech["price"]
        fee = (c.get("borrow") or {}).get("fee_pct")
        pos = {"symbol": c["symbol"], "issuer": c.get("issuer") or "",
               "entry_date": today, "entry_price": price,
               "shares": round(notional / price, 4), "notional": notional,
               "tier": "confirmed" if tech.get("breakdown") else "aggressive",
               "score": c["score"], "reason": pick_reason(c),
               "entry_fee_pct": fee, "fee_known": fee is not None,
               "accrued_fee_drag": 0.0,
               "current_price": price, "current_date": today,
               "unrealized_pnl": 0.0, "unrealized_pct": 0.0,
               "market_value": notional, "days_held": 0,
               "max_adverse_pct": 0.0,
               "below_sma50_seen": bool(tech.get("sma50")
                                        and price < tech["sma50"]),
               "stop_price": round(price * BOOK_STOP_MULT, 4),
               "target_price": round(price * BOOK_TARGET_MULT, 4)}
        book["cash"] = round(book["cash"] - notional, 2)
        book["positions"].append(pos)
        held.add(c["symbol"])
        entered.append(pos)

    # ---- 3) the day's record (one entry per date, rebuilt each run) ----
    todays_picks = [
        {"symbol": p["symbol"], "issuer": p.get("issuer") or "",
         "tier": p["tier"], "score": p["score"],
         "entry_price": p["entry_price"], "notional": p["notional"],
         "reason": p["reason"]}
        for p in book["positions"] + book["closed"]
        if p["entry_date"] == today]
    if entered:
        note = ""
    elif len(book["positions"]) >= BOOK_MAX_POSITIONS:
        note = "Book full — no free slots today."
    elif cash_blocked:
        note = "Free slots exist but the book is out of cash to reserve."
    elif eligible_seen == 0:
        note = ("No candidate passed the book's absolute safety bars today "
                "— no picks invented.")
    else:
        note = ""
    day_entry = {"date": today, "picks": todays_picks,
                 "exits": exits_today, "note": note}
    book["picks_history"] = [e for e in book["picks_history"]
                             if e["date"] != today] + [day_entry]
    book["picks_history"] = book["picks_history"][-120:]

    # ---- 4) account snapshot ----
    closed = book["closed"]
    wins = sum(1 for r in closed if r["net_pnl"] > 0)
    book["equity"] = book_equity(book)
    book["unrealized_pnl"] = round(sum(
        p.get("unrealized_pnl", 0.0) for p in book["positions"]), 2)
    book["total_return_pct"] = round(
        (book["equity"] / BOOK_START_CAPITAL - 1) * 100, 2)
    book["win_rate_pct"] = (round(wins / len(closed) * 100, 1)
                            if closed else None)
    book["updated"] = today
    save_json(BOOK_PATH, book)
    return book

# --------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="Filing date anchor YYYY-MM-DD (default: latest)")
    ap.add_argument("--days", type=int, default=1,
                    help="Filing days to scan for insider sells (default 1)")
    ap.add_argument("--max-filings", type=int, default=0)
    ap.add_argument("--skip-insider", action="store_true",
                    help="Reuse rolling state only (no EDGAR scan)")
    args = ap.parse_args()

    state_path = os.path.join(DATA, "short_state.json")
    state = load_json(state_path, {"sells": [], "borrow_history": {}})

    # ---- insider sells: scan + roll the 30-day window ----
    scan_info = {"filing_days": [], "filings_scanned": 0, "sell_txns_found": 0}
    if not args.skip_insider:
        new_sells, scan_info = scan_sells(args.days, args.max_filings)
        known = {s["key"] for s in state["sells"]}
        state["sells"].extend(s for s in new_sells if s["key"] not in known)
    cutoff = (date.today() - timedelta(days=45)).isoformat()
    state["sells"] = [s for s in state["sells"] if s["date"] >= cutoff]
    clusters = cluster_sells(state["sells"], date.today())

    # ---- market legs ----
    finra, finra_status = finra_leg()
    threshold, threshold_status = threshold_leg()
    puts = fund_puts()
    cong = congress_sells()

    # ---- candidate universe ----
    universe = set(clusters) | set(puts) | set(cong)
    if finra:
        ranked = sorted(finra.items(),
                        key=lambda kv: -((kv[1].get("pct_change") or -999)))
        universe |= {s for s, _ in ranked[:25]}
        ranked_dtc = sorted(finra.items(),
                            key=lambda kv: -(kv[1].get("days_to_cover") or 0))
        universe |= {s for s, _ in ranked_dtc[:25]}
    universe = sorted(universe)[:80]
    # Symbols the paper book already holds always get priced/borrow data,
    # even after they fall off the candidate board — exits depend on it.
    _held_book = load_json(BOOK_PATH, {})
    _held_syms = [p["symbol"] for p in _held_book.get("positions", [])
                  if p.get("symbol")]
    if _held_syms:
        universe = sorted(set(universe) | set(_held_syms))

    # ---- borrow (needs the universe first) ----
    borrow, borrow_status = borrow_leg(universe, state["borrow_history"])

    # ---- prices / technicals / floats ----
    tech, floats = {}, {}
    for sym in universe:
        hist = yahoo_history(sym)
        if hist:
            tech[sym] = technicals(hist)
        if sym in finra:
            floats[sym] = nasdaq_shares_out(sym)
        time.sleep(0.05)

    ctx = {"tech": tech, "finra": finra, "floats": floats, "borrow": borrow,
           "borrow_history": state["borrow_history"], "clusters": clusters,
           "fund_puts": puts, "congress": cong, "threshold": threshold}
    candidates = assemble(universe, ctx)
    watchlist = sorted((c for c in candidates if c["squeeze_score"] >= 4),
                       key=lambda x: -x["squeeze_score"])[:20]
    preds = update_short_tracker(candidates, borrow)
    book = update_short_book(candidates, ctx)

    state["updated"] = str(date.today())
    save_json(state_path, state)

    out = {
        "updated": str(date.today()),
        "version": 2,
        "legs": {
            "insider_sells": {"status": "live" if scan_info["filings_scanned"] else "state-only",
                              "filing_days": scan_info["filing_days"],
                              "filings_scanned": scan_info["filings_scanned"],
                              "sell_txns_in_window": len(state["sells"]),
                              "clusters_found": len(clusters),
                              "note": "SEC Form 4 code-S sales, 10b5-1 excluded, "
                                      "rolling 30-day cluster window. Confirmation "
                                      "only — never a standalone trigger (report §1)."},
            "finra_short_interest": finra_status,
            "borrow_ibkr": borrow_status,
            "threshold_list": threshold_status,
            "prices": {"status": "live", "source": "Yahoo Finance chart API",
                       "symbols_priced": len(tech)},
        },
        "candidates": candidates[:25],
        "squeeze_watchlist": [
            {"symbol": c["symbol"], "issuer": c["issuer"],
             "squeeze_score": c["squeeze_score"], "detail": c["squeeze_detail"],
             "si": c["si"], "tech": c["tech"]} for c in watchlist],
        "fund_puts_evidence": sorted(puts.values(), key=lambda x: -x["points"]),
        "congress_sells_evidence": sorted(cong.values(), key=lambda x: -x["count"]),
        "short_calls_tracked": len(preds),
        "caveats": [
            "Short interest is FINRA twice-monthly data — already ~1-4 weeks old "
            "when you see it. Borrow fees are IBKR-inventory snapshots. Insider "
            "selling alone predicts almost nothing; it only confirms.",
            "13F put dollar figures are underlying NOTIONAL, not premium paid — "
            "a $912M put position can cost single-digit millions.",
            "Crowded names (high SI + high days-to-cover) are gated OUT of the "
            "short book and listed on the squeeze watchlist instead: at extremes, "
            "high short interest marks squeeze risk, not opportunity.",
            "Every short call is graded at 30/90 days NET of borrow-fee drag, "
            "with its worst adverse excursion published alongside.",
        ],
    }
    save_json(os.path.join(DATA, "shorts.json"), out)
    print(f"Short engine v2: {len(candidates)} candidates "
          f"({sum(1 for c in candidates if c['status'] == 'call')} calls, "
          f"{len(watchlist)} on squeeze watchlist), "
          f"FINRA={finra_status['status']}, borrow={borrow_status['status']}, "
          f"insider clusters={len(clusters)}, tracked short calls={len(preds)}")
    print(f"$100 Short Book: equity ${book['equity']:.2f} "
          f"({book['total_return_pct']:+.2f}%), cash ${book['cash']:.2f}, "
          f"{len(book['positions'])} open, {len(book['closed'])} closed")

if __name__ == "__main__":
    main()
