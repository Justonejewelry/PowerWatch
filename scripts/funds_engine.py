#!/usr/bin/env python3
"""
Funds Tracker — PowerWatch Phase 3 (13F institutional holdings).

For a watchlist of marquee funds, reads their two most recent Form 13F-HR
filings from SEC EDGAR, parses the information-table XML, and diffs the
quarters: new positions, exits, and big share-count increases/decreases.

Tickers come from OpenFIGI CUSIP mapping, but only for CUSIPs that will
actually be displayed (top holdings, changes, consensus candidates) —
the unauthenticated OpenFIGI limit is ~25 requests/min, and quant funds
hold thousands of names nobody needs tickers for. Mappings persist in
data/cusip_cache.json. Per-fund results persist in data/funds_state.json:
when a fund's latest accession is unchanged, its summary is reused, so
most daily runs finish in seconds (13Fs land quarterly).

Output: data/funds.json for the dashboard.

Honest limits (also stated on the page): 13Fs are quarterly with up to a
45-day filing lag, cover long positions only, and values are as reported
(in dollars on current filings).
"""
import json, os, time
import xml.etree.ElementTree as ET
from datetime import date

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(BASE), "data")
UA = {"User-Agent": "PepperResearch/1.0 (mr.jsciaraffa@gmail.com)"}
NS = "{http://www.sec.gov/edgar/document/thirteenf/informationtable}"

# name, CIK, label — CIKs verified against data.sec.gov submissions
# (name match + a recent 13F-HR on file), 2026-11-15.
FUNDS = [
    ("Berkshire Hathaway", 1067983, "Warren Buffett"),
    ("Pershing Square", 1336528, "Bill Ackman"),
    ("Scion Asset Management", 1649339, "Michael Burry"),
    ("Renaissance Technologies", 1037389, "quant giant"),
    ("Citadel Advisors", 1423053, "Ken Griffin"),
    ("Bridgewater Associates", 1350694, "world's largest hedge fund"),
    ("Tiger Global", 1167483, ""),
    ("Coatue Management", 1135730, "Philippe Laffont"),
    ("Third Point", 1040273, "Dan Loeb"),
    ("Baupost Group", 1061768, "Seth Klarman"),
    ("Appaloosa", 1656456, "David Tepper"),
]


def get(url, **kw):
    r = requests.get(url, headers=UA, timeout=kw.pop("timeout", 60), **kw)
    r.raise_for_status()
    return r


def recent_13fs(cik):
    d = get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").json()
    rec = d["filings"]["recent"]
    out, seen_periods = [], set()
    for form, acc, rep, filed in zip(rec["form"], rec["accessionNumber"],
                                     rec["reportDate"], rec["filingDate"]):
        if form.startswith("13F-HR") and rep not in seen_periods:
            seen_periods.add(rep)
            out.append({"accession": acc, "period": rep, "filed": filed})
        if len(out) == 2:
            break
    return out


def info_table_url(cik, accession):
    acc = accession.replace("-", "")
    idx = get(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json").json()
    xmls = [f for f in idx["directory"]["item"]
            if f["name"].endswith(".xml") and f["name"] != "primary_doc.xml"]
    if not xmls:
        return None
    xmls.sort(key=lambda f: -int(f.get("size") or 0))
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{xmls[0]['name']}"


def parse_info_table(url):
    """Aggregate a 13F information table by CUSIP. Values are dollars."""
    root = ET.fromstring(get(url, timeout=180).content)
    holds = {}
    for e in root.iter(f"{NS}infoTable"):
        g = lambda tag: (e.findtext(f"{NS}{tag}") or "").strip()
        cusip = g("cusip")
        if not cusip:
            continue
        put_call = g("putCall")
        sh_node = e.find(f"{NS}shrsOrPrnAmt")
        shares, stype = 0, ""
        if sh_node is not None:
            shares = int(sh_node.findtext(f"{NS}sshPrnamt") or 0)
            stype = (sh_node.findtext(f"{NS}sshPrnamtType") or "").strip()
        h = holds.setdefault(cusip, {"name": g("nameOfIssuer"), "value": 0,
                                     "shares": 0, "options": False})
        h["value"] += int(g("value") or 0)
        if put_call:
            h["options"] = True
        elif stype == "SH":
            h["shares"] += shares
    return holds


def load_cache():
    p = os.path.join(DATA, "cusip_cache.json")
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception:
            return {}
    return {}


def map_tickers(cusips, cache):
    """Map display-worthy CUSIPs via OpenFIGI, respecting its rate limit.
    Errors leave the CUSIP uncached so a later run retries it."""
    todo = [c for c in cusips if c not in cache]
    print(f"OpenFIGI: {len(todo)} CUSIPs to map ({len(cusips) - len(todo)} cached)",
          flush=True)
    for i in range(0, len(todo), 10):
        batch = todo[i:i + 10]
        try:
            r = requests.post(
                "https://api.openfigi.com/v3/mapping",
                json=[{"idType": "ID_CUSIP", "idValue": c} for c in batch],
                headers={"Content-Type": "application/json"}, timeout=30)
            results = r.json()
            if not isinstance(results, list):
                raise ValueError("unexpected OpenFIGI response")
            for cusip, res in zip(batch, results):
                if "error" in res:
                    continue
                data = res.get("data") or []
                cache[cusip] = data[0]["ticker"] if data else None
        except Exception as exc:
            print(f"  OpenFIGI batch failed ({type(exc).__name__}), will retry next run",
                  flush=True)
        time.sleep(2.5)  # stay under the unauthenticated 25 req/min
    return cache


def brief(cusip, h, cache):
    return {"symbol": cache.get(cusip), "name": h["name"], "cusip": cusip,
            "value": h["value"], "shares": h["shares"]}


def diff_sets(latest, prior):
    """CUSIP sets for display: new, exited, and big movers."""
    new = [c for c in latest if c not in prior]
    exited = [c for c in prior if c not in latest]
    movers = []
    for c, h in latest.items():
        if c in prior and prior[c]["shares"] and h["shares"]:
            d = h["shares"] - prior[c]["shares"]
            if abs(100 * d / prior[c]["shares"]) >= 5:
                movers.append((c, abs(h["value"] - prior[c]["value"])))
    movers.sort(key=lambda x: -x[1])
    return new, exited, [c for c, _ in movers[:12]]


def build_summary(name, label, meta, latest, prior, cache):
    total = sum(h["value"] for h in latest.values())

    def with_pct(b):
        b = dict(b)
        b["pct"] = round(100 * b["value"] / total, 2) if total else 0
        return b

    top = [with_pct(brief(c, h, cache)) for c, h in
           sorted(latest.items(), key=lambda kv: -kv[1]["value"])[:10]]
    new_c, exited_c, _ = diff_sets(latest, prior)
    new = sorted((brief(c, latest[c], cache) for c in new_c), key=lambda b: -b["value"])
    exited = sorted((brief(c, prior[c], cache) for c in exited_c), key=lambda b: -b["value"])
    inc, dec = [], []
    for c, h in latest.items():
        if c in prior and prior[c]["shares"] and h["shares"]:
            d = h["shares"] - prior[c]["shares"]
            pct = 100 * d / prior[c]["shares"]
            if abs(pct) >= 5:
                e = brief(c, h, cache)
                e["pct_change"] = round(pct, 1)
                e["value_change"] = h["value"] - prior[c]["value"]
                (inc if d > 0 else dec).append(e)
    inc.sort(key=lambda b: -abs(b["value_change"]))
    dec.sort(key=lambda b: -abs(b["value_change"]))
    return {
        "name": name, "label": label, "accession": meta["accession"],
        "period": meta["period"], "filed": meta["filed"],
        "total_value": total, "positions": len(latest),
        "top_holdings": top,
        "changes": {"new": new[:12], "exited": exited[:12],
                    "increased": inc[:6], "decreased": dec[:6]}}


def main():
    cache = load_cache()
    state_path = os.path.join(DATA, "funds_state.json")
    state = {}
    if os.path.exists(state_path):
        try:
            state = json.load(open(state_path))
        except Exception:
            state = {}

    # Pass 1: fetch/parse raw holdings per fund (or reuse state).
    raw = {}   # name -> (meta, latest, prior)
    order = []
    for name, cik, label in FUNDS:
        try:
            filings = recent_13fs(cik)
            if not filings:
                print(f"{name}: no 13F-HR found, skipped", flush=True)
                continue
            prev = state.get(name)
            if prev and prev.get("accession") == filings[0]["accession"]:
                raw[name] = ({"accession": filings[0]["accession"],
                              "period": filings[0]["period"],
                              "filed": filings[0]["filed"]},
                             prev["latest_raw"], prev["prior_raw"])
                order.append((name, label, True))
                print(f"{name}: unchanged ({filings[0]['period']}), reused", flush=True)
                continue
            latest = parse_info_table(info_table_url(cik, filings[0]["accession"]))
            prior = {}
            if len(filings) > 1:
                prior = parse_info_table(info_table_url(cik, filings[1]["accession"]))
            raw[name] = (filings[0], latest, prior)
            order.append((name, label, False))
            print(f"{name}: {filings[0]['period']} parsed — {len(latest)} positions",
                  flush=True)
        except Exception as exc:
            print(f"{name}: FAILED ({type(exc).__name__}: {exc})", flush=True)
        time.sleep(0.3)

    # Which CUSIPs deserve a ticker? Top holdings, all changes, and
    # anything held by 2+ funds or with big combined value (consensus).
    display = set()
    holder_count, combined = {}, {}
    for name, (meta, latest, prior) in raw.items():
        for c, h in latest.items():
            holder_count[c] = holder_count.get(c, 0) + 1
            combined[c] = combined.get(c, 0) + h["value"]
        top_c = [c for c, _ in sorted(latest.items(), key=lambda kv: -kv[1]["value"])[:25]]
        new_c, exited_c, mover_c = diff_sets(latest, prior)
        # Quant funds churn thousands of tiny positions — only the biggest
        # new/exited names are ever displayed, so only those need tickers.
        new_c = sorted(new_c, key=lambda c: -latest[c]["value"])[:40]
        exited_c = sorted(exited_c, key=lambda c: -prior[c]["value"])[:40]
        display.update(top_c, new_c, exited_c, mover_c)
    multi = [c for c, n in holder_count.items() if n >= 2]
    multi.sort(key=lambda c: -combined[c])
    display.update(multi[:300])
    display.update(c for c, _ in sorted(combined.items(), key=lambda kv: -kv[1])[:40])
    cache = map_tickers(display, cache)

    # Pass 2: summaries, consensus, state.
    funds_out, cons = [], {}
    fresh = 0
    for name, label, reused in order:
        meta, latest, prior = raw[name]
        summary = build_summary(name, label, meta, latest, prior, cache)
        funds_out.append(summary)
        if not reused:
            fresh += 1
            state[name] = {"accession": meta["accession"], "summary": summary,
                           "latest_raw": latest, "prior_raw": prior}
        for c, h in latest.items():
            b = brief(c, h, cache)
            key = b["symbol"] or c
            a = cons.setdefault(key, {"symbol": b["symbol"], "name": b["name"],
                                      "funds": 0, "total_value": 0})
            a["funds"] += 1
            a["total_value"] += b["value"]
    consensus = sorted(cons.values(), key=lambda a: (a["funds"], a["total_value"]),
                       reverse=True)[:15]

    data = {"updated": date.today().isoformat(),
            "stats": {"funds_tracked": len(funds_out), "fresh_filings": fresh},
            "consensus": consensus, "funds": funds_out}
    with open(os.path.join(DATA, "funds.json"), "w") as fh:
        json.dump(data, fh, indent=1)
    with open(state_path, "w") as fh:
        json.dump(state, fh)
    with open(os.path.join(DATA, "cusip_cache.json"), "w") as fh:
        json.dump(cache, fh)
    print(f"Wrote funds.json: {len(funds_out)} funds ({fresh} fresh), "
          f"consensus top: {consensus[0] if consensus else None}")


if __name__ == "__main__":
    main()
