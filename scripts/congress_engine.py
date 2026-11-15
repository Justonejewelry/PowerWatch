#!/usr/bin/env python3
"""
Congress Tracker — PowerWatch Phase 2 (House first).

Reads U.S. House Periodic Transaction Reports (STOCK Act disclosures)
from the Clerk of the House's official disclosure archive:
  - Yearly index:  disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip
    (XML listing every filing; FilingType "P" = Periodic Transaction Report)
  - PTR PDFs:      disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{DocID}.pdf

Electronically filed PTRs are text PDFs with a regular layout:
asset name + "(TICKER) [ST]", a P/S/E type column, transaction date,
notification date, and an amount range. Paper-filed PTRs are scanned
images — they yield no text and are counted honestly as unreadable.

Output: data/congress.json for the dashboard.
"""
import argparse, io, json, os, re, subprocess, sys, zipfile
from datetime import date, datetime, timedelta
import xml.etree.ElementTree as ET

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "PepperResearch/1.0 (mr.jsciaraffa@gmail.com)"}
CLERK = "https://disclosures-clerk.house.gov/public_disc"

TXN_RE = re.compile(
    r"\b(?P<type>[PSE])\s+(?P<d1>\d{2}/\d{2}/\d{4})\s+(?P<d2>\d{2}/\d{2}/\d{4})\s+"
    r"(?:\$(?P<lo>[\d,]+)\s*-\s*\$(?P<hi>[\d,]+)"
    r"|\$(?P<lo2>[\d,]+)\s*-\s*\n[^\$]{0,160}?[^\$]\$(?P<hi2>[\d,]+)"
    r"|Over \$(?P<over>[\d,]+))")
TICKER_RE = re.compile(r"\(([A-Z]{1,5})\)\s*\[ST\]")
AMOUNT_BRACKETS = "amount range as disclosed"


def fetch_index(year):
    r = requests.get(f"{CLERK}/financial-pdfs/{year}FD.zip", headers=UA, timeout=60)
    r.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    xml_name = [n for n in zf.namelist() if n.endswith(".xml")][0]
    root = ET.fromstring(zf.read(xml_name))
    out = []
    for m in root.iter("Member"):
        if (m.findtext("FilingType") or "") != "P":
            continue
        try:
            filed = datetime.strptime(m.findtext("FilingDate"), "%m/%d/%Y").date()
        except (TypeError, ValueError):
            continue
        out.append({
            "member": f"{m.findtext('First')} {m.findtext('Last')}".strip(),
            "state_dst": m.findtext("StateDst") or "",
            "filed": filed.isoformat(),
            "doc_id": m.findtext("DocID") or "",
            "year": year})
    out.sort(key=lambda p: p["filed"], reverse=True)
    return out


def pdf_text(pdf_bytes):
    """Prefer pdftotext -layout (column-preserving); fall back to pypdf."""
    try:
        p = subprocess.run(["pdftotext", "-layout", "-", "-"],
                           input=pdf_bytes, capture_output=True, timeout=60)
        if p.returncode == 0 and p.stdout:
            return p.stdout.decode("utf-8", "replace")
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join((pg.extract_text() or "") for pg in reader.pages)
    except Exception:
        return ""


def parse_ptr(text, meta):
    """Extract stock transactions from one PTR's text."""
    if len(text) < 400:  # scanned paper filing or cover-only
        return []
    tickers = [(m.start(), m.group(1)) for m in TICKER_RE.finditer(text)]
    if not tickers:
        return []
    trades = []
    for m in TXN_RE.finditer(text):
        # The "(TICKER) [ST]" tag can sit before OR after the type/date
        # columns (long asset names wrap it to the next line) — take the
        # closest ticker tag within the same transaction block.
        sym, best = None, 500
        for pos, s in tickers:
            d = abs(pos - m.start())
            if d < best:
                sym, best = s, d
        if not sym:
            continue
        ttype, tdate = m.group("type"), m.group("d1")
        if m.group("over"):  # "Over $X"
            low = int(m.group("over").replace(",", ""))
            est, rng = low, f"Over ${low:,}"
        else:
            low = int((m.group("lo") or m.group("lo2")).replace(",", ""))
            high = int((m.group("hi") or m.group("hi2")).replace(",", ""))
            est = (low + high) // 2
            rng = f"${low:,} - ${high:,}"
        try:
            tdate_iso = datetime.strptime(tdate, "%m/%d/%Y").date().isoformat()
        except ValueError:
            continue
        trades.append({
            "member": meta["member"], "state_dst": meta["state_dst"],
            "symbol": sym, "type": {"P": "Purchase", "S": "Sale", "E": "Exchange"}[ttype],
            "date": tdate_iso, "filed": meta["filed"],
            "amount_range": rng, "est_value": est, "doc_id": meta["doc_id"]})
    return trades


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-ptrs", type=int, default=150)
    ap.add_argument("--window-days", type=int, default=120)
    args = ap.parse_args()

    today = date.today()
    ptrs = fetch_index(today.year)
    if today.month <= 2:  # early year: filings still land in last year's index
        try:
            ptrs += fetch_index(today.year - 1)
            ptrs.sort(key=lambda p: p["filed"], reverse=True)
        except Exception:
            pass
    ptrs = ptrs[:args.max_ptrs]
    print(f"Processing {len(ptrs)} most recent House PTRs…", flush=True)

    all_trades, unreadable, with_trades, no_stock = [], 0, 0, 0
    for i, meta in enumerate(ptrs):
        try:
            r = requests.get(
                f"{CLERK}/ptr-pdfs/{meta['year']}/{meta['doc_id']}.pdf",
                headers=UA, timeout=60)
            r.raise_for_status()
            text = pdf_text(r.content)
            trades = parse_ptr(text, meta)
        except Exception:
            text, trades = "", []
        if trades:
            with_trades += 1
            all_trades.extend(trades)
        elif len(text) < 400:
            unreadable += 1  # paper-scanned filing, no extractable text
        else:
            no_stock += 1  # readable, but no stock trades (bonds/funds only)
        if (i + 1) % 50 == 0:
            print(f"  {i + 1}/{len(ptrs)} PTRs, {len(all_trades)} trades so far", flush=True)

    cutoff = (today - timedelta(days=args.window_days)).isoformat()
    window = [t for t in all_trades if t["date"] >= cutoff]

    def aggregate(rows, ttype):
        by = {}
        for t in rows:
            if t["type"] != ttype:
                continue
            a = by.setdefault(t["symbol"], {
                "symbol": t["symbol"], "count": 0, "members": set(),
                "est_value": 0})
            a["count"] += 1
            a["members"].add(t["member"])
            a["est_value"] += t["est_value"]
        out = [{"symbol": a["symbol"], "count": a["count"],
                "members": len(a["members"]), "est_value": a["est_value"]}
               for a in by.values()]
        out.sort(key=lambda a: (a["members"], a["count"], a["est_value"]), reverse=True)
        return out

    recent = sorted(all_trades, key=lambda t: t["date"], reverse=True)[:60]
    data = {
        "updated": today.isoformat(),
        "source": "U.S. House Clerk — Periodic Transaction Reports (STOCK Act)",
        "stats": {"ptrs_processed": len(ptrs), "ptrs_with_trades": with_trades,
                  "ptrs_unreadable": unreadable, "ptrs_no_stock": no_stock,
                  "trades_found": len(all_trades),
                  "window_days": args.window_days},
        "top_buys": aggregate(window, "Purchase")[:12],
        "top_sells": aggregate(window, "Sale")[:8],
        "recent_trades": recent}
    out_path = os.path.join(os.path.dirname(BASE), "data", "congress.json")
    with open(out_path, "w") as fh:
        json.dump(data, fh, indent=1)
    print(f"Wrote {out_path}: {len(all_trades)} trades from {with_trades} PTRs "
          f"({unreadable} unreadable/paper), "
          f"top buy: {data['top_buys'][0] if data['top_buys'] else None}")


if __name__ == "__main__":
    main()
