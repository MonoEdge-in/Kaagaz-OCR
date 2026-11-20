// Issue 692, quick UI fixes from a first-time user's walkthrough of the test site:
// 1. Escape closes the Uploads tray wherever focus is (it worked only inside the panel), and focus
//    goes back to the Uploads button, as Hide does. Rendered in the small fake DOM (tests/fake-dom.ts).
// 3. Every icon-only button in the document viewer's toolbars has a name and a visible tooltip.
// 4. The document screen offers Download once (the button beside Clean copy, not again in "⋯"),
//    and its two "⋯" buttons have different names: the document's actions, and the other tabs.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, createElement, useState } from "react";
import type { Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { documentMoreItems } from "@/app/(app)/documents/[id]/WordReview";
import { RowMenu } from "@/components/admin/RowMenu";
import { UploadStatus } from "@/components/documents/UploadArea";
import { buildTree } from "@/components/documents/folderTree";
import type { UploadEntry } from "@/components/documents/uploadQueue";
import { StatusBarSlot } from "@/components/shell/StatusBar";
import type { ReviewStatus } from "@/lib/types";
import { install } from "./fake-dom";
import type { FakeDocument, FakeElement } from "./fake-dom";
import { fire } from "./fake-dom";

vi.mock("@/lib/session", () => ({ useSession: () => ({ me: { user: { id: "u1", role: "admin" } } }) }));

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

describe("1. Escape closes the Uploads tray", () => {
  let doc: FakeDocument;
  let root: Root | null = null;

  beforeEach(() => {
    doc = install();
  });

  afterEach(async () => {
    await act(async () => root?.unmount());
    root = null;
    vi.unstubAllGlobals();
  });

  const failed: UploadEntry = { key: "k1", name: "deed.pdf", size: 1000, state: { kind: "error", message: "Not sent." } };

  async function renderTray() {
    const slot = doc.body.appendChild(doc.createElement("div")) as FakeElement;
    function Tray() {
      const [open, setOpen] = useState(false);
      return createElement(
        StatusBarSlot.Provider,
        { value: slot as unknown as HTMLElement },
        createElement(UploadStatus, {
          open,
          onOpenChange: setOpen,
          entries: [failed],
          views: {},
          stored: [],
          onClearFinished: () => undefined,
          onRestore: () => undefined,
          onRetry: () => undefined,
          tree: buildTree([]),
          onUndone: () => undefined,
          canReread: true,
        }),
      );
    }
    const { createRoot } = await import("react-dom/client");
    root = createRoot(doc.body.appendChild(doc.createElement("div")) as unknown as Element);
    await act(async () => root!.render(createElement(Tray)));
    const toggle = slot.querySelector("[aria-expanded]")!;
    const panel = doc.getElementById(toggle.getAttribute("aria-controls") ?? "")!;
    return { toggle, panel };
  }

  it("closes when Escape is pressed with focus on the Uploads button, and keeps focus there", async () => {
    const { toggle, panel } = await renderTray();
    await act(async () => void fire(toggle, "click"));
    expect(panel.hasAttribute("hidden")).toBe(false);
    toggle.focus();
    await act(async () => void fire(toggle, "keydown", { key: "Escape" }));
    expect(panel.hasAttribute("hidden")).toBe(true);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(doc.activeElement).toBe(toggle);
  });

  it("closes when Escape is pressed anywhere on the page, and puts focus on the Uploads button", async () => {
    const { toggle, panel } = await renderTray();
    await act(async () => void fire(toggle, "click"));
    doc.body.focus();
    await act(async () => void fire(doc.body, "keydown", { key: "Escape" }));
    expect(panel.hasAttribute("hidden")).toBe(true);
    expect(doc.activeElement).toBe(toggle);
  });

  it("leaves the tray open for another key, and for an Escape that closes a dialog", async () => {
    const { toggle, panel } = await renderTray();
    await act(async () => void fire(toggle, "click"));
    await act(async () => void fire(doc.body, "keydown", { key: "Enter" }));
    expect(panel.hasAttribute("hidden")).toBe(false);
    const dialog = doc.body.appendChild(doc.createElement("dialog")) as FakeElement;
    const field = dialog.appendChild(doc.createElement("input")) as FakeElement;
    await act(async () => void fire(field, "keydown", { key: "Escape" }));
    expect(panel.hasAttribute("hidden")).toBe(false);
  });
});

/** Each <button>, <Button> and <IconButton> element in `source`: its whole text, and where it starts. */
function buttons(source: string): { text: string; at: number }[] {
  const found: { text: string; at: number }[] = [];
  const start = /<(button|Button|IconButton)\b/g;
  for (let match = start.exec(source); match; match = start.exec(source)) {
    const tag = match[1] ?? "";
    let depth = 0;
    let at = match.index + match[0].length;
    for (; at < source.length; at += 1) {
      const char = source[at];
      if (char === "{") depth += 1;
      else if (char === "}") depth -= 1;
      else if (char === ">" && depth === 0) break;
    }
    const selfClosing = source[at - 1] === "/";
    const end = selfClosing ? at + 1 : source.indexOf(`</${tag}>`, at) + tag.length + 3;
    found.push({ text: source.slice(match.index, end), at: match.index });
  }
  return found;
}

/** The words a sighted person sees on a button: no tags, no {expressions}, no words hidden on a phone. */
function shownWords(element: string): string {
  let text = element.replace(/<span className="narrow-hide">[^<]*<\/span>/g, "");
  for (let before = ""; before !== text; ) {
    before = text;
    text = text.replace(/\{[^{}]*\}/g, "");
  }
  const inner = text.replace(/^<[^>]*>/, "").replace(/<\/\w+>$/, "");
  return inner.replace(/<[^>]*>/g, "").trim();
}

describe("3. the viewer's icon buttons each say what they do", () => {
  const files = ["components/viewer/PageViewer.tsx", "components/viewer/PdfDocumentViewer.tsx", "components/extend/pdf-viewer.tsx"];

  it.each(files)("%s: every icon-only button has a name and a tooltip", (file) => {
    const source = read(file);
    const unnamed: string[] = [];
    for (const { text, at } of buttons(source)) {
      if (text.startsWith("<IconButton")) {
        if (!/\blabel=/.test(text) || /\bshowLabel\b/.test(text)) unnamed.push(text); // IconButton gives aria-label and title
        continue;
      }
      const iconOnly = /\/>\s*$/.test(text.split("\n")[0] ?? "") || /<\w*Icon\b|<svg\b/.test(text);
      if (!iconOnly || /[A-Za-z]/.test(shownWords(text))) continue;
      const before = source.slice(Math.max(0, at - 200), at);
      // A title, the PDF toolbar's own tooltip around it, or a role="tooltip" it points at (the
      // reading problems' legend, which shows on hover and focus).
      const tooltip =
        /title=/.test(text) ||
        before.lastIndexOf("<ToolbarTooltip label=") > before.lastIndexOf("</ToolbarTooltip>") ||
        (/aria-describedby=\{legendId\}/.test(text) && source.includes('<span id={legendId} role="tooltip"'));
      if (!/aria-label=/.test(text) || !tooltip) unnamed.push(text.split("\n").slice(0, 6).join("\n"));
    }
    expect(unnamed).toEqual([]);
  });
});

describe("4. one Download, and two different menus", () => {
  it("leaves Download original out of the document's ⋯ menu: the Download button sits beside it", () => {
    const status = { status: "ready", ensemble: { allowed: false, reason: null, run: null } } as unknown as ReviewStatus;
    const items = documentMoreItems(status, { onEnsemble: () => undefined, onExport: () => undefined });
    expect(items.map((item) => item.label)).toEqual(["Export as Data"]);
    const view = read("app/(app)/documents/[id]/DocumentView.tsx");
    expect(view.match(/<Icon name="download" \/>/g)).toHaveLength(1);
    const menu = view.slice(view.indexOf("more={documentMoreItems("), view.indexOf("})}", view.indexOf("more={documentMoreItems(")));
    expect(menu).toContain("onExport");
    expect(menu).not.toContain("download");
    expect(read("app/(app)/documents/[id]/WordReview.tsx")).not.toContain("Save the file exactly as it was uploaded.");
  });

  it("names the other tabs' ⋯ More tabs, apart from the document's own More actions for <name>", () => {
    const html = renderToStaticMarkup(
      createElement(RowMenu, { name: "the other tabs", compact: true, label: "More tabs", items: [{ label: "Versions", onSelect: () => undefined }] }),
    );
    expect(html).toContain('aria-label="More tabs"');
    expect(html).toContain('title="More tabs"');
    const view = read("app/(app)/documents/[id]/DocumentView.tsx");
    expect(view).toMatch(/<RowMenu\s+name="the other tabs"\s+compact=\{room < 3\}\s+label=\{room < 3 \? "More tabs" : undefined\}/);
    expect(read("components/documents/DocumentActions.tsx")).toContain("const name = `More actions for ${item.display_name}`;");
  });
});
