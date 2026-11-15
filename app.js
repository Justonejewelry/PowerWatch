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

  $("signalCards").innerHTML = signals.slice(0, 15).map(s => {
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
      ${sparkline(s.history)}
      ${s.mixed_selling ? `<div class="mixed-flag">⚠️ Heavy insider selling at this company offsets the buying.</div>` : ""}
      <div class="card-actions">
        <a class="btn rh" href="${rh(s.symbol)}" target="_blank" rel="noopener">Open in Robinhood ↗</a>
      </div>
    </article>`;
  }).join("");

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

async function load() {
  try {
    const [latest, preds] = await Promise.all([
      fetch("data/latest.json?v=" + Date.now()).then(r => r.json()),
      fetch("data/predictions.json?v=" + Date.now()).then(r => r.json()).catch(() => []),
    ]);
    renderSignals(latest);
    renderPredictions(Array.isArray(preds) ? preds : []);
  } catch (e) {
    $("updatedLine").textContent = "Could not load the latest scan data.";
  }
  fetch("data/congress.json?v=" + Date.now()).then(r => r.json())
    .then(renderCongress)
    .catch(() => { $("congressStats").textContent = "Congress data is on its way."; });
}
load();
