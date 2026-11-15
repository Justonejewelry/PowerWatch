"use strict";
const $ = (id) => document.getElementById(id);
const money = (v) => v >= 1e6 ? "$" + (v / 1e6).toFixed(2) + "M"
  : v >= 1e3 ? "$" + Math.round(v / 1e3) + "K" : "$" + Math.round(v);
const pct = (v) => (v == null ? "—" : (v > 0 ? "+" : "") + v.toFixed(2) + "%");
const rh = (sym) => `https://robinhood.com/stocks/${encodeURIComponent(sym)}`;

function sparkline(hist, w = 300, h = 52) {
  if (!hist || hist.length < 2) return "";
  const closes = hist.map(p => p[1]);
  const min = Math.min(...closes), max = Math.max(...closes), span = (max - min) || 1;
  const pts = closes.map((c, i) =>
    `${(i / (closes.length - 1) * w).toFixed(1)},${(h - 5 - (c - min) / span * (h - 10)).toFixed(1)}`).join(" ");
  const up = closes[closes.length - 1] >= closes[0];
  const color = up ? "#00c805" : "#ff5000";
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2"/></svg>`;
}

function drawChart(idx, hist) {
  const el = document.getElementById("chart-" + idx);
  if (!el || !window.LightweightCharts || !hist || hist.length < 2) {
    if (el) el.style.display = "none";
    return;
  }
  try {
    const chart = LightweightCharts.createChart(el, {
      width: el.clientWidth || 300, height: 120,
      layout: {background: {color: "transparent"}, textColor: "#9aa4b2", fontSize: 10},
      grid: {vertLines: {color: "rgba(255,255,255,.04)"},
             horzLines: {color: "rgba(255,255,255,.04)"}},
      timeScale: {visible: false}, rightPriceScale: {borderVisible: false},
      leftPriceScale: {visible: false}, crosshair: {mode: 0},
      handleScroll: false, handleScale: false});
    if (hist[0].length >= 5) {
      const ser = chart.addCandlestickSeries({
        upColor: "#00c805", downColor: "#ff5000",
        wickUpColor: "#00c805", wickDownColor: "#ff5000", borderVisible: false});
      ser.setData(hist.map(r => ({time: r[0], open: r[1], high: r[2], low: r[3], close: r[4]})));
    } else {
      const ser = chart.addAreaSeries({
        lineColor: "#00c805", lineWidth: 2,
        topColor: "rgba(0,200,5,.25)", bottomColor: "rgba(0,200,5,0)"});
      ser.setData(hist.map(r => ({time: r[0], value: r[r.length - 1]})));
    }
    chart.timeScale().fitContent();
  } catch (e) { el.style.display = "none"; }
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

  // cluster strip
  const clusters = signals.filter(s => s.n_buyers >= 2 && !s.mixed_selling);
  if (clusters.length) {
    $("clustersSection").hidden = false;
    $("clusterStrip").innerHTML = clusters.slice(0, 6).map(s => `
      <div class="cluster">
        <div class="ticker">${s.symbol} <span class="pill gold">score ${s.score}</span></div>
        <p><b>${s.n_buyers} insiders</b> bought <b>${money(s.buy_value)}</b> — ${s.issuer}</p>
        <p>${s.buyers.map(b => `${b.owner} (${b.role})`).join(" · ")}</p>
      </div>`).join("");
  }

  $("signalCards").innerHTML = signals.slice(0, 15).map((s, i) => {
    const scorePct = Math.min(100, s.score / 12 * 100);
    const buyers = s.buyers.slice(0, 4).map(b => `
      <li><span><span class="who">${b.owner}</span> <span class="role">${b.role}</span></span>
      <span class="amt">${money(b.value)}</span></li>`).join("");
    return `<article class="card${s.mixed_selling ? " mixed" : ""}">
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
  signals.slice(0, 15).forEach((s, i) => drawChart(i, s.history));

  // heavy sellers
  const sellers = latest.heavy_sellers || [];
  $("sellEmpty").hidden = sellers.length > 0;
  $("sellBody").innerHTML = sellers.map(s => {
    const top = Object.entries(s.sellers || {}).sort((a, b) => b[1] - a[1]).slice(0, 3)
      .map(([k, v]) => `${k} (${money(v)})`).join(" · ");
    return `<tr><td class="tk">${s.symbol}</td><td>${s.issuer || ""}</td>
      <td class="neg">${money(s.sell_value)}</td><td>${top}</td>
      <td><a class="mini-link" href="${rh(s.symbol)}" target="_blank" rel="noopener">Robinhood ↗</a></td></tr>`;
  }).join("");
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
  } else {
    $("stAvg").textContent = "—";
    $("stWin").textContent = "—";
  }
  $("predEmpty").hidden = preds.length > 0;
  $("predBody").innerHTML = preds.map(p => {
    const r = p.return_pct || 0;
    return `<tr>
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
  const aggRow = (t, kind) => `<tr>
      <td class="tk">${t.symbol}</td><td>${t.count}</td><td>${t.members}</td>
      <td>${money(t.est_value)}</td>
      <td><a class="mini-link" href="${rh(t.symbol)}" target="_blank" rel="noopener">Robinhood ↗</a></td></tr>`;
  $("congBuyBody").innerHTML = (c.top_buys || []).map(t => aggRow(t, "buy")).join("");
  $("congSellBody").innerHTML = (c.top_sells || []).map(t => aggRow(t, "sell")).join("");
  $("congRecentBody").innerHTML = (c.recent_trades || []).slice(0, 25).map(t => {
    const cls = t.type === "Purchase" ? "buy" : t.type === "Sale" ? "sell" : "exch";
    return `<tr>
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
  $("consensusBody").innerHTML = (f.consensus || []).map(c => `<tr>
      <td class="tk">${c.symbol || "—"}</td><td>${c.name || ""}</td>
      <td>${c.funds}</td><td>${money(c.total_value)}</td>
      <td>${c.symbol ? `<a class="mini-link" href="${rh(c.symbol)}" target="_blank" rel="noopener">Robinhood ↗</a>` : ""}</td></tr>`).join("");
  const symList = (arr, extra) => arr.slice(0, 6).map(x =>
    `<span class="chip">${x.symbol || x.name}${x.option ? ` (${x.option}s)` : ""}${extra ? " " + extra(x) : ""}</span>`).join("");
  $("fundGrid").innerHTML = (f.funds || []).map(fd => {
    const ch = fd.changes || {};
    return `<div class="fund-card">
      <h3>${fd.name}</h3>
      <div class="fmeta">${fd.label ? fd.label + " · " : ""}quarter ended ${fd.period} · filed ${fd.filed}<br>
      Portfolio ${money(fd.total_value)} across ${fd.positions} positions</div>
      <div class="chips">${(fd.top_holdings || []).slice(0, 5).map(h =>
        `<span class="chip"><b>${h.symbol || h.name}</b> ${h.pct}%${h.option ? ` · ${h.option}s` : ""}</span>`).join("")}</div>
      ${ch.new && ch.new.length ? `<p class="chg"><span class="up"><b>NEW</b></span> ${symList(ch.new)}</p>` : ""}
      ${ch.exited && ch.exited.length ? `<p class="chg"><span class="down"><b>EXITED</b></span> ${symList(ch.exited)}</p>` : ""}
      ${ch.increased && ch.increased.length ? `<p class="chg"><b>ADDED</b> ${symList(ch.increased, x => `<span class="up">+${x.pct_change}%</span>`)}</p>` : ""}
      ${ch.decreased && ch.decreased.length ? `<p class="chg"><b>TRIMMED</b> ${symList(ch.decreased, x => `<span class="down">${x.pct_change}%</span>`)}</p>` : ""}
    </div>`;
  }).join("");
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
      // Put positions are bearish bets — they don't count as bullish evidence.
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
    <div class="smart-card">
      <div class="ticker">${e.symbol} <span class="pill gold">${e.kinds} of 3 circles</span></div>
      <div class="issuer">${e.name || ""}</div>
      ${e.ins ? `<p class="src">🟢 <b>Insiders</b> — ${e.ins.text}</p>` : ""}
      ${e.cong ? `<p class="src">🏛️ <b>Congress</b> — ${e.cong.text}</p>` : ""}
      ${e.fund ? `<p class="src">🐋 <b>Big funds</b> — ${e.fund.text}</p>` : ""}
      <div class="card-actions"><a class="btn rh" href="${rh(e.symbol)}" target="_blank" rel="noopener">Open in Robinhood ↗</a></div>
    </div>`).join("");
}

async function load() {
  let latest = null, congress = null, funds = null;
  try {
    const [l, preds] = await Promise.all([
      fetch("data/latest.json?v=" + Date.now()).then(r => r.json()),
      fetch("data/predictions.json?v=" + Date.now()).then(r => r.json()).catch(() => []),
    ]);
    latest = l;
    renderSignals(latest);
    renderPredictions(Array.isArray(preds) ? preds : []);
  } catch (e) {
    $("updatedLine").textContent = "Could not load the latest scan data.";
  }
  try {
    congress = await fetch("data/congress.json?v=" + Date.now()).then(r => r.json());
    renderCongress(congress);
  } catch (e) { $("congressStats").textContent = "Congress data is on its way."; }
  try {
    funds = await fetch("data/funds.json?v=" + Date.now()).then(r => r.json());
    renderFunds(funds);
  } catch (e) { $("fundsStats").textContent = "Funds data is on its way."; }
  if (latest) renderSmart(latest, congress, funds);
  const feeds = [];
  if (latest) feeds.push(`Insiders ${latest.date}`);
  if (congress) feeds.push(`Congress ${congress.updated}`);
  if (funds) feeds.push(`Funds ${funds.updated}`);
  $("autopilotLine").innerHTML =
    `🤖 <b>Autopilot ON</b> — every scan refreshes itself each weekday after the market close` +
    (feeds.length ? ` · ${feeds.join(" · ")}` : "");
}
load();
