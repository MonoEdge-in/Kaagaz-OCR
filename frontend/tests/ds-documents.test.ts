// The MonoEdge design system (Tushar, 16 Oct 2026) on the documents list, the upload tray, the
// page viewer and the document screen. Each guard reads the source, so a rule that brings back a
// shadow, a gradient, a pill, an old token or a button order fails here.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { BusyLabel } from "@/components/documents/BusyLabel";
import { LabelBadge } from "@/components/viewer/LabelBadge";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");

const MODULES = [
  "components/documents/Documents.module.css",
  "components/documents/Library.module.css",
  "components/documents/Table.module.css",
  "components/documents/ActionDialog.module.css",
  "components/viewer/PageViewer.module.css",
  "components/viewer/ScannedText.module.css",
  "app/(app)/documents/[id]/DocumentView.module.css",
];

/** "selector { body }" pairs of a stylesheet, at-rules flattened (enough for these files). */
function rules(text: string): { selector: string; body: string }[] {
  return [...text.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({ selector: m[1]!.trim(), body: m[2]! }));
}

const declarations = (property: RegExp) =>
  MODULES.flatMap((file) =>
    rules(css(file)).flatMap(({ selector, body }) =>
      body
        .split(";")
        .map((part) => part.trim())
        .filter((part) => property.test(part))
        .map((part) => `${file}: ${selector}: ${part}`),
    ),
  );

describe("the MonoEdge design system on the documents screens", () => {
  // Changed on purpose (UI rebuild, 21 Oct 2026): the handoff's soft corners replace the 2px square ones.
  it("has no gradients; every corner is one of the handoff's radius tokens", () => {
    expect(declarations(/gradient\(/)).toEqual([]);
    const radius = declarations(/^border(-[a-z-]+)?-radius:/).filter(
      (d) => !/radius:\s*(var\(--k-r-(\d|pill)\)|0|inherit|2px 2px 0 0)$/.test(d),
    );
    expect(radius).toEqual([]);
  });

  // Changed on purpose (UI rebuild, 21 Oct 2026): the handoff's soft surfaces, from its shadow tokens only.
  it("has no shadows but the handoff's tokens, and the 0-blur rings round marks on the page", () => {
    const shadows = declarations(/^box-shadow:/).filter((d) => !/box-shadow:\s*var\(--k-shadow-(1|2|page)\)$/.test(d));
    const rings = /^box-shadow:\s*0 0 0 \d+px var\(--[\w-]+\)(, 0 0 0 \d+px var\(--[\w-]+\))?$/;
    const marks = /PageViewer\.module\.css: \.(box|mine|mineActive|problem|problem:hover|problem:focus-visible):/;
    expect(shadows.filter((d) => !(marks.test(d) && rings.test(d.split(": ").slice(2).join(": "))))).toEqual([]);
  });

  it("uses the MonoEdge token names, never the old ones", () => {
    // Changed on purpose (Tushar, 16 Oct 2026, one control height / centred icons): --control-h is
    // now THE control height (36px, 44px on touch), not an old alias, so screens use it.
    const old = /var\(--(fg|fg-muted|bg|surface|surface-2|accent-soft|accent-fg|danger-fg|danger-bg|review-[a-z]+|verified-[a-z]+|r-[a-z]+|e-\d|space-5|space-7|font-sans|paper-frame)\)/;
    expect(MODULES.filter((file) => old.test(css(file)))).toEqual([]);
  });

  it("spaces only on the 4-8-12-16-24-32-48-64 scale", () => {
    const off = declarations(/^(gap|row-gap|column-gap|margin|padding)(-[a-z-]+)?:/)
      .filter((d) => !d.endsWith(": margin: -1px")) // the visually-hidden pattern, not spacing
      .filter((d) =>
      [...d.split(": ").slice(2).join(": ").matchAll(/(\d+(\.\d+)?)px/g)].some(
        (m) => ![0, 4, 8, 12, 16, 24, 32, 48, 64].includes(Number(m[1])),
      ),
    );
    expect(off).toEqual([]);
  });

  it("never sizes by the window's width (vw)", () => {
    expect(MODULES.filter((file) => /\d+vw\b/.test(css(file)))).toEqual([]);
  });

  it("lets every flex or grid child that holds text shrink (min-width: 0), so nothing bleeds", () => {
    const shrinks = (file: string, selector: string) =>
      rules(css(file)).some(
        (rule) => rule.selector.split(",").map((s) => s.trim()).includes(selector) && /min-width:\s*0/.test(rule.body),
      );
    const docs = "components/documents/Documents.module.css";
    const view = "app/(app)/documents/[id]/DocumentView.module.css";
    const wanted: [string, string][] = [
      [docs, ".uploadBody"],
      [docs, ".fileName"],
      [docs, ".errorBody"],
      [docs, ".searchField"],
      [docs, ".nameCell"],
      [docs, ".result"],
      ["components/documents/Library.module.css", ".main"],
      ["components/documents/Library.module.css", ".place"],
      [view, ".title"],
      [view, ".panel"],
      [view, ".problemBody"],
      // Changed on purpose (Vrushit UAT, 25 Oct 2026): .factRow dd went with the Invoice section;
      // (Vrushit UAT 2, 27 Oct 2026, #416) .valuesWrap went with Values found.
      ["components/viewer/ScannedText.module.css", ".lineButton"],
      ["components/viewer/PageViewer.module.css", ".viewer"],
    ];
    expect(wanted.filter(([file, selector]) => !shrinks(file, selector)).map((w) => w.join(": "))).toEqual([]);
  });

  it("puts tables in a wrapper that alone scrolls sideways (the documents list is the one noted exception)", () => {
    const wrap = (file: string, selector: string) =>
      rules(css(file)).find((rule) => rule.selector === selector)?.body ?? "";
    expect(wrap("components/documents/Table.module.css", ".reflowWrap")).toMatch(/overflow-x:\s*auto;/);
    // Changed on purpose (Vrushit UAT 2, 27 Oct 2026, #416): the document page has no values table.
    expect(read("app/(app)/documents/[id]/DocumentView.tsx")).not.toContain("<table");
    // Changed on purpose (handoff section 21, 5 Nov 2026): the list's table scrolls inside its own box,
    // but only below a 1024px window; its Actions menus open in a portal, so the box never cuts one off.
    const docs = read("components/documents/Documents.module.css");
    expect(docs).toMatch(/@media \(max-width: 1023\.98px\) \{\s*\.tableBox \{\s*overflow-x: auto;/);
    expect(read("components/documents/DocumentActions.tsx")).toContain("createPortal(");
  });

  // Changed on purpose (Tushar, 16 Oct 2026: Ready to verify).
  it("tells Verified and Ready to verify apart by icon and word, not colour alone", () => {
    const verified = renderToStaticMarkup(createElement(LabelBadge, { label: "verified" }));
    const review = renderToStaticMarkup(createElement(LabelBadge, { label: "needs_review" }));
    expect(verified).toContain("Verified");
    expect(review).toContain("Ready to verify");
    expect(verified).toContain("<svg");
    expect(review).toContain("<svg");
    expect(verified).not.toEqual(review.replace("Ready to verify", "Verified"));
  });

  it("keeps a button's width while it works: both labels stay laid out, one hidden", () => {
    const idle = renderToStaticMarkup(createElement(BusyLabel, { busy: false, idle: "Delete forever", working: "Deleting…" }));
    expect(idle).toContain('data-busy="false"');
    expect(idle).toContain("Delete forever");
    expect(idle).toContain("Deleting…");
    expect(idle).not.toContain("delayed"); // the spinner waits 300 ms only once busy
    const busy = renderToStaticMarkup(createElement(BusyLabel, { busy: true, idle: "Delete forever", working: "Deleting…" }));
    expect(busy).toContain('class="delayed"');
    const docs = css("components/documents/Documents.module.css");
    expect(docs).toMatch(/\.busyLabel\[data-busy="true"\] > :first-child,\s*\.busyLabel\[data-busy="false"\] > :last-child \{\s*visibility: hidden;/);
  });

  it("orders buttons [Destructive] ... [Cancel] [Primary], the primary last", () => {
    const before = (file: string, first: string, second: string, from = 0) => {
      const text = read(file);
      const a = text.indexOf(first, from);
      return a >= 0 && text.indexOf(second, a) > a;
    };
    // Dialog forms (rename, move, tags): Cancel, then the primary.
    expect(before("components/documents/DocumentActions.tsx", "function FormButtons", 'type="submit"')).toBe(true);
    expect(before("components/documents/DocumentActions.tsx", "onClick={onCancel}", 'type="submit"')).toBe(true);
    // Read again: Cancel, then Read again.
    expect(before("app/(app)/documents/[id]/ReadAgain.tsx", "onClick={cancel}", 'idle="Read again"')).toBe(true);
    // The rows of buttons end at the end; a destructive one stands apart at the start.
    expect(css("components/documents/Library.module.css")).toMatch(/\.formButtons \{[^}]*justify-content:\s*flex-end;/);
    expect(read("components/documents/FolderActions.tsx")).toContain("`btn btn-danger ${styles.apart}`");
    expect(read("components/documents/DocumentsTable.tsx")).toContain("`btn btn-danger ${styles.apart}`");
    // One primary per view: Upload on the list, Ask on a document.
    // Changed on purpose (UI audit, 16 Oct 2026): G5 puts the list's one primary Upload in the app
    // header (it opens #upload-input), so the screen's own Upload files is a secondary button.
    expect(read("components/documents/DocumentsView.tsx")).not.toContain("btn btn-primary");
    expect(read("components/documents/DocumentsView.tsx")).toContain('id="upload-input"');
    expect(read("app/(app)/documents/[id]/DocumentView.tsx").match(/btn btn-primary/g)).toHaveLength(1);
  });

  it("never shifts the viewer's toolbar: Fit stays laid out, the page readout keeps its width", () => {
    const viewer = read("components/viewer/PageViewer.tsx");
    // Changed on purpose (Tushar, 16 Oct 2026): the zoom readout is the way back to the fit,
    // so no hidden button holds room in the row; its least width keeps the row still.
    expect(viewer).toContain("className={`btn btn-quiet tabular ${styles.readout}`}");
    expect(viewer).not.toContain("styles.unseen");
    expect(viewer).toContain("style={{ minWidth: `${9 + 2 * String(numbers.length).length}ch` }}");
    // Changed on purpose (UI audit, 16 Oct 2026): DD1, the wheel zooms only with Ctrl or Cmd held
    // (wheelZoom); every other wheel is left to scroll the page and then <main>.
    expect(viewer).toContain('frame.addEventListener("wheel", listener, { passive: false })');
    expect(viewer).toMatch(/const direction = wheelZoom\(event\);\s*if \(direction === null\) return;\s*event\.preventDefault\(\);/);
  });

  it("lets only the page's pan areas and the tables' wrappers keep their scroll; the scanned lines never scroll on their own", () => {
    const scanned = css("components/viewer/ScannedText.module.css");
    expect(scanned).not.toMatch(/overflow(-y)?:\s*(auto|scroll)/);
    // Changed on purpose (owner's report, 29 Oct 2026): on the row, not the <li>, whose paint
    // containment hid the line numbers (text-panel-lines.test.ts).
    expect(scanned).toMatch(/\.lineRow \{[^}]*content-visibility:\s*auto;[^}]*contain-intrinsic-size:\s*auto 44px;/);
    // Changed on purpose (UI audit, 16 Oct 2026): DD1, no scroll trap. The page's and the sheet's
    // frames hold only a sideways swipe (never Back); a wheel up or down runs on into <main>.
    const viewer = css("components/viewer/PageViewer.module.css");
    expect(viewer).not.toMatch(/overscroll-behavior(-y)?:/);
    expect(viewer.match(/overscroll-behavior-x:\s*contain/g)).toHaveLength(2);
  });
});
