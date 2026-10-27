// Your words on the page (package H, 24 Oct 2026): a link into the viewer from an Ask answer
// (a citation, a listed document, a closest document) or a search result carries the asker's
// key words, never stop words, at most 8; the viewer reads them back, asks for the page's
// boxes and draws them under the cited spot in their own paper colour. Vrushit UAT 2 (27 Oct
// 2026, #412): no "Your words in this document" panel under the page. Static renders and the
// CSS source, as chat-answer-v2.test.ts and layout-guards.test.ts do.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { AnswerView } from "@/components/chat/AnswerView";
import { DocumentRow } from "@/components/chat/SourceCard";
import { citationLink, documentLink, linkWords, markParts, matchTerms } from "@/components/chat/answer";
import { resultView } from "@/components/documents/insideSearch";
import * as yourWords from "@/components/viewer/YourWords";
import { loadDocumentWords, wordColour } from "@/components/viewer/YourWords";
import type { Answer, Citation, FoundDocument } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
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

describe("on the page: the lit lines, and nothing under the page", () => {
  it("has no Your words panel under the page; the lines holding them stay lit (Vrushit UAT 2, #412)", () => {
    const view = read("app/(app)/documents/[id]/DocumentView.tsx");
    expect(view).not.toMatch(/<YourWords\b|wordsOn|Your words in this document|Next page with your words/);
    expect(view).toContain("const wordBoxes = wordsFound ? wordsFound.boxes : NO_REGIONS;");
    expect(view.match(/wordBoxes=\{wordBoxes\}/g)).toHaveLength(2); // the PDF viewer and PageViewer
    expect(Object.keys(yourWords).sort()).toEqual(["WORD_COLOURS", "loadDocumentWords", "wordColour"]);
    const css = read("components/viewer/PageViewer.module.css");
    expect(css).not.toMatch(/\.(yourWords|wordsHeading|wordLegend|legendRow|legendJump|wordSwatch|wordChips?|wordsNote)\b/);
  });

  it("draws each word's lines in its own paper colour, sweeping in only where motion is welcome", () => {
    const css = read("components/viewer/PageViewer.module.css").replace(/\/\*[\s\S]*?\*\//g, "");
    expect([wordColour(undefined), wordColour(0), wordColour(5)]).toEqual(["0", "0", "1"]);
    for (const [n, token] of [["1", "2"], ["2", "3"], ["3", "4"]]) {
      expect(css).toContain(`.word[data-word="${n}"] {\n  background: color-mix(in srgb, var(--words-on-paper-${token}) 14%, transparent);`);
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

describe("drawn under the spot, in their own colour", () => {
  const css = read("components/viewer/PageViewer.module.css").replace(/\/\*[\s\S]*?\*\//g, "");

  it("uses the one paper-fixed green token, drawn before the spot so it never hides it", () => {
    const globals = read("app/globals.css");
    expect(globals.match(/--words-on-paper: #[0-9a-f]+/g)).toEqual(["--words-on-paper: #1b7a4b"]);
    expect(css).toMatch(/\.word\s*\{[^}]*border-bottom: 3px solid var\(--words-on-paper\)/);
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
});
