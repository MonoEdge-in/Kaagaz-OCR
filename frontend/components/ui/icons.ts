// The drawings behind <Icon>. Every icon is a few simple strokes on a 24 x 24
// grid, drawn for Kaagaz (not copied from an icon set). They are data, not
// markup strings: Icon.tsx turns each shape into a React SVG element, so no
// HTML is ever parsed from text.

export const ICON_NAMES = [
  "documents",
  "chat",
  "review",
  "admin",
  "menu",
  "close",
  "sun",
  "moon",
  "monitor",
  "upload",
  "download",
  "file",
  "search",
  "send",
  "plus",
  "check-seal",
  "eye-alert",
  "thumbs-up",
  "thumbs-down",
  "open",
  "chevron-left",
  "chevron-right",
  "zoom-in",
  "zoom-out",
  "trash",
  "alert",
  "logout",
  "search-off",
  "refresh",
  "history",
  "scan-text",
  "words",
  "to-top",
  "more",
  "home",
] as const;

export type IconName = (typeof ICON_NAMES)[number];

export type IconShape =
  | { kind: "path"; d: string }
  | { kind: "circle"; cx: number; cy: number; r: number }
  | { kind: "rect"; x: number; y: number; width: number; height: number; rx: number };

const path = (d: string): IconShape => ({ kind: "path", d });
const circle = (cx: number, cy: number, r: number): IconShape => ({ kind: "circle", cx, cy, r });
const rect = (x: number, y: number, width: number, height: number, rx: number): IconShape => ({
  kind: "rect",
  x,
  y,
  width,
  height,
  rx,
});

/** A seal: a star of `points` tips around the centre, alternating two radii. */
export function sealPath(points: number, outer: number, inner: number, cx = 12, cy = 12): string {
  const steps = points * 2;
  const corners: string[] = [];
  for (let i = 0; i < steps; i += 1) {
    const angle = -Math.PI / 2 + (i * Math.PI) / points;
    const radius = i % 2 === 0 ? outer : inner;
    const x = cx + radius * Math.cos(angle);
    const y = cy + radius * Math.sin(angle);
    corners.push(`${i === 0 ? "M" : "L"}${x.toFixed(2)} ${y.toFixed(2)}`);
  }
  return `${corners.join("")}Z`;
}

// A magnifying glass: the lens and the handle, shared by search and zoom.
const LENS = [circle(11, 11, 6.5), path("M16 16l4.5 4.5")];
// An eye: the outline, shared by eye, eye-off and eye-alert.
const EYE = path("M2.5 12C4.5 8 8 5.5 12 5.5s7.5 2.5 9.5 6.5c-2 4-5.5 6.5-9.5 6.5S4.5 16 2.5 12Z");

export const ICONS: Record<IconName, readonly IconShape[]> = {
  // A house: the roof and the walls with a door (the dashboard, File management D1).
  home: [path("M3.5 11L12 4l8.5 7"), path("M6 9.5V20h12V9.5"), path("M10 20v-5h4v5")],
  documents: [
    path("M8 3h7l4 4v11a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z"),
    path("M15 3v4h4"),
    path("M4 7v12a2 2 0 0 0 2 2h9"),
  ],
  chat: [path("M4 5h16a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1h-10l-5 4v-4H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1Z")],
  review: [
    path("M15 5h3a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h3"),
    rect(9, 3.5, 6, 3, 1),
    path("M8.5 14l2.5 2.5 4.5-5"),
  ],
  admin: [
    path("M4 7h10"),
    path("M18 7h2"),
    circle(16, 7, 2),
    path("M4 17h4"),
    path("M12 17h8"),
    circle(10, 17, 2),
  ],
  menu: [path("M4 7h16"), path("M4 12h16"), path("M4 17h16")],
  close: [path("M6 6l12 12"), path("M18 6L6 18")],
  sun: [
    circle(12, 12, 4),
    path("M12 2.5v2"),
    path("M12 19.5v2"),
    path("M2.5 12h2"),
    path("M19.5 12h2"),
    path("M5.3 5.3l1.4 1.4"),
    path("M17.3 17.3l1.4 1.4"),
    path("M5.3 18.7l1.4-1.4"),
    path("M17.3 6.7l1.4-1.4"),
  ],
  moon: [path("M20 14.5A8 8 0 1 1 9.5 4a7.5 7.5 0 0 0 10.5 10.5Z")],
  monitor: [rect(3, 4, 18, 12, 1.5), path("M8 20h8"), path("M12 16v4")],
  upload: [
    path("M12 15V4"),
    path("M7.5 8.5L12 4l4.5 4.5"),
    path("M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"),
  ],
  download: [
    path("M12 4v11"),
    path("M7.5 10.5L12 15l4.5-4.5"),
    path("M4 15v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4"),
  ],
  file: [
    path("M6 3h8l5 5v12a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z"),
    path("M14 3v5h5"),
    path("M8.5 13h7"),
    path("M8.5 17h5"),
  ],
  search: LENS,
  send: [path("M20 4L3 11l7 3 3 7Z"), path("M10 14L20 4")],
  plus: [path("M12 5v14"), path("M5 12h14")],
  "check-seal": [path(sealPath(8, 9.5, 8)), path("M8.6 12.2l2.3 2.3 4.4-4.6")],
  "eye-alert": [EYE, path("M12 9v3"), path("M12 15v0.01")],
  "thumbs-up": [
    path("M7 11v9H4a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1h3Z"),
    path("M7 11l4-7a2 2 0 0 1 2.5 1.8L13 10h5.5a2 2 0 0 1 2 2.3l-1.2 6a2 2 0 0 1-2 1.7H7"),
  ],
  "thumbs-down": [
    path("M7 13V4H4a1 1 0 0 0-1 1v7a1 1 0 0 0 1 1h3Z"),
    path("M7 13l4 7a2 2 0 0 0 2.5-1.8L13 14h5.5a2 2 0 0 0 2-2.3l-1.2-6a2 2 0 0 0-2-1.7H7"),
  ],
  open: [
    path("M14 4h6v6"),
    path("M20 4l-9 9"),
    path("M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"),
  ],
  "chevron-left": [path("M15 5l-7 7 7 7")],
  "chevron-right": [path("M9 5l7 7-7 7")],
  "zoom-in": [...LENS, path("M8 11h6"), path("M11 8v6")],
  "zoom-out": [...LENS, path("M8 11h6")],
  trash: [
    path("M4 7h16"),
    path("M9 7V4.5a0.5 0.5 0 0 1 0.5-0.5h5a0.5 0.5 0 0 1 0.5 0.5V7"),
    path("M6 7l1 12.1a1 1 0 0 0 1 0.9h8a1 1 0 0 0 1-0.9L18 7"),
    path("M10 11v5"),
    path("M14 11v5"),
  ],
  alert: [path("M12 3.5L2.5 20h19Z"), path("M12 10v4.5"), path("M12 17.3v0.01")],
  logout: [
    path("M10 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h5"),
    path("M15 8l4 4-4 4"),
    path("M19 12H9"),
  ],
  "search-off": [...LENS, path("M9 9l4 4"), path("M13 9l-4 4")],
  refresh: [
    path("M20 11a8 8 0 0 0-14.3-4.3L4 8.5"),
    path("M4 4v4.5h4.5"),
    path("M4 13a8 8 0 0 0 14.3 4.3L20 15.5"),
    path("M20 20v-4.5h-4.5"),
  ],
  history: [
    path("M4 12a8 8 0 1 0 2.3-5.7L4 8.6"),
    path("M4 4.5v4.1h4.1"),
    path("M12 8v4.5l3 2"),
  ],
  // Scanned text: corner brackets (a scan's frame) around lines of text.
  "scan-text": [
    path("M4 8V5a1 1 0 0 1 1-1h3"),
    path("M16 4h3a1 1 0 0 1 1 1v3"),
    path("M20 16v3a1 1 0 0 1-1 1h-3"),
    path("M8 20H5a1 1 0 0 1-1-1v-3"),
    path("M8 9h8"),
    path("M8 12h8"),
    path("M8 15h5"),
  ],
  // An open book with a line of text on each page: my words.
  words: [
    path("M12 6.5C10 5 7 4.5 3.5 5v13c3.5-0.5 6.5 0 8.5 1.5 2-1.5 5-2 8.5-1.5V5C17 4.5 14 5 12 6.5Z"),
    path("M12 6.5v13"),
    path("M6 9h3.5"),
    path("M14.5 9H18"),
  ],
  // A bar with an arrow rising to it: put this source on top (ranked sources, 16 Oct 2026).
  "to-top": [path("M5 4h14"), path("M12 20V8"), path("M7 13l5-5 5 5")],
  // Three dots in a row: more actions for this row (Ask screen polish, 17 Oct 2026).
  more: [circle(5.5, 12, 1), circle(12, 12, 1), circle(18.5, 12, 1)],
};
