// The motion pass (16 Oct 2026; MonoEdge brief C, rendering): every transition and animation
// in the CSS moves only transform or opacity, on the --ease curve, in 120 to 240ms (a loop
// only to show work in progress); reduced motion makes all of it instant; the screens that
// should move carry their motion classes; script motion goes through lib/motion.ts only.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { barFrames, EASE, play } from "@/lib/motion";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const files = (dir: string, ending: RegExp): string[] =>
  readdirSync(join(ROOT, dir)).flatMap((name) => {
    const path = `${dir}/${name}`;
    return statSync(join(ROOT, path)).isDirectory() ? files(path, ending) : ending.test(name) ? [path] : [];
  });
const CSS = [...files("app", /\.css$/), ...files("components", /\.css$/)];
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");

/** Each declaration of `property` in a file, with the first selector of its rule. */
function declarations(text: string, property: RegExp): { selector: string; value: string }[] {
  const out: { selector: string; value: string }[] = [];
  for (const [, selector, body] of text.matchAll(/([^{}]*)\{([^{}]*)\}/g)) {
    for (const [, name, value] of (body ?? "").matchAll(/(?:^|;)\s*([\w-]+)\s*:([^;]*)/g)) {
      if (property.test(name ?? "")) out.push({ selector: (selector ?? "").split(",")[0]?.trim() ?? "", value: (value ?? "").trim() });
    }
  }
  return out;
}

/** "a 1s, b 2s" as ["a 1s", "b 2s"], keeping commas inside brackets. */
const items = (value: string) => value.split(/,(?![^(]*\))/).map((item) => item.trim());
const ms = (item: string) => {
  const time = /(?:^|\s)(\d*\.?\d+)(ms|s)\b/.exec(item);
  return time ? Number(time[1]) * (time[2] === "s" ? 1000 : 1) : null;
};
const tokenMs = (name: string) => Number(new RegExp(`${name}:\\s*(\\d+)ms;`).exec(read("app/globals.css"))?.[1]);
const durationOf = (item: string) => {
  const token = /var\((--(?:t|motion)-[\w-]+)\)/.exec(item)?.[1];
  return token ? tokenMs(token) : ms(item);
};
// Changed on purpose (document panel UX, 25 Oct 2026): the check that draws in once when the
// status line turns to "Everything is checked" moves its stroke (an SVG's dash), over 400ms.
const DRAW = "app/(app)/documents/[id]/WordReview.module.css";

// Not motion: held off for good, or a delayed show.
const NOT_MOTION = [
  "app/globals.css: input:-webkit-autofill", // the autofill colour, held off for ever
  "app/globals.css: .delayed", // shown after 300ms, in no time
];
const LOOPS = /\binfinite\b/; // only work in progress (layout-guards.test.ts holds the list)

describe("motion in the CSS", () => {
  it("transitions only transform and opacity (display and overlay only to let a popover fade out)", () => {
    const found: string[] = [];
    for (const file of CSS) {
      for (const { selector, value } of declarations(css(file), /^transition(-property)?$/)) {
        if (NOT_MOTION.includes(`${file}: ${selector}`) || value === "none") continue;
        for (const item of items(value)) {
          const property = item.split(/\s+/)[0] ?? "";
          const discrete = /^(display|overlay)$/.test(property) && /\ballow-discrete\b/.test(item);
          if (!/^(transform|opacity)$/.test(property) && !discrete) found.push(`${file}: ${selector}: ${item}`);
        }
      }
    }
    expect(found).toEqual([]);
  });

  it("keyframes set only transform and opacity: no layout, paint or filter property moves", () => {
    const found: string[] = [];
    for (const file of CSS) {
      for (const [, name, body] of css(file).matchAll(/@keyframes ([\w-]+)\s*\{([\s\S]*?\})\s*\}/g)) {
        if (file === "app/globals.css" && name === "appear") continue; // .delayed's 0s show
        if (file === DRAW && name === "draw-check" && /^\s*from\s*\{\s*stroke-dashoffset:[^;]*;\s*\}$/.test(body ?? "")) continue;
        for (const [, property] of (body ?? "").matchAll(/([\w-]+)\s*:/g)) {
          if (!/^(transform|opacity)$/.test(property ?? "")) found.push(`${file}: @keyframes ${name}: ${property}`);
        }
      }
    }
    expect(found).toEqual([]);
  });

  // Changed on purpose (document panel UX, 25 Oct 2026): the panel's motion tokens, 160 and 280ms
  // on an ease-out curve, and the 400ms check drawn once (DRAW).
  it("runs every move in 120 to 280ms on the --ease or --ease-out curve, loops and the drawn check aside", () => {
    expect(read("app/globals.css")).toMatch(/--ease: cubic-bezier\(0\.2, 0, 0\.2, 1\);/);
    expect(read("app/globals.css")).toMatch(/--ease-out: cubic-bezier\(0\.2, 0\.8, 0\.2, 1\);/);
    expect([tokenMs("--t-fast"), tokenMs("--t-med"), tokenMs("--t-slow"), tokenMs("--t-drawer")]).toEqual([120, 160, 200, 220]);
    expect([tokenMs("--motion-quick"), tokenMs("--motion-calm"), tokenMs("--motion-draw")]).toEqual([160, 280, 400]);
    const found: string[] = [];
    for (const file of CSS) {
      for (const { selector, value } of declarations(css(file), /^(transition|animation)$/)) {
        if (NOT_MOTION.includes(`${file}: ${selector}`) || value === "none" || LOOPS.test(value)) continue;
        if (file === DRAW && value === "draw-check var(--motion-draw) var(--ease-out) both") continue;
        for (const item of items(value)) {
          const duration = durationOf(item);
          const eased = /var\(--ease(-out)?\)|cubic-bezier\(0\.2, 0, 0\.2, 1\)/.test(item) || /allow-discrete/.test(item);
          if (duration === null || duration < 120 || duration > 280 || !eased) found.push(`${file}: ${selector}: ${item}`);
        }
      }
    }
    expect(found).toEqual([]);
  });

  it("makes every move instant under reduced motion, dialog backdrops too", () => {
    const text = css("app/globals.css");
    const reduced = /@media \(prefers-reduced-motion: reduce\) \{\s*\*,\s*\*::before,\s*\*::after,\s*::backdrop \{([^}]*)\}/.exec(text)?.[1] ?? "";
    expect(reduced).toMatch(/animation-duration: 0\.01ms !important;/);
    expect(reduced).toMatch(/animation-iteration-count: 1 !important;/);
    expect(reduced).toMatch(/transition-duration: 0\.01ms !important;/);
  });

  it("never sets will-change ahead of time (the browser promotes a running transform)", () => {
    expect(CSS.filter((file) => /will-change/.test(css(file)))).toEqual([]);
  });

  it("opens menus from their edge and lets a popover fade out quicker than it came", () => {
    const text = css("app/globals.css");
    expect(text).toMatch(/\.motion-pop \{\s*--motion-y: var\(--pop-y, -4px\);\s*animation: motion-in var\(--t-med\) var\(--ease\);/);
    expect(text).toMatch(/\.motion-pop\[popover\]:not\(:popover-open\) \{\s*opacity: 0;\s*transform: translateY\(var\(--motion-y\)\);/);
    expect(text).toMatch(/\.toast\[data-leaving\] \{\s*opacity: 0;\s*pointer-events: none;/);
    expect(css("components/chat/Chat.module.css")).toMatch(/\.menu \{\s*--pop-y: 4px;/); // opens above the composer
  });
});

describe("motion on the screens", () => {
  const has = (path: string, text: string) => expect(read(path), path).toContain(text);

  it("gives menus, panels, answers and the next review card their motion classes", () => {
    for (const path of [
      "components/shell/AccountMenu.tsx",
      "components/admin/RowMenu.tsx",
      "components/chat/ComposerMenu.tsx",
      "components/documents/DocumentActions.tsx",
      "components/documents/UploadArea.tsx",
    ]) {
      has(path, "motion-pop");
    }
    has("components/documents/LibraryFilters.tsx", "motion-fade");
    has("components/chat/AnswerView.tsx", "motion-rise");
    has("components/review/ReviewCard.tsx", "onSkip ? `card motion-next");
    has("components/ui/Toast.tsx", "data-leaving={leaving || undefined}");
  });

  it("slides the tab bar and fades the panel on both MonoEdge tab sets", () => {
    has("components/admin/Tabs.tsx", "useTabMotion(listRef, active);");
    has("app/(app)/documents/[id]/DocumentView.tsx", "useTabMotion(list, active);");
    expect(css("app/globals.css")).toMatch(/\.tab\[aria-selected="true"\]::after \{[^}]*transform-origin: left;/);
    expect(css("components/admin/Admin.module.css")).toMatch(/\.tab\[aria-selected="true"\]::after \{[^}]*transform-origin: left;/);
  });

  it("fades in a new row, a drawn page and a zoom in place, through lib/motion.ts only", () => {
    has("components/documents/DocumentsTable.tsx", "play(row, FADE_IN, MOTION_MS.med)");
    has("components/viewer/PageViewer.tsx", "if (drawn) play(stageRef.current, FADE_IN, MOTION_MS.med);");
    has("components/viewer/PageViewer.tsx", "transform: `scale(${was / zoom})`");
    const direct = [...files("app", /\.tsx?$/), ...files("components", /\.tsx?$/)].filter((path) =>
      /\.animate\(/.test(read(path)),
    );
    expect(direct).toEqual([]);
  });
});

describe("lib/motion.ts", () => {
  afterEach(() => vi.unstubAllGlobals());
  const reduce = (on: boolean) => vi.stubGlobal("window", { matchMedia: () => ({ matches: on }) });

  it("plays transform and opacity frames on the --ease curve", () => {
    reduce(false);
    const animate = vi.fn();
    play({ animate } as unknown as Element, [{ opacity: 0 }, { opacity: 1 }], 160, "::after");
    expect(animate).toHaveBeenCalledWith([{ opacity: 0 }, { opacity: 1 }], { duration: 160, easing: EASE, pseudoElement: "::after" });
    expect(EASE).toBe("cubic-bezier(0.2, 0, 0.2, 1)");
  });

  it("moves nothing under reduced motion, or without an element or the API", () => {
    reduce(true);
    const animate = vi.fn();
    play({ animate } as unknown as Element, [{ opacity: 0 }], 160);
    expect(animate).not.toHaveBeenCalled();
    reduce(false);
    expect(() => play(null, [{ opacity: 0 }], 160)).not.toThrow();
    expect(() => play({} as Element, [{ opacity: 0 }], 160)).not.toThrow();
  });

  it("moves the tab bar from where it was, as a translate and a stretch", () => {
    expect(barFrames({ left: 10, width: 40 }, { left: 90, width: 80 })).toEqual([
      { transform: "translateX(-80px) scaleX(0.5)" },
      { transform: "none" },
    ]);
    expect(barFrames({ left: 10, width: 40 }, { left: 10, width: 40 })).toBeNull();
    expect(barFrames({ left: 10, width: 40 }, { left: 90, width: 0 })).toBeNull();
  });
});
