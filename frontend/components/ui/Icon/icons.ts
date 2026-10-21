// The handoff's icon set (design/kaagaz-ui-handoff/screens/system/Main.html,
// "Icons"), copied path for path: a 24 unit grid drawn with a 1.6 stroke. Each
// icon is one path, plus a circle for the ones the handoff draws with one. The
// last ten are drawn on the handoff's screens but missing from that sheet; each
// names the screen it is copied from.

export type KIconShape = { d: string; circle?: { cx: number; cy: number; r: number }; stroke?: number };

export const K_ICONS = {
  home: { d: "M3 10.5 12 3l9 7.5V21h-6v-6H9v6H3z" },
  documents: { d: "M14 3H6v18h12V7zM14 3v4h4M9 12h6M9 16h6" },
  folder: { d: "M3 6h6l2 2h10v11H3z" },
  ask: { d: "M4 5h16v11H9l-5 4zM8 9h8M8 12h5" },
  review: { d: "M4 4h16v16H4zM8 12l3 3 5-6" },
  words: { d: "M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3zM5 17a3 3 0 0 1 3-3h11" },
  admin: { d: "M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" },
  search: { d: "M20 20l-4.5-4.5", circle: { cx: 11, cy: 11, r: 6 } },
  upload: { d: "M12 15V4M7 9l5-5 5 5M4 15v5h16v-5" },
  download: { d: "M12 4v11M7 10l5 5 5-5M4 20h16" },
  bell: { d: "M6 16v-5a6 6 0 0 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0" },
  check: { d: "M5 12l5 5 9-10" },
  "check-circle": { d: "M8 12l3 3 5-6", circle: { cx: 12, cy: 12, r: 9 } },
  alert: { d: "M12 4 2.5 20h19zM12 10v4M12 17v.5" },
  info: { d: "M12 11v5M12 8v.5", circle: { cx: 12, cy: 12, r: 9 } },
  question: { d: "M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5V14M12 17v.5", circle: { cx: 12, cy: 12, r: 9 } },
  "not-found": { d: "M8 12h8", circle: { cx: 12, cy: 12, r: 9 } },
  x: { d: "M6 6l12 12M18 6 6 18" },
  "chev-right": { d: "M9 6l6 6-6 6" },
  "chev-down": { d: "M6 9l6 6 6-6" },
  "arrow-right": { d: "M5 12h14M13 6l6 6-6 6" },
  send: { d: "M12 19V5M5 12l7-7 7 7" },
  versions: { d: "M12 3 3 8l9 5 9-5zM3 13l9 5 9-5" },
  clock: { d: "M12 7v5l3 2", circle: { cx: 12, cy: 12, r: 9 } },
  tag: { d: "M3 12V4h8l10 10-8 8z", circle: { cx: 7.5, cy: 7.5, r: 1 } },
  trash: { d: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" },
  move: { d: "M3 6h6l2 2h10v11H3zM10 13h6M14 11l2 2-2 2" },
  "read-again": { d: "M20 12a8 8 0 1 1-2.3-5.7M20 4v4h-4" },
  "zoom-in": { d: "M20 20l-4.5-4.5M11 8v6M8 11h6", circle: { cx: 11, cy: 11, r: 6 } },
  grid: { d: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z" },
  list: { d: "M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01" },
  filter: { d: "M4 5h16l-6 8v6l-4 2v-8z" },
  user: { d: "M4 21a8 8 0 0 1 16 0", circle: { cx: 12, cy: 8, r: 4 } },
  lock: { d: "M6 11h12v10H6zM8 11V7a4 4 0 0 1 8 0v4" },
  key: { d: "M11 12l9-9M17 6l3 3", circle: { cx: 8, cy: 15, r: 4 } },
  eye: { d: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z", circle: { cx: 12, cy: 12, r: 3 } },
  copy: { d: "M8 8h12v12H8zM4 16V4h12" },
  keyboard: { d: "M3 6h18v12H3zM7 10h.01M11 10h.01M15 10h.01M7 14h10" },
  settings: { d: "M12 2v3M12 19v3M4.9 4.9 7 7M17 17l2.1 2.1M2 12h3M19 12h3M4.9 19.1 7 17M17 7l2.1-2.1", circle: { cx: 12, cy: 12, r: 3 } },
  plus: { d: "M12 5v14M5 12h14" },
  "sign-out": { d: "M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10" },
  offline: { d: "M3 3l18 18M8.5 16a5 5 0 0 1 7 0M5 12.5a10 10 0 0 1 3.5-2.2M19 12.5a10 10 0 0 0-3-2M2 8.5a15 15 0 0 1 4.5-2.8M22 8.5A15 15 0 0 0 10.5 5M12 19.5h.01" },
  link: { d: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" },
  compare: { d: "M4 4h7v16H4zM13 4h7v16h-7z" },
  department: { d: "M4 21V5l8-3 8 3v16M9 21v-4h6v4M8 9h.01M12 9h.01M16 9h.01M8 13h.01M12 13h.01M16 13h.01" },
  sheet: { d: "M4 4h16v16H4zM4 10h16M4 15h16M10 4v16" },
  camera: { d: "M4 8h3l2-3h6l2 3h3v11H4z", circle: { cx: 12, cy: 13, r: 3.5 } },
  undo: { d: "M9 14 4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3" },
  "open-spot": { d: "M4 4h16v16H4zM9 9h6v6H9z" },
  chart: { d: "M4 20V4M4 20h16M8 16v-5M12 16V8M16 16v-3" },
  handwriting: { d: "M4 18c3-6 5-9 7-9s-1 8 2 8 3-5 5-5 2 3 2 3" },
  server: { d: "M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01" },
  // Not on the Main.html sheet; copied from the screens that draw them.
  more: { d: "M5 12h.01M12 12h.01M19 12h.01", stroke: 3 }, // Components.html, "More actions"
  file: { d: "M14 3H6v18h12V7zM14 3v4h4" }, // Components.html, the From documents badge
  "chev-left": { d: "M15 6l-6 6 6 6" }, // DocumentPage.html, page back
  menu: { d: "M4 6h16M4 12h16M4 18h16" }, // PhoneHome.html, the phone menu
  "zoom-out": { d: "M20 20l-4.5-4.5M8 11h6", circle: { cx: 11, cy: 11, r: 6 } }, // DocumentPage.html
  external: { d: "M7 17 17 7M8 7h9v9" }, // Home.html, open a department
  calendar: { d: "M4 6h16v14H4zM4 10h16M8 3v4M16 3v4" }, // Components.html, Document date
  sun: { d: "M12 2v2M12 20v2M4 12H2M22 12h-2M5 5l1.5 1.5M17.5 17.5 19 19M5 19l1.5-1.5M17.5 6.5 19 5", circle: { cx: 12, cy: 12, r: 4 } }, // Components.html, Theme
  moon: { d: "M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" }, // Components.html, Theme
  unlock: { d: "M6 11h12v10H6zM8 11V7a4 4 0 0 1 7.5-2" }, // Admin.html
} satisfies Record<string, KIconShape>;

export type KIconName = keyof typeof K_ICONS;

export const K_ICON_NAMES = Object.keys(K_ICONS) as KIconName[];
