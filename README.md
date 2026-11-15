# 👁️ PowerWatch

**Follow the money in power.**

PowerWatch tracks what corporate insiders — CEOs, CFOs, directors, and 10% owners — do with their own money, using their mandatory SEC Form 4 disclosures.

Insiders sell for many reasons: taxes, diversification, pre-scheduled 10b5-1 plans. They buy on the open market with their own cash for basically one reason. PowerWatch reads **every Form 4 filed**, extracts open-market purchases, and scores them:

- **Who** — CEO/CFO buys weigh most, then officers, directors, 10% owners
- **How much** — bigger personal checks score higher
- **Clusters** — multiple insiders at the same company buying together is the strongest tell
- **Offsets** — heavy non-planned insider selling cuts the score

Every top call is logged with its entry price in the **Prediction Tracker** and graded at 30 and 90 days. No cherry-picking — the scoreboard is public.

## How it works

- `scripts/insider_engine.py` scans the SEC EDGAR daily index for Form 4 filings, parses the ownership XML, scores insider buying, and refreshes prediction prices/grades (Yahoo Finance).
- A GitHub Actions workflow runs it every weekday after the US market close and commits fresh data to `data/`.
- This site (GitHub Pages) renders `data/latest.json` and `data/predictions.json` — signals, cluster buys, heavy selling, and the tracker. Every ticker links straight to its Robinhood page.

## Roadmap

- **Phase 2 — Congress Tracker:** STOCK Act disclosures from House & Senate members, built from the official disclosure portals.
- **Phase 3 — Big Fund Tracker:** quarterly 13F institutional holdings.

## Disclaimer

Signals, not financial advice. Insider buying is one of the best-documented edges in public markets, but nothing here guarantees a result. Do your own homework before buying anything.
