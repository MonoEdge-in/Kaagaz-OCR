// One control height and centred icons (Tushar, 16 Oct 2026: "buttons and icons up and down
// in very bad symmetry"). Each guard reads the CSS source, so a rule that brings an old bug back
// fails here: a field taller than the button beside it, an icon riding on the text's baseline,
// a screen hard-coding its own control height.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

interface Rule {
  at: string;
  selector: string;
  body: string;
}

/** The style rules of a CSS file (no nesting, no braces in strings: enough for this repo). */
function rules(css: string): Rule[] {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const out: Rule[] = [];
  const stack: string[] = [];
  let start = 0;
  for (let i = 0; i < text.length; i += 1) {
    if (text[i] === "{") {
      const prelude = text.slice(start, i).trim();
      if (prelude.startsWith("@")) {
        stack.push(prelude);
        start = i + 1;
        continue;
      }
      const end = text.indexOf("}", i);
      out.push({ at: stack.join(" "), selector: prelude.replace(/\s+/g, " "), body: text.slice(i + 1, end) });
      i = end;
      start = end + 1;
    } else if (text[i] === "}") {
      stack.pop();
      start = i + 1;
    }
  }
  return out;
}

const GLOBALS = "app/globals.css";
const top = (selector: string) =>
  rules(read(GLOBALS)).filter((rule) => rule.at === "" && rule.selector === selector);

const MODULES = ["app", "components"].flatMap((dir) =>
  readdirSync(join(ROOT, dir), { encoding: "utf8", recursive: true })
    .filter((file) => file.endsWith(".css"))
    .map((file) => `${dir}/${file.replaceAll("\\", "/")}`)
    .filter((file) => file !== GLOBALS),
);

// A height of 20 to 48px written in px: a control's height belongs in a token
// (--control-h, --control-min, --h-sm/md/lg, --badge-h), so every row shares one.
const PX_HEIGHT = /(?:^|[;\s])(?:min-|max-)?(?:height|block-size):\s*(?:2\d|3\d|4[0-8])px/;

// Changed on purpose (UI rebuild, 20 Oct 2026): the new kit (components/ui/<Name>/ and
// app/styles/) takes each control's size from the handoff, which sets them per control
// (Components.html 06 and 07), not from --control-h. A kit height passes only when
// design/kaagaz-ui-handoff/styles/kaagaz.css uses the same one.
const PX_HEIGHTS = /(?:^|[;\s])(?:min-|max-)?(?:height|block-size):\s*(\d+)px/g;
const HANDOFF_HEIGHTS = new Set(
  Array.from(readFileSync(join(ROOT, "..", "design", "kaagaz-ui-handoff", "styles", "kaagaz.css"), "utf8").matchAll(PX_HEIGHTS), (m) => m[1]),
);
// The shell and each screen rebuilt from the handoff's own screen (the dashboard: desktop/Home.html;
// the sign-in frame: desktop/SignIn.html)
// follow the same rule.
const KIT = /^(components\/ui\/[A-Z]\w*\/|app\/styles\/|components\/shell\/|components\/dashboard\/|app\/login\/)/;
const handoffHeights = (file: string, body: string) =>
  KIT.test(file) && Array.from(body.matchAll(PX_HEIGHTS)).every((m) => HANDOFF_HEIGHTS.has(m[1]));

/** Not controls in a row of controls, each with its reason ("file: selector"). */
const ALLOWED = new Map<string, string>([
  ["app/(app)/documents/[id]/DocumentView.module.css: .valueButton", "a value is a word in a table cell: the 24px WCAG target, 44px on touch"],
  ["components/chat/Chat.module.css: .openLink", "a link in a source card's text: the 24px target, 44px on touch"],
  ["components/viewer/ScannedText.module.css: .lineButton", "a scanned line, as tall as its text (tests/layout-guards.test.ts)"],
  ["app/login/login.module.css: .chip", "the sign-in page's drawing of a citation number, not a control"],
  ["components/words/Words.module.css: .toggle", "a switch on a row of its own, a full 44px target"],
  // components/documents/* (the Documents pass owns these): inline targets and table-row heights.
  ["components/documents/Documents.module.css: .inlineAction", "an action word inside a line of text: 24px, 44px on touch"],
  ["components/documents/Documents.module.css: .selectAll, .selectHit", "a tick box's hit area: 24px, 44px on touch"],
  ["components/documents/Documents.module.css: .newerVersion", "a link inside a line of text: 24px, 44px on touch"],
  ["components/documents/Documents.module.css: .skeletonRow", "a skeleton as tall as a table row"],
  ["components/documents/Documents.module.css: table.docs td", "a table row's height, not a control"],
  ["components/documents/Table.module.css: .reflow td", "a table row's height, not a control"],
]);

describe("one control height", () => {
  it("makes .btn and every single-line field exactly --control-h tall, centred with no block padding", () => {
    const btn = top(".btn")[0]?.body ?? "";
    expect(btn).toMatch(/\bheight:\s*var\(--control-h\);/);
    expect(btn).toMatch(/padding-block:\s*0;/);
    expect(btn).toMatch(/line-height:\s*1\.2;/);
    const field = top(".input, select.input, textarea.input")[0]?.body ?? "";
    expect(field).toMatch(/\bheight:\s*var\(--control-h\);/);
    // Changed on purpose (UI rebuild, 21 Oct 2026): the handoff's 16px field inset.
    expect(field).toMatch(/padding:\s*0 var\(--space-4\);/);
    expect(field).toMatch(/line-height:\s*1\.25;/);
    // Only a textarea grows.
    const area = top("textarea.input")[0]?.body ?? "";
    expect(area).toMatch(/height:\s*auto;[\s\S]*min-height:\s*var\(--control-h\);/);
  });

  it("switches --control-h to 44px on touch, so buttons and fields grow together", () => {
    expect(read(GLOBALS)).toMatch(/@media \(pointer: coarse\) \{\s*:root \{\s*--control-h: var\(--h-lg\);/);
  });

  it("never hard-codes a 20-48px height in a screen's CSS, outside the reasoned list", () => {
    const found = MODULES.flatMap((file) =>
      rules(read(file))
        .filter((rule) => PX_HEIGHT.test(rule.body) && !handoffHeights(file, rule.body))
        .map((rule) => `${file}: ${rule.selector}`),
    );
    expect(found.filter((name) => !ALLOWED.has(name))).toEqual([]);
  });

  it("gives badges and status chips one height and centred words", () => {
    expect(read(GLOBALS)).toMatch(/--badge-h: 24px;/);
    for (const selector of [".badge", ".status-chip"]) {
      const body = top(selector)[0]?.body ?? "";
      expect(body, selector).toMatch(/min-height:\s*var\(--badge-h\);/);
      expect(body, selector).toMatch(/line-height:\s*1\.25;/);
      // Changed on purpose (UI rebuild, 21 Oct 2026): the handoff's soft corners replace the 2px square ones: pills.
      expect(body, selector).toMatch(/border-radius:\s*var\(--r-full\);/);
    }
    // Changed on purpose (Tushar, 16 Oct 2026): the sidebar Review count is gone.
  });
});

describe("centred icons", () => {
  it("sets vertical-align on an icon inside words, so it sits on their middle, not their baseline", () => {
    const inline = rules(read(GLOBALS)).find((rule) => rule.selector.startsWith(":where(") && rule.selector.endsWith("> svg"));
    expect(inline?.body).toMatch(/display:\s*inline-block;/);
    expect(inline?.body).toMatch(/vertical-align:\s*-0\.125em;/);
    // Table cells and headings too: an icon there was a block on a line of its own.
    for (const parent of ["td", "th", "h2", "dd"]) expect(inline?.selector).toMatch(new RegExp(`\\b${parent}\\b`));
  });

  it("puts a button that sits in a line of words on the line's middle", () => {
    expect(top(".btn")[0]?.body).toMatch(/vertical-align:\s*middle;/);
  });

  it("nudges nothing by a pixel or two to line it up", () => {
    const nudge = /(?:margin-top|margin-block-start|top):\s*-?[1-3]px;/;
    const found = MODULES.flatMap((file) =>
      rules(read(file))
        .filter((rule) => nudge.test(rule.body))
        .map((rule) => `${file}: ${rule.selector}`),
    );
    // The bar over the bottom bar's 1px top edge covers that edge; it is not a nudge.
    expect(found.filter((name) => name !== 'components/shell/Shell.module.css: .bottomLink[aria-current="page"]::before')).toEqual([]);
  });
});
