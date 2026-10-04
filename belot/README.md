# Belot Scorekeeper

A single-file web app for keeping score in Bulgarian belot. Open `index.html` in any browser; no build step or server needed. Scores are saved in the browser's local storage.

What it handles:

- Play as 2 teams, or individually with 2, 3 or 4 players. When playing individually the bidder must beat every other player; if inside, the bidder's points are shared among the others.
- Contracts: clubs, diamonds, hearts, spades, no trumps, all trumps, with double (×2) and redouble (×4).
- Trick points: enter one team's points and the other is filled in (162 suit, 258 all trumps, 130 no trumps counted double). Capot (one side takes all 8 tricks) adds 90.
- Declarations: tierce 20, quarte 50, quint 100, four jacks 200, four nines 150, four A/K/Q/10 100, and belot (K + Q of trumps) 20, each shown with card icons of what it is made of. Declarations are disabled in no trumps.
- A −10 penalty for a side that took no tricks or broke a rule (not doubled).
- Rounding to game points (suit: 7+ rounds up; all trumps / no trumps: 5+ rounds up).
- Inside (contract failed), hanging points on a tie, and the game target (151 by default).
- Score sheet with running totals, undo and per-hand delete.
