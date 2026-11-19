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
import { flaggedShort, shortWhen } from "@/components/documents/logic";
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
    // Changed on purpose (PRD 1, screens): File management Q5 adds Owner, Label, Party, Property
    // and Document date (Department too, once there are departments) to Type, Tags and Date uploaded.
    expect(html.match(/_chipOpens_/g)).toHaveLength(8);
    expect(html).not.toContain("Remove filter");
    expect(html).not.toContain("Clear all");
    expect(css("components/documents/Library.module.css")).toMatch(/\.chipOpens \{[^}]*background-image:\s*var\(--select-caret\);/);
  });

  // SoW 19 Oct 2026: the chips sit behind one Filters button, closed until a filter is set.
  it("keeps the chips behind a closed Filters button until a filter is set, then opens it with the count", () => {
    const closed = render();
    expect(closed).toMatch(/^<details class="_filters_\w+"><summary class="_filterToggle_\w+">/);
    expect(closed).toContain("Filters</summary>");
    const set = render({ ...ALL_DOCUMENTS, type: "pdf", attention: true });
    expect(set).toMatch(/^<details class="_filters_\w+" open="">/);
    expect(set).toMatch(/Filters<span class="_filterCount_\w+ tabular">2<\/span><\/summary>/);
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
    // Changed on purpose (Tushar, 16 Oct 2026, one control height): --control-h, 44px on touch.
    // Changed on purpose (SoW 19 Oct 2026, gaps and alignment): one control tall, no line kept
    // for the hint, which left a gap under the heading; a hint now adds its own line.
    expect(docs).toMatch(/\.uploadControls \{[^}]*min-height:\s*calc\(var\(--control-h\)\);/);
    expect(docs).toMatch(/@media \(pointer: coarse\) \{\s*\.uploadControls \{\s*min-height:\s*calc\(var\(--h-lg\)/);
  });

  it("D2: gives the tick box its own column and the name's first-line height, 12px before the name", () => {
    expect(docs).toMatch(/\.selectHit \{[^}]*height:\s*var\(--row-line\);/);
    // Changed on purpose (Tushar, 16 Oct 2026, Documents table sans): every first line is the
    // control height, the ⋯ button's own; a long name's first line is centred in it by padding.
    // Changed on purpose (Tushar, 16 Oct 2026, one control height): --control-h, 44px on touch.
    expect(docs).toMatch(/table\.docs \{\s*--row-line:\s*var\(--control-h\);/);
    expect(docs).toMatch(/@media \(pointer: coarse\) \{\s*table\.docs \{\s*--row-line:\s*var\(--h-lg\);/);
    expect(docs).toMatch(/\.name \{[^}]*padding-block:\s*calc\(\(var\(--row-line\) - var\(--name-line\)\) \/ 2\);/);
    expect(docs).toMatch(/table\.docs \.selectCell \{\s*flex: 0 0 auto;\s*padding-inline-end:\s*var\(--space-3\);/);
  });

  it("D3: is a table from 36rem, cards only below", () => {
    expect(docs).toContain("@container docs-list (max-width: 35.98rem)");
    expect(docs).not.toContain("(max-width: 47.98rem) {\n  /* A card");
  });

  it("D6: the search box and Search share a height; clearing is an x inside the box", () => {
    // Changed on purpose (Tushar, 16 Oct 2026, one control height): --control-h, 44px on touch.
    expect(docs).toMatch(/\.searchInput \{[^}]*height:\s*var\(--control-h\);/);
    expect(view).toContain('<IconButton icon="close" label="Clear search" className={styles.searchClear}');
  });

  it("D9: heads the select column with Select all; the bar shows only for a selection, stuck below", () => {
    const head = table.slice(table.indexOf("<thead"), table.indexOf("</thead>"));
    expect(head).toContain('allBox("Select all visible")');
    expect(table.indexOf("</table>")).toBeLessThan(table.indexOf("className={styles.selectionBar}"));
    expect(docs).toMatch(/\.selectionBar \{[^}]*position:\s*sticky;\s*bottom:\s*0;/);
    expect(docs).toMatch(/@container docs-list \(min-width: 36rem\)[\s\S]*?\.selectionBar\[data-idle\] \{\s*display:\s*none;/);
  });

  it("QA #370: on cards the idle bar sits after the list, and the facts line is never repeated", () => {
    expect(docs).toMatch(/@container docs-list \(max-width: 35\.98rem\) \{\s*\.selectionBar\[data-idle\] \{\s*position:\s*static;/);
    // The card's own cells lose to "table.docs td { display: block }" unless at least as specific.
    const cards = docs.slice(docs.search(/@container docs-list \(max-width: 35\.98rem\) \{\s*table\.docs,/));
    expect(cards).toMatch(/^@container[\s\S]*?table\.docs td \{\s*display:\s*block;/);
    for (const cell of ["ownerCell", "labelCell", "versionCell", "docDateCell"]) {
      expect(cards).toMatch(new RegExp(`table\\.docs \\.${cell}[,\\s][^{]*\\{\\s*display:\\s*none;`));
    }
  });

  it("D10: with documents listed the whole screen takes files, and only files", () => {
    expect(view).toMatch(/<Dropzone\s+variant="page"/);
    expect(view).not.toContain('variant="slim"');
    const upload = read("components/documents/UploadArea.tsx");
    expect(upload).toContain('event.dataTransfer.types.includes("Files")');
    expect(docs).toMatch(/\.dropOverlay \{[^}]*position:\s*fixed;[\s\S]*?pointer-events:\s*none;/);
  });

  it("D11: opens a document from anywhere on its row, the name not underlined, a file icon first", () => {
    // Changed on purpose (QA video, 17 Oct 2026): no stretched ::after link, which covered the
    // whole page in browsers that do not make a <tr> its containing block (Safari); the row
    // itself opens the document, and controls inside it keep their own clicks.
    expect(docs).not.toMatch(/\.name::after/);
    expect(table).toContain("onClick={openRow}");
    expect(table).toMatch(/closest\("a, button, input, label, select, textarea/);
    expect(table).toContain("querySelector<HTMLAnchorElement>(`.${styles.name}`)?.click()");
    expect(docs).toMatch(/\.name \{[^}]*text-decoration:\s*none;/);
    // Changed on purpose (UI rebuild, 21 Oct 2026): the handoff's paper icon with the file's extension.
    expect(table).toContain('<span className={`k-file-ic ${styles.fileIcon}`} data-ext={extensionOf(document.display_name)} aria-hidden="true" />');
  });

  it("D12: puts New folder in the folder column's header and moves a document dropped on a folder", () => {
    expect(view).toContain('<IconButton icon="plus" label="New folder" onClick={() => setNewFolderAsk((n) => n + 1)} />');
    // Tushar, 16 Oct 2026: one way to make a folder, the column's "+" (named "New folder", its
    // tooltip too); the place's own "+ New folder" button is gone, and the column shows at every
    // width (stacked above the list below 1024px, no drawer), so the "+" is always there.
    const actions = read("components/documents/FolderActions.tsx");
    // Changed on purpose (PRD 9 F-18): testers found no way to make a folder, so the place's header
    // has a "New folder" button beside Rename and Move again; the column's "+" stays.
    expect(actions).toContain('New folder<span className="visually-hidden"> here</span>');
    expect(actions).not.toContain('<Icon name="plus" />');
    expect(view).not.toContain("<Drawer");
    expect(css("components/documents/Library.module.css")).toMatch(/^\.treePane \{\s*display: flex;/m);
    // Changed on purpose (PRD 7 PR 11b, #637): a drop moves through moveDocument, which knows PR
    // 10c's `{id, hidden: true}` reply.
    expect(view).toContain("moveDocument(documentId, folderId)");
    expect(table).toContain("event.dataTransfer.setData(DOCUMENT_DRAG, document.id);");
  });

  // Tushar's fix list, 16 Oct 2026.
  it("puts Read with in the list's head row, at its right edge, not in the page header", () => {
    const header = view.slice(view.indexOf('<header className="page-header">'), view.indexOf("</header>"));
    expect(header).not.toContain("readerSelect");
    const place = view.slice(view.indexOf("className={libraryStyles.placeHead}"), view.indexOf("{folderGone ?"));
    // Changed on purpose (PRD 1, screens): File management F2, the Department picker sits just
    // before Read with, in the same controls, where no folder decides where files go.
    expect(place).toMatch(/<div className=\{styles\.uploadControls\}>[\s\S]*<DepartmentPicker[\s\S]*\{readerSelect\}\s*<\/div>/);
  });

  it("writes Upload files with one space: its words are one span, not two flex items", () => {
    expect(view).toMatch(/<Icon name="upload" \/>\s*<span>\s*Upload<span className="narrow-hide"> files<\/span>\s*<\/span>/);
  });

  it("makes the search row as wide as the list, and the header's words a name search", () => {
    // Changed on purpose (UI rebuild, 21 Oct 2026): the head is a column (desktop/Documents.html),
    // so the search pill is a full row; the tools sit on the row under it.
    expect(docs).toMatch(/\.listHead \{[^}]*flex-direction: column;/);
    expect(view).toContain('useState<SearchMode>(startQ === "" ? DEFAULT_SEARCH_MODE : "names")');
    expect(view).toMatch(/if \(startQ !== ""\) \{\s*setSearch\(\{ view: viewKey, q: startQ, offset: 0 \}\);\s*setModeChoice\("names"\);/);
  });

  it("shows Uploaded as one short line, the exact date in its title and read out", () => {
    const now = Date.parse("2026-10-16T12:00:00Z");
    const before = (minutes: number) => new Date(now - minutes * 60_000).toISOString();
    expect(shortWhen(before(0), now)).toBe("Just now");
    expect(shortWhen(before(5), now)).toBe("5m ago");
    expect(shortWhen(before(120), now)).toBe("2h ago");
    expect(shortWhen(before(60 * 26), now)).toBe("1d ago");
    expect(shortWhen(before(60 * 24 * 9), now)).toBe("7 Oct 2026");
    expect(shortWhen("not a date", now)).toBe("");
    expect(table).toContain(
      '<time dateTime={when} title={formatDateTime(when)} className={styles.when} aria-hidden="true">',
    );
    expect(table).toContain('<span className="visually-hidden">{whenSpoken}</span>');
    expect(table).toContain('<span className={styles.bandBy} title={by}>');
  });

  it("says 7 flagged in full, and names its Review link with what and where", () => {
    const counts = { pages_unreadable: 0, pages_uncertain: 0, words_unsure: 0, values_to_review: 7 };
    // Changed on purpose (plain words, 25 Oct 2026): "to check", never "flagged".
    expect(flaggedShort(counts)).toEqual({ shown: "7 to check", spoken: "7 words to check" });
    expect(flaggedShort({ ...counts, values_to_review: 1 }).spoken).toBe("1 word to check");
    // Changed on purpose (owner, 29 Oct 2026, #452): one count shown, words and pages together,
    // so it never gets cut beside Review; the words and pages apart are its title and read out.
    expect(flaggedShort({ ...counts, pages_unreadable: 2 })).toEqual({
      shown: "9 to check",
      spoken: "7 words to check and 2 pages to check",
    });
    expect(flaggedShort({ ...counts, values_to_review: 0, pages_unreadable: 1 })).toEqual({
      shown: "1 to check",
      spoken: "1 page to check",
    });
    expect(flaggedShort({ ...counts, values_to_review: 0 }).shown).toBe("");
  });

  it("sets the whole table row in the UI's sans with tabular figures", () => {
    // Changed on purpose (Tushar, 16 Oct 2026, Documents table sans): the name, size, flagged
    // count and date were mono beside sans words. Values elsewhere stay mono (.mono).
    expect(table).not.toMatch(/className=\{?[`"][^`"]*\bmono\b/);
    expect(docs).toMatch(/table\.docs \{[^}]*font-variant-numeric: tabular-nums;/); // the body's sans, inherited
    expect(docs).toMatch(/\.bandBy,\s*\.byName \{[^}]*white-space: nowrap;[^}]*text-overflow: ellipsis;/);
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
    // Changed on purpose (PRD 9 F-14): numbered over the open marks (`at`); `index` stays the page's.
    expect(panels).toContain("{at + 1}/{shownMarks.length}");
    expect(panels).toContain("aria-label={name}");
    expect(read("components/viewer/PageViewer.tsx")).toContain('data-hot={hotMark === index ? "true" : undefined}');
  });

  it("DD9: Show reading problems is a toolbar toggle, its legend a tooltip that never takes the pointer", () => {
    const viewer = css("components/viewer/PageViewer.module.css");
    expect(viewer).not.toMatch(/\.problemBar\b/);
    expect(viewer).toMatch(/\.legendTip \{[^}]*pointer-events:\s*none;[^}]*visibility:\s*hidden;/);
  });
});

// QA 17 Oct (a11y): the bulk confirm group names its question, and "Review N flagged" fills its line.
describe("the bulk confirm and the status action", () => {
  it("describes the group by its question, and stretches the status action", () => {
    const table = read("components/documents/DocumentsTable.tsx");
    expect(table).toMatch(/role="group"[^>]*aria-describedby="bulk-confirm-question"\s*>\s*<span id="bulk-confirm-question">/);
    expect(css("components/documents/Documents.module.css")).toMatch(/\.statusAction \{[^}]*align-self: stretch;/);
  });
});
