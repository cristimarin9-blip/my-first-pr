// Small helpers shared by every scoring page.
window.GS = (function () {
  "use strict";

  function el(tag, attrs, children) {
    const n = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      const v = attrs[k];
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k === "style" && typeof v === "object") Object.assign(n.style, v);
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) n.setAttribute(k, v === true ? "" : v);
    }
    (children || []).forEach((c) => { if (c !== null && c !== undefined && c !== false) n.append(c); });
    return n;
  }

  // Browser storage can be missing (private windows, previews), so every call is guarded.
  function load(key, fallback) {
    try { const v = JSON.parse(localStorage.getItem(key) || "null"); return v == null ? fallback : v; }
    catch (e) { return fallback; }
  }
  function save(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* ignore */ }
  }

  const range = (n) => Array.from({ length: n }, (_, i) => i);

  // Meeple / pawn colours used across games.
  const COLORS = [
    { name: "Red", hex: "#d64545" }, { name: "Blue", hex: "#2f6fd1" }, { name: "Yellow", hex: "#e0a21b" },
    { name: "Green", hex: "#2e9e5b" }, { name: "Black", hex: "#2b3134" }, { name: "Purple", hex: "#8a55c9" },
    { name: "Orange", hex: "#e2702b" }, { name: "Pink", hex: "#d8609a" }
  ];

  function defaultPlayers(n) {
    return range(n).map((i) => ({ name: "", color: i % COLORS.length }));
  }
  function playerName(p, i) { return (p.name || "").trim() || "Player " + (i + 1); }

  function dot(colorIndex) {
    return el("span", { class: "dot", style: { background: COLORS[colorIndex % COLORS.length].hex }, "aria-hidden": "true" });
  }

  // Count chips + name/colour inputs. Inputs are rebuilt only when the count changes,
  // so typing a name never loses focus.
  function playerSetup(box, opts) {
    let key = "";
    return function sync(players, locked) {
      const k = players.length + "|" + locked;
      if (k === key) return;
      key = k;
      const counts = el("div", { class: "seg" }, range(opts.max - opts.min + 1).map((i) => {
        const c = opts.min + i;
        return el("button", {
          type: "button", class: "chip", "aria-pressed": String(players.length === c), disabled: locked,
          onclick: () => opts.onCount(c)
        }, [String(c)]);
      }));
      const names = el("div", { class: "names" }, players.map((p, i) => {
        const input = el("input", { id: opts.id + "-name" + i, maxlength: "20", placeholder: "Player " + (i + 1), value: p.name || "" });
        input.addEventListener("input", () => { p.name = input.value; opts.onName(); });
        const color = el("button", {
          type: "button", class: "chip", title: "Change colour", "aria-label": "Change colour for player " + (i + 1),
          onclick: () => { p.color = (p.color + 1) % COLORS.length; key = ""; opts.onName(); }
        }, [dot(p.color)]);
        return el("label", { for: opts.id + "-name" + i }, ["Player " + (i + 1), el("div", { class: "name-row" }, [color, input])]);
      }));
      box.replaceChildren(
        el("div", { class: "row" }, [el("span", { class: "label", text: "Players" }), counts]),
        names
      );
    };
  }

  function stepper(value, onChange, opts) {
    opts = opts || {};
    const min = opts.min == null ? 0 : opts.min;
    const max = opts.max == null ? Infinity : opts.max;
    const label = opts.label || "value";
    return el("span", { class: "stepper" }, [
      el("button", { type: "button", text: "−", disabled: value <= min, "aria-label": "Decrease " + label, onclick: () => onChange(Math.max(min, value - 1)) }),
      el("output", { text: String(value), "aria-label": label }),
      el("button", { type: "button", text: "+", disabled: value >= max, "aria-label": "Increase " + label, onclick: () => onChange(Math.min(max, value + 1)) })
    ]);
  }

  // A button that asks for confirmation inline (the viewer blocks confirm()).
  function confirmButton(label, question, onYes, cls) {
    const box = el("span", { class: "row" });
    function idle() {
      box.replaceChildren(el("button", { type: "button", class: "btn " + (cls || "danger"), text: label, onclick: ask }));
    }
    function ask() {
      box.replaceChildren(
        el("span", { class: "small", text: question }),
        el("button", { type: "button", class: "btn danger sm", text: "Yes", onclick: () => { onYes(); idle(); } }),
        el("button", { type: "button", class: "btn sm", text: "Cancel", onclick: idle })
      );
    }
    idle();
    return box;
  }

  return { el, load, save, range, COLORS, defaultPlayers, playerName, dot, playerSetup, stepper, confirmButton };
})();
