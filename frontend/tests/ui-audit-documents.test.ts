// The owner's UI audit (Tushar, 16 Oct 2026), sections 2 and 3: the documents list and the
// document screen. Pure decisions are run; layout rules are read from the source.

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { menuEntries, menuItemAfterKey, menuPlace } from "@/components/documents/library";
import { LibraryFilters } from "@/components/documents/LibraryFilters";
import { ALL_DOCUMENTS } from "@/components/documents/libraryQuery";
import { markChipText } from "@/components/viewer/readingMarks";
import { SPLIT, splitAfterDrag, splitAfterKey, wheelZoom } from "@/components/viewer/viewer";
import type { ReadingMark } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
const sources = (dir: string): string[] =>
  readdirSync(join(ROOT, dir), { encoding: "utf8", recursive: true })
    .filter((file) => /\.tsx?$/.test(file))
    .map((file) => `${dir}/${file.replaceAll("\\", "/")}`);

describe("DD1: the wheel over the page", () => {
  it("zooms only with Ctrl or Cmd held; any other wheel scrolls", () => {
    expect(wheelZoom({ ctrlKey: false, metaKey: false, deltaY: 120 })).toBeNull();
    expect(wheelZoom({ ctrlKey: true, metaKey: false, deltaY: -120 })).toBe("in");
    expect(wheelZoom({ ctrlKey: false, metaKey: true, deltaY: 40 })).toBe("out");
    expect(wheelZoom({ ctrlKey: true, metaKey: false, deltaY: 0 })).toBeNull();
  });

  it("lets the wheel reach <main>: no vertical scroll trap on the page or sheet frames", () => {
    const viewer = css("components/viewer/PageViewer.module.css");
    expect(viewer).not.toMatch(/overscroll-behavior(-y)?:/);
    expect(viewer).toMatch(/\.frame \{[^}]*overscroll-behavior-x:\s*contain;/);
    expect(viewer).toMatch(/\.sheetFrame \{[^}]*overscroll-behavior-x:\s*contain;/);
  });

  it("never goes back a screen from code: no router.back or history.back/go anywhere", () => {
    const files = [...sources("app"), ...sources("components"), ...sources("lib")];
    expect(files.filter((file) => /router\.back\(|history\.(back|go)\(/.test(read(file)))).toEqual([]);
    // <main> and the page hold a sideways swipe, so it never turns into Back.
    expect(css("components/shell/Shell.module.css")).toMatch(/\.main \{[^}]*overscroll-behavior:\s*(contain|none)/);
  });
});

describe("DD5: the split between the page and the panels", () => {
  it("follows a drag across the layout, kept between its ends", () => {
    expect(splitAfterDrag(60, 100, 1000)).toBe(70);
    expect(splitAfterDrag(60, -1000, 1000)).toBe(SPLIT.min);
    expect(splitAfterDrag(60, 1000, 1000)).toBe(SPLIT.max);
    expect(splitAfterDrag(60, 50, 0)).toBe(60);
  });

  it("moves a step with Left and Right, to its ends with Home and End", () => {
    expect(splitAfterKey(60, "ArrowLeft")).toBe(55);
    expect(splitAfterKey(SPLIT.max, "ArrowRight")).toBe(SPLIT.max);
    expect(splitAfterKey(60, "Home")).toBe(SPLIT.min);
    expect(splitAfterKey(60, "End")).toBe(SPLIT.max);
    expect(splitAfterKey(60, "Enter")).toBeNull();
  });

  it("starts at fit width in the sticky column, with a Fit page toggle", () => {
    const viewer = read("components/viewer/PageViewer.tsx");
    expect(viewer).toContain('useState<"width" | "page">("width")');
    expect(viewer).toContain("const fit = fill ? fitChoice : \"width\";");
    expect(read("app/(app)/documents/[id]/DocumentView.tsx")).toContain("fill={stickyViewer}");
  });
});

describe("D5: a row's menu", () => {
  const view = { width: 1200, height: 800 };
  it("opens under its button, its end on the button's end", () => {
    const place = menuPlace({ top: 100, bottom: 136, right: 900 }, { width: 208, height: 200 }, view);
    expect(place).toEqual({ top: 140, left: 692, up: false });
  });

  it("flips above near the window's bottom, and stays 8px inside the window", () => {
    const up = menuPlace({ top: 700, bottom: 736, right: 900 }, { width: 208, height: 200 }, view);
    expect(up).toEqual({ top: 496, left: 692, up: true });
    expect(menuPlace({ top: 100, bottom: 136, right: 100 }, { width: 208, height: 50 }, view).left).toBe(8);
  });

  it("moves with the arrow keys, wrapping, and Home and End", () => {
    expect(menuItemAfterKey(0, "ArrowDown", 3)).toBe(1);
    expect(menuItemAfterKey(2, "ArrowDown", 3)).toBe(0);
    expect(menuItemAfterKey(0, "ArrowUp", 3)).toBe(2);
    expect(menuItemAfterKey(-1, "ArrowDown", 3)).toBe(0);
    expect(menuItemAfterKey(1, "End", 3)).toBe(2);
    expect(menuItemAfterKey(1, "a", 3)).toBeNull();
  });

  it("is an icon button with a name, its menu in a portal with arrow keys and Esc", () => {
    const actions = read("components/documents/DocumentActions.tsx");
    expect(actions).toContain("createPortal(");
    expect(actions).toContain("const name = `More actions for ${item.display_name}`;");
    expect(actions).toContain('aria-haspopup="menu"');
    expect(actions).toMatch(/if \(event\.key === "Escape"\) \{[\s\S]*close\(true\);/);
    expect(css("components/documents/Library.module.css")).toMatch(
      /\.menuList \{[^}]*position:\s*fixed;[^}]*border:\s*1px solid var\(--border-strong\);/,
    );
  });

  it("DD2: on the document screen starts with Read again, without Download or a new version", () => {
    const item = { id: "d1", status: "ready" as const, deleted_at: null, can_delete: true };
    const words = (entries: ReturnType<typeof menuEntries>) =>
      entries.map((entry) => (entry.kind === "action" ? entry.words : "Download original"));
    expect(words(menuEntries(item, false, "document", true))).toEqual([
      "Read again",
      "Rename",
      "Move to folder",
      "Tags",
      "Move to Trash",
    ]);
    expect(words(menuEntries(item, false, "document", false))[0]).toBe("Rename");
    expect(words(menuEntries({ ...item, deleted_at: "2026-10-16T10:00:00Z" }, true, "document", true))).toEqual([
      "Restore",
    ]);
    // The list's menu is as it was.
    expect(words(menuEntries(item, false))).toEqual([
      "Rename",
      "Move to folder",
      "Tags",
      "Upload new version",
      "Download original",
      "Move to Trash",
    ]);
  });
});

describe("D7: the filter chips", () => {
  const render = (library = ALL_DOCUMENTS) =>
    renderToStaticMarkup(
      createElement(LibraryFilters, {
        library,
        tags: [],
        tagsError: null,
        onRetryTags: () => undefined,
        showSubfolders: false,
        onChange: () => undefined,
      }),
    );

  it("gives a panel's chip a chevron, and nothing to clear while none is set", () => {
    const html = render();
    expect(html.match(/_chipOpens_/g)).toHaveLength(3);
    expect(html).not.toContain("Remove filter");
    expect(html).not.toContain("Clear all");
    expect(css("components/documents/Library.module.css")).toMatch(/\.chipOpens \{[^}]*background-image:\s*var\(--select-caret\);/);
  });

  it("fills a set chip, gives it its own x, and counts the filters in Clear all", () => {
    const html = render({ ...ALL_DOCUMENTS, type: "pdf", attention: true });
    expect(html).toMatch(/data-active="true"[^>]*>Type: PDF<\/button>/);
    expect(html).toContain('aria-label="Remove filter: Type: PDF"');
    expect(html).toMatch(/Clear all <span class="tabular">\(2\)<\/span>/);
  });
});

describe("the documents list (D1, D2, D3, D6, D9, D10, D11)", () => {
  const view = read("components/documents/DocumentsView.tsx");
  const table = read("components/documents/DocumentsTable.tsx");
  const docs = css("components/documents/Documents.module.css");

  it("D1: keeps Read with's room from the first paint, a skeleton until the limits come", () => {
    expect(view).toMatch(/const readerSelect = uploads\.limitsLoaded \? \([\s\S]*?\) : \(\s*<span className=\{`skeleton \$\{styles\.readerSkeleton\}`\}/);
    expect(docs).toMatch(/\.uploadControls \{[^}]*min-height:\s*calc\(var\(--h-md\)/);
  });

  it("D2: gives the tick box its own column and the name's first-line height, 12px before the name", () => {
    expect(docs).toMatch(/\.selectHit \{[^}]*height:\s*var\(--row-line\);/);
    expect(docs).toMatch(/\.name \{[^}]*line-height:\s*var\(--row-line\);/);
    expect(docs).toMatch(/table\.docs \.selectCell \{\s*flex: 0 0 auto;\s*padding-inline-end:\s*var\(--space-3\);/);
  });

  it("D3: is a table from 36rem, cards only below", () => {
    expect(docs).toContain("@container docs-list (max-width: 35.98rem)");
    expect(docs).not.toContain("(max-width: 47.98rem) {\n  /* A card");
  });

  it("D6: the search box and Search share a height; clearing is an x inside the box", () => {
    expect(docs).toMatch(/\.searchInput \{[^}]*height:\s*var\(--h-md\);/);
    expect(view).toContain('<IconButton icon="close" label="Clear search" className={styles.searchClear}');
  });

  it("D9: heads the select column with Select all; the bar shows only for a selection, stuck below", () => {
    const head = table.slice(table.indexOf("<thead"), table.indexOf("</thead>"));
    expect(head).toContain('allBox("Select all visible documents")');
    expect(table.indexOf("</table>")).toBeLessThan(table.indexOf("className={styles.selectionBar}"));
    expect(docs).toMatch(/\.selectionBar \{[^}]*position:\s*sticky;\s*bottom:\s*0;/);
    expect(docs).toMatch(/@container docs-list \(min-width: 36rem\)[\s\S]*?\.selectionBar\[data-idle\] \{\s*display:\s*none;/);
  });

  it("D10: with documents listed the whole screen takes files, and only files", () => {
    expect(view).toMatch(/<Dropzone\s+variant="page"/);
    expect(view).not.toContain('variant="slim"');
    const upload = read("components/documents/UploadArea.tsx");
    expect(upload).toContain('event.dataTransfer.types.includes("Files")');
    expect(docs).toMatch(/\.dropOverlay \{[^}]*position:\s*fixed;[\s\S]*?pointer-events:\s*none;/);
  });

  it("D11: opens a document from anywhere on its row, the name not underlined, a file icon first", () => {
    expect(docs).toMatch(/\.name::after \{\s*content: "";\s*position: absolute;\s*inset: 0;/);
    expect(docs).toMatch(/\.name \{[^}]*text-decoration:\s*none;/);
    expect(table).toContain('<Icon name="file" className={styles.fileIcon} />');
  });

  it("D12: puts New folder in the folder column's header and moves a document dropped on a folder", () => {
    expect(view).toContain('<IconButton icon="plus" label="New folder" onClick={() => setNewFolderAsk((n) => n + 1)} />');
    expect(view).toContain("updateDocument(documentId, { folder_id: folderId })");
    expect(table).toContain("event.dataTransfer.setData(DOCUMENT_DRAG, document.id);");
  });
});

describe("the document screen (DD6, DD7, DD9)", () => {
  const view = read("app/(app)/documents/[id]/DocumentView.tsx");

  it("DD6: the MonoEdge tabs, roving tabindex, the count in tabular figures", () => {
    expect(view).toContain('role="tablist"');
    expect(view).toContain('className={`tabs ${styles.tabs}`}');
    expect(view).toContain("tabIndex={item.id === active ? 0 : -1}");
    expect(view).toContain("<span data-label={item.label}>{item.label}</span>");
    expect(view).toContain("className={`tab-count ${styles.tabCount}`}");
    expect(view).toContain('role="tabpanel"');
  });

  it("DD7: a mark's chip is its word as read, else what the mark is", () => {
    const mark = (text: string): ReadingMark =>
      ({ quality: "unsure", text, region: { kind: "box", page: 1, box: [0, 0, 1, 1] } }) as ReadingMark;
    expect(markChipText(mark("Fettari"))).toBe("Fettari");
    expect(markChipText(mark("  "))).toBe("Unsure word");
    const panels = read("app/(app)/documents/[id]/DocumentPanels.tsx");
    expect(panels).toContain("{index + 1}/{shownMarks.length}");
    expect(panels).toContain("aria-label={name}");
    expect(read("components/viewer/PageViewer.tsx")).toContain('data-hot={hotMark === index ? "true" : undefined}');
  });

  it("DD9: Show reading problems is a toolbar toggle, its legend a tooltip that never takes the pointer", () => {
    const viewer = css("components/viewer/PageViewer.module.css");
    expect(viewer).not.toMatch(/\.problemBar\b/);
    expect(viewer).toMatch(/\.legendTip \{[^}]*pointer-events:\s*none;[^}]*visibility:\s*hidden;/);
  });
});
