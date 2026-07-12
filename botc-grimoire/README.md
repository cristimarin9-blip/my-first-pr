# 🔮 Grimoire — a mobile Storyteller companion

A phone-first, installable **web app (PWA)** that lets you run games of a
hidden-role, social-deduction game **without the physical set**. It gives the
Game Master ("Storyteller") a digital **Grimoire**: a visual circle of player
tokens with names, character assignments, life/death shrouds, ghost votes, and
attachable status/reminder tokens — plus a night-order helper, demon-bluff
slots, and team-composition guidance.

It works **fully offline** once loaded and stores everything **on your device
only** — no accounts, no servers.

---

## ⚠️ Unofficial fan project — please read

This is an **unofficial, fan-made companion tool**. Blood on the Clocktower is a
trademark of **Steven Medway and The Pandemonium Institute**, and this project
is not affiliated with or endorsed by them.

To respect their rights, this repository ships **no official artwork and no
ability/rules text**. Character tokens use generic emoji/SVG **placeholders**,
and the only character data included is functional (names, team, and
night-order flags). **Please buy the real game** — you need its rulebook to play,
and the physical set is wonderful.

**Using your own art:** personal, non-commercial use does *not* grant a licence
to redistribute someone else's copyrighted images or text, and this repo is
public. So instead of bundling those assets, the app has a **Settings → Customise**
screen where *you* can upload your own images and edit every name/emoji. Anything
you personally own or create loads in and is stored only in your browser.

---

## Features

- **The Grimoire circle** — players seated in a ring, each a movable token with
  name and assigned character.
- **Movable tokens** — drag a seat around the circle to re-order the table.
- **Character assignment** — searchable picker grouped by team (Townsfolk,
  Outsider, Minion, Demon, Traveller, Fabled).
- **Life & death** — one-tap shroud, with automatic ghost-vote tracking for the
  dead.
- **Status / reminder tokens** — attach Poisoned, Drunk, Protected, Red Herring,
  and more to any player; add your own custom ones too.
- **Night-order helper** — auto-builds the first-night and other-nights wake
  order from who's in play, numbered right on the tokens.
- **Demon bluffs** strip and **recommended team composition** for 5–15 players.
- **Customise everything** — upload your own art, change emoji, rename
  characters and reassign teams (Settings).
- **Share a game** between phones with an export/import code.
- **Installable & offline** — "Add to Home Screen" for a full-screen app that
  works with no signal.

## Run it

It's a static site — no build step.

```bash
cd botc-grimoire
python3 -m http.server 8000
# open http://localhost:8000 on your computer or phone (same Wi-Fi)
```

Or host the `botc-grimoire/` folder on any static host (GitHub Pages, Netlify,
etc.) and open it on your phone. On the phone, use your browser's **Add to Home
Screen** to install it as an app.

> A service worker caches the app for offline use. If you change the source,
> bump the `CACHE` version string in `service-worker.js` to force an update.

## Customise art & names

1. Open **☰ menu → 🎨 Customise art & names** (or Settings).
2. Tap any character.
3. **Upload custom art** (auto-downscaled and stored locally), or set an emoji,
   rename it, and change its team.
4. Toggle **Show uploaded images** off any time to fall back to emoji.

Create brand-new characters with **➕ New custom character**.

## Project layout

```
botc-grimoire/
├── index.html              # app shell
├── manifest.webmanifest    # PWA manifest (installable)
├── service-worker.js       # offline cache
├── css/styles.css          # mobile-first dark theme
├── icons/icon.svg          # app icon (original artwork)
└── js/
    ├── app.js              # UI, state, drag, night order, settings
    └── roles.js            # functional character data + generic reminders
```

## Tech

Vanilla JavaScript (ES modules), no dependencies, no build. State persists to
`localStorage`. Uploaded images are downscaled with a `<canvas>` before saving
to keep storage small.

## License

The **code** in this repository is released under the MIT License (see
`LICENSE`). This licence covers only the original code and placeholder assets
here — it does **not** grant any rights to Blood on the Clocktower, its
artwork, characters, or text, which remain the property of The Pandemonium
Institute.
