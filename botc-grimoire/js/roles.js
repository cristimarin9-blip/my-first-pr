// Character data for the Grimoire companion tool.
//
// IMPORTANT (IP note): This is an UNOFFICIAL fan-made companion. To keep it
// safe and respectful of the creators' rights, this file contains ONLY
// functional game data: character names, their team, a placeholder emoji
// icon (NOT the official artwork), and night-order flags used by the night
// helper. It deliberately contains NO ability/rules text. Buy the real game
// from The Pandemonium Institute for the rulebook, art, and abilities.
//
// Storytellers can also add fully custom characters from the app UI.

export const TEAMS = {
  townsfolk: { label: 'Townsfolk', color: '#2f6df6', ring: '#5b8dff' },
  outsider: { label: 'Outsider', color: '#12a5a5', ring: '#3fd0d0' },
  minion: { label: 'Minion', color: '#e0662b', ring: '#ff8f4d' },
  demon: { label: 'Demon', color: '#c0392b', ring: '#ff5a45' },
  traveller: { label: 'Traveller', color: '#8e44ad', ring: '#b06fd6' },
  fabled: { label: 'Fabled', color: '#c9a227', ring: '#efc94c' },
};

// Night-order helpers use `firstNight` and `otherNight` booleans to decide
// whether a role appears in each list; ordering follows array order which
// Storytellers can reorder live in the app.
export const ROLES = [
  // ---- Trouble Brewing (starter edition) : Townsfolk ----
  { id: 'washerwoman', name: 'Washerwoman', team: 'townsfolk', icon: '🧺', firstNight: true, otherNight: false, edition: 'tb' },
  { id: 'librarian', name: 'Librarian', team: 'townsfolk', icon: '📚', firstNight: true, otherNight: false, edition: 'tb' },
  { id: 'investigator', name: 'Investigator', team: 'townsfolk', icon: '🔎', firstNight: true, otherNight: false, edition: 'tb' },
  { id: 'chef', name: 'Chef', team: 'townsfolk', icon: '🍳', firstNight: true, otherNight: false, edition: 'tb' },
  { id: 'empath', name: 'Empath', team: 'townsfolk', icon: '💗', firstNight: true, otherNight: true, edition: 'tb' },
  { id: 'fortuneteller', name: 'Fortune Teller', team: 'townsfolk', icon: '🔮', firstNight: true, otherNight: true, edition: 'tb' },
  { id: 'undertaker', name: 'Undertaker', team: 'townsfolk', icon: '⚰️', firstNight: false, otherNight: true, edition: 'tb' },
  { id: 'monk', name: 'Monk', team: 'townsfolk', icon: '🙏', firstNight: false, otherNight: true, edition: 'tb' },
  { id: 'ravenkeeper', name: 'Ravenkeeper', team: 'townsfolk', icon: '🐦‍⬛', firstNight: false, otherNight: true, edition: 'tb' },
  { id: 'virgin', name: 'Virgin', team: 'townsfolk', icon: '🕊️', firstNight: false, otherNight: false, edition: 'tb' },
  { id: 'slayer', name: 'Slayer', team: 'townsfolk', icon: '🗡️', firstNight: false, otherNight: false, edition: 'tb' },
  { id: 'soldier', name: 'Soldier', team: 'townsfolk', icon: '🛡️', firstNight: false, otherNight: false, edition: 'tb' },
  { id: 'mayor', name: 'Mayor', team: 'townsfolk', icon: '🎩', firstNight: false, otherNight: false, edition: 'tb' },

  // ---- Trouble Brewing : Outsiders ----
  { id: 'butler', name: 'Butler', team: 'outsider', icon: '🤵', firstNight: true, otherNight: true, edition: 'tb' },
  { id: 'drunk', name: 'Drunk', team: 'outsider', icon: '🍺', firstNight: false, otherNight: false, edition: 'tb' },
  { id: 'recluse', name: 'Recluse', team: 'outsider', icon: '🏚️', firstNight: false, otherNight: false, edition: 'tb' },
  { id: 'saint', name: 'Saint', team: 'outsider', icon: '😇', firstNight: false, otherNight: false, edition: 'tb' },

  // ---- Trouble Brewing : Minions ----
  { id: 'poisoner', name: 'Poisoner', team: 'minion', icon: '🧪', firstNight: true, otherNight: true, edition: 'tb' },
  { id: 'spy', name: 'Spy', team: 'minion', icon: '🕵️', firstNight: true, otherNight: true, edition: 'tb' },
  { id: 'scarletwoman', name: 'Scarlet Woman', team: 'minion', icon: '💃', firstNight: false, otherNight: true, edition: 'tb' },
  { id: 'baron', name: 'Baron', team: 'minion', icon: '🎭', firstNight: false, otherNight: false, edition: 'tb' },

  // ---- Trouble Brewing : Demon ----
  { id: 'imp', name: 'Imp', team: 'demon', icon: '👹', firstNight: false, otherNight: true, edition: 'tb' },
];

// Generic reminder / status tokens available to attach to any player.
// Kept generic on purpose (no per-character reminder wording).
export const REMINDERS = [
  { id: 'poisoned', label: 'Poisoned', icon: '🧪', color: '#7b2fbf' },
  { id: 'drunk', label: 'Drunk', icon: '🍺', color: '#b7791f' },
  { id: 'dead', label: 'Dead', icon: '💀', color: '#555' },
  { id: 'protected', label: 'Protected', icon: '🛡️', color: '#2f6df6' },
  { id: 'used', label: 'Used ability', icon: '✔️', color: '#2f9e44' },
  { id: 'red-herring', label: 'Red Herring', icon: '🐟', color: '#c0392b' },
  { id: 'is-the-demon', label: 'Marked', icon: '🎯', color: '#c0392b' },
  { id: 'wrong', label: 'Is Wrong', icon: '❌', color: '#c0392b' },
  { id: 'no-ability', label: 'No Ability', icon: '🚫', color: '#666' },
  { id: 'good', label: 'Good', icon: '🔵', color: '#2f6df6' },
  { id: 'evil', label: 'Evil', icon: '🔴', color: '#c0392b' },
  { id: 'note', label: 'Note', icon: '📝', color: '#333' },
];
