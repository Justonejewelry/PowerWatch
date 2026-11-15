"use strict";
const $ = (id) => document.getElementById(id);
const money = (v) => v >= 1e6 ? "$" + (v / 1e6).toFixed(2) + "M"
  : v >= 1e3 ? "$" + Math.round(v / 1e3) + "K" : "$" + Math.round(v);
const pct = (v) => (v == null ? "—" : (v > 0 ? "+" : "") + v.toFixed(2) + "%");
const money2 = (v) => v == null ? "—" : (v < 0 ? "-$" : "$") + Math.abs(v).toFixed(2);
const rh = (sym) => `https://robinhood.com/stocks/${encodeURIComponent(sym)}`;
const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;");

/* ================= global state (for ticker + drawer) ================= */
const STATE = {latest: null, preds: [], congress: null, funds: null, shorts: null, shortPreds: [], book: null};
const lastPrice = (hist) => {
  if (!hist || !hist.length) return null;
  const row = hist[hist.length - 1];
  return row[row.length - 1];
};
const arrowFor = (v) => v == null ? "" :
  `<span class="${v > 0 ? "up" : v < 0 ? "down" : "flat"}">${v > 0 ? "▲" : v < 0 ? "▼" : "•"} ${pct(v)}</span>`;

/* ================= shades (window-shade navigation) ================= */
const SHADES = ["overview", "signals", "tracker", "congress", "funds", "shorts", "selling", "guide"];
let activeShade = "overview";
function moveIndicator() {
  const rail = $("shadeRail"), ind = $("pivotIndicator");
  const tab = rail && rail.querySelector('[aria-selected="true"]');
  if (!tab || !ind) return;
  if (window.innerWidth <= 900) {
    ind.style.left = tab.offsetLeft + "px";
    ind.style.width = tab.offsetWidth + "px";
    ind.style.top = tab.offsetTop + "px";
    ind.style.height = tab.offsetHeight + "px";
  } else {
    ind.style.left = "8px";
    ind.style.width = "calc(100% - 16px)";
    ind.style.top = tab.offsetTop + "px";
    ind.style.height = tab.offsetHeight + "px";
  }
}
function activateShade(name, save = true) {
  if (!SHADES.includes(name)) name = "overview";
  activeShade = name;
  document.querySelectorAll(".shade-tab").forEach(t =>
    t.setAttribute("aria-selected", t.dataset.shade === name ? "true" : "false"));
  document.querySelectorAll(".shade").forEach(p =>
    p.classList.toggle("active", p.dataset.shadePanel === name));
  if (save) { try { localStorage.setItem("pw-shade", name); } catch (e) {} }
  requestAnimationFrame(moveIndicator);
  if (name === "signals") requestAnimationFrame(maybeDrawCharts);
  const rail = $("shadeRail");
  const tab = rail && rail.querySelector('[aria-selected="true"]');
  if (tab && window.innerWidth <= 900) tab.scrollIntoView({block: "nearest", inline: "center", behavior: "smooth"});
}
document.querySelectorAll(".shade-tab").forEach(t =>
  t.addEventListener("click", () => activateShade(t.dataset.shade)));
window.addEventListener("resize", moveIndicator);

/* M2 Reveal — pointer-tracking highlight */
document.addEventListener("pointermove", (e) => {
  const t = e.target.closest && e.target.closest(".card,.fund-card,.smart-card,.cluster,.exp-card,.shade-tab,.stat");
  if (!t) return;
  const r = t.getBoundingClientRect();
  if (!r.width || !r.height) return;
  t.style.setProperty("--mx", ((e.clientX - r.left) / r.width * 100).toFixed(1) + "%");
  t.style.setProperty("--my", ((e.clientY - r.top) / r.height * 100).toFixed(1) + "%");
}, {passive: true});

/* keyboard: 1–8 shades, "/" search, Esc drawer */
document.addEventListener("keydown", (e) => {
  const typing = /INPUT|TEXTAREA|SELECT/.test(document.activeElement && document.activeElement.tagName || "");
  if (e.key === "Escape") { closeDrawer(); if (typing) document.activeElement.blur(); return; }
  if (typing) return;
  if (e.key === "/") { e.preventDefault(); $("globalSearch").focus(); return; }
  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= SHADES.length) activateShade(SHADES[n - 1]);
});

/* ================= search filter ================= */
function applySearch() {
  const q = $("globalSearch").value.trim().toUpperCase();
  $("searchClear").hidden = !q;
  document.querySelectorAll("#stage .card, #stage .cluster, #stage .smart-card, #stage .fund-card, #stage tbody tr")
    .forEach(el => {
      /* ticker-bearing elements match on the ticker itself (so "FF" doesn't
         hit "OFFICER"); the rest fall back to a text match */
      const hit = !q || (el.dataset && el.dataset.ticker
        ? el.dataset.ticker.toUpperCase().startsWith(q)
        : el.textContent.toUpperCase().includes(q));
      el.classList.toggle("is-filtered-out", !hit);
    });
}
$("globalSearch").addEventListener("input", applySearch);
$("searchClear").addEventListener("click", () => { $("globalSearch").value = ""; applySearch(); $("globalSearch").focus(); });

/* ================= ticker tape ================= */
function buildTicker() {
  const items = [];
  const L = STATE.latest, B = STATE.book;
  (L && L.signals || []).slice(0, 10).forEach(s => {
    const px = lastPrice(s.history);
    const mom = s.indicators && s.indicators.mom_1m_pct;
    items.push(`<span class="ticker-item" data-ticker="${s.symbol}" data-kind="signal"><span class="src">Insider</span><span class="tk">${s.symbol}</span>${px != null ? " $" + Number(px).toFixed(2) : ""} · score ${s.score}${mom != null ? " " + arrowFor(mom) + " <span class=\"flat\">1M</span>" : ""}</span>`);
  });
  STATE.preds.slice(0, 12).forEach(p => {
    items.push(`<span class="ticker-item" data-ticker="${p.symbol}" data-kind="call"><span class="src">Call</span><span class="tk">${p.symbol}</span>${p.current_price != null ? " $" + Number(p.current_price).toFixed(2) : ""} ${arrowFor(p.return_pct)}</span>`);
  });
  ((STATE.congress && STATE.congress.top_buys) || []).slice(0, 8).forEach(t => {
    items.push(`<span class="ticker-item" data-ticker="${t.symbol}" data-kind="congress"><span class="src">Congress</span><span class="tk">${t.symbol}</span> <span class="flat">${t.count} buys · ${t.members} members</span></span>`);
  });
  ((STATE.shorts && STATE.shorts.candidates) || []).slice(0, 8).forEach(c => {
    const px = c.tech && c.tech.price;
    const mom = c.tech && c.tech.mom_1m_pct;
    items.push(`<span class="ticker-item" data-ticker="${c.symbol}" data-kind="short"><span class="src">Short</span><span class="tk">${c.symbol}</span>${px != null ? " $" + Number(px).toFixed(2) : ""} · score ${c.score}${mom != null ? " " + arrowFor(mom) + " <span class=\"flat\">1M</span>" : ""}</span>`);
  });
  if (B) {
    items.push(`<span class="ticker-item muted"><span class="src">Book</span><span class="tk">$100 Book</span> equity ${money2(B.equity)} ${arrowFor(B.total_return_pct)}</span>`);
    (B.positions || []).forEach(p => {
      items.push(`<span class="ticker-item" data-ticker="${p.symbol}" data-kind="book"><span class="src">Book</span><span class="tk">${p.symbol}</span> short ${p.current_price != null ? "$" + Number(p.current_price).toFixed(2) : ""} ${arrowFor(p.unrealized_pct)}</span>`);
    });
  }
  const track = $("tickerTrack");
  if (!items.length) { track.innerHTML = `<span class="ticker-item muted">Tape is waiting on the feeds…</span>`; return; }
  const group = `<div class="ticker-group">${items.join("")}</div>`;
  track.innerHTML = group + group; /* duplicated for the seamless loop */
}

/* ================= sideways shade: detail drawer ================= */
function dRow(k, v, cls) { return `<div class="d-row"><span class="k">${k}</span><span class="v ${cls || ""}">${v}</span></div>`; }
function openDrawer(sym) {
  if (!sym) return;
  sym = String(sym).toUpperCase();
  const L = STATE.latest, C = STATE.congress, F = STATE.funds, S = STATE.shorts, B = STATE.book;
  const sig = (L && L.signals || []).find(s => s.symbol === sym);
  const seller = (L && L.heavy_sellers || []).find(s => s.symbol === sym);
  const pred = STATE.preds.find(p => p.symbol === sym);
  const cBuy = (C && C.top_buys || []).find(t => t.symbol === sym);
  const cSell = (C && C.top_sells || []).find(t => t.symbol === sym);
  const recent = (C && C.recent_trades || []).filter(t => t.symbol === sym).slice(0, 4);
  const cons = (F && F.consensus || []).find(c => c.symbol === sym);
  const fundNames = [];
  (F && F.funds || []).forEach(fd => {
    const held = (fd.top_holdings || []).some(h => h.symbol === sym);
    const isNew = (fd.changes && fd.changes.new || []).some(n => n.symbol === sym && n.option !== "put");
    if (held) fundNames.push(fd.name + " (holds)");
    else if (isNew) fundNames.push(fd.name + " (new)");
  });
  const cand = (S && S.candidates || []).find(c => c.symbol === sym);
  const squeeze = (S && S.squeeze_watchlist || []).find(w => w.symbol === sym);
  const pos = (B && B.positions || []).find(p => p.symbol === sym);
  const closedPos = (B && [...(B.closed || [])].reverse() || []).find(p => p.symbol === sym);
  const issuer = (sig && sig.issuer) || (pred && pred.issuer) || (cand && cand.issuer) ||
    (pos && pos.issuer) || (cons && cons.name) || (seller && seller.issuer) || "";

  let h = "";
  if (sig) {
    const px = lastPrice(sig.history);
    h += `<div class="d-sec"><h4>🟢 Insider-buy signal</h4>
      ${dRow("Signal score", sig.score)}
      ${px != null ? dRow("Last price", "$" + Number(px).toFixed(2)) : ""}
      ${dRow("Insiders bought", money(sig.buy_value) + " · " + sig.n_buyers + " buyer" + (sig.n_buyers === 1 ? "" : "s"))}
      ${(sig.buyers || []).map(b => dRow(esc(b.owner) + ` <span style="color:var(--dim2)">` + esc(b.role) + "</span>", money(b.value), "pos")).join("")}
      ${sig.indicators && sig.indicators.rsi != null ? dRow("RSI(14)", sig.indicators.rsi) : ""}
      ${sig.indicators && sig.indicators.vs_sma50_pct != null ? dRow("vs 50-day avg", pct(sig.indicators.vs_sma50_pct), sig.indicators.vs_sma50_pct >= 0 ? "pos" : "neg") : ""}
      ${sig.indicators && sig.indicators.mom_1m_pct != null ? dRow("Last month", pct(sig.indicators.mom_1m_pct), sig.indicators.mom_1m_pct >= 0 ? "pos" : "neg") : ""}
      ${sig.mixed_selling ? `<p class="d-note">⚠️ Heavy insider selling at this company offsets the buying.</p>` : ""}
    </div>`;
  }
  if (pred) {
    const r = pred.return_pct || 0;
    h += `<div class="d-sec"><h4>🎯 Tracked call</h4>
      ${dRow("Signal date", pred.signal_date)}
      ${dRow("Entry", pred.entry_price != null ? "$" + Number(pred.entry_price).toFixed(2) : "—")}
      ${dRow("Now", pred.current_price != null ? "$" + Number(pred.current_price).toFixed(2) : "—")}
      ${dRow("Return", pct(r), r >= 0 ? "pos" : "neg")}
      ${dRow("Days held", (pred.days_held ?? 0) + "d")}
      ${dRow("30-day grade", pct(pred.grade_30d_pct), (pred.grade_30d_pct ?? 0) >= 0 ? "pos" : "neg")}
      ${dRow("90-day grade", pct(pred.grade_90d_pct), (pred.grade_90d_pct ?? 0) >= 0 ? "pos" : "neg")}
    </div>`;
  }
  if (cBuy || cSell || recent.length) {
    h += `<div class="d-sec"><h4>🏛️ Congress</h4>
      ${cBuy ? dRow("House buys (120d)", cBuy.count + " buys · " + cBuy.members + " members · ~" + money(cBuy.est_value)) : ""}
      ${cSell ? dRow("House sales (120d)", cSell.count + " sales · " + cSell.members + " members · ~" + money(cSell.est_value)) : ""}
      ${recent.map(t => dRow(esc(t.date) + " · " + esc(t.member), esc(t.type) + " " + esc(t.amount_range))).join("")}
    </div>`;
  }
  if (cons || fundNames.length) {
    h += `<div class="d-sec"><h4>🐋 Big funds (13F)</h4>
      ${cons ? dRow("Funds holding", cons.funds + " · " + money(cons.total_value) + " combined") : ""}
      ${fundNames.map(n => dRow(esc(n), "")).join("")}
    </div>`;
  }
  if (cand) {
    const incl = cand.insider && cand.insider.cluster;
    h += `<div class="d-sec"><h4>📉 Short candidate</h4>
      ${dRow("Status", (cand.status || "").toUpperCase())}
      ${dRow("Short score", cand.score)}
      ${cand.tech && cand.tech.price != null ? dRow("Price", "$" + Number(cand.tech.price).toFixed(2)) : ""}
      ${incl ? dRow("Insider selling", incl.n_insiders + " insider" + (incl.n_insiders === 1 ? "" : "s") + " · " + money(incl.total_value)) : ""}
      ${cand.si ? dRow("Short interest", (cand.si.shares_short / 1e6).toFixed(1) + "M sh" + (cand.si.pct_of_float != null ? " · " + cand.si.pct_of_float + "% of float" : "")) : dRow("Short interest", "no data yet")}
      ${cand.borrow ? dRow("Borrow fee", cand.borrow.fee_pct + "%/yr") : dRow("Borrow fee", "pending")}
      ${cand.tech ? dRow("Trend", cand.tech.breakdown ? "💥 50-day breakdown" : "no breakdown") : ""}
    </div>`;
  }
  if (squeeze) {
    h += `<div class="d-sec"><h4>🚨 Squeeze watchlist</h4>
      ${dRow("Squeeze risk", squeeze.squeeze_score)}
      <p class="d-note">${(squeeze.detail || []).map(esc).join("; ")}</p>
      <p class="d-note">Crowded names are gated OUT of the $100 Short Book.</p>
    </div>`;
  }
  if (pos) {
    const cur = pos.current_price;
    const stopMove = cur ? (pos.stop_price / cur - 1) * 100 : null;
    const tgtMove = cur ? (pos.target_price / cur - 1) * 100 : null;
    h += `<div class="d-sec"><h4>💵 $100 Short Book — open short</h4>
      ${dRow("Tier", (pos.tier || "").toUpperCase() + " · score " + pos.score)}
      ${dRow("Entered", pos.entry_date + " at $" + Number(pos.entry_price).toFixed(2))}
      ${dRow("Now", cur != null ? "$" + Number(cur).toFixed(2) : "—")}
      ${dRow("Notional", money2(pos.notional))}
      ${dRow("Unrealized P&L", money2(pos.unrealized_pnl) + " (" + pct(pos.unrealized_pct) + ")", (pos.unrealized_pnl || 0) >= 0 ? "pos" : "neg")}
      ${dRow("Stop (+20%)", "$" + Number(pos.stop_price).toFixed(2) + (stopMove != null ? " · +" + stopMove.toFixed(1) + "% away" : ""))}
      ${dRow("Target (−25%)", "$" + Number(pos.target_price).toFixed(2) + (tgtMove != null ? " · " + tgtMove.toFixed(1) + "% away" : ""))}
      ${dRow("Days held", (pos.days_held ?? 0) + "d")}
      ${pos.reason ? `<p class="d-note">${esc(pos.reason)}</p>` : ""}
    </div>`;
  }
  if (closedPos) {
    h += `<div class="d-sec"><h4>💵 Short Book — closed</h4>
      ${dRow("Entry → exit", closedPos.entry_date + " → " + closedPos.exit_date)}
      ${dRow("Net P&L", money2(closedPos.net_pnl) + " (" + pct(closedPos.return_pct) + ")", closedPos.net_pnl >= 0 ? "pos" : "neg")}
      ${dRow("Exit reason", esc(closedPos.exit_reason))}
    </div>`;
  }
  if (seller) {
    const top = Object.entries(seller.sellers || {}).sort((a, b) => b[1] - a[1]).slice(0, 3);
    h += `<div class="d-sec"><h4>🔴 Heavy insider selling</h4>
      ${dRow("Sold (non-planned)", money(seller.sell_value), "neg")}
      ${top.map(([k, v]) => dRow(esc(k), money(v), "neg")).join("")}
    </div>`;
  }
  if (!h) h = `<p class="d-empty">No loaded evidence for ${esc(sym)} yet — it appears on the tape because another feed references it. Open a shade to see the full board.</p>`;

  $("drawerTicker").textContent = sym;
  $("drawerIssuer").textContent = issuer;
  $("drawerBody").innerHTML = h;
  $("drawerFoot").innerHTML = `<a class="btn rh" href="${rh(sym)}" target="_blank" rel="noopener">Open in Robinhood ↗</a>`;
  const bd = $("drawerBackdrop"), dr = $("detailDrawer");
  bd.hidden = false;
  requestAnimationFrame(() => { bd.classList.add("show"); dr.classList.add("open"); });
  dr.setAttribute("aria-hidden", "false");
}
function closeDrawer() {
  const bd = $("drawerBackdrop"), dr = $("detailDrawer");
  if (!dr.classList.contains("open")) return;
  bd.classList.remove("show");
  dr.classList.remove("open");
  dr.setAttribute("aria-hidden", "true");
  setTimeout(() => { bd.hidden = true; }, 320);
}
$("drawerClose").addEventListener("click", closeDrawer);
$("drawerBackdrop").addEventListener("click", closeDrawer);
/* click any ticker-bearing element (but let real links work) */
document.addEventListener("click", (e) => {
  if (e.target.closest("a")) return;
  if (e.target.closest("#detailDrawer")) return;
  const t = e.target.closest("[data-ticker]");
  if (t && t.dataset.ticker) openDrawer(t.dataset.ticker);
});

/* ================= sparklines / charts ================= */
function sparkline(hist, w = 300, h = 52) {
  if (!hist || hist.length < 2) return "";
  const closes = hist.map(p => p[p.length - 1]);
  const min = Math.min(...closes), max = Math.max(...closes), span = (max - min) || 1;
  const pts = closes.map((c, i) =>
    `${(i / (closes.length - 1) * w).toFixed(1)},${(h - 5 - (c - min) / span * (h - 10)).toFixed(1)}`).join(" ");
  const up = closes[closes.length - 1] >= closes[0];
  const color = up ? "#3dff7a" : "#ff6b5e";
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polygon points="0,${h} ${pts} ${w},${h}" fill="${color}" opacity="0.13" stroke="none"/>
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2"/></svg>`;
}

const signalHistories = [];
const chartsDrawn = new Set();
function drawChart(idx, hist) {
  const el = document.getElementById("chart-" + idx);
  if (!el || !hist || hist.length < 2) {
    if (el) el.style.display = "none";
    return;
  }
  if (!window.LightweightCharts) {
    /* CDN unreachable — fall back to the SVG sparkline instead of a hole */
    el.innerHTML = sparkline(hist);
    el.style.display = "block";
    chartsDrawn.add(idx);
    return;
  }
  try {
    const chart = LightweightCharts.createChart(el, {
      width: el.clientWidth || 300, height: 120,
      layout: {background: {color: "transparent"}, textColor: "#8fa38c", fontSize: 10},
      grid: {vertLines: {color: "rgba(61,255,122,.05)"},
             horzLines: {color: "rgba(61,255,122,.05)"}},
      timeScale: {visible: false}, rightPriceScale: {borderVisible: false},
      leftPriceScale: {visible: false}, crosshair: {mode: 0},
      handleScroll: false, handleScale: false});
    if (hist[0].length >= 5) {
      const ser = chart.addCandlestickSeries({
        upColor: "#3dff7a", downColor: "#ff6b5e",
        wickUpColor: "#3dff7a", wickDownColor: "#ff6b5e", borderVisible: false});
      ser.setData(hist.map(r => ({time: r[0], open: r[1], high: r[2], low: r[3], close: r[4]})));
    } else {
      const ser = chart.addAreaSeries({
        lineColor: "#3dff7a", lineWidth: 2,
        topColor: "rgba(61,255,122,.30)", bottomColor: "rgba(61,255,122,0)"});
      ser.setData(hist.map(r => ({time: r[0], value: r[r.length - 1]})));
    }
    chart.timeScale().fitContent();
    chartsDrawn.add(idx);
  } catch (e) { el.style.display = "none"; }
}
/* Charts live in a shade that starts rolled up (0-size) — draw lazily
   the first time the Signals shade is open and visible. */
function maybeDrawCharts() {
  if (activeShade !== "signals") return;
  signalHistories.forEach((hist, i) => { if (!chartsDrawn.has(i)) drawChart(i, hist); });
}

function indRow(ind) {
  if (!ind) return "";
  const chips = [];
  if (ind.rsi != null) {
    const cls = ind.rsi >= 70 ? "hot" : ind.rsi <= 30 ? "cool" : "";
    const note = ind.rsi >= 70 ? "overbought" : ind.rsi <= 30 ? "oversold" : "neutral";
    chips.push(`<span class="ind ${cls}">RSI ${ind.rsi} · ${note}</span>`);
  }
  if (ind.vs_sma50_pct != null) {
    chips.push(`<span class="ind ${ind.vs_sma50_pct >= 0 ? "up" : "down"}">${ind.vs_sma50_pct >= 0 ? "▲" : "▼"} ${Math.abs(ind.vs_sma50_pct)}% vs 50-day avg</span>`);
  }
  if (ind.mom_1m_pct != null) {
    chips.push(`<span class="ind ${ind.mom_1m_pct >= 0 ? "up" : "down"}">${ind.mom_1m_pct >= 0 ? "+" : ""}${ind.mom_1m_pct}% last month</span>`);
  }
  return chips.length ? `<div class="ind-row">${chips.join("")}</div>` : "";
}

/* ================= renders (unchanged logic, + data-ticker hooks) ================= */
function renderSignals(latest) {
  const stats = latest.stats || {};
  $("stFilings").textContent = (stats.filings ?? 0).toLocaleString();
  $("stBuys").textContent = (stats.with_buys ?? 0).toLocaleString();
  $("signalDate").textContent = latest.date ? `— filings from ${latest.date}` : "";
  $("updatedLine").textContent = latest.date
    ? `Latest scan: SEC Form 4 filings dated ${latest.date}. Refreshes every weekday after the close.`
    : "";

  const signals = latest.signals || [];
  $("signalsEmpty").hidden = signals.length > 0;

  const clusters = signals.filter(s => s.n_buyers >= 2 && !s.mixed_selling);
  if (clusters.length) {
    $("clustersSection").hidden = false;
    $("clusterStrip").innerHTML = clusters.slice(0, 6).map(s => `
      <div class="cluster" data-ticker="${s.symbol}" data-kind="signal">
        <div class="ticker">${s.symbol} <span class="pill gold">score ${s.score}</span></div>
        <p><b>${s.n_buyers} insiders</b> bought <b>${money(s.buy_value)}</b> — ${s.issuer}</p>
        <p>${s.buyers.map(b => `${b.owner} (${b.role})`).join(" · ")}</p>
      </div>`).join("");
  }

  signalHistories.length = 0;
  signals.slice(0, 15).forEach(s => signalHistories.push(s.history));
  $("signalCards").innerHTML = signals.slice(0, 15).map((s, i) => {
    const scorePct = Math.min(100, s.score / 12 * 100);
    const buyers = s.buyers.slice(0, 4).map(b => `
      <li><span><span class="who">${b.owner}</span> <span class="role">${b.role}</span></span>
      <span class="amt">${money(b.value)}</span></li>`).join("");
    return `<article class="card${s.mixed_selling ? " mixed" : ""}" data-ticker="${s.symbol}" data-kind="signal">
      <div class="card-top">
        <div><div class="ticker">${s.symbol}</div><div class="issuer">${s.issuer || ""}</div></div>
        <div><div class="score-ring" style="--p:${scorePct}"><span>${s.score}</span></div>
        <div class="score-cap">SIGNAL SCORE</div></div>
      </div>
      <div class="buy-total">Insiders bought <b>${money(s.buy_value)}</b> · ${s.n_buyers} buyer${s.n_buyers === 1 ? "" : "s"}</div>
      <ul class="buyers">${buyers}</ul>
      <div class="lwchart" id="chart-${i}"></div>
      ${indRow(s.indicators)}
      ${s.mixed_selling ? `<div class="mixed-flag">⚠️ Heavy insider selling at this company offsets the buying.</div>` : ""}
      <div class="card-actions">
        <a class="btn rh" href="${rh(s.symbol)}" target="_blank" rel="noopener">Open in Robinhood ↗</a>
      </div>
    </article>`;
  }).join("");
  maybeDrawCharts();

  const sellers = latest.heavy_sellers || [];
  $("sellEmpty").hidden = sellers.length > 0;
  $("sellBody").innerHTML = sellers.map(s => {
    const top = Object.entries(s.sellers || {}).sort((a, b) => b[1] - a[1]).slice(0, 3)
      .map(([k, v]) => `${k} (${money(v)})`).join(" · ");
    return `<tr data-ticker="${s.symbol}" data-kind="seller"><td class="tk">${s.symbol}</td><td>${s.issuer || ""}</td>
      <td class="neg">${money(s.sell_value)}</td><td>${top}</td>
      <td><a class="mini-link" href="${rh(s.symbol)}" target="_blank" rel="noopener">Robinhood ↗</a></td></tr>`;
  }).join("");
}

function kpiSpark(el, values) {
  if (!el) return;
  if (!values || values.length < 2) { el.innerHTML = ""; return; }
  const w = 86, h = 32;
  const min = Math.min(...values), max = Math.max(...values), span = (max - min) || 1;
  const pts = values.map((v, i) =>
    `${(i / (values.length - 1) * w).toFixed(1)},${(h - 4 - (v - min) / span * (h - 8)).toFixed(1)}`).join(" ");
  const up = values[values.length - 1] >= values[0];
  const c = up ? "#3dff7a" : "#ff6b5e";
  el.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polygon points="0,${h} ${pts} ${w},${h}" fill="${c}" opacity="0.14" stroke="none"/>
    <polyline points="${pts}" fill="none" stroke="${c}" stroke-width="2"/></svg>`;
}

function renderPredictions(preds) {
  $("stCalls").textContent = preds.length.toLocaleString();
  if (preds.length) {
    const rets = preds.map(p => p.return_pct || 0);
    const avg = rets.reduce((a, b) => a + b, 0) / rets.length;
    const wins = rets.filter(r => r > 0).length;
    const avgEl = $("stAvg");
    avgEl.textContent = pct(avg);
    avgEl.style.color = avg >= 0 ? "var(--green)" : "var(--red)";
    $("stWin").textContent = Math.round(wins / rets.length * 100) + "%";
    const byDate = [...preds].sort((a, b) => String(a.signal_date).localeCompare(String(b.signal_date)));
    const runAvg = [], runWin = [], runCount = [];
    let sum = 0, w = 0;
    byDate.forEach((p, i) => {
      const r = p.return_pct || 0;
      sum += r; if (r > 0) w++;
      runAvg.push(sum / (i + 1));
      runWin.push(w / (i + 1) * 100);
      runCount.push(i + 1);
    });
    kpiSpark($("sparkCalls"), runCount);
    kpiSpark($("sparkAvg"), runAvg);
    kpiSpark($("sparkWin"), runWin);
    const best = Math.max(...rets), worst = Math.min(...rets);
    const losses = rets.filter(r => r < 0).length;
    $("footAvg").innerHTML =
      `<span class="kpi-chip ${best >= 0 ? "" : "neg"}">${best >= 0 ? "▲" : "▼"} best ${pct(best)}</span>` +
      `<span class="kpi-note">worst ${pct(worst)}</span>`;
    $("footWin").innerHTML =
      `<span class="kpi-chip">▲ ${wins} winning</span>` +
      `<span class="kpi-note">${losses} down</span>`;
  } else {
    $("stAvg").textContent = "—";
    $("stWin").textContent = "—";
  }
  $("predEmpty").hidden = preds.length > 0;
  $("predBody").innerHTML = preds.map(p => {
    const r = p.return_pct || 0;
    return `<tr data-ticker="${p.symbol}" data-kind="call">
      <td>${p.signal_date}</td>
      <td class="tk">${p.symbol}</td>
      <td><span class="pill gold">${p.score}</span></td>
      <td>${money(p.buy_value)} · ${p.n_buyers} insider${p.n_buyers === 1 ? "" : "s"}</td>
      <td>${p.entry_price ? "$" + p.entry_price.toFixed(2) : "—"}</td>
      <td>${p.current_price ? "$" + p.current_price.toFixed(2) : "—"}</td>
      <td class="${r >= 0 ? "pos" : "neg"}">${pct(r)}</td>
      <td>${p.days_held ?? 0}d</td>
      <td class="${(p.grade_30d_pct ?? 0) >= 0 ? "pos" : "neg"}">${pct(p.grade_30d_pct)}</td>
      <td class="${(p.grade_90d_pct ?? 0) >= 0 ? "pos" : "neg"}">${pct(p.grade_90d_pct)}</td>
      <td><a class="mini-link" href="${rh(p.symbol)}" target="_blank" rel="noopener">Robinhood ↗</a></td>
    </tr>`;
  }).join("");
}

function renderCongress(c) {
  const st = c.stats || {};
  $("congressStats").textContent =
    `Read ${st.ptrs_processed ?? 0} recent House PTR filings → ${st.trades_found ?? 0} stock trades found` +
    (st.ptrs_unreadable ? ` · ${st.ptrs_unreadable} paper filings couldn't be read` : "") +
    (c.updated ? ` · updated ${c.updated}` : "");
  const aggRow = (t, kind) => `<tr data-ticker="${t.symbol}" data-kind="congress">
      <td class="tk">${t.symbol}</td><td>${t.count}</td><td>${t.members}</td>
      <td>${money(t.est_value)}</td>
      <td><a class="mini-link" href="${rh(t.symbol)}" target="_blank" rel="noopener">Robinhood ↗</a></td></tr>`;
  $("congBuyBody").innerHTML = (c.top_buys || []).map(t => aggRow(t, "buy")).join("");
  $("congSellBody").innerHTML = (c.top_sells || []).map(t => aggRow(t, "sell")).join("");
  $("congRecentBody").innerHTML = (c.recent_trades || []).slice(0, 25).map(t => {
    const cls = t.type === "Purchase" ? "buy" : t.type === "Sale" ? "sell" : "exch";
    return `<tr data-ticker="${t.symbol}" data-kind="congress">
      <td>${t.date}</td>
      <td>${t.member} <span style="color:var(--dim)">${t.state_dst || ""}</span></td>
      <td class="tk">${t.symbol}</td>
      <td><span class="badge ${cls}">${t.type}</span></td>
      <td>${t.amount_range}</td>
      <td><a class="mini-link" href="${rh(t.symbol)}" target="_blank" rel="noopener">↗</a></td></tr>`;
  }).join("");
}

function renderFunds(f) {
  const st = f.stats || {};
  $("fundsStats").textContent =
    `Tracking ${st.funds_tracked ?? 0} funds · each card shows its latest 13F quarter vs the one before` +
    (f.updated ? ` · updated ${f.updated}` : "");
  $("consensusBody").innerHTML = (f.consensus || []).map(c => `<tr${c.symbol ? ` data-ticker="${c.symbol}" data-kind="fund"` : ""}>
      <td class="tk">${c.symbol || "—"}</td><td>${c.name || ""}</td>
      <td>${c.funds}</td><td>${money(c.total_value)}</td>
      <td>${c.symbol ? `<a class="mini-link" href="${rh(c.symbol)}" target="_blank" rel="noopener">Robinhood ↗</a>` : ""}</td></tr>`).join("");
  const symList = (arr, extra) => arr.slice(0, 6).map(x =>
    `<span class="chip"${x.symbol ? ` data-ticker="${x.symbol}" data-kind="fund"` : ""}>${x.symbol || x.name}${x.option ? ` (${x.option}s)` : ""}${extra ? " " + extra(x) : ""}</span>`).join("");
  $("fundGrid").innerHTML = (f.funds || []).map(fd => {
    const ch = fd.changes || {};
    return `<div class="fund-card">
      <h3>${fd.name}</h3>
      <div class="fmeta">${fd.label ? fd.label + " · " : ""}quarter ended ${fd.period} · filed ${fd.filed}<br>
      Portfolio ${money(fd.total_value)} across ${fd.positions} positions</div>
      <div class="chips">${(fd.top_holdings || []).slice(0, 5).map(h =>
        `<span class="chip"${h.symbol ? ` data-ticker="${h.symbol}" data-kind="fund"` : ""}><b>${h.symbol || h.name}</b> ${h.pct}%${h.option ? ` · ${h.option}s` : ""}</span>`).join("")}</div>
      ${ch.new && ch.new.length ? `<p class="chg"><span class="up"><b>NEW</b></span> ${symList(ch.new)}</p>` : ""}
      ${ch.exited && ch.exited.length ? `<p class="chg"><span class="down"><b>EXITED</b></span> ${symList(ch.exited)}</p>` : ""}
      ${ch.increased && ch.increased.length ? `<p class="chg"><b>ADDED</b> ${symList(ch.increased, x => `<span class="up">+${x.pct_change}%</span>`)}</p>` : ""}
      ${ch.decreased && ch.decreased.length ? `<p class="chg"><b>TRIMMED</b> ${symList(ch.decreased, x => `<span class="down">${x.pct_change}%</span>`)}</p>` : ""}
    </div>`;
  }).join("");
}

function renderShorts(s, spreds) {
  const legs = s.legs || {};
  const legTxt = [];
  const ins = legs.insider_sells || {};
  legTxt.push(`Insider sells: <b>${ins.status || "—"}</b>${ins.clusters_found != null ? ` · ${ins.clusters_found} clusters in the 30-day window` : ""}`);
  const fi = legs.finra_short_interest || {};
  legTxt.push(`FINRA short interest: <b>${fi.status || "—"}</b>${fi.settlement_latest ? ` · settlement ${fi.settlement_latest}` : ""}`);
  const bo = legs.borrow_ibkr || {};
  legTxt.push(`Borrow (IBKR): <b>${bo.status || "—"}</b>${bo.snapshot_date ? ` · ${bo.snapshot_date}` : ""}`);
  const th = legs.threshold_list || {};
  legTxt.push(`Threshold list: <b>${th.status || "—"}</b>${th.date ? ` · ${th.date}` : ""}`);
  $("shortLegs").innerHTML =
    `Data legs — ${legTxt.join(" · ")}. ` +
    `<span title="${(fi.note || "").replace(/"/g, "&quot;")}">${fi.status === "live" || fi.status === "stale" ? "" : "FINRA files are unreachable from some networks; the leg retries every run."}</span>` +
    (bo.status !== "live" ? ` Borrow fees populate when the IBKR file is reachable (it runs from the daily pipeline).` : "");

  const cands = s.candidates || [];
  $("shortEmpty").hidden = cands.length > 0;
  $("shortBody").innerHTML = cands.map(c => {
    const insCell = c.insider && c.insider.cluster
      ? `${c.insider.cluster.n_insiders} insider${c.insider.cluster.n_insiders === 1 ? "" : "s"} · ${money(c.insider.cluster.total_value)} · score ${c.insider.score}`
      : "—";
    const siCell = c.si
      ? `${(c.si.shares_short / 1e6).toFixed(1)}M sh · DTC ${c.si.days_to_cover ?? "—"}${c.si.pct_of_float != null ? ` · ${c.si.pct_of_float}% of float` : ""}${c.si.pct_change != null ? ` · ${c.si.pct_change > 0 ? "▲" : "▼"} ${Math.abs(c.si.pct_change)}%` : ""}`
      : "no data yet";
    const boCell = c.borrow
      ? `${c.borrow.fee_pct}%/yr${c.borrow.available != null ? ` · ${(c.borrow.available / 1e3).toFixed(0)}K avail` : ""}${c.borrow.rising ? " · ⚠️ fee doubling" : ""}`
      : "pending";
    const trCell = c.tech
      ? (c.tech.breakdown ? "💥 breakdown" : c.tech.extended_context ? "extended (watching)" : "—") +
        (c.tech.vs_sma50_pct != null ? ` · ${c.tech.vs_sma50_pct}% vs 50d` : "")
      : "—";
    const gates = (c.gates || []).map(g =>
      `<span class="gate ${g.status}" title="${(g.gate + ": " + g.detail).replace(/"/g, "&quot;")}">${g.status === "block" ? "⛔" : g.status === "pass" ? "✅" : "❔"} ${g.gate}</span>`).join("");
    return `<tr data-ticker="${c.symbol}" data-kind="short">
      <td class="tk">${c.symbol}</td>
      <td><span class="badge ${c.status}">${c.status.toUpperCase()}</span></td>
      <td><span class="pill gold">${c.score}</span></td>
      <td>${insCell}</td><td>${siCell}</td><td>${boCell}</td><td>${trCell}</td>
      <td class="gates">${gates}</td>
      <td><a class="mini-link" href="${rh(c.symbol)}" target="_blank" rel="noopener">Robinhood ↗</a></td></tr>`;
  }).join("");

  const watch = s.squeeze_watchlist || [];
  $("squeezeEmpty").hidden = watch.length > 0;
  $("squeezeBody").innerHTML = watch.map(w => {
    const siCell = w.si
      ? `${(w.si.shares_short / 1e6).toFixed(1)}M sh · DTC ${w.si.days_to_cover ?? "—"}${w.si.pct_of_float != null ? ` · ${w.si.pct_of_float}% float` : ""}`
      : "—";
    return `<tr data-ticker="${w.symbol}" data-kind="short">
      <td class="tk">${w.symbol}</td>
      <td><span class="pill risk">${w.squeeze_score}</span></td>
      <td style="white-space:normal;min-width:220px">${(w.detail || []).join("; ")}</td>
      <td>${siCell}</td>
      <td class="${(w.tech && w.tech.mom_1m_pct || 0) >= 0 ? "pos" : "neg"}">${w.tech ? pct(w.tech.mom_1m_pct) : "—"}</td>
      <td><a class="mini-link" href="${rh(w.symbol)}" target="_blank" rel="noopener">↗</a></td></tr>`;
  }).join("");

  const preds = Array.isArray(spreds) ? spreds : [];
  $("shortPredEmpty").hidden = preds.length > 0;
  $("shortPredBody").innerHTML = preds.map(p => {
    const net = p.net_return_pct || 0;
    return `<tr data-ticker="${p.symbol}" data-kind="short">
      <td>${p.signal_date}</td>
      <td class="tk">${p.symbol}</td>
      <td><span class="pill gold">${p.score}</span></td>
      <td>${p.entry_price ? "$" + p.entry_price.toFixed(2) : "—"}</td>
      <td>${p.current_price ? "$" + p.current_price.toFixed(2) : "—"}</td>
      <td class="${(p.gross_return_pct || 0) >= 0 ? "pos" : "neg"}">${pct(p.gross_return_pct)}</td>
      <td class="neg">−${(p.fee_drag_pct || 0).toFixed(2)}%</td>
      <td class="${net >= 0 ? "pos" : "neg"}">${pct(net)}</td>
      <td class="neg">${pct(p.max_adverse_pct)}</td>
      <td>${p.days_held ?? 0}d</td>
      <td class="${(p.grade_30d_pct ?? 0) >= 0 ? "pos" : "neg"}">${pct(p.grade_30d_pct)}</td>
      <td class="${(p.grade_90d_pct ?? 0) >= 0 ? "pos" : "neg"}">${pct(p.grade_90d_pct)}</td>
      <td><span class="badge ${p.status === "open" ? "watch" : "blocked"}">${(p.status || "open").replace("_", " ")}</span></td>
      <td><a class="mini-link" href="${rh(p.symbol)}" target="_blank" rel="noopener">Robinhood ↗</a></td>
    </tr>`;
  }).join("");

  $("putEvidence").innerHTML = (s.fund_puts_evidence || []).map(e => `
    <p class="chg"><b>${e.symbol}</b>${e.points ? ` <span class="pill gold">+1 confirmation</span>` : ""} — ` +
    e.funds.map(f => `${f.fund}: ${f.kind}${f.notional_value ? ` (${money(f.notional_value)} <b>notional</b> underlying value — not premium paid)` : ""}`).join("; ") +
    `</p>`).join("") || `<p class="chg">No fund put positions in the latest 13F data.</p>`;
  $("congSellEvidence").innerHTML = (s.congress_sells_evidence || []).slice(0, 10).map(t =>
    `<span class="chip" data-ticker="${t.symbol}" data-kind="congress"><b>${t.symbol}</b> ${t.count} sales · ${t.members} members</span>`).join("");
  $("shortCaveats").innerHTML = (s.caveats || []).map(cv =>
    `<p class="congress-note">${cv}</p>`).join("");
}

function renderShortBook(b) {
  if (!b) { $("shortBookPanel").hidden = true; return; }
  $("bkEquity").textContent = money2(b.equity);
  const retEl = $("bkReturn");
  retEl.textContent = pct(b.total_return_pct);
  retEl.style.color = (b.total_return_pct || 0) >= 0 ? "var(--green)" : "var(--red)";
  $("bkCash").textContent = money2(b.cash);
  const poss = b.positions || [];
  $("bkOpen").textContent = poss.length + " / 3";
  $("bkWin").textContent = b.win_rate_pct == null ? "—" : b.win_rate_pct + "%";

  const hist = b.picks_history || [];
  const lastDay = hist.length ? hist[hist.length - 1] : null;
  const picks = lastDay ? (lastDay.picks || []) : [];
  $("bookPicksDate").textContent = lastDay ? `— ${lastDay.date}` : "";
  $("bookPicksEmpty").hidden = picks.length > 0;
  if (!picks.length) {
    $("bookPicksEmpty").textContent = lastDay && lastDay.note
      ? lastDay.note : "No picks recorded yet.";
  }
  const noteEl = $("bookPicksNote");
  if (lastDay && lastDay.note && picks.length) {
    noteEl.hidden = false;
    noteEl.textContent = lastDay.note;
  } else {
    noteEl.hidden = true;
  }
  $("bookPicks").innerHTML = picks.map(p => `
    <article class="card book-pick" data-ticker="${p.symbol}" data-kind="book">
      <div class="card-top">
        <div><div class="ticker">${p.symbol}</div><div class="issuer">${p.issuer || ""}</div></div>
        <span class="badge ${p.tier === "confirmed" ? "confirmed" : "aggressive"}">${(p.tier || "").toUpperCase()}</span>
      </div>
      <div class="buy-total">Shorted <b>${money2(p.notional)}</b> at $${Number(p.entry_price).toFixed(2)} · score ${p.score}</div>
      <p class="pick-reason">${p.reason || ""}</p>
      <div class="card-actions">
        <a class="btn rh" href="${rh(p.symbol)}" target="_blank" rel="noopener">Open in Robinhood ↗</a>
      </div>
    </article>`).join("");

  $("bookPosEmpty").hidden = poss.length > 0;
  $("bookPosBody").innerHTML = poss.map(p => {
    const up = p.unrealized_pnl || 0;
    const cur = p.current_price;
    const stopMove = cur ? (p.stop_price / cur - 1) * 100 : null;
    const tgtMove = cur ? (p.target_price / cur - 1) * 100 : null;
    return `<tr data-ticker="${p.symbol}" data-kind="book">
      <td class="tk">${p.symbol}</td>
      <td>${p.entry_date}</td>
      <td>$${Number(p.entry_price).toFixed(2)}</td>
      <td>${cur ? "$" + Number(cur).toFixed(2) : "—"}</td>
      <td>${money2(p.notional)}</td>
      <td class="${up >= 0 ? "pos" : "neg"}">${money2(up)} (${pct(p.unrealized_pct)})</td>
      <td>${p.days_held ?? 0}d</td>
      <td><span class="dist stop">${stopMove == null ? "stop —" : "+" + stopMove.toFixed(1) + "% to stop"}</span>
          <span class="dist tgt">${tgtMove == null ? "target —" : tgtMove.toFixed(1) + "% to target"}</span></td>
      <td><a class="mini-link" href="${rh(p.symbol)}" target="_blank" rel="noopener">Robinhood ↗</a></td>
    </tr>`;
  }).join("");

  const closed = [...(b.closed || [])].reverse();
  $("bookClosedEmpty").hidden = closed.length > 0;
  $("bookClosedBody").innerHTML = closed.map(r => `<tr data-ticker="${r.symbol}" data-kind="book">
      <td class="tk">${r.symbol}</td>
      <td>${r.entry_date} → ${r.exit_date}</td>
      <td>$${Number(r.entry_price).toFixed(2)} → $${Number(r.exit_price).toFixed(2)}</td>
      <td class="${r.net_pnl >= 0 ? "pos" : "neg"}">${money2(r.net_pnl)}</td>
      <td class="${r.return_pct >= 0 ? "pos" : "neg"}">${pct(r.return_pct)}</td>
      <td><span class="badge exit">${r.exit_reason}</span></td>
    </tr>`).join("");
}

function renderSmart(latest, congress, funds) {
  const bySym = {};
  const entry = (sym) => bySym[sym] = bySym[sym] || {symbol: sym, name: "", ins: null, cong: null, fund: null};
  (latest.signals || []).forEach(s => {
    const e = entry(s.symbol);
    e.name = e.name || s.issuer || "";
    e.ins = {score: s.score, text: `score ${s.score} · ${money(s.buy_value)} bought by ${s.n_buyers} insider${s.n_buyers === 1 ? "" : "s"}`};
  });
  ((congress && congress.top_buys) || []).forEach(t => {
    const e = entry(t.symbol);
    e.cong = {members: t.members, text: `${t.members} House members bought (${t.count} buys)`};
  });
  if (funds) {
    const newBy = {};
    (funds.funds || []).forEach(fd => (fd.changes.new || []).forEach(n => {
      if (n.symbol && n.option !== "put") (newBy[n.symbol] = newBy[n.symbol] || []).push(fd.name);
    }));
    Object.entries(newBy).forEach(([sym, names]) => {
      const e = entry(sym);
      e.fund = {n: names.length, text: `new position at ${names.join(", ")}`};
    });
    (funds.consensus || []).filter(c => c.symbol && c.funds >= 4).forEach(c => {
      const e = entry(c.symbol);
      if (!e.fund) e.fund = {n: c.funds, text: `held by ${c.funds} of the tracked funds`};
      e.name = e.name || c.name || "";
    });
  }
  const overlaps = Object.values(bySym)
    .map(e => ({...e, kinds: [e.ins, e.cong, e.fund].filter(Boolean).length}))
    .filter(e => e.kinds >= 2)
    .sort((a, b) => b.kinds - a.kinds ||
      ((b.ins || {}).score || 0) - ((a.ins || {}).score || 0) ||
      ((b.cong || {}).members || 0) - ((a.cong || {}).members || 0));
  if (!overlaps.length) return;
  $("smartSection").hidden = false;
  $("smartGrid").innerHTML = overlaps.slice(0, 9).map(e => `
    <div class="smart-card" data-ticker="${e.symbol}" data-kind="smart">
      <div class="ticker">${e.symbol} <span class="pill gold">${e.kinds} of 3 circles</span></div>
      <div class="issuer">${e.name || ""}</div>
      ${e.ins ? `<p class="src">🟢 <b>Insiders</b> — ${e.ins.text}</p>` : ""}
      ${e.cong ? `<p class="src">🏛️ <b>Congress</b> — ${e.cong.text}</p>` : ""}
      ${e.fund ? `<p class="src">🐋 <b>Big funds</b> — ${e.fund.text}</p>` : ""}
      <div class="card-actions"><a class="btn rh" href="${rh(e.symbol)}" target="_blank" rel="noopener">Open in Robinhood ↗</a></div>
    </div>`).join("");
}

/* ================= load ================= */
async function load() {
  let latest = null, congress = null, funds = null;
  try {
    const [l, preds] = await Promise.all([
      fetch("data/latest.json?v=" + Date.now()).then(r => r.json()),
      fetch("data/predictions.json?v=" + Date.now()).then(r => r.json()).catch(() => []),
    ]);
    latest = l;
    STATE.latest = l;
    STATE.preds = Array.isArray(preds) ? preds : [];
    renderSignals(latest);
    renderPredictions(STATE.preds);
  } catch (e) {
    $("updatedLine").textContent = "Could not load the latest scan data.";
  }
  try {
    congress = await fetch("data/congress.json?v=" + Date.now()).then(r => r.json());
    STATE.congress = congress;
    renderCongress(congress);
  } catch (e) { $("congressStats").textContent = "Congress data is on its way."; }
  try {
    funds = await fetch("data/funds.json?v=" + Date.now()).then(r => r.json());
    STATE.funds = funds;
    renderFunds(funds);
  } catch (e) { $("fundsStats").textContent = "Funds data is on its way."; }
  let shorts = null;
  try {
    const [sh, sp] = await Promise.all([
      fetch("data/shorts.json?v=" + Date.now()).then(r => r.json()),
      fetch("data/short_predictions.json?v=" + Date.now()).then(r => r.json()).catch(() => []),
    ]);
    shorts = sh;
    STATE.shorts = sh;
    STATE.shortPreds = Array.isArray(sp) ? sp : [];
    renderShorts(shorts, STATE.shortPreds);
  } catch (e) { $("shortLegs").textContent = "Short-side data is on its way."; }
  try {
    const bk = await fetch("data/short_book.json?v=" + Date.now())
      .then(r => { if (!r.ok) throw new Error("no book"); return r.json(); });
    STATE.book = bk;
    renderShortBook(bk);
  } catch (e) { const p = $("shortBookPanel"); if (p) p.hidden = true; }
  if (latest) renderSmart(latest, congress, funds);
  buildTicker();
  applySearch();
  maybeDrawCharts();
  const feeds = [];
  if (latest) feeds.push(`Insiders ${latest.date}`);
  if (congress) feeds.push(`Congress ${congress.updated}`);
  if (funds) feeds.push(`Funds ${funds.updated}`);
  if (shorts) feeds.push(`Shorts ${shorts.updated}`);
  $("autopilotLine").innerHTML =
    `🤖 <b>Autopilot ON</b> — every scan refreshes itself each weekday after the market close` +
    (feeds.length ? ` · ${feeds.join(" · ")}` : "");
}

/* init shade from saved state (before data arrives so layout is stable) */
(function initShade() {
  let saved = "overview";
  try { saved = localStorage.getItem("pw-shade") || "overview"; } catch (e) {}
  activateShade(saved, false);
  setTimeout(moveIndicator, 60);
  setTimeout(moveIndicator, 400);
})();
load();
