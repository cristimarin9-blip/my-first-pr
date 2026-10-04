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

  // ---------- photo scoring ----------
  // Claude reads photos through the viewer's `sample` capability. It only exists on the
  // artifact's main page, so game pages opened inside the menu borrow the menu's bridge.
  function makePhotoApi() {
    let samplePromise = null;
    function getSample() {
      if (!samplePromise) samplePromise = (async () => {
        if (!window.claude || !window.claude.use) return null;
        const s = await window.claude.use("sample");
        if (!s) return null;
        const lim = await s.limits().catch(() => null);
        return lim && lim.images ? s : null;
      })().catch(() => null);
      return samplePromise;
    }
    return {
      available: async () => !!(await getSample()),
      // Takes plain data (string + ArrayBuffer) so it can be called from another frame.
      ask: async (prompt, buffer, type) => {
        const s = await getSample();
        if (!s) throw { code: "images_unavailable", message: "Photos are not available here." };
        return s.json(prompt, { images: [new Blob([buffer], { type: type || "image/jpeg" })] });
      }
    };
  }

  // When the viewer isolates frames, the game cannot touch the menu directly, so it asks by message.
  function messageBridge() {
    let seq = 0;
    const waiting = {};
    window.addEventListener("message", (e) => {
      const d = e.data;
      if (e.source !== window.parent || !d || d.gs !== "photo-reply" || !waiting[d.id]) return;
      const w = waiting[d.id];
      delete waiting[d.id];
      if (d.ok) w.resolve(d.value); else w.reject(d.error || { code: "other" });
    });
    function call(kind, payload, transfer, timeout) {
      return new Promise((resolve, reject) => {
        const id = ++seq;
        waiting[id] = { resolve, reject };
        window.parent.postMessage(Object.assign({ gs: kind, id }, payload), "*", transfer || []);
        if (timeout) setTimeout(() => { if (waiting[id]) { delete waiting[id]; reject({ code: "no_bridge" }); } }, timeout);
      });
    }
    return {
      available: () => call("photo-available", {}, [], 4000).then(Boolean, () => false),
      ask: (prompt, buffer, type) => call("photo-ask", { prompt, buffer, type }, [buffer])
    };
  }

  let photoApi = null;
  let photoKnown = null; // true / false once known
  function photo() {
    if (photoApi) return photoApi;
    if (window.parent !== window) {
      try {
        if (window.parent.GS_photo) { photoApi = window.parent.GS_photo; return photoApi; }
      } catch (e) { /* isolated from the menu: use messages */ }
      const bridge = messageBridge();
      const local = makePhotoApi();
      let via = null;
      const pick = () => {
        if (!via) via = bridge.available().then((ok) => (ok ? bridge : local.available().then((ok2) => (ok2 ? local : null))));
        return via;
      };
      photoApi = {
        available: async () => !!(await pick()),
        ask: async (prompt, buffer, type) => {
          const api = await pick();
          if (!api) throw { code: "images_unavailable" };
          return api.ask(prompt, buffer, type);
        }
      };
      return photoApi;
    }
    photoApi = makePhotoApi();
    return photoApi;
  }

  async function readPhoto(file, prompt) {
    const buffer = await file.arrayBuffer();
    return photo().ask(prompt, buffer, file.type);
  }

  const PHOTO_ERRORS = {
    en: {
      not_granted: "Photo reading was not allowed for this page.",
      rate_limited: "Too many photos at once. Wait a moment and try again.",
      image_rejected: "That photo could not be read. Try another one.",
      refused: "Claude could not read this photo. Try another one.",
      invalid_json: "The photo was read but the answer was unclear. Try again or enter the score by hand.",
      session_expired: "Sign in to Claude again, then retry.",
      images_unavailable: "Photo reading is not available here.",
      other: "Something went wrong reading the photo. Try again or enter the score by hand."
    },
    ro: {
      not_granted: "Citirea pozelor nu a fost permisă pentru această pagină.",
      rate_limited: "Prea multe poze deodată. Așteptați puțin și încercați din nou.",
      image_rejected: "Poza nu a putut fi citită. Încercați alta.",
      refused: "Claude nu a putut citi poza. Încercați alta.",
      invalid_json: "Poza a fost citită, dar răspunsul nu e clar. Încercați din nou sau scrieți punctele de mână.",
      session_expired: "Conectați-vă din nou la Claude, apoi reîncercați.",
      images_unavailable: "Citirea pozelor nu e disponibilă aici.",
      other: "Ceva n-a mers la citirea pozei. Încercați din nou sau scrieți punctele de mână."
    }
  };
  function photoError(e, lang) {
    const t = PHOTO_ERRORS[lang || "en"];
    return t[(e && e.code) || "other"] || t.other;
  }

  const UNAVAILABLE = {
    en: "Photo reading only works in this app on claude.ai, after you allow it to use Claude. It is not available in this view.",
    ro: "Citirea pozelor merge doar în aplicație pe claude.ai, după ce îi permiteți să folosească Claude. Aici nu e disponibilă."
  };
  // A camera button. The phone offers to take a photo or pick one from the gallery.
  // If photo reading is not available here, tapping it says so instead of failing silently.
  function photoButton(label, onFile, cls, lang) {
    const input = el("input", { type: "file", accept: "image/*", hidden: true });
    const msg = el("span", { class: "small muted", hidden: true, text: UNAVAILABLE[lang || "en"] });
    const btn = el("button", { type: "button", class: "btn photo-btn " + (cls || ""), onclick: () => {
      if (photoKnown === false) { msg.hidden = false; return; }
      input.click();
    } }, [cameraIcon(), label]);
    input.addEventListener("change", () => {
      const f = input.files && input.files[0];
      input.value = "";
      if (f) onFile(f);
    });
    if (photoKnown === null) photo().available().then((ok) => { photoKnown = ok; }).catch(() => { photoKnown = false; });
    return el("span", { class: "photo-wrap" }, [btn, input, msg]);
  }

  function cameraIcon() {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("width", "18"); svg.setAttribute("height", "18"); svg.setAttribute("aria-hidden", "true");
    svg.innerHTML = '<path fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round" d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5" fill="none" stroke="currentColor" stroke-width="2"/>';
    return svg;
  }

  // Inside the menu, "back" closes the game instead of loading the menu again.
  document.addEventListener("click", (e) => {
    const a = e.target.closest && e.target.closest("a.back");
    if (!a) return;
    try {
      if (window.parent !== window && window.parent.GS_closeGame) { e.preventDefault(); window.parent.GS_closeGame(); }
    } catch (err) { /* follow the link */ }
  });

  return { el, load, save, range, COLORS, defaultPlayers, playerName, dot, playerSetup, stepper, confirmButton,
    makePhotoApi, photo, readPhoto, photoButton, photoError };
})();
