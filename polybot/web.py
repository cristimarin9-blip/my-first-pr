from __future__ import annotations

import csv
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from polybot.broker import Broker
from polybot.config import Config
from polybot.risk import RiskGuardedBroker
from polybot.tracker import EquityTracker

log = logging.getLogger(__name__)


def build_summary(config: Config, broker: Broker) -> dict:
    inner = getattr(broker, "inner", broker)
    positions = [
        {
            "token_id": p.token_id,
            "condition_id": p.condition_id,
            "outcome": p.outcome,
            "size": round(p.size, 4),
            "avg_price": round(p.avg_price, 4),
            "cost_usd": round(p.cost_basis_usd, 2),
        }
        for p in broker.get_positions().values()
    ]
    summary = {
        "mode": "paper" if config.is_paper else "live",
        "cash": round(broker.get_cash_balance(), 2),
        "exposure": round(broker.get_exposure_usd(), 2),
        "realized_pnl": round(getattr(inner, "realized_pnl_usd", 0.0), 2),
        "starting_balance": config.paper.starting_balance_usd if config.is_paper else None,
        "positions": positions,
        "risk": None,
    }
    if isinstance(broker, RiskGuardedBroker):
        s = broker.today_summary()
        summary["risk"] = {
            "date": s["date"],
            "buys_today": s["buys_today"],
            "max_buys_per_day": broker.config.max_buys_per_day,
            "buy_notional_today": round(s["buy_notional_today"], 2),
            "max_buy_notional_per_day_usd": broker.config.max_buy_notional_per_day_usd,
            "realized_pnl_today": round(s["realized_pnl_today"], 2),
            "halted": s["halted"],
            "kill_switch": s["kill_switch"],
        }
    return summary


def load_journal_rows(path: str, limit: int = 100) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-limit:][::-1]  # newest first


class DashboardServer:
    """Read-only dashboard served alongside the trading loop.

    GET only, no auth: bind to 127.0.0.1 (default) unless you trust the
    network. It never exposes keys -- only portfolio state.
    """

    def __init__(self, config: Config, broker: Broker, tracker: EquityTracker):
        server_self = self
        self.config = config
        self.broker = broker
        self.tracker = tracker

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # route access logs to our logger
                log.debug("dashboard: " + fmt, *args)

            def _send(self, status: int, content_type: str, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _send_json(self, payload) -> None:
                self._send(200, "application/json", json.dumps(payload).encode())

            def do_GET(self):
                try:
                    path = self.path.split("?", 1)[0]
                    if path == "/":
                        self._send(200, "text/html; charset=utf-8", DASHBOARD_HTML.encode())
                    elif path == "/api/summary":
                        self._send_json(build_summary(server_self.config, server_self.broker))
                    elif path == "/api/equity":
                        self._send_json(server_self.tracker.get_points())
                    elif path == "/api/journal":
                        self._send_json(
                            load_journal_rows(server_self.config.engine.journal_file)
                        )
                    else:
                        self._send(404, "text/plain", b"not found")
                except BrokenPipeError:
                    pass
                except Exception:
                    log.exception("dashboard request failed: %s", self.path)
                    try:
                        self._send(500, "text/plain", b"internal error")
                    except Exception:
                        pass

        self.httpd = ThreadingHTTPServer((config.web.host, config.web.port), Handler)
        self.httpd.daemon_threads = True

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    def start_background(self) -> None:
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="dashboard")
        thread.start()
        log.info("dashboard: http://%s:%d", self.config.web.host, self.port)

    def stop(self) -> None:
        self.httpd.shutdown()


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>polybot dashboard</title>
<style>
  :root {
    --page: #f9f9f7; --surface: #fcfcfb;
    --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
    --grid: #e1e0d9; --baseline: #c3c2b7;
    --series: #2a78d6; --good: #006300; --bad: #d03b3b;
    --border: rgba(11,11,11,0.10);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --page: #0d0d0d; --surface: #1a1a19;
      --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
      --grid: #2c2c2a; --baseline: #383835;
      --series: #3987e5; --good: #0ca30c; --bad: #d03b3b;
      --border: rgba(255,255,255,0.10);
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--page); color: var(--ink);
    font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  .wrap { max-width: 960px; margin: 0 auto; padding: 16px; }
  header { display: flex; align-items: baseline; gap: 10px; margin-bottom: 12px; }
  header h1 { font-size: 17px; margin: 0; font-weight: 600; }
  #mode { font-size: 12px; color: var(--ink-2); border: 1px solid var(--border);
          border-radius: 999px; padding: 2px 10px; text-transform: uppercase; letter-spacing: .04em; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
           gap: 10px; margin-bottom: 14px; }
  .tile { background: var(--surface); border: 1px solid var(--border);
          border-radius: 10px; padding: 12px 14px; }
  .tile .label { color: var(--ink-2); font-size: 12px; }
  .tile .value { font-size: 22px; font-weight: 600; margin-top: 2px; }
  .tile .delta { font-size: 12px; margin-top: 2px; }
  .pos { color: var(--good); } .neg { color: var(--bad); }
  .card { background: var(--surface); border: 1px solid var(--border);
          border-radius: 10px; padding: 14px; margin-bottom: 14px; }
  .card h2 { font-size: 13px; font-weight: 600; margin: 0 0 10px; color: var(--ink-2); }
  .ranges { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
  .ranges button {
    font: inherit; color: var(--ink-2); background: transparent;
    border: 1px solid var(--border); border-radius: 999px;
    padding: 8px 14px; min-height: 40px; cursor: pointer;
  }
  .ranges button[aria-pressed="true"] { color: var(--ink); border-color: var(--ink-2); font-weight: 600; }
  #chartbox { position: relative; }
  #chart { display: block; width: 100%; height: 260px; touch-action: pan-y; }
  #tip { position: absolute; pointer-events: none; display: none;
         background: var(--surface); border: 1px solid var(--border); border-radius: 8px;
         padding: 8px 10px; font-size: 12px; box-shadow: 0 2px 10px rgba(0,0,0,.18); white-space: nowrap; }
  #tip .v { font-weight: 600; font-size: 13px; }
  #tip .t { color: var(--ink-2); }
  #empty { color: var(--muted); text-align: center; padding: 40px 0 30px; display: none; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; color: var(--muted); font-weight: 500; padding: 4px 8px 6px 0;
       border-bottom: 1px solid var(--grid); }
  td { padding: 6px 8px 6px 0; border-bottom: 1px solid var(--grid);
       font-variant-numeric: tabular-nums; }
  td.name { max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .scroll { overflow-x: auto; }
  .warn { color: var(--bad); font-weight: 600; }
  footer { color: var(--muted); font-size: 12px; text-align: center; padding: 8px 0 20px; }
  @media (max-width: 480px) {
    .wrap { padding: 10px; }
    .tile .value { font-size: 19px; }
    #chart { height: 210px; }
  }
</style>
</head>
<body>
<div class="wrap">
  <header><h1>polybot</h1><span id="mode">&hellip;</span></header>

  <div class="tiles">
    <div class="tile"><div class="label">Equity</div><div class="value" id="equity">&ndash;</div>
      <div class="delta" id="equity-delta"></div></div>
    <div class="tile"><div class="label">Cash</div><div class="value" id="cash">&ndash;</div></div>
    <div class="tile"><div class="label">Realized PnL</div><div class="value" id="rpnl">&ndash;</div></div>
    <div class="tile"><div class="label">Open positions</div><div class="value" id="npos">&ndash;</div>
      <div class="delta" id="exposure"></div></div>
  </div>

  <div class="card">
    <h2>PnL</h2>
    <div class="ranges" id="ranges" role="group" aria-label="Time period"></div>
    <div id="chartbox">
      <svg id="chart" role="img" aria-label="PnL over selected period"></svg>
      <div id="tip"><div class="v"></div><div class="t"></div></div>
      <div id="empty">No history yet &mdash; the chart fills in as the bot runs.</div>
    </div>
  </div>

  <div class="card"><h2>Today's limits</h2><div class="scroll"><table id="risk"></table></div></div>
  <div class="card"><h2>Open positions</h2><div class="scroll"><table id="positions"></table></div></div>
  <div class="card"><h2>Recent trades</h2><div class="scroll"><table id="trades"></table></div></div>
  <footer>auto-refreshes every 30s</footer>
</div>

<script>
"use strict";
const PERIODS = [
  { label: "1H", s: 3600 }, { label: "6H", s: 6 * 3600 }, { label: "1D", s: 86400 },
  { label: "1W", s: 7 * 86400 }, { label: "1M", s: 30 * 86400 }, { label: "ALL", s: 0 },
];
let period = PERIODS[2];
let equitySeries = [];

const fmtUsd = (v, sign) =>
  (sign && v > 0 ? "+" : "") + (v < 0 ? "-" : "") + "$" + Math.abs(v).toFixed(2);
const el = (id) => document.getElementById(id);

function buildRangeButtons() {
  const box = el("ranges");
  for (const p of PERIODS) {
    const b = document.createElement("button");
    b.textContent = p.label;
    b.setAttribute("aria-pressed", String(p === period));
    b.addEventListener("click", () => {
      period = p;
      for (const other of box.children) other.setAttribute("aria-pressed", "false");
      b.setAttribute("aria-pressed", "true");
      drawChart();
    });
    box.appendChild(b);
  }
}

function windowPoints() {
  if (!equitySeries.length) return [];
  if (!period.s) return equitySeries;
  const cutoff = Date.now() / 1000 - period.s;
  const pts = equitySeries.filter((p) => p.ts >= cutoff);
  return pts.length >= 2 ? pts : equitySeries.slice(-2);
}

function drawChart() {
  const svg = el("chart");
  const box = svg.getBoundingClientRect();
  const W = Math.max(box.width, 200), H = Math.max(box.height, 120);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  while (svg.firstChild) svg.removeChild(svg.firstChild);

  const pts = windowPoints();
  el("empty").style.display = pts.length < 2 ? "block" : "none";
  if (pts.length < 2) { updateDeltaTile(null); return; }

  const base = pts[0].equity;
  const series = pts.map((p) => ({ ts: p.ts, pnl: p.equity - base, equity: p.equity }));
  updateDeltaTile(series[series.length - 1].pnl);

  const padL = 46, padR = 12, padT = 10, padB = 22;
  const x0 = series[0].ts, x1 = series[series.length - 1].ts;
  let lo = Math.min(0, ...series.map((p) => p.pnl));
  let hi = Math.max(0, ...series.map((p) => p.pnl));
  if (hi - lo < 1e-9) { hi += 1; lo -= 1; }
  const pad = (hi - lo) * 0.08; lo -= pad; hi += pad;
  const X = (t) => padL + ((t - x0) / Math.max(x1 - x0, 1)) * (W - padL - padR);
  const Y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);

  const css = getComputedStyle(document.documentElement);
  const C = (name) => css.getPropertyValue(name).trim();
  const NS = "http://www.w3.org/2000/svg";
  const add = (tag, attrs) => {
    const n = document.createElementNS(NS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    svg.appendChild(n);
    return n;
  };

  // y gridlines at ~4 clean values, hairline, with tick labels in muted ink
  const step = niceStep((hi - lo) / 4);
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
    const y = Y(v);
    add("line", { x1: padL, x2: W - padR, y1: y, y2: y, stroke: C("--grid"), "stroke-width": 1 });
    const t = add("text", { x: padL - 6, y: y + 4, "text-anchor": "end", fill: C("--muted"), "font-size": 11 });
    t.textContent = Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + "K" : v.toFixed(step < 1 ? 2 : 0);
  }
  // zero baseline, one step stronger than the grid
  if (lo < 0 && hi > 0)
    add("line", { x1: padL, x2: W - padR, y1: Y(0), y2: Y(0), stroke: C("--baseline"), "stroke-width": 1 });

  // x tick labels
  const nx = W < 480 ? 3 : 5;
  for (let i = 0; i <= nx; i++) {
    const ts = x0 + ((x1 - x0) * i) / nx;
    const t = add("text", {
      x: X(ts), y: H - 6, "text-anchor": i === 0 ? "start" : i === nx ? "end" : "middle",
      fill: C("--muted"), "font-size": 11,
    });
    t.textContent = fmtTime(ts, x1 - x0);
  }

  // area wash (series hue at 10%) between line and zero, then the 2px line
  const zeroY = Y(Math.max(lo, Math.min(hi, 0)));
  let dLine = "", dArea = `M ${X(series[0].ts)} ${zeroY}`;
  for (const p of series) {
    const x = X(p.ts), y = Y(p.pnl);
    dLine += (dLine ? " L " : "M ") + x + " " + y;
    dArea += ` L ${x} ${y}`;
  }
  dArea += ` L ${X(series[series.length - 1].ts)} ${zeroY} Z`;
  add("path", { d: dArea, fill: C("--series"), opacity: 0.1 });
  add("path", {
    d: dLine, fill: "none", stroke: C("--series"), "stroke-width": 2,
    "stroke-linejoin": "round", "stroke-linecap": "round",
  });

  // end marker: >=8px dot with a 2px surface ring
  const last = series[series.length - 1];
  add("circle", { cx: X(last.ts), cy: Y(last.pnl), r: 6, fill: C("--surface") });
  add("circle", { cx: X(last.ts), cy: Y(last.pnl), r: 4, fill: C("--series") });

  attachHover(svg, series, X, Y, { padL, padR, padT, padB, W, H, C });
}

function attachHover(svg, series, X, Y, g) {
  const tip = el("tip");
  let hairline = null, dotRing = null, dot = null;
  const NS = "http://www.w3.org/2000/svg";

  const move = (ev) => {
    const rect = svg.getBoundingClientRect();
    const px = ((ev.clientX - rect.left) / rect.width) * g.W;
    let best = series[0], bd = Infinity;
    for (const p of series) {
      const d = Math.abs(X(p.ts) - px);
      if (d < bd) { bd = d; best = p; }
    }
    const x = X(best.ts), y = Y(best.pnl);
    if (!hairline) {
      hairline = document.createElementNS(NS, "line");
      hairline.setAttribute("stroke", g.C("--baseline"));
      hairline.setAttribute("stroke-width", "1");
      svg.appendChild(hairline);
      dotRing = document.createElementNS(NS, "circle");
      dotRing.setAttribute("r", "6"); dotRing.setAttribute("fill", g.C("--surface"));
      svg.appendChild(dotRing);
      dot = document.createElementNS(NS, "circle");
      dot.setAttribute("r", "4"); dot.setAttribute("fill", g.C("--series"));
      svg.appendChild(dot);
    }
    hairline.setAttribute("x1", x); hairline.setAttribute("x2", x);
    hairline.setAttribute("y1", g.padT); hairline.setAttribute("y2", g.H - g.padB);
    dotRing.setAttribute("cx", x); dotRing.setAttribute("cy", y);
    dot.setAttribute("cx", x); dot.setAttribute("cy", y);

    tip.querySelector(".v").textContent =
      fmtUsd(best.pnl, true) + "  (equity " + fmtUsd(best.equity) + ")";
    tip.querySelector(".t").textContent = new Date(best.ts * 1000).toLocaleString();
    tip.style.display = "block";
    const bx = el("chartbox").getBoundingClientRect();
    const tw = tip.offsetWidth;
    let left = ((x / g.W) * rect.width) + 12;
    if (left + tw > bx.width - 4) left = ((x / g.W) * rect.width) - tw - 12;
    tip.style.left = Math.max(4, left) + "px";
    tip.style.top = "8px";
  };
  const leave = () => {
    tip.style.display = "none";
    if (hairline) { hairline.remove(); dotRing.remove(); dot.remove(); hairline = null; }
  };
  svg.addEventListener("pointermove", move);
  svg.addEventListener("pointerdown", move);
  svg.addEventListener("pointerleave", leave);
}

function niceStep(raw) {
  const mag = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-9))));
  for (const m of [1, 2, 5, 10]) if (raw <= m * mag) return m * mag;
  return 10 * mag;
}
function fmtTime(ts, span) {
  const d = new Date(ts * 1000);
  if (span <= 86400) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (span <= 32 * 86400) return d.toLocaleDateString([], { month: "short", day: "numeric" });
  return d.toLocaleDateString([], { month: "short", year: "2-digit" });
}
function updateDeltaTile(pnl) {
  const node = el("equity-delta");
  node.textContent = pnl === null ? "" : fmtUsd(pnl, true) + " " + period.label.toLowerCase();
  node.className = "delta " + (pnl > 0 ? "pos" : pnl < 0 ? "neg" : "");
}

function fillTable(id, headers, rows) {
  const table = el(id);
  while (table.firstChild) table.removeChild(table.firstChild);
  const tr = document.createElement("tr");
  for (const h of headers) {
    const th = document.createElement("th");
    th.textContent = h;
    tr.appendChild(th);
  }
  table.appendChild(tr);
  for (const row of rows) {
    const tr2 = document.createElement("tr");
    row.forEach((cell, i) => {
      const td = document.createElement("td");
      if (typeof cell === "object") {
        td.textContent = cell.text;
        if (cell.cls) td.className = cell.cls;
      } else {
        td.textContent = cell;
      }
      if (i <= 1) td.classList.add("name");
      tr2.appendChild(td);
    });
    table.appendChild(tr2);
  }
  if (!rows.length) {
    const tr3 = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = headers.length;
    td.textContent = "none";
    td.style.color = "var(--muted)";
    tr3.appendChild(td);
    table.appendChild(tr3);
  }
}

async function refresh() {
  try {
    const [summary, equity, journal] = await Promise.all([
      fetch("/api/summary").then((r) => r.json()),
      fetch("/api/equity").then((r) => r.json()),
      fetch("/api/journal").then((r) => r.json()),
    ]);
    equitySeries = equity;

    el("mode").textContent = summary.mode;
    el("cash").textContent = fmtUsd(summary.cash);
    el("equity").textContent = fmtUsd(summary.cash + summary.exposure);
    const r = el("rpnl");
    r.textContent = fmtUsd(summary.realized_pnl, true);
    r.className = "value " + (summary.realized_pnl > 0 ? "pos" : summary.realized_pnl < 0 ? "neg" : "");
    el("npos").textContent = String(summary.positions.length);
    el("exposure").textContent = "exposure " + fmtUsd(summary.exposure);

    if (summary.risk) {
      const k = summary.risk;
      fillTable("risk", ["", "used", "limit"], [
        ["Buys today", String(k.buys_today), String(k.max_buys_per_day)],
        ["Spend today", fmtUsd(k.buy_notional_today), fmtUsd(k.max_buy_notional_per_day_usd)],
        ["Realized PnL today", { text: fmtUsd(k.realized_pnl_today, true),
          cls: k.realized_pnl_today < 0 ? "neg" : "pos" }, ""],
        ["Status", k.kill_switch ? { text: "KILL SWITCH", cls: "warn" }
          : k.halted ? { text: "HALTED (daily loss)", cls: "warn" } : "trading", ""],
      ]);
    } else {
      fillTable("risk", ["", ""], [["Risk guard", "disabled"]]);
    }

    fillTable("positions", ["Outcome", "Token", "Size", "Avg", "Cost"],
      summary.positions.map((p) => [
        p.outcome || "?", p.token_id.slice(0, 10) + "\\u2026",
        p.size.toFixed(2), p.avg_price.toFixed(3), fmtUsd(p.cost_usd),
      ]));

    fillTable("trades", ["Time", "Market", "Strategy", "Side", "Price", "Size", "Notional"],
      journal.map((t) => [
        (t.date_utc || "").replace("T", " ").replace("Z", ""),
        t.market || t.condition_id || "?", t.strategy || "?", t.side || "?",
        t.price, t.size, "$" + t.notional_usd,
      ]));

    drawChart();
  } catch (e) { /* keep previous render on transient errors */ }
}

buildRangeButtons();
refresh();
setInterval(refresh, 30000);
window.addEventListener("resize", () => drawChart());
</script>
</body>
</html>
"""
