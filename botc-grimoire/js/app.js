import { ROLES, TEAMS, REMINDERS } from './roles.js';

/* ============================================================
   Grimoire — an unofficial mobile Storyteller companion.
   State is kept in a single object and persisted to localStorage.
   ============================================================ */

const STORAGE_KEY = 'grimoire.state.v1';

const DEFAULT_STATE = () => ({
  players: [],          // { id, name, roleId, customRole, dead, ghostVote, reminders: [] }
  bluffs: [null, null, null],
  phase: 'setup',       // 'setup' | 'night' | 'day'
  dayCount: 0,
  customRoles: [],      // user-defined roles: { id, name, team, icon, custom:true }
  roleOverrides: {},    // per-role user edits: { [roleId]: { name?, icon?, image?(dataURL), team? } }
  showImages: true,     // prefer uploaded images over emoji when available
});

let state = load() || DEFAULT_STATE();
let seatDrag = null;    // active seat-drag info
let uid = () => Math.random().toString(36).slice(2, 9);

/* ---------- persistence ---------- */
function save() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (e) {}
}
function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const s = JSON.parse(raw);
    if (!s.bluffs) s.bluffs = [null, null, null];
    if (!s.customRoles) s.customRoles = [];
    if (!s.roleOverrides) s.roleOverrides = {};
    if (s.showImages === undefined) s.showImages = true;
    return s;
  } catch (e) { return null; }
}

/* ---------- role lookup ---------- */
function allRoles() { return [...ROLES, ...state.customRoles].map(resolve); }
function resolve(r) {
  const o = state.roleOverrides[r.id];
  return o ? { ...r, ...o } : r;
}
function roleById(id) {
  const base = [...ROLES, ...state.customRoles].find(r => r.id === id);
  return base ? resolve(base) : null;
}

/* Build an icon node for a role: uploaded image if present & enabled, else emoji. */
function roleIcon(role, cls, fallback = '🎟️') {
  if (role && role.image && state.showImages) {
    return el('img', { class: `${cls} role-img`, src: role.image, alt: '', draggable: 'false' });
  }
  return el('span', { class: cls }, role ? role.icon : fallback);
}

/* ---------- DOM helpers ---------- */
const $ = sel => document.querySelector(sel);
const $$ = sel => Array.from(document.querySelectorAll(sel));
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  Object.entries(props).forEach(([k, v]) => {
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  });
  children.flat().forEach(c => node.append(c?.nodeType ? c : document.createTextNode(c ?? '')));
  return node;
}

/* ============================================================
   Rendering
   ============================================================ */
function render() {
  renderTopBar();
  renderCircle();
  renderBluffs();
  save();
}

function renderTopBar() {
  const phaseLabel = $('#phase-label');
  const aliveCount = state.players.filter(p => !p.dead).length;
  let text = 'Setup';
  if (state.phase === 'night') text = `Night ${state.dayCount}`;
  else if (state.phase === 'day') text = `Day ${state.dayCount}`;
  phaseLabel.textContent = text;
  $('#alive-count').textContent = `${aliveCount} alive / ${state.players.length}`;

  document.body.dataset.phase = state.phase;
}

/* ---- the seating circle ---- */
function renderCircle() {
  const stage = $('#circle');
  stage.innerHTML = '';
  const n = state.players.length;

  if (n === 0) {
    stage.append(el('div', { class: 'empty-hint' },
      el('p', {}, 'No players yet.'),
      el('p', { class: 'muted' }, 'Tap “Add player” or use Setup to seat a group.')));
    return;
  }

  const rect = stage.getBoundingClientRect();
  const size = Math.min(rect.width, rect.height);
  const cx = rect.width / 2;
  const cy = rect.height / 2;
  const radius = size / 2 - Math.max(46, size * 0.11);

  // Night-order badge counter (only when in a night phase).
  const nightList = currentNightOrder();

  state.players.forEach((p, i) => {
    const angle = (i / n) * 2 * Math.PI - Math.PI / 2; // start at top
    const x = cx + radius * Math.cos(angle);
    const y = cy + radius * Math.sin(angle);
    const role = p.roleId ? roleById(p.roleId) : null;
    const team = role ? TEAMS[role.team] : null;

    const seat = el('div', {
      class: `seat ${p.dead ? 'dead' : ''}`,
      style: `left:${x}px; top:${y}px; --ring:${team ? team.ring : '#666'};`,
      'data-idx': i,
    });

    // token
    const token = el('button', { class: 'token', title: 'Tap to edit', 'aria-label': `Player ${p.name}` },
      roleIcon(role, 'token-icon'));
    if (p.dead) token.append(el('span', { class: 'shroud', title: 'Dead' }, '💀'));

    // night-order badge
    const nightIdx = nightList.findIndex(x => x.playerId === p.id);
    if (nightIdx > -1) token.append(el('span', { class: 'night-badge' }, String(nightIdx + 1)));

    token.addEventListener('click', (e) => { e.stopPropagation(); openPlayerSheet(p.id); });

    // drag handle to reposition around circle
    seat.addEventListener('pointerdown', (e) => startSeatDrag(e, i));

    // name + role
    const label = el('div', { class: 'seat-label' },
      el('div', { class: 'seat-name' }, p.name || `Seat ${i + 1}`),
      el('div', { class: 'seat-role muted' }, role ? role.name : 'no role'));

    // reminder tokens attached to player
    const rem = el('div', { class: 'reminders' });
    (p.reminders || []).forEach((r, ri) => {
      rem.append(el('button', {
        class: 'reminder-chip', style: `--rc:${r.color || '#555'}`,
        title: r.label,
        onclick: (e) => { e.stopPropagation(); p.reminders.splice(ri, 1); render(); },
      }, el('span', {}, r.icon), el('span', { class: 'reminder-x' }, '×')));
    });
    if (p.ghostVote === false && p.dead) {
      rem.append(el('span', { class: 'reminder-chip used-vote', title: 'Ghost vote used' }, '🚫🗳️'));
    }

    seat.append(token, label, rem);
    stage.append(seat);
  });
}

function renderBluffs() {
  const wrap = $('#bluffs');
  if (!wrap) return;
  wrap.innerHTML = '';
  state.bluffs.forEach((bid, i) => {
    const role = bid ? roleById(bid) : null;
    wrap.append(el('button', {
      class: 'bluff-slot', title: 'Demon bluff',
      onclick: () => pickRole((rid) => { state.bluffs[i] = rid; render(); }, { allowClear: true, current: bid }),
    },
      roleIcon(role, 'bluff-icon', '➕'),
      el('span', { class: 'bluff-name muted' }, role ? role.name : 'bluff')));
  });
}

/* ============================================================
   Seat dragging (reposition around the circle by swapping)
   ============================================================ */
function startSeatDrag(e, idx) {
  // Only drag from the seat body, not the token button.
  if (e.target.closest('.token') || e.target.closest('.reminder-chip')) return;
  seatDrag = { idx, moved: false };
  const stage = $('#circle');
  const onMove = (ev) => {
    seatDrag.moved = true;
    const rect = stage.getBoundingClientRect();
    const cx = rect.width / 2, cy = rect.height / 2;
    const ang = Math.atan2(ev.clientY - rect.top - cy, ev.clientX - rect.left - cx) + Math.PI / 2;
    const n = state.players.length;
    let target = Math.round(((ang < 0 ? ang + 2 * Math.PI : ang) / (2 * Math.PI)) * n) % n;
    if (target !== seatDrag.idx) {
      const [moved] = state.players.splice(seatDrag.idx, 1);
      state.players.splice(target, 0, moved);
      seatDrag.idx = target;
      renderCircle();
    }
  };
  const onUp = () => {
    window.removeEventListener('pointermove', onMove);
    window.removeEventListener('pointerup', onUp);
    if (seatDrag && seatDrag.moved) save();
    seatDrag = null;
  };
  window.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
}

/* ============================================================
   Player editing sheet
   ============================================================ */
function openPlayerSheet(pid) {
  const p = state.players.find(x => x.id === pid);
  if (!p) return;
  const role = p.roleId ? roleById(p.roleId) : null;

  const body = el('div', {},
    // name
    el('label', { class: 'field' }, 'Name',
      el('input', {
        type: 'text', value: p.name || '', placeholder: 'Player name',
        oninput: (e) => { p.name = e.target.value; renderCircle(); },
      })),

    // role
    el('div', { class: 'field' }, 'Character',
      el('button', { class: 'role-pick', onclick: () => pickRole((rid) => { p.roleId = rid; render(); refreshSheet(pid); }, { current: p.roleId }) },
        role ? `${role.icon}  ${role.name}` : '➕  Assign character')),

    // status toggles
    el('div', { class: 'toggle-row' },
      toggleBtn(p.dead ? '💀 Dead' : '🫀 Alive', p.dead, () => {
        p.dead = !p.dead;
        p.ghostVote = p.dead ? true : undefined; // dead players get one ghost vote
        render(); refreshSheet(pid);
      }),
      p.dead ? toggleBtn(p.ghostVote ? '🗳️ Vote available' : '🚫 Vote used', !p.ghostVote, () => {
        p.ghostVote = !p.ghostVote; render(); refreshSheet(pid);
      }) : el('span'),
    ),

    // reminders
    el('div', { class: 'field' }, 'Reminder / status tokens'),
    el('div', { class: 'reminder-grid' },
      ...REMINDERS.map(r => el('button', {
        class: 'reminder-add', style: `--rc:${r.color}`,
        onclick: () => { (p.reminders ||= []).push({ ...r }); render(); refreshSheet(pid); },
      }, el('span', {}, r.icon), el('span', {}, r.label))),
      el('button', {
        class: 'reminder-add custom',
        onclick: () => {
          const label = prompt('Custom reminder text:');
          if (label) { (p.reminders ||= []).push({ id: uid(), label, icon: '📝', color: '#333' }); render(); refreshSheet(pid); }
        },
      }, el('span', {}, '➕'), el('span', {}, 'Custom')),
    ),

    (p.reminders && p.reminders.length)
      ? el('div', { class: 'active-reminders' },
          el('span', { class: 'muted' }, 'Active: '),
          ...p.reminders.map((r, ri) => el('button', {
            class: 'reminder-chip', style: `--rc:${r.color || '#555'}`,
            onclick: () => { p.reminders.splice(ri, 1); render(); refreshSheet(pid); },
          }, el('span', {}, r.icon), ' ', r.label, el('span', { class: 'reminder-x' }, '×'))))
      : el('div'),

    // remove player
    el('button', { class: 'danger-btn', onclick: () => {
      if (confirm(`Remove ${p.name || 'this player'} from the game?`)) {
        state.players = state.players.filter(x => x.id !== pid);
        closeSheet(); render();
      }
    } }, '🗑️ Remove player'),
  );

  openSheet(p.name || 'Player', body);
  refreshSheet._current = pid;
}
function refreshSheet(pid) { if ($('#sheet').classList.contains('open')) openPlayerSheet(pid); }

function toggleBtn(label, active, onclick) {
  return el('button', { class: `toggle-btn ${active ? 'active' : ''}`, onclick }, label);
}

/* ============================================================
   Role picker (grouped by team, searchable)
   ============================================================ */
function pickRole(onPick, opts = {}) {
  const teamsOrder = ['townsfolk', 'outsider', 'minion', 'demon', 'traveller', 'fabled'];
  const list = el('div', { class: 'role-list' });

  const build = (filter = '') => {
    list.innerHTML = '';
    if (opts.allowClear) {
      list.append(el('button', { class: 'role-item clear', onclick: () => { onPick(null); closeSheet(); } }, '🚫  Clear'));
    }
    teamsOrder.forEach(tk => {
      const roles = allRoles().filter(r => r.team === tk &&
        r.name.toLowerCase().includes(filter.toLowerCase()));
      if (!roles.length) return;
      list.append(el('div', { class: 'role-group-h', style: `--tc:${TEAMS[tk].color}` }, TEAMS[tk].label));
      roles.forEach(r => list.append(el('button', {
        class: `role-item ${opts.current === r.id ? 'current' : ''}`,
        style: `--tc:${TEAMS[r.team].color}`,
        onclick: () => { onPick(r.id); closeSheet(); },
      }, roleIcon(r, 'ri-icon'), el('span', {}, r.name),
        r.custom ? el('span', { class: 'ri-tag' }, 'custom') : '')));
    });
    if (!list.children.length) list.append(el('p', { class: 'muted' }, 'No matches.'));
  };

  const body = el('div', {},
    el('input', { class: 'role-search', type: 'text', placeholder: 'Search characters…',
      oninput: (e) => build(e.target.value) }),
    el('button', { class: 'add-custom-role', onclick: () => addCustomRole(onPick) }, '➕ New custom character'),
    list);
  build();
  openSheet('Choose character', body);
}

function addCustomRole(onPick, after) {
  const name = prompt('Character name:');
  if (!name) return;
  const team = (prompt('Team? townsfolk / outsider / minion / demon / traveller / fabled', 'townsfolk') || 'townsfolk').toLowerCase();
  const icon = prompt('Pick an emoji icon (you can upload art afterwards in Settings):', '⭐') || '⭐';
  const r = { id: 'c_' + uid(), name, team: TEAMS[team] ? team : 'townsfolk', icon, custom: true, firstNight: false, otherNight: false };
  state.customRoles.push(r);
  save();
  if (onPick) { onPick(r.id); closeSheet(); }
  else if (after) { after(); }
}

/* ============================================================
   Settings — customise art, icons & names (upload your own).
   Uploaded images are downscaled and stored ONLY on this device.
   ============================================================ */
function pickImageFile(onData) {
  const input = el('input', { type: 'file', accept: 'image/*', style: 'display:none' });
  input.addEventListener('change', () => {
    const file = input.files && input.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => downscale(reader.result, 256, onData);
    reader.readAsDataURL(file);
  });
  document.body.append(input);
  input.click();
  setTimeout(() => input.remove(), 0);
}
// Downscale to <= max px (keeps localStorage small) and return a JPEG/PNG dataURL.
function downscale(dataUrl, max, cb) {
  const img = new Image();
  img.onload = () => {
    const scale = Math.min(1, max / Math.max(img.width, img.height));
    const w = Math.round(img.width * scale), h = Math.round(img.height * scale);
    const c = el('canvas'); c.width = w; c.height = h;
    c.getContext('2d').drawImage(img, 0, 0, w, h);
    try { cb(c.toDataURL('image/png')); } catch (e) { cb(dataUrl); }
  };
  img.onerror = () => cb(dataUrl);
  img.src = dataUrl;
}

function setOverride(roleId, patch) {
  const cur = state.roleOverrides[roleId] || {};
  state.roleOverrides[roleId] = { ...cur, ...patch };
  save();
}
function clearOverride(roleId) { delete state.roleOverrides[roleId]; save(); }

function openSettings() {
  const teamsOrder = ['townsfolk', 'outsider', 'minion', 'demon', 'traveller', 'fabled'];
  const list = el('div', { class: 'settings-list' });
  const build = () => {
    list.innerHTML = '';
    teamsOrder.forEach(tk => {
      const roles = allRoles().filter(r => r.team === tk);
      if (!roles.length) return;
      list.append(el('div', { class: 'role-group-h', style: `--tc:${TEAMS[tk].color}` }, TEAMS[tk].label));
      roles.forEach(r => {
        const edited = !!state.roleOverrides[r.id];
        list.append(el('button', {
          class: `settings-item ${edited ? 'edited' : ''}`, style: `--tc:${TEAMS[r.team].color}`,
          onclick: () => openRoleEditor(r.id, build),
        }, roleIcon(r, 'ri-icon'), el('span', {}, r.name),
          edited ? el('span', { class: 'ri-tag' }, 'edited') : el('span', { class: 'ri-tag ghost' }, 'edit')));
      });
    });
  };
  const body = el('div', {},
    el('label', { class: 'toggle-line' },
      el('input', { type: 'checkbox', ...(state.showImages ? { checked: 'checked' } : {}),
        onchange: (e) => { state.showImages = e.target.checked; render(); } }),
      ' Show uploaded images (off = use emoji)'),
    el('button', { class: 'add-custom-role', onclick: () => addCustomRole(null, build) }, '➕ New custom character'),
    el('p', { class: 'tiny muted' }, 'Tap any character to upload your own art and edit its name or team. Images are stored only on this device, never uploaded anywhere.'),
    list,
    el('button', { class: 'danger-btn', onclick: () => {
      if (confirm('Remove ALL custom art and name edits?')) { state.roleOverrides = {}; render(); build(); }
    } }, '↺ Reset all customisations'));
  build();
  openSheet('Customise (Settings)', body);
}

function openRoleEditor(roleId, after) {
  const r = roleById(roleId);
  if (!r) return;
  const preview = el('div', { class: 'editor-preview' });
  const drawPreview = () => { preview.innerHTML = ''; preview.append(roleIcon(roleById(roleId), 'token-icon')); };
  drawPreview();

  const body = el('div', {},
    el('div', { class: 'editor-preview-wrap' }, preview),
    el('button', { class: 'wide-btn', onclick: () => pickImageFile((data) => { setOverride(roleId, { image: data }); drawPreview(); render(); }) }, '🖼️ Upload custom art'),
    (state.roleOverrides[roleId] && state.roleOverrides[roleId].image)
      ? el('button', { class: 'wide-btn', onclick: () => { const o = state.roleOverrides[roleId]; delete o.image; save(); drawPreview(); render(); } }, '🚫 Remove image (use emoji)')
      : el('div'),
    el('label', { class: 'field' }, 'Emoji (used when no image)',
      el('input', { type: 'text', value: r.icon || '', maxlength: '4',
        oninput: (e) => { setOverride(roleId, { icon: e.target.value }); drawPreview(); render(); } })),
    el('label', { class: 'field' }, 'Name / text',
      el('input', { type: 'text', value: r.name || '',
        oninput: (e) => { setOverride(roleId, { name: e.target.value }); render(); } })),
    el('div', { class: 'field' }, 'Team'),
    el('div', { class: 'team-row' },
      ...['townsfolk', 'outsider', 'minion', 'demon', 'traveller', 'fabled'].map(tk =>
        el('button', { class: `team-chip ${r.team === tk ? 'active' : ''}`, style: `--tc:${TEAMS[tk].color}`,
          onclick: () => { setOverride(roleId, { team: tk }); render(); openRoleEditor(roleId, after); } }, TEAMS[tk].label))),
    el('button', { class: 'danger-btn', onclick: () => { clearOverride(roleId); render(); closeSheet(); if (after) { openSettings(); } } }, '↺ Reset this character'),
    el('button', { class: 'wide-btn', onclick: () => { closeSheet(); openSettings(); } }, '‹ Back to list'),
  );
  openSheet(`Edit — ${r.name}`, body);
}

/* ============================================================
   Night order helper
   ============================================================ */
function currentNightOrder() {
  if (state.phase !== 'night') return [];
  const first = state.dayCount <= 1;
  return state.players
    .map(p => ({ p, role: p.roleId ? roleById(p.roleId) : null }))
    .filter(({ role }) => role && (first ? role.firstNight : role.otherNight))
    .map(({ p, role }) => ({ playerId: p.id, name: p.name, role }));
}

function openNightOrder() {
  const first = state.dayCount <= 1;
  const items = currentNightOrder();
  const body = el('div', {},
    el('p', { class: 'muted' }, first ? 'First night order' : 'Other nights order'),
    items.length
      ? el('ol', { class: 'night-order' }, ...items.map((it, i) => el('li', {},
          roleIcon(it.role, 'no-icon'),
          el('span', { class: 'no-name' }, it.role.name),
          el('span', { class: 'no-player muted' }, it.name || '—'))))
      : el('p', { class: 'muted' }, state.phase === 'night'
          ? 'No acting characters assigned for this night.'
          : 'Start the night to see the wake order.'),
    el('p', { class: 'tiny muted' }, 'Order follows seating & assigned characters. Reorder seats by dragging to fine-tune. This tool has no ability text — keep your rulebook handy.'),
  );
  openSheet(first ? 'First Night' : 'Night Order', body);
}

/* ============================================================
   Setup / seating
   ============================================================ */
function openSetup() {
  const body = el('div', {},
    el('div', { class: 'field' }, 'Quick seat a group'),
    el('div', { class: 'count-grid' },
      ...[5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15].map(nn =>
        el('button', { class: 'count-btn', onclick: () => seatCount(nn) }, String(nn)))),
    el('p', { class: 'muted tiny' }, 'Creates that many empty, numbered seats. You can rename each player and assign characters after.'),
    el('div', { class: 'field' }, 'Recommended composition'),
    el('div', { id: 'composition' }),
    el('hr'),
    el('button', { class: 'wide-btn', onclick: () => { addPlayer(); closeSheet(); } }, '➕ Add single player'),
    el('button', { class: 'wide-btn', onclick: () => shufflePlayers() }, '🔀 Shuffle seats'),
    el('button', { class: 'danger-btn', onclick: () => {
      if (confirm('Clear all players and reset the game?')) { state = DEFAULT_STATE(); closeSheet(); render(); }
    } }, '♻️ Reset game'),
  );
  openSheet('Setup', body);
  renderComposition();
}

// Standard BotC team composition by player count (functional game data).
const COMPOSITION = {
  5: [3, 0, 1, 1], 6: [3, 1, 1, 1], 7: [5, 0, 1, 1], 8: [5, 1, 1, 1], 9: [5, 2, 1, 1],
  10: [7, 0, 2, 1], 11: [7, 1, 2, 1], 12: [7, 2, 2, 1], 13: [9, 0, 3, 1], 14: [9, 1, 3, 1], 15: [9, 2, 3, 1],
};
function renderComposition() {
  const wrap = $('#composition');
  if (!wrap) return;
  const n = state.players.length;
  const c = COMPOSITION[n];
  wrap.innerHTML = '';
  if (!c) { wrap.append(el('span', { class: 'muted tiny' }, 'Seat 5–15 players to see the recommended split.')); return; }
  const [tf, out, min, dem] = c;
  const chip = (label, val, tk) => el('span', { class: 'comp-chip', style: `--tc:${TEAMS[tk].color}` }, `${label}: ${val}`);
  wrap.append(chip('Townsfolk', tf, 'townsfolk'), chip('Outsiders', out, 'outsider'),
    chip('Minions', min, 'minion'), chip('Demon', dem, 'demon'));
}

function seatCount(n) {
  state.players = Array.from({ length: n }, (_, i) => makePlayer(`Player ${i + 1}`));
  closeSheet(); render();
}
function makePlayer(name) {
  return { id: uid(), name: name || '', roleId: null, dead: false, ghostVote: undefined, reminders: [] };
}
function addPlayer() {
  state.players.push(makePlayer(`Player ${state.players.length + 1}`));
  render();
}
function shufflePlayers() {
  for (let i = state.players.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [state.players[i], state.players[j]] = [state.players[j], state.players[i]];
  }
  closeSheet(); render();
}

/* ============================================================
   Phase controls
   ============================================================ */
function toNight() {
  if (state.phase === 'setup') state.dayCount = 1;
  else if (state.phase === 'day') state.dayCount += 1;
  state.phase = 'night';
  render();
  openNightOrder();
}
function toDay() {
  if (state.phase === 'setup') state.dayCount = 1;
  state.phase = 'day';
  render();
}

/* ============================================================
   Bottom sheet primitives
   ============================================================ */
function openSheet(title, bodyNode) {
  const sheet = $('#sheet');
  $('#sheet-title').textContent = title;
  const body = $('#sheet-body');
  body.innerHTML = '';
  body.append(bodyNode);
  sheet.classList.add('open');
  $('#scrim').classList.add('show');
}
function closeSheet() {
  $('#sheet').classList.remove('open');
  $('#scrim').classList.remove('show');
  refreshSheet._current = null;
}

/* ============================================================
   Data export / import (share a game between phones)
   ============================================================ */
function exportGame() {
  const data = btoa(unescape(encodeURIComponent(JSON.stringify(state))));
  navigator.clipboard?.writeText(data).then(
    () => alert('Game state copied to clipboard. Paste it on another device via Menu → Import.'),
    () => prompt('Copy this game code:', data));
}
function importGame() {
  const code = prompt('Paste game code:');
  if (!code) return;
  try {
    state = JSON.parse(decodeURIComponent(escape(atob(code.trim()))));
    if (!state.bluffs) state.bluffs = [null, null, null];
    if (!state.customRoles) state.customRoles = [];
    render();
  } catch (e) { alert('That code could not be read.'); }
}

function openMenu() {
  const body = el('div', {},
    el('button', { class: 'wide-btn', onclick: () => { closeSheet(); openSetup(); } }, '⚙️ Setup / players'),
    el('button', { class: 'wide-btn', onclick: () => { closeSheet(); openSettings(); } }, '🎨 Customise art & names'),
    el('button', { class: 'wide-btn', onclick: () => { closeSheet(); openNightOrder(); } }, '🌙 Night order'),
    el('button', { class: 'wide-btn', onclick: () => { exportGame(); } }, '📤 Export game (copy)'),
    el('button', { class: 'wide-btn', onclick: () => { importGame(); } }, '📥 Import game (paste)'),
    el('hr'),
    el('p', { class: 'tiny muted' },
      'Unofficial fan companion for the social-deduction game by The Pandemonium Institute. ' +
      'No official art or ability text is included — bring your rulebook. Everything is stored ' +
      'only on this device.'),
  );
  openSheet('Menu', body);
}

/* ============================================================
   Wire up static controls
   ============================================================ */
function bind() {
  $('#btn-menu').addEventListener('click', openMenu);
  $('#btn-setup').addEventListener('click', openSetup);
  $('#btn-add').addEventListener('click', addPlayer);
  $('#btn-night').addEventListener('click', toNight);
  $('#btn-day').addEventListener('click', toDay);
  $('#btn-nightorder').addEventListener('click', openNightOrder);
  $('#sheet-close').addEventListener('click', closeSheet);
  $('#scrim').addEventListener('click', closeSheet);
  window.addEventListener('resize', () => renderCircle());
}

bind();
render();

// Register service worker for offline / installable use.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => navigator.serviceWorker.register('./service-worker.js').catch(() => {}));
}
