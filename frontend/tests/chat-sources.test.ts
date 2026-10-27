// Compact, ranked sources in Ask (Tushar, 16 Oct 2026): no page image, a few lines of
// text with the question's words marked, most relevant first, "Also in" for documents the
// answer does not cite, and "Most relevant" to put one on top, kept for the answer.
// Also: the Ask column uses the full width (Tushar, 16 Oct 2026, Ask full width).

import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  markParts,
  matchTerms,
  mentionPages,
  rankedRelated,
  rankedSources,
  snippetAround,
  sourcesCount,
} from "@/components/chat/answer";
import { AnswerView } from "@/components/chat/AnswerView";
import { SourceCard } from "@/components/chat/SourceCard";
import { pinSource } from "@/lib/api";
import type { Answer, Citation, Message, RelatedDocument } from "@/lib/types";

const citation = (n: number, overrides: Partial<Citation> = {}): Citation => ({
  n,
  kind: "piece",
  ref_id: `p${n}`,
  document_id: `doc-${n}`,
  document_name: `Invoice ${n}.pdf`,
  detected_type: "pdf",
  page_no: n,
  regions: [{ kind: "box", page: n, box: [0.1, 0.2, 0.3, 0.4] }],
  snippet: `Seller: Sharma Traders\nGrand total: Rs. ${n},000`,
  label: "verified",
  ...overrides,
});

const related = (id: string, overrides: Partial<RelatedDocument> = {}): RelatedDocument => ({
  document_id: id,
  document_name: `${id}.pdf`,
  detected_type: "pdf",
  page_no: 4,
  regions: [{ kind: "box", page: 4, box: [0.1, 0.1, 0.5, 0.2] }],
  snippet: "Jalna depot\nGrand total paid in full",
  ...overrides,
});

const message = (answer: Partial<Answer>, pin: Message["pin"] = null): Message => ({
  id: "a1",
  role: "assistant",
  text: "",
  created_at: "2026-10-16T10:00:00Z",
  feedback: null,
  pin,
  answer: {
    status: "answered",
    label: "verified",
    sentences: [{ text: "The grand total is Rs. 2,000.", citations: [1, 2] }],
    citations: [citation(1, { rank: 3 }), citation(2, { rank: 1 })],
    closest_documents: [],
    route: "search",
    normalized_question: "grand total",
    model_used: true,
    ...answer,
  },
});

const render = (m: Message, question = "What is the grand total?") =>
  renderToStaticMarkup(createElement(AnswerView, { message: m, question }));

describe("a compact source card", () => {
  it("shows no page image, crop or boxes, only where the source is", () => {
    const html = renderToStaticMarkup(createElement(SourceCard, { citation: citation(1), messageId: "a1" }));
    expect(html).not.toMatch(/<img|<canvas|skeleton|preview/i);
    expect(html).toContain('title="Invoice 1.pdf"'); // the whole name, cut on screen
    expect(html).toContain("Page 1 · PDF");
    expect(html).toContain("Open page 1");
    const code = readFileSync("components/chat/SourceCard.tsx", "utf8");
    expect(code).not.toMatch(/PageViewer|useDocumentDetail|useNearView/);
  });

  it("marks the question's words in its text, as plain text", () => {
    const terms = matchTerms("What is the grand total?");
    expect(terms).toEqual(["grand", "total"]); // fillers left out, longest first
    expect(markParts("Grand Total: Rs. 1", terms)).toEqual([
      { text: "Grand", mark: true },
      { text: " ", mark: false },
      { text: "Total", mark: true },
      { text: ": Rs. 1", mark: false },
    ]);
    expect(markParts("<b>total</b>", ["total"]).map((part) => part.text).join("")).toBe("<b>total</b>");
    // Whole words only: never the "total" inside "Subtotal".
    expect(markParts("Subtotal: 1", ["total"])).toEqual([{ text: "Subtotal: 1", mark: false }]);
    expect(markParts("जालना total", ["जालना"])[0]).toEqual({ text: "जालना", mark: true });
    const html = renderToStaticMarkup(
      createElement(SourceCard, { citation: citation(1), messageId: "a1", terms }),
    );
    expect(html).toContain("<mark>Grand</mark> <mark>total</mark>");
  });

  it("starts its text near the first match and keeps at most four lines", () => {
    const long = `${"Header line with nothing.\n".repeat(20)}Grand total: Rs. 5,000\nThe end.`;
    const shown = snippetAround(long, ["grand"]);
    expect(shown.startsWith("…Grand total")).toBe(true);
    expect(snippetAround("Grand total first", ["grand"])).toBe("Grand total first");
    const css = readFileSync("components/chat/Chat.module.css", "utf8");
    expect(css).toMatch(/\.snippet \{[^}]*-webkit-line-clamp: 4;[^}]*line-clamp: 4;/);
    expect(css).toMatch(/\.snippetShort \{\s*-webkit-line-clamp: 2;\s*line-clamp: 2;/);
    expect(css).toMatch(/\.snippet mark \{[^}]*font-weight: 600;/); // never colour alone
    expect(css).toMatch(/\.sourceName \{[^}]*text-overflow: ellipsis;[^}]*white-space: nowrap;/);
  });
});

describe("sources in order of relevance", () => {
  it("moves only the picked one of two sources on the same page", () => {
    const same = { document_id: "doc-x", page_no: 1 };
    const list = [citation(1, { ...same, rank: 1 }), citation(2, { ...same, rank: 2 })];
    expect(rankedSources(list, { ...same, ref_id: "p2" }).map((c) => c.n)).toEqual([2, 1]);
    expect(rankedSources(list, { ...same, ref_id: "p1" }).map((c) => c.n)).toEqual([1, 2]);
    const html = render(message({ citations: list }, { ...same, ref_id: "p2" }));
    const pins = [...html.matchAll(/aria-pressed="(true|false)"[^>]*title="Most relevant"/g)].map((m) => m[1]);
    expect(pins).toEqual(["true", "false"]); // one pressed, not both
  });

  it("lists the search's best first, then the unranked, numbers as written", () => {
    const list = [citation(1, { rank: 4 }), citation(2, { rank: null }), citation(3, { rank: 1 }), citation(4)];
    expect(rankedSources(list).map((c) => c.n)).toEqual([3, 1, 2, 4]);
    expect(rankedSources(list, { document_id: "doc-2", page_no: 2 }).map((c) => c.n)).toEqual([2, 3, 1, 4]);
  });

  it("renders the Sources bar most relevant first, the chips in the text unchanged", () => {
    const html = render(message({}));
    const order = [...html.matchAll(/<article id="source-a1-(\d)"/g)].map((match) => match[1]);
    expect(order).toEqual(["2", "1"]);
    // Changed on purpose (Vrushit UAT 3, 27 Oct 2026): each number is a link that opens its
    // document at the cited spot, lit; it was a button that moved to the source card.
    expect(html).toMatch(/<a[^>]*aria-label="Source 1: Invoice 1.pdf, page 1" href="\/documents\/doc-1\?page=1&amp;region=[^"]+"[^>]*>1<\/a>/);
    expect(html).toMatch(/aria-label="Source 1[^"]*"[^>]*>1<\/a><a[^>]*aria-label="Source 2/);
  });

  it("puts the pinned source on top after a reload, pressed", () => {
    const html = render(message({}, { document_id: "doc-1", page_no: 1 }));
    const order = [...html.matchAll(/<article id="source-a1-(\d)"/g)].map((match) => match[1]);
    expect(order).toEqual(["1", "2"]);
    expect(html).toMatch(/<li class="motion-rise"><article id="source-a1-1"/);
    const pins = [...html.matchAll(/aria-pressed="(true|false)"[^>]*title="Most relevant"/g)].map((m) => m[1]);
    expect(pins).toEqual(["true", "false"]);
    expect(html).toContain('title="Most relevant"');
  });
});

describe("Also in", () => {
  it("lists the documents the answer does not cite, under the sources, and counts them", () => {
    const html = render(message({ related_documents: [related("ledger"), related("register")] }));
    expect(html).toContain(">Also in</h3>");
    expect(html.indexOf("ledger.pdf")).toBeGreaterThan(html.indexOf("Invoice 2.pdf"));
    expect(html).toContain("Sources</span><span");
    expect(html).toContain(">2 · Also in 2 documents<");
    expect(html).toContain('href="/documents/ledger?page=4');
    expect(html).toContain("<mark>Grand</mark> <mark>total</mark>");
  });

  it("puts a pinned document first among them", () => {
    const list = [related("a"), related("b")];
    expect(rankedRelated(list, { document_id: "b", page_no: 4 }).map((d) => d.document_id)).toEqual(["b", "a"]);
    expect(rankedRelated(list, null)).toEqual(list);
  });

  it("says the counts in the Sources bar", () => {
    expect(sourcesCount(4, 3)).toBe("4 · Also in 3 documents");
    expect(sourcesCount(1, 1, 2)).toBe("1 · Also in 1 document · 2 pages to check");
    expect(sourcesCount(2, 0)).toBe("2");
  });

  it("not found's closest documents use the same compact rows", () => {
    const html = render(
      message({ status: "not_found", label: null, sentences: [], citations: [], closest_documents: [{ id: "d9", display_name: "Bill 9.pdf" }] }),
    );
    // Changed on purpose (package H, 24 Oct 2026): the row opens with the question's words lit.
    expect(html).toMatch(/<a class="[^"]*source[^"]* [^"]*documentRow[^"]*" title="Bill 9.pdf" href="\/documents\/d9\?words=grand\+total">/);
  });
});

describe("Also mentions (Tushar, 17 Oct 2026)", () => {
  const mentions = {
    terms: ["Jalna", "जालना"],
    total: 14,
    documents: [
      { document_id: "deed", document_name: "deed.pdf", page_no: 3, pages: [3], snippet: "Village Jalna\nTaluka Ambad" },
      { document_id: "satbara", document_name: "7-12.pdf", page_no: 2, pages: [2, 5], snippet: "जिल्हा : जालना\nend" },
    ],
  };

  it("lists the other documents with the terms, their pages and marked lines, last in the bar", () => {
    const html = render(message({ related_documents: [related("ledger")], mentions }));
    expect(html).toContain("Also mentions “Jalna” · “जालना”");
    expect(html).toContain("· 14</span>");
    expect(html.indexOf("deed.pdf")).toBeGreaterThan(html.indexOf("ledger.pdf"));
    expect(html).toContain(">Page 3<");
    expect(html).toContain(">Pages 2, 5<");
    expect(html).toContain("Village <mark>Jalna</mark>");
    expect(html).toContain("<mark>जालना</mark>");
    // Changed on purpose (package H): it opens with the terms lit, as a citation's link does.
    const href = /href="(\/documents\/satbara\?[^"]*)"/.exec(html)?.[1]?.replace(/&amp;/g, "&") ?? "";
    const url = new URL(href, "http://x");
    expect(url.searchParams.get("page")).toBe("2");
    expect(url.searchParams.get("words")).toBe("jalna जालना");
    expect(html).toContain("and 12 more");
    expect(html).toContain(">2 · Also in 1 document · Mentioned in 14 more documents<");
    expect(html).not.toMatch(/Needs review|Review|₹|cost|price/i);
  });

  it("shows under a not found answer too, and nothing when there is none", () => {
    const notFound = { status: "not_found" as const, label: null, sentences: [], citations: [] };
    expect(render(message({ ...notFound, mentions }))).toContain("Also mentions");
    expect(render(message({}))).not.toContain("Also mentions");
    expect(render(message({ mentions: { ...mentions, documents: [] } }))).not.toContain("Also mentions");
    expect(mentionPages([1, 2, 3, 4, 5, 6, 7], 1)).toBe("Pages 1, 2, 3, 4, 5 and 2 more");
    expect(sourcesCount(1, 0, 0, 1)).toBe("1 · Mentioned in 1 more document");
  });
});

describe("Most relevant is kept", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("sends the message, the source and where it was", async () => {
    const fetch = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetch);
    await pinSource("m1", { document_id: "d1", page_no: 3 }, 2);
    expect(fetch).toHaveBeenCalledWith(
      "/api/messages/m1/pin",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ document_id: "d1", page_no: 3, previous_rank: 2 }),
      }),
    );
  });
});

describe("the Ask column", () => {
  // Changed on purpose (Tushar, 16 Oct 2026, Ask full width): was a 46rem column (UI audit A7).
  it("uses the full width of the main area, answers and the composer alike", () => {
    const css = readFileSync("components/chat/Chat.module.css", "utf8");
    const inner = /\.threadInner \{([^}]*)\}/.exec(css)?.[1] ?? "";
    expect(inner).toContain("width: 100%;");
    expect(inner).not.toContain("max-width");
    expect(/\.answerText \{([^}]*)\}/.exec(css)?.[1]).not.toContain("max-width");
  });
});
