// Your words on the page (package H, 24 Oct 2026): a link into the viewer from an Ask answer
// (a citation, a listed document, a closest document) or a search result carries the asker's
// key words, never stop words, at most 8; the viewer reads them back, asks for the page's
// boxes, draws them under the cited spot in their own paper colour, and says so under the page
// with a toggle and "Next page with your words". The layout holds at 320px (chips of their
// own, never .btn, shrinking, Devanagari marked). Static renders and the CSS source, as
// chat-answer-v2.test.ts and layout-guards.test.ts do.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { AnswerView } from "@/components/chat/AnswerView";
import { DocumentRow } from "@/components/chat/SourceCard";
import { citationLink, documentLink, linkWords, markParts, matchTerms } from "@/components/chat/answer";
import { resultView } from "@/components/documents/insideSearch";
import {
  NEXT_WORDS_PAGE,
  SHOW_WORDS,
  YourWords,
  loadDocumentWords,
  nextWordsPage,
  WORDS_HEADING,
  pagesText,
  wordColour,
  wordWhereText,
} from "@/components/viewer/YourWords";
import type { Answer, Citation, DocumentWords, FoundDocument } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const text = (html: string) => html.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
const wordsOf = (href: string) => new URL(href, "https://kaagaz.test").searchParams.get("words");

const QUESTION = "Which land does Krishna Sohoni own in Maval?";

const citation: Citation = {
  n: 1,
  kind: "piece",
  ref_id: "p1",
  document_id: "d1",
  document_name: "sale-deed.pdf",
  detected_type: "pdf",
  page_no: 2,
  regions: [{ kind: "box", page: 2, box: [0.1, 0.2, 0.3, 0.4] }],
  snippet: "Buyer: Krishna Sohoni, village Maval",
  label: "verified",
};

const answer = (overrides: Partial<Answer>): Answer => ({
  version: 2,
  status: "answered",
  label: "verified",
  sentences: [{ text: "Krishna Sohoni owns land in Maval.", citations: [1] }],
  citations: [citation],
  closest_documents: [],
  documents: [{ document_id: "d1", title: "Sale deed", why: "Buyer", citations: [1] }],
  assumptions: [],
  route: "search",
  normalized_question: "land krishna sohoni maval",
  model_used: true,
  ...overrides,
});

const renderAnswer = (value: Answer) =>
  renderToStaticMarkup(
    createElement(AnswerView, {
      message: { id: "a1", role: "assistant", text: "", created_at: "2026-10-24T13:00:00Z", answer: value, feedback: null },
      question: QUESTION,
    }),
  );
const hrefs = (html: string) => [...html.matchAll(/href="([^"]+)"/g)].map((match) => match[1]!.replace(/&amp;/g, "&"));

describe("a link into the viewer carries the asker's words", () => {
  it("takes the question's content words, never stop words, at most 8, each at most 40 characters", () => {
    expect(linkWords(QUESTION).sort()).toEqual(["krishna", "land", "maval", "own", "sohoni"]);
    const many = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo";
    expect(linkWords(many)).toHaveLength(8);
    expect(linkWords(`${"k".repeat(60)} maval`)).toEqual(["k".repeat(40), "maval"]);
    expect(linkWords(null)).toEqual([]);
  });

  it("keeps a number or an identifier whole, never its tail (package H review)", () => {
    const words = linkWords("Was 1,042 paid on INV-1042, the ₹1,04,200 for 7/12?");
    expect(words).toEqual(expect.arrayContaining(["1,042", "inv-1042", "1,04,200", "7/12"]));
    expect(words).not.toEqual(expect.arrayContaining(["042"]));
    expect(words).not.toContain("inv");
    expect(words).not.toContain("200");
    const marked = markParts("Rs. 3,042 and Rs. 1,042", matchTerms("1,042")).filter((part) => part.mark);
    expect(marked.map((part) => part.text)).toEqual(["1,042"]);
  });

  it("from a citation, a listed document's page and a closest document", () => {
    const terms = matchTerms(QUESTION);
    expect(wordsOf(citationLink(citation, terms))?.split(" ").sort()).toEqual(["krishna", "land", "maval", "own", "sohoni"]);
    expect(citationLink(citation)).not.toContain("words=");
    expect(documentLink("d 2", terms)).toMatch(/^\/documents\/d%202\?words=/);
    expect(documentLink("d2")).toBe("/documents/d2");

    const listed = hrefs(renderAnswer(answer({}))).filter((href) => href.startsWith("/documents/d1?page=2"));
    expect(listed.length).toBeGreaterThan(0);
    for (const href of listed) expect(wordsOf(href)).toContain("krishna");

    const closest = answer({ status: "not_found", sentences: [], citations: [], documents: [{ document_id: "d3", title: "Lease", why: "", citations: [] }] });
    const open = hrefs(renderAnswer(closest)).find((href) => href.startsWith("/documents/d3"));
    expect(wordsOf(open ?? "")).toContain("maval");

    const row = renderToStaticMarkup(createElement(DocumentRow, { id: "d4", name: "lease.pdf", terms }));
    expect(wordsOf(hrefs(row)[0] ?? "")).toContain("sohoni");
  });

  it("from a search result", () => {
    const found: FoundDocument = {
      id: "d5",
      display_name: "7-12.pdf",
      doc_type: null,
      score: 0.03,
      best: { snippet: "Krishna Sohoni", page_from: 3, page_to: 3, regions: [{ kind: "box", page: 3, box: [0.1, 0.1, 0.5, 0.2] }] },
      more: 0,
    };
    expect(wordsOf(resultView(found, "krishna sohoni the").href)?.split(" ").sort()).toEqual(["krishna", "sohoni"]);
    expect(resultView(found).href).not.toContain("words=");
  });

  it("is asked once a document and words, never again as the pages change (package H review)", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ boxes: [], pages: [] }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    // Each page the viewer shows (a PDF's scroll changes it page by page) asks as the screen does.
    for (let page = 1; page <= 12; page += 1) await loadDocumentWords("d1", ["krishna", "maval"], 0);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toContain("/documents/d1/words?w=krishna&w=maval");
    await loadDocumentWords("d1", ["krishna", "maval"], 1); // read again: asked anew
    await loadDocumentWords("d2", ["krishna", "maval"], 1);
    await loadDocumentWords("d2", ["krishna", "maval"], 1);
    vi.unstubAllGlobals();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    // The screen's hook takes no page: it asks per document, words and reload only.
    const view = read("app/(app)/documents/[id]/DocumentView.tsx");
    expect(view).toContain("useDocumentWords(documentId, yourWords, reloads)");
    expect(view).toMatch(/\}, \[documentId, key, wanted, version\]\);/);
  });
});

describe("under the page: the note, the toggle and the next page", () => {
  const on = (page: number) => ({ kind: "box" as const, page, box: [0.1, 0.2, 0.9, 0.25] as [number, number, number, number] });
  const box = on(2);
  // Changed on purpose (24 Oct 2026): the server says which word lit each line and where each
  // word is; the legend under the page shows them (the old one-line note is gone).
  const found = (boxes: DocumentWords["boxes"], pages: number[], hits: number[] = boxes.map(() => 0)): DocumentWords => ({
    boxes,
    pages,
    hits,
    found: [
      { word: "krishna", lines: hits.filter((hit) => hit === 0).length, pages: [...new Set(boxes.filter((_, i) => hits[i] === 0).map((b) => b.page))] },
      { word: "maval", lines: hits.filter((hit) => hit === 1).length, pages: [...new Set(boxes.filter((_, i) => hits[i] === 1).map((b) => b.page))] },
    ],
  });
  const render = (props: Partial<Parameters<typeof YourWords>[0]>) =>
    renderToStaticMarkup(
      createElement(YourWords, {
        words: ["krishna", "maval"],
        found: found([box], [2, 5]),
        pageNo: 2,
        on: true,
        onToggle: () => undefined,
        onPage: () => undefined,
        ...props,
      }),
    );

  it("says which words are lit, with a pressed toggle and the next page with them", () => {
    const html = render({});
    expect(text(html)).toContain(WORDS_HEADING);
    expect(text(html)).toContain("krishna 1 line on this page");
    expect(text(html)).toContain("maval not in this document");
    expect(html).toMatch(new RegExp(`<button type="button" class="[^"]*wordChip[^"]*" aria-pressed="true">[\\s\\S]*?${SHOW_WORDS}</button>`));
    expect(text(html)).toContain(NEXT_WORDS_PAGE);
  });

  it("turned off: no note, the toggle not pressed", () => {
    const html = render({ on: false });
    expect(text(html)).not.toContain(WORDS_HEADING);
    expect(html).toContain('aria-pressed="false"');
  });

  it("shows nothing extra on a page with no match, only the way to one that has", () => {
    // Changed on purpose (24 Oct 2026): words in no line are said plainly, not left unexplained.
    expect(text(render({ found: found([], []) }))).toContain("krishna not in this document");
    expect(render({ words: [] })).toBe("");
    const elsewhere = render({ found: found([on(5)], [5]) });
    expect(text(elsewhere)).toContain("krishna on page 5");
    expect(elsewhere).not.toContain(SHOW_WORDS);
    expect(text(elsewhere)).toContain(NEXT_WORDS_PAGE);
    expect([nextWordsPage([1, 2, 5], 2), nextWordsPage([1, 2], 2), nextWordsPage([2], 2)]).toEqual([5, 1, null]);
  });

  it("keeps the result and the focused Next button in place as the page changes (package H review)", () => {
    const both = found([on(2), on(5)], [2, 5]);
    const buttons = (html: string) => [...html.matchAll(/<button[^>]*>[\s\S]*?<\/button>/g)].map((m) => text(m[0]).trim());
    // Page 2, then page 5 reached by Next: the same result, Next still the last button of the row.
    expect(buttons(render({ found: both, pageNo: 2 }))).toEqual([SHOW_WORDS, NEXT_WORDS_PAGE]);
    expect(buttons(render({ found: both, pageNo: 5 }))).toEqual([SHOW_WORDS, NEXT_WORDS_PAGE]);
    expect(buttons(render({ found: both, pageNo: 3 }))).toEqual([NEXT_WORDS_PAGE]);
    // A page reached with no next: the focus goes to its toggle (no DOM in these tests: the source).
    const source = read("components/viewer/YourWords.tsx");
    expect(source).toContain("if (moved.current && next === null) toggle.current?.focus();");
    expect(source).toMatch(/<button ref=\{toggle\}/);
  });

  it("marks Devanagari words as Marathi", () => {
    const marathi = found([box], [2]);
    marathi.found = [{ word: "कृष्ण", lines: 1, pages: [2] }];
    expect(render({ words: ["कृष्ण", "maval"], found: marathi })).toContain('<span lang="mr">कृष्ण</span>');
  });

  it("counts a sheet's lit cells as on the page, so a spreadsheet gets the note and toggle", () => {
    const html = render({ found: found([{ kind: "cell", page: 2, row: 3, column: 2 }], [2]) });
    expect(text(html)).toContain("krishna 1 line on this page");
    expect(text(html)).toContain(SHOW_WORDS);
  });

  it("colours each word, and says where each one is, with a jump to its page", () => {
    const html = render({ found: found([on(2), on(5), on(7)], [2, 5, 7], [0, 0, 1]), pageNo: 2 });
    expect(html.match(/data-word="0"/g)).toHaveLength(1);
    expect(html.match(/data-word="1"/g)).toHaveLength(1);
    expect(text(html)).toContain("krishna 1 line on this page");
    expect(text(html)).toContain("maval on page 7");
    // Next goes to page 5; maval's own jump goes further, to 7 (never two buttons to one page).
    expect(html).toMatch(/<button type="button" class="[^"]*legendJump[^"]*">Page 7<span class="visually-hidden">, where maval is<\/span><\/button>/);
    expect([wordColour(undefined), wordColour(0), wordColour(5)]).toEqual(["0", "0", "1"]);
    expect([pagesText([3]), pagesText([3, 5]), pagesText([2, 5, 7, 9]), pagesText([1, 2, 3, 4, 5, 6])]).toEqual([
      "page 3",
      "pages 3 and 5",
      "pages 2, 5, 7 and 9",
      "pages 1, 2, 3 and 3 more",
    ]);
    expect(wordWhereText({ word: "x", lines: 0, pages: [] }, 0)).toBe("not in this document");
    expect(wordWhereText({ word: "x", lines: 3, pages: [1] }, 2)).toBe("2 lines on this page");
  });

  it("draws each word's lines in its own paper colour, sweeping in only where motion is welcome", () => {
    const css = read("components/viewer/PageViewer.module.css").replace(/\/\*[\s\S]*?\*\//g, "");
    for (const [n, token] of [["1", "2"], ["2", "3"], ["3", "4"]]) {
      expect(css).toContain(`.word[data-word="${n}"],\n.wordSwatch[data-word="${n}"] {\n  background: color-mix(in srgb, var(--words-on-paper-${token}) 14%, transparent);`);
    }
    const calm = css.slice(css.indexOf("@media (prefers-reduced-motion: no-preference)"));
    expect(calm).toMatch(/\.word \{\s*animation: word-sweep var\(--motion-calm\) var\(--ease-out\) both;/);
    const globals = read("app/globals.css");
    for (const hex of ["a35c00", "6a3fb5", "b0285c"]) {
      const channel = (part: string) => {
        const value = parseInt(part, 16) / 255;
        return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
      };
      const ink = 0.2126 * channel(hex.slice(0, 2)) + 0.7152 * channel(hex.slice(2, 4)) + 0.0722 * channel(hex.slice(4));
      expect(globals).toContain(`#${hex};`);
      expect((1 + 0.05) / (ink + 0.05)).toBeGreaterThanOrEqual(4.5); // readable against the white page
    }
    for (const file of ["components/viewer/PageViewer.tsx", "components/viewer/PdfDocumentViewer.tsx"]) {
      expect(read(file)).toContain("data-word={wordColour(wordHits[index])}");
    }
  });
});

// A spreadsheet shows cells, not a page image (package H): the full-page grid gets the same
// regions the page lines come from (GET /documents/{id}/words, matched on the server by the
// page's rules) and underlines this sheet's cells in the same green; never in a crop preview.
describe("your words in a spreadsheet's cells", () => {
  const css = read("components/viewer/PageViewer.module.css").replace(/\/\*[\s\S]*?\*\//g, "");
  const grid = read("components/viewer/SheetGrid.tsx");
  const viewer = read("components/viewer/PageViewer.tsx");

  it("passes the words' regions to the full-page grid only, which lights this sheet's cells", () => {
    expect(viewer.match(/wordCells=\{wordBoxes\}/g)).toHaveLength(1);
    expect(viewer.match(/<SheetGrid[^>]*crop=/)?.[0]).not.toContain("wordCells");
    expect(grid).toContain("const lit = highlightedCells(wordCells, page.page_no);");
    expect(grid).toMatch(/word && styles\.cellWord/);
    expect(grid).toContain('{word ? <span className="visually-hidden"> (your words)</span> : null}');
  });

  it("uses the words' green token, under the cited spot's fill", () => {
    expect(css).toMatch(/\.cellWord\s*\{[^}]*text-decoration: underline 3px var\(--words-on-paper\)/);
    expect(css.indexOf(".cellWord {")).toBeLessThan(css.indexOf(".cellHit {"));
  });
});

describe("drawn under the spot, in their own colour, and the 320px layout", () => {
  const css = read("components/viewer/PageViewer.module.css").replace(/\/\*[\s\S]*?\*\//g, "");
  const body = (selector: string) => {
    const at = css.search(new RegExp(`(^|\\n)${selector.replace(".", "\\.")}\\s*\\{`));
    expect(at, selector).toBeGreaterThanOrEqual(0);
    return css.slice(css.indexOf("{", at) + 1, css.indexOf("}", at));
  };

  it("uses the one paper-fixed green token, drawn before the spot so it never hides it", () => {
    const globals = read("app/globals.css");
    expect(globals.match(/--words-on-paper: #[0-9a-f]+/g)).toEqual(["--words-on-paper: #1b7a4b"]);
    expect(css).toMatch(/\.word,\s*\.wordSwatch\s*\{[^}]*border-bottom: 3px solid var\(--words-on-paper\)/);
    expect(css).not.toMatch(/\.word[^{]*\{[^}]*#[0-9a-f]{3,6}/i);
    for (const file of ["components/viewer/PageViewer.tsx", "components/viewer/PdfDocumentViewer.tsx"]) {
      const source = read(file);
      expect(source.indexOf("styles.word}"), file).toBeGreaterThan(0);
      expect(source.indexOf("styles.word}"), file).toBeLessThan(source.indexOf("styles.box"));
    }
    const channel = (hex: string) => {
      const value = parseInt(hex, 16) / 255;
      return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    };
    const ink = 0.2126 * channel("1b") + 0.7152 * channel("7a") + 0.0722 * channel("4b");
    expect((1 + 0.05) / (ink + 0.05)).toBeGreaterThanOrEqual(3); // WCAG 1.4.11 against white paper
  });

  it("gives the chips their own class, never .btn, wrapping and shrinking at 320px", () => {
    expect(body(".wordChips")).toMatch(/flex-wrap:\s*wrap/);
    expect(body(".wordChip")).toMatch(/min-width:\s*0/);
    expect(body(".wordChip")).toMatch(/max-width:\s*100%/);
    expect(body(".wordChip")).toMatch(/overflow-wrap:\s*break-word/);
    expect(body(".wordChip")).toMatch(/min-height:\s*var\(--control-h\)/);
    expect(body(".yourWords")).toMatch(/min-width:\s*0/);
    expect(body(".wordsNote")).toMatch(/overflow-wrap:\s*break-word/);
    expect(read("components/viewer/YourWords.tsx")).not.toMatch(/\bbtn\b/);
  });
});
