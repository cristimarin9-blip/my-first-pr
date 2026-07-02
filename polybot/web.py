from __future__ import annotations

import csv
import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from polybot.risk import RiskGuardedBroker

log = logging.getLogger(__name__)


def tail_file(path: str, limit: int = 200) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    try:
        with p.open(errors="replace") as f:
            return [line.rstrip("\n") for line in f.readlines()[-limit:]]
    except OSError:
        return []


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

    def __init__(self, runtime):
        server_self = self
        self.runtime = runtime
        # Per-run secret embedded in the page. Same-origin policy stops other
        # sites from reading it, so it doubles as a CSRF token for actions.
        self.control_token = secrets.token_urlsafe(16)

        def render_page() -> bytes:
            return (
                DASHBOARD_HTML
                .replace("__CONTROL_TOKEN__", server_self.control_token)
                .replace(
                    "__CONTROLS_ENABLED__",
                    "true" if server_self.runtime.config.web.controls_enabled else "false",
                )
                .encode()
            )

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

            def _send_json(self, payload, status: int = 200) -> None:
                self._send(status, "application/json", json.dumps(payload).encode())

            def do_GET(self):
                try:
                    rt = server_self.runtime
                    path = self.path.split("?", 1)[0]
                    if path == "/":
                        self._send(200, "text/html; charset=utf-8", render_page())
                    elif path == "/api/summary":
                        summary = build_summary(rt.config, rt.broker)
                        summary["paused"] = rt.is_paused()
                        self._send_json(summary)
                    elif path == "/api/equity":
                        self._send_json(rt.tracker.get_points())
                    elif path == "/api/journal":
                        self._send_json(load_journal_rows(rt.config.engine.journal_file))
                    elif path == "/api/config":
                        self._send_json(rt.editable_config())
                    elif path == "/api/logs":
                        self._send_json({"lines": tail_file(rt.config.engine.log_file, 200)})
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

            def do_POST(self):
                try:
                    path = self.path.split("?", 1)[0]
                    if path != "/api/action":
                        self._send(404, "text/plain", b"not found")
                        return
                    if not server_self.runtime.config.web.controls_enabled:
                        self._send_json({"ok": False, "error": "controls are disabled in config"}, 403)
                        return
                    token = self.headers.get("X-Polybot-Token", "")
                    if not secrets.compare_digest(token, server_self.control_token):
                        self._send_json({"ok": False, "error": "invalid or missing control token"}, 403)
                        return
                    length = int(self.headers.get("Content-Length", 0) or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    try:
                        payload = json.loads(raw or b"{}")
                    except ValueError:
                        payload = {}
                    result = server_self.runtime.dispatch(str(payload.get("action", "")), payload)
                    self._send_json(result, 200 if result.get("ok") else 400)
                except BrokenPipeError:
                    pass
                except Exception:
                    log.exception("dashboard action failed: %s", self.path)
                    try:
                        self._send_json({"ok": False, "error": "internal error"}, 500)
                    except Exception:
                        pass

        self.httpd = ThreadingHTTPServer(
            (runtime.config.web.host, runtime.config.web.port), Handler
        )
        self.httpd.daemon_threads = True

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    def start_background(self) -> None:
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="dashboard")
        thread.start()
        cfg = self.runtime.config.web
        log.info("dashboard: http://%s:%d", cfg.host, self.port)

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
  .actions { display: flex; gap: 8px; flex-wrap: wrap; }
  .actions button {
    font: inherit; color: var(--ink); background: transparent;
    border: 1px solid var(--border); border-radius: 999px;
    padding: 9px 16px; min-height: 42px; cursor: pointer;
  }
  .actions button:hover { border-color: var(--ink-2); }
  .actions button:disabled { opacity: .5; cursor: default; }
  .actions button.danger { color: var(--bad); border-color: var(--bad); }
  #action-note { font-size: 12px; margin-top: 10px; min-height: 16px; }
  .tabs { display: flex; gap: 6px; margin-bottom: 14px; flex-wrap: wrap; }
  .tabs button {
    font: inherit; color: var(--ink-2); background: transparent;
    border: 1px solid var(--border); border-radius: 999px;
    padding: 8px 16px; min-height: 40px; cursor: pointer;
  }
  .tabs button[aria-selected="true"] { color: var(--ink); border-color: var(--ink-2); font-weight: 600; }
  .panel { display: none; } .panel.active { display: block; }
  fieldset { border: 1px solid var(--border); border-radius: 10px; margin: 0 0 12px; padding: 12px 14px; }
  legend { font-size: 12px; font-weight: 600; color: var(--ink-2); padding: 0 6px;
           text-transform: capitalize; }
  .field { display: flex; align-items: center; justify-content: space-between;
           gap: 12px; padding: 6px 0; border-bottom: 1px solid var(--grid); }
  .field:last-child { border-bottom: 0; }
  .field label { color: var(--ink-2); font-size: 13px; }
  .field input[type=number], .field input[type=text], .field select {
    font: inherit; color: var(--ink); background: var(--page);
    border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px;
    min-height: 40px; width: 160px; max-width: 46vw;
  }
  .field input[type=checkbox] { width: 22px; height: 22px; }
  textarea {
    font: 13px/1.4 ui-monospace, monospace; color: var(--ink); background: var(--page);
    border: 1px solid var(--border); border-radius: 8px; padding: 10px; width: 100%;
    min-height: 90px; resize: vertical;
  }
  #logs { font: 12px/1.5 ui-monospace, monospace; white-space: pre; margin: 0;
          max-height: 60vh; overflow: auto; color: var(--ink-2); }
  .hint { color: var(--muted); font-size: 12px; margin: 2px 0 10px; }
  .savebar { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
  .savebar button { font: inherit; color: #fff; background: var(--series); border: 0;
    border-radius: 999px; padding: 11px 22px; min-height: 44px; cursor: pointer; font-weight: 600; }
  .savebar button:disabled { opacity: .5; cursor: default; }
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
  <header><h1>polybot</h1><span id="mode">&hellip;</span><span id="paused-badge"></span></header>

  <div class="tabs" role="tablist">
    <button id="tab-overview" role="tab" aria-selected="true" type="button">Overview</button>
    <button id="tab-settings" role="tab" aria-selected="false" type="button">Settings</button>
    <button id="tab-logs" role="tab" aria-selected="false" type="button">Logs</button>
  </div>

  <!-- OVERVIEW ------------------------------------------------------------- -->
  <div class="panel active" id="panel-overview">
    <div class="tiles">
      <div class="tile"><div class="label">Equity</div><div class="value" id="equity">&ndash;</div>
        <div class="delta" id="equity-delta"></div></div>
      <div class="tile"><div class="label">Cash</div><div class="value" id="cash">&ndash;</div></div>
      <div class="tile"><div class="label">Realized PnL</div><div class="value" id="rpnl">&ndash;</div></div>
      <div class="tile"><div class="label">Open positions</div><div class="value" id="npos">&ndash;</div>
        <div class="delta" id="exposure"></div></div>
    </div>

    <div class="card" id="controls">
      <h2>Controls</h2>
      <div class="actions">
        <button id="btn-halt" class="danger" type="button">Halt trading</button>
        <button id="btn-resume" type="button">Resume</button>
        <button id="btn-pause" type="button">Pause polling</button>
        <button id="btn-unpause" type="button">Resume polling</button>
        <button id="btn-poll" type="button">Poll now</button>
        <button id="btn-refresh" type="button">Refresh leaderboard</button>
        <button id="btn-reset" class="danger" type="button">Reset paper</button>
      </div>
      <div id="action-note"></div>
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

  <!-- SETTINGS ------------------------------------------------------------- -->
  <div class="panel" id="panel-settings">
    <div class="card">
      <h2>Settings</h2>
      <p class="hint">Changes save to config.yaml and apply immediately &mdash; no restart needed.</p>
      <div id="settings-form"></div>
      <div class="savebar">
        <button id="btn-save" type="button">Save &amp; apply</button>
        <button id="btn-reload" type="button" style="background:transparent;color:var(--ink-2);border:1px solid var(--border)">Reload from disk</button>
        <span id="save-note"></span>
      </div>
    </div>
  </div>

  <!-- LOGS ----------------------------------------------------------------- -->
  <div class="panel" id="panel-logs">
    <div class="card">
      <h2>Logs</h2>
      <pre id="logs">&hellip;</pre>
    </div>
  </div>
</div>

<script>
"use strict";
const CONTROL_TOKEN = "__CONTROL_TOKEN__";
const CONTROLS_ENABLED = __CONTROLS_ENABLED__;
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

    const pb = el("paused-badge");
    pb.textContent = summary.paused ? "paused" : "";
    pb.className = summary.paused ? "neg" : "";

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

async function doAction(action, opts) {
  opts = opts || {};
  if (!CONTROLS_ENABLED) return { ok: false };
  if (opts.confirm && !window.confirm(opts.confirm)) return { ok: false };
  const note = el(opts.noteId || "action-note");
  const buttons = document.querySelectorAll(".actions button, .savebar button");
  buttons.forEach((b) => (b.disabled = true));
  note.textContent = "Working\\u2026";
  note.className = "";
  try {
    const r = await fetch("/api/action", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Polybot-Token": CONTROL_TOKEN },
      body: JSON.stringify(Object.assign({ action }, opts.body || {})),
    });
    const j = await r.json();
    note.textContent = j.ok ? j.message : "Error: " + (j.error || "failed");
    note.className = j.ok ? "pos" : "neg";
    await refresh();
    return j;
  } catch (e) {
    note.textContent = "Error: " + e;
    note.className = "neg";
    return { ok: false };
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}

function setupControls() {
  if (!CONTROLS_ENABLED) {
    el("controls").style.display = "none";
    el("panel-settings").innerHTML =
      '<div class="card"><p class="hint">Controls are disabled (web.controls_enabled: false).</p></div>';
    return;
  }
  el("btn-halt").addEventListener("click", () =>
    doAction("halt", { confirm: "Halt trading? New buys blocked until you resume; open positions can still be sold." }));
  el("btn-resume").addEventListener("click", () => doAction("resume"));
  el("btn-pause").addEventListener("click", () => doAction("pause"));
  el("btn-unpause").addEventListener("click", () => doAction("unpause"));
  el("btn-poll").addEventListener("click", () => doAction("poll_now"));
  el("btn-refresh").addEventListener("click", () => doAction("refresh_leaderboard"));
  el("btn-reset").addEventListener("click", () =>
    doAction("reset_paper", { confirm: "Reset the paper portfolio back to its starting balance? This clears simulated positions and cash." }));
}

// --- tabs -------------------------------------------------------------------
function setupTabs() {
  const tabs = [
    ["tab-overview", "panel-overview"],
    ["tab-settings", "panel-settings"],
    ["tab-logs", "panel-logs"],
  ];
  for (const [tabId, panelId] of tabs) {
    el(tabId).addEventListener("click", () => {
      for (const [t, p] of tabs) {
        const active = t === tabId;
        el(t).setAttribute("aria-selected", String(active));
        el(p).classList.toggle("active", active);
      }
      if (panelId === "panel-settings") loadSettings();
      if (panelId === "panel-logs") loadLogs();
    });
  }
}

// --- settings form ----------------------------------------------------------
let settingsMeta = null;

function inputFor(value, path) {
  let node;
  if (typeof value === "boolean") {
    node = document.createElement("input");
    node.type = "checkbox";
    node.checked = value;
  } else if (typeof value === "number") {
    node = document.createElement("input");
    node.type = "number";
    node.step = Number.isInteger(value) ? "1" : "any";
    node.value = String(value);
  } else {
    node = document.createElement("input");
    node.type = "text";
    node.value = value == null ? "" : String(value);
  }
  node.dataset.path = path;
  node.dataset.type = typeof value === "number" ? (Number.isInteger(value) ? "int" : "float") : typeof value;
  return node;
}

function fieldRow(labelText, inputNode) {
  const row = document.createElement("div");
  row.className = "field";
  const label = document.createElement("label");
  label.textContent = labelText;
  row.appendChild(label);
  row.appendChild(inputNode);
  return row;
}

async function loadSettings() {
  if (!CONTROLS_ENABLED) return;
  const cfg = await fetch("/api/config").then((r) => r.json());
  settingsMeta = cfg;
  const form = el("settings-form");
  form.innerHTML = "";

  // Mode + wallets first.
  const top = document.createElement("fieldset");
  top.innerHTML = "<legend>general</legend>";
  const modeSel = document.createElement("select");
  for (const m of ["paper", "live"]) {
    const o = document.createElement("option");
    o.value = m; o.textContent = m;
    if (m === cfg.mode) o.selected = true;
    modeSel.appendChild(o);
  }
  modeSel.id = "set-mode";
  top.appendChild(fieldRow("mode", modeSel));
  const keyInput = document.createElement("input");
  keyInput.type = "password"; keyInput.id = "set-key";
  keyInput.placeholder = "unchanged";
  top.appendChild(fieldRow("live private key", keyInput));
  const keyHint = document.createElement("p");
  keyHint.className = "hint";
  keyHint.textContent = "Only needed for live mode. Write-only \\u2014 never shown back. Leave blank to keep the current key.";
  top.appendChild(keyHint);
  form.appendChild(top);

  const wallets = document.createElement("fieldset");
  wallets.innerHTML = "<legend>watchlist wallets</legend>";
  const ta = document.createElement("textarea");
  ta.id = "set-wallets";
  ta.value = (cfg.target_wallets || []).join("\\n");
  ta.placeholder = "0x... (one wallet address per line)";
  wallets.appendChild(ta);
  form.appendChild(wallets);

  // One fieldset per config section.
  for (const [section, fields] of Object.entries(cfg.sections)) {
    const fs = document.createElement("fieldset");
    const lg = document.createElement("legend");
    lg.textContent = section;
    fs.appendChild(lg);
    for (const [name, value] of Object.entries(fields)) {
      if (Array.isArray(value)) {
        const t = document.createElement("textarea");
        t.dataset.path = section + "." + name;
        t.dataset.type = "list";
        t.value = value.join("\\n");
        const row = document.createElement("div");
        const lbl = document.createElement("label");
        lbl.textContent = name; lbl.style.display = "block"; lbl.style.margin = "6px 0 4px";
        lbl.style.color = "var(--ink-2)"; lbl.style.fontSize = "13px";
        row.appendChild(lbl); row.appendChild(t);
        fs.appendChild(row);
      } else {
        fs.appendChild(fieldRow(name, inputFor(value, section + "." + name)));
      }
    }
    form.appendChild(fs);
  }
}

function collectSettings() {
  const sections = {};
  document.querySelectorAll("#settings-form [data-path]").forEach((node) => {
    const [section, name] = node.dataset.path.split(".");
    sections[section] = sections[section] || {};
    const t = node.dataset.type;
    let v;
    if (t === "boolean") v = node.checked;
    else if (t === "int") v = parseInt(node.value, 10);
    else if (t === "float") v = parseFloat(node.value);
    else if (t === "list") v = node.value.split("\\n").map((s) => s.trim()).filter(Boolean);
    else v = node.value;
    if ((t === "int" || t === "float") && Number.isNaN(v)) return;
    sections[section][name] = v;
  });
  const wallets = el("set-wallets").value.split("\\n").map((s) => s.trim()).filter(Boolean);
  const body = { mode: el("set-mode").value, target_wallets: wallets, sections };
  const key = el("set-key").value.trim();
  if (key) body.live_private_key = key;
  return body;
}

function setupSettings() {
  if (!CONTROLS_ENABLED) return;
  el("btn-save").addEventListener("click", async () => {
    const config = collectSettings();
    const confirmMsg = config.mode === "live"
      ? "Save settings and switch to LIVE mode? Real orders with real funds will be placed."
      : null;
    await doAction("save_config", { body: { config }, noteId: "save-note", confirm: confirmMsg });
  });
  el("btn-reload").addEventListener("click", async () => {
    await doAction("reload", { noteId: "save-note" });
    loadSettings();
  });
}

// --- logs -------------------------------------------------------------------
async function loadLogs() {
  try {
    const j = await fetch("/api/logs").then((r) => r.json());
    const pre = el("logs");
    pre.textContent = (j.lines || []).join("\\n") || "(no log output yet)";
    pre.scrollTop = pre.scrollHeight;
  } catch (e) { /* ignore */ }
}

buildRangeButtons();
setupTabs();
setupControls();
setupSettings();
refresh();
setInterval(refresh, 30000);
window.addEventListener("resize", () => drawChart());
</script>
</body>
</html>
"""
