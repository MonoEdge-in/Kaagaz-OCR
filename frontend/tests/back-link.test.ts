// #436 (Tushar, 28 Oct 2026): "If I come to any document from Ask, I must have a back to
// chat, not anywhere else." A document opened from Ask, Review, search, Home, My words or
// another document leads back there; one opened any other way keeps its folder back link.
// The helper (lib/backLink.ts) is tested as pure functions, the links as static renders,
// and the screens as source checks.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import type { ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AnswerView } from "@/components/chat/AnswerView";
import { DocumentRow, MentionsSection, RelatedRow, SourceCard } from "@/components/chat/SourceCard";
import { chatHref } from "@/components/chat/logic";
import { reviewHref } from "@/components/review/review";
import { searchHref } from "@/components/search/SearchScreen";
import { StoryMatches } from "@/components/stories/StoryMatches";
import { BackFrom, backFrom, withFrom } from "@/lib/backLink";
import type { Answer, Citation, Message } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

const CONVERSATION = "0f8fad5b-d9cb-469f-a165-70867728950e";
const CHAT = chatHref(CONVERSATION);

describe("backFrom: where ?from= leads back to", () => {
  it("names each known place from its own list, never from the address", () => {
    expect(backFrom(CHAT)).toEqual({ href: CHAT, name: "chat" });
    expect(backFrom("/chat")).toEqual({ href: "/chat", name: "chat" });
    expect(backFrom("/review?document_id=d1&reason=low_confidence")).toEqual({
      href: "/review?document_id=d1&reason=low_confidence",
      name: "Review",
    });
    expect(backFrom("/search?q=rent&in=names")).toEqual({ href: "/search?q=rent&in=names", name: "search results" });
    expect(backFrom("/dashboard")).toEqual({ href: "/dashboard", name: "Home" });
    expect(backFrom("/words")).toEqual({ href: "/words", name: "My words" });
    expect(backFrom("/documents/0f8fad5b-d9cb")).toEqual({ href: "/documents/0f8fad5b-d9cb", name: "previous document" });
  });

  it("keeps a search's own words as the address holds them, and a #part", () => {
    const search = searchHref("जालना rent"); // changed on purpose (#730): one search, no "in"
    expect(backFrom(search)?.href).toBe(search);
    expect(backFrom("/chat?c=1#answer-a1")?.href).toBe("/chat?c=1#answer-a1");
  });

  it("ignores no origin: a typed URL, a shared link, a folder or list click", () => {
    expect(backFrom(null)).toBeNull();
    expect(backFrom("")).toBeNull();
  });

  it("ignores anything that would leave the site", () => {
    for (const evil of [
      "//evil.com",
      "//evil.com/chat",
      "https://evil.com/chat",
      "http:/chat",
      "/\\evil.com",
      "\\\\evil.com",
      "/\\/evil.com",
      "javascript:alert(1)",
      "JavaScript:alert(1)//chat",
      "data:text/html,<script>alert(1)</script>",
      "/\t/evil.com",
      "/\n/evil.com",
      " /chat",
      "/chat ",
      "chat",
      "evil.com/chat",
      "/chat/../..//evil.com",
    ]) {
      expect(backFrom(evil), evil).toBeNull();
    }
  });

  it("ignores a path on this site that is not one of the places", () => {
    for (const other of ["/", "/admin", "/documents", "/documents?trash=1", "/documents/d1/compare", "/chat/x", "/login", "/%2F%2Fevil.com", "/documents/%2e%2e"]) {
      expect(backFrom(other), other).toBeNull();
    }
  });

  it("gives back the path as parsed, never dot segments that reach somewhere else", () => {
    expect(backFrom("/review/../chat?c=1")).toEqual({ href: "/chat?c=1", name: "chat" });
  });
});

describe("withFrom: a link that carries where it was opened", () => {
  it("adds ?from=, or &from= after a query, before any #part", () => {
    expect(withFrom("/documents/d1", "/dashboard")).toBe("/documents/d1?from=%2Fdashboard");
    expect(withFrom("/documents/d1?page=2", CHAT)).toBe(`/documents/d1?page=2&from=${encodeURIComponent(CHAT)}`);
    expect(withFrom("/documents/d1#your-story", "/words")).toBe("/documents/d1?from=%2Fwords#your-story");
  });

  it("round trips: the document page reads back the very place", () => {
    const from = reviewHref("d9", "readers_disagree");
    const link = new URL(withFrom("/documents/d1?page=2&words=rent", from), "https://kaagaz.test");
    expect(link.searchParams.get("page")).toBe("2");
    expect(link.searchParams.get("words")).toBe("rent");
    expect(backFrom(link.searchParams.get("from"))).toEqual({ href: from, name: "Review" });
  });

  it("leaves the link as it is with no origin, or one that is not a place", () => {
    expect(withFrom("/documents/d1?page=2", null)).toBe("/documents/d1?page=2");
    expect(withFrom("/documents/d1", "/admin")).toBe("/documents/d1");
    expect(withFrom("/documents/d1", "//evil.com")).toBe("/documents/d1");
  });
});

describe("Review keeps its filters in its address", () => {
  it("names the document and the reason, or neither", () => {
    expect(reviewHref(undefined, null)).toBe("/review");
    expect(reviewHref("d1", null)).toBe("/review?document_id=d1");
    expect(reviewHref(undefined, "low_confidence")).toBe("/review?reason=low_confidence");
    expect(reviewHref("d1", "low_confidence")).toBe("/review?document_id=d1&reason=low_confidence");
  });
});

// --- Every link from an Ask answer carries the conversation --------------------------

const citation = (n: number, documentId: string): Citation => ({
  n,
  kind: "value",
  ref_id: `v${n}`,
  document_id: documentId,
  document_name: `${documentId}.pdf`,
  detected_type: "pdf",
  page_no: 2,
  page_to: 3,
  regions: [{ kind: "box", page: 2, box: [0.1, 0.2, 0.3, 0.4] }],
  snippet: "total_amount: ₹1,234.00",
  label: "verified",
});

const answer = (overrides: Partial<Answer>): Message => ({
  id: "a1",
  role: "assistant",
  text: "",
  created_at: "2026-10-28T10:00:00Z",
  feedback: null,
  answer: {
    version: 2,
    status: "answered",
    label: "verified",
    sentences: [{ text: "The total is 1,234.", citations: [1, 2] }],
    citations: [citation(1, "d1"), citation(2, "d2")],
    documents: [{ document_id: "d1", title: "Invoice 1", why: "Total", citations: [1] }],
    closest_documents: [],
    related_documents: [
      { document_id: "r1", document_name: "r1.pdf", detected_type: "pdf", page_no: 4, regions: [], snippet: "total" },
    ],
    mentions: {
      terms: ["total"],
      total: 1,
      documents: [{ document_id: "m1", document_name: "m1.pdf", page_no: 1, pages: [1], snippet: "total" }],
    },
    route: "search",
    normalized_question: "total",
    model_used: true,
    ...overrides,
  },
});

const inChat = (child: ReactElement) => renderToStaticMarkup(createElement(BackFrom, { value: CHAT }, child));
const documentHrefs = (html: string) => [...html.matchAll(/href="(\/documents\/[^"]*)"/g)].map((m) => m[1]!.replace(/&amp;/g, "&"));
const fromOf = (href: string) => backFrom(new URL(href, "https://kaagaz.test").searchParams.get("from"));

describe("Ask's sources and citations lead back to the conversation", () => {
  it("every document link in an answer carries it: the numbers, the documents, the sources, Also in, Also mentions", () => {
    const hrefs = documentHrefs(inChat(createElement(AnswerView, { message: answer({}), question: "total?" })));
    for (const id of ["d1", "d2", "r1", "m1"]) expect(hrefs.some((href) => href.startsWith(`/documents/${id}`)), id).toBe(true);
    expect(hrefs.length).toBeGreaterThan(5);
    for (const href of hrefs) expect(fromOf(href), href).toEqual({ href: CHAT, name: "chat" });
  });

  it("so do a not-found answer's closest documents and its pages to check", () => {
    const notFound = answer({
      version: undefined,
      status: "not_found",
      label: null,
      sentences: [],
      citations: [],
      documents: undefined,
      related_documents: undefined,
      mentions: undefined,
      closest_documents: [{ id: "c1", display_name: "c1.pdf" }],
      reading_note: "Not found in the readable text.",
      unreadable_pages: [{ document_id: "u1", document_name: "u1.pdf", page_no: 3, read_quality: "unreadable" }],
      unreadable_page_count: 1,
    });
    const hrefs = documentHrefs(inChat(createElement(AnswerView, { message: notFound, question: "total?" })));
    for (const id of ["c1", "u1"]) expect(hrefs.some((href) => href.startsWith(`/documents/${id}`)), id).toBe(true);
    for (const href of hrefs) expect(fromOf(href), href).toEqual({ href: CHAT, name: "chat" });
  });

  it("each source row on its own carries it too", () => {
    const rows = [
      createElement(SourceCard, { citation: citation(1, "d1"), messageId: "a1" }),
      createElement(RelatedRow, { doc: answer({}).answer!.related_documents![0]!, terms: [] }),
      createElement(MentionsSection, { mentions: answer({}).answer!.mentions! }),
      createElement(DocumentRow, { id: "c1", name: "c1.pdf" }),
    ];
    for (const row of rows) {
      const hrefs = documentHrefs(inChat(row));
      expect(hrefs.length).toBeGreaterThan(0);
      for (const href of hrefs) expect(fromOf(href)?.name, href).toBe("chat");
    }
  });

  it("outside a screen that says where it is (the admin's Answer quality), the links are as before", () => {
    const html = renderToStaticMarkup(createElement(AnswerView, { message: answer({}), question: "total?", readOnly: true }));
    expect(documentHrefs(html).length).toBeGreaterThan(5);
    expect(html).not.toContain("from=");
  });

  it("the stories found beside an answer or a search carry it", () => {
    const story = createElement(StoryMatches, {
      matches: [{ document_id: "s1", display_name: "s1.pdf", doc_type: null, band: "match" }],
      headingLevel: 2,
    });
    expect(documentHrefs(inChat(story))).toEqual([`/documents/s1?from=${encodeURIComponent(CHAT)}`]);
    expect(renderToStaticMarkup(story)).toContain('href="/documents/s1"');
  });

  it("the chat route says which conversation, from its address", () => {
    const route = read("app/(app)/chat/page.tsx");
    expect(route).toContain("<BackFrom value={chatHref(conversationFromSearch(search))}>");
  });
});

// --- The other screens that open documents --------------------------------------------

describe("Review, search, Home, My words and a document say where they are", () => {
  it("Review: its cards' links carry the queue's address, which keeps the document and reason", () => {
    const queue = read("components/review/ReviewQueue.tsx");
    expect(queue).toContain('const reason = parseReason(searchParams.get("reason") ?? "");');
    expect(queue).toContain("<BackFrom value={reviewHref(documentId, reason)}>");
    expect(queue).toContain("useState<ReviewReason | null>(initialReason)");
    expect(queue).toContain('window.history.replaceState(null, "", reviewHref(documentId, choice.value));');
    const card = read("components/review/ReviewCard.tsx");
    expect(card).toContain("const link = withFrom(pageLink(item.document_id, value.region.page, value.region), from);");
    expect(card).toContain("{ scanned: true }), from)");
  });

  // Changed on purpose (#730): one search, by name or path; inside documents is the Documents screen's.
  it("search: the files found carry the search, with its words", () => {
    expect(read("app/(app)/search/page.tsx")).toContain("<BackFrom value={searchHref(q)}>");
    expect(read("components/documents/InsideResults.tsx")).toContain("href={withFrom(view.href, from)}");
    expect(read("components/search/SearchScreen.tsx")).toContain("href={withFrom(`/documents/${document.id}`, from)}");
    // The documents screen's own search sets no place, so its results keep the folder back link.
    expect(read("components/documents/DocumentsView.tsx")).not.toContain("BackFrom");
  });

  it("Home: the newest documents and the activity lead back to Home", () => {
    const home = read("components/dashboard/Dashboard.tsx");
    expect(home).toContain('const HOME = "/dashboard";');
    expect(home.match(/withFrom\(`\/documents\/\$\{(document\.id|linked)\}`, HOME\)/g)).toHaveLength(2);
  });

  it("My words: a suggestion's page leads back to My words", () => {
    expect(read("components/words/MyWordsScreen.tsx")).toContain('?page=${item.page_no}`, "/words")');
  });

  it("a related document, a version and a better scan lead back to the document they came from", () => {
    const from = "`/documents/${encodeURIComponent(detail.id)}`";
    expect(read("app/(app)/documents/[id]/DocumentPanels.tsx")).toContain(`withFrom(\`/documents/\${link.id}\`, ${from})`);
    expect(read("components/versions/VersionsPanel.tsx")).toContain(`withFrom(\`/documents/\${version.id}\`, ${from})`);
    expect(read("app/(app)/documents/[id]/PageProblems.tsx")).toContain(
      "withFrom(`/documents/${upload.id}`, `/documents/${encodeURIComponent(documentId)}`)",
    );
  });
});

describe("the document page's one back link", () => {
  const view = read("app/(app)/documents/[id]/DocumentView.tsx");

  it("reads ?from= from its address", () => {
    expect(read("app/(app)/documents/[id]/page.tsx")).toContain("fromParam={search.get(FROM_PARAM)}");
  });

  it("goes where the document was opened from, else to its folder, Documents or Trash as before", () => {
    expect(view).toMatch(/const back =\s*backFrom\(fromParam\) \?\?\s*\(detail\.deleted_at !== null/);
    expect(view).toContain('{ href: "/documents?trash=1", name: "Trash" }');
    expect(view).toContain("name: folder.name");
    // Still the one house link, in the same place: chevron left, "Back to …".
    expect(view).toMatch(/<Link href=\{back\.href\} className=\{styles\.back\}>\s*<Icon name="chevron-left" \/>\s*<span>\s*Back to <ScriptText text=\{back\.name\} \/>/);
  });

  it("says the same when the document could not be shown", () => {
    expect(view).toContain("<BackLink to={backFrom(fromParam)} />");
    expect(view).toMatch(/href=\{to\?\.href \?\? "\/documents"\}[\s\S]*?Back to \{to\?\.name \?\? "documents"\}/);
  });
});
