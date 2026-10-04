# Game Night Scores

Score sheets for board and card games, with a menu to pick the game. Every page is plain HTML: open `index.html` in a browser, no build step or server needed. Each game saves its own scores in the browser.

| Page | Game | What it scores |
|---|---|---|
| `belot.html` | Belot (Moldovan rules, in Romanian) | Bile, BT, combinations with card icons, last hand, penalties, 1 vs 1 / 3 / 4 / pairs. |
| `carcassonne.html` | Carcassonne | Feature calculator (roads, cities, monasteries, fields, castles) plus Inns & Cathedrals, Traders & Builders, Princess & Dragon, The Tower, Abbey & Mayor, King & Robber, Hills & Sheep, Bridges, Castles & Bazaars. |
| `mexican-train.html` | Mexican Train | Pips per round for double-9, 12 or 15 sets, optional 50 for the double blank, lowest total wins. |
| `catan.html` | Catan | Live victory points, automatic Longest Road and Largest Army, Seafarers island tokens, Cities & Knights metropolises, defender and merchant. |
| `ticket-to-ride.html` | Ticket to Ride | Routes by length, tickets, stations, longest path and Globetrotter for USA, USA 1910, Europe and Nordic Countries. |

Shared pieces: `shared.css` (colours, type, components) and `shared.js` (element helper, storage, player setup, steppers, photo reading).

## Scoring from a photo

When the app runs as a claude.ai artifact, games show camera buttons. The photo goes to Claude through the artifact's `sample` capability (on the viewer's own Claude account, after they allow it), and Claude replies with JSON. The app does the maths and always shows the result for a person to check first; anything Claude marks as unsure is highlighted and has to be fixed or confirmed before it can be used.

| Game | Photo of | What is read |
|---|---|---|
| Belot | Cards won by a player or pair | Each card; points are calculated by the app using the chosen coz |
| Carcassonne | One road, city, monastery or field | Type, completed or not, tiles, pennants, inn/cathedral, meeples by colour (fills the calculator) |
| Mexican Train | Dominoes left in a hand | Pips on every domino, the double blank |
| Catan | The board | Settlements and cities per colour |
| Ticket to Ride | A player's destination tickets | Cities and points; the player marks each completed or not |

The `sample` capability only exists on the artifact's main page, so `index.html` opens each game in a frame and lends it `window.GS_photo`. Opened on its own (or from the files directly), a game works the same but without camera buttons.

## Adding a game

1. Copy one of the game pages, for example `mexican-train.html`, and keep the `← All games` link, `shared.css` and `shared.js`.
2. Give it its own storage key (`gs-<game>-v1`).
3. Add an entry to the `GAMES` list in `index.html` with the page, name, short description and storage key.
