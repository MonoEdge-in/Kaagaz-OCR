import { describe, expect, it } from "vitest";
import {
  answerScrollTop,
  askErrorText,
  chatHref,
  conversationFromSearch,
  conversationGroup,
  conversationTime,
  groupConversations,
  isNearBottom,
  labelPlace,
  NEAR_BOTTOM_PX,
  pageJumps,
  openText,
  sourceMeta,
  sourcePlace,
  STILL_LOOKING_AFTER_MS,
} from "@/components/chat/logic";
import { formatDate } from "@/lib/format";
import { decodeRegion } from "@/lib/regions";
import type { Citation, ConversationSummary, Region } from "@/lib/types";

const at = (iso: string) => Date.parse(iso);

// Noon on 27 Sep 2026 in India (06:30 UTC).
const NOON_IST = at("2026-09-27T12:00:00+05:30");

const conversation = (id: string, created_at: string): ConversationSummary => ({
  id,
  title: `Conversation ${id}`,
  created_at,
});

describe("grouping conversations by the India day they were started", () => {
  it("puts a conversation from just after midnight India time under Today, though UTC still says yesterday", () => {
    expect(conversationGroup("2026-09-26T18:35:00Z", NOON_IST)).toBe("today");
  });

  it("splits Yesterday from Earlier at India midnight", () => {
    expect(conversationGroup("2026-09-26T00:00:00+05:30", NOON_IST)).toBe("yesterday");
    expect(conversationGroup("2026-09-26T23:59:59+05:30", NOON_IST)).toBe("yesterday");
    expect(conversationGroup("2026-09-25T23:59:59+05:30", NOON_IST)).toBe("earlier");
  });

  it("uses India time for now as well: ten past midnight makes 11:59 pm yesterday", () => {
    const justAfterMidnight = at("2026-09-27T00:10:00+05:30");
    expect(conversationGroup("2026-09-26T23:59:00+05:30", justAfterMidnight)).toBe("yesterday");
    expect(conversationGroup("2026-09-27T00:01:00+05:30", justAfterMidnight)).toBe("today");
  });

  it("counts a time a little ahead of now as Today and an unreadable time as Earlier", () => {
    expect(conversationGroup("2026-09-27T12:05:00+05:30", NOON_IST)).toBe("today");
    expect(conversationGroup("not a date", NOON_IST)).toBe("earlier");
  });

  it("orders the groups Today, Yesterday, Earlier, keeps order inside each, and leaves out empty ones", () => {
    const groups = groupConversations(
      [
        conversation("a", "2026-09-27T11:00:00+05:30"),
        conversation("b", "2026-09-20T10:00:00+05:30"),
        conversation("c", "2026-09-27T09:00:00+05:30"),
        conversation("d", "2026-09-10T10:00:00+05:30"),
      ],
      NOON_IST,
    );
    expect(groups.map((g) => g.title)).toEqual(["Today", "Earlier"]);
    expect(groups[0]?.items.map((i) => i.id)).toEqual(["a", "c"]);
    expect(groups[1]?.items.map((i) => i.id)).toEqual(["b", "d"]);
  });

  it("gives no groups for no conversations", () => {
    expect(groupConversations([], NOON_IST)).toEqual([]);
  });

  it("shows the India time for Today and Yesterday, the date for Earlier", () => {
    expect(conversationTime("2026-09-27T09:00:00Z", "today")).toMatch(/^2:30\spm$/u);
    expect(conversationTime("2026-09-20T09:00:00Z", "earlier")).toBe(formatDate("2026-09-20T09:00:00Z"));
    expect(conversationTime("nope", "today")).toBe("");
  });
});

describe("following new answers", () => {
  it("counts as near the end within the threshold, not beyond", () => {
    expect(isNearBottom({ scrollTop: 900, scrollHeight: 1500, clientHeight: 600 })).toBe(true);
    expect(
      isNearBottom({ scrollTop: 1500 - 600 - NEAR_BOTTOM_PX, scrollHeight: 1500, clientHeight: 600 }),
    ).toBe(true);
    expect(
      isNearBottom({ scrollTop: 1500 - 600 - NEAR_BOTTOM_PX - 1, scrollHeight: 1500, clientHeight: 600 }),
    ).toBe(false);
  });

  it("scrolls a long new answer to its first line, and a short one only to the end", () => {
    // Answer starts at 1000 in a 3000 px thread with a 600 px view: show its top.
    expect(answerScrollTop({ scrollHeight: 3000, clientHeight: 600 }, 1000)).toBe(984);
    // The end is at 2400 - 600 = 1800, before the answer's top: stop at the end.
    expect(answerScrollTop({ scrollHeight: 2400, clientHeight: 600 }, 2000)).toBe(1800);
    // A thread shorter than the view never scrolls.
    expect(answerScrollTop({ scrollHeight: 400, clientHeight: 600 }, 100)).toBe(0);
  });

  it("says it is still looking after ten seconds", () => {
    expect(STILL_LOOKING_AFTER_MS).toBe(10_000);
  });
});

describe("source cards", () => {
  const citation = (overrides: Partial<Citation> = {}): Citation => ({
    n: 1,
    kind: "piece",
    ref_id: "p1",
    document_id: "doc-1",
    document_name: "Invoice.pdf",
    detected_type: "pdf",
    page_no: 3,
    regions: [],
    snippet: "Total 1,234",
    label: "verified",
    ...overrides,
  });

  it("says Page for PDFs and images, Sheet for spreadsheets and CSV", () => {
    expect(sourcePlace(citation())).toBe("Page 3");
    expect(sourcePlace(citation({ detected_type: "jpeg" }))).toBe("Page 3");
    expect(sourcePlace(citation({ detected_type: "zip" }))).toBe("Sheet 3");
    expect(sourcePlace(citation({ detected_type: "ole2" }))).toBe("Sheet 3");
    // Changed on purpose (#522): a CSV is csv once read, and a text file has pages.
    expect(sourcePlace(citation({ detected_type: "csv" }))).toBe("Sheet 3");
    expect(sourcePlace(citation({ detected_type: "xlsx" }))).toBe("Sheet 3");
    expect(sourcePlace(citation({ detected_type: "txt" }))).toBe("Page 3");
    expect(sourcePlace(citation({ detected_type: "html" }))).toBe("Page 3");
    expect(sourcePlace(citation({ detected_type: "heic" }))).toBe("Page 3");
    expect(sourcePlace(citation({ detected_type: "xls" }))).toBe("Sheet 3");
    expect(sourcePlace(citation({ detected_type: "docx" }))).toBe("Page 3");
  });

  it("writes the meta line as place and file kind", () => {
    expect(sourceMeta(citation())).toBe("Page 3 · PDF");
    // Changed on purpose (#522): a read spreadsheet is xlsx.
    expect(sourceMeta(citation({ detected_type: "xlsx", page_no: 2 }))).toBe("Sheet 2 · Spreadsheet");
    expect(sourceMeta(citation({ detected_type: "md", page_no: 2 }))).toBe("Page 2 · Markdown");
  });

  // Changed on purpose (Tushar, 16 Oct 2026: no Needs review in Ask): every source
  // opens its page the same way; no "Check page 3" and no reason to check.
  it("opens the page of every source the same way, whatever its label", () => {
    expect(openText(citation())).toBe("Open page 3");
    expect(openText(citation({ label: "needs_review", kind: "value" }))).toBe("Open page 3");
    expect(openText(citation({ label: "needs_review", detected_type: "zip" }))).toBe("Open sheet 3");
  });

  it("names the pages a source covers", () => {
    expect(sourcePlace(citation({ page_no: 15, page_from: 15, page_to: 16 }))).toBe("Pages 15 to 16");
    expect(sourcePlace(citation({ page_no: 15, page_from: 15, page_to: 15 }))).toBe("Page 15");
    expect(sourceMeta(citation({ page_from: 3, page_to: 4 }))).toBe("Pages 3 to 4 · PDF");
    expect(sourcePlace(citation({ detected_type: "zip", page_from: 1, page_to: 2 }))).toBe(
      "Sheets 1 to 2",
    );
  });

  it("says where a value's label is only when it is on another page", () => {
    const value = { kind: "value" as const, page_no: 16, page_from: 16, page_to: 16 };
    expect(labelPlace(citation({ ...value, label_page_no: 15 }))).toBe(
      "Label on page 15, value on page 16",
    );
    expect(labelPlace(citation({ ...value, label_page_no: 16 }))).toBeNull();
    expect(labelPlace(citation({ ...value, label_page_no: null }))).toBeNull();
    expect(labelPlace(citation(value))).toBeNull();
  });

  // Where a chip leads: the page and the highlighted spot, read back from its link.
  const opens = (href: string) => {
    const url = new URL(href, "http://x");
    return [url.pathname, url.searchParams.get("page"), decodeRegion(url.searchParams.get("region"))];
  };

  it("gives a cross-page source one chip for its later page, at that page's spot", () => {
    const later: Region = { kind: "box", page: 4, box: [0.1, 0.1, 0.5, 0.2] };
    const first: Region = { kind: "box", page: 3, box: [0.1, 0.8, 0.5, 0.9] };
    const jumps = pageJumps(citation({ page_from: 3, page_to: 4, regions: [first, later] }));
    expect(jumps.map((jump) => jump.text)).toEqual(["Page 4"]);
    expect(opens(jumps[0]!.href)).toEqual(["/documents/doc-1", "4", later]);
    expect(pageJumps(citation({ regions: [first] }))).toEqual([]);
    const pages = [5, 4].map((page): Region => ({ kind: "box", page, box: [0, 0, 0.1, 0.1] }));
    expect(pageJumps(citation({ regions: [first, ...pages] })).map((jump) => jump.text)).toEqual([
      "Page 4",
      "Page 5",
    ]);
  });

  it("gives a value's label on another page a chip at the label's spot, or the page alone", () => {
    const label: Region = { kind: "box", page: 2, box: [0.1, 0.9, 0.3, 0.95] };
    const value = { kind: "value" as const, label_page_no: 2 };
    const [jump] = pageJumps(citation({ ...value, label_regions: [label] }));
    expect(jump?.text).toBe("Label, page 2");
    expect(opens(jump!.href)).toEqual(["/documents/doc-1", "2", label]);
    // The label's spot can be missing (an invalid region, or the document is in Trash).
    expect(opens(pageJumps(citation(value))[0]!.href)).toEqual(["/documents/doc-1", "2", null]);
  });

  it("carries the asker's words on every chip, as the main link does (package H)", () => {
    const later: Region = { kind: "box", page: 4, box: [0.1, 0.1, 0.5, 0.2] };
    const first: Region = { kind: "box", page: 3, box: [0.1, 0.8, 0.5, 0.9] };
    const label: Region = { kind: "box", page: 2, box: [0.1, 0.9, 0.3, 0.95] };
    const cited = citation({ kind: "value", page_from: 3, page_to: 4, regions: [first, later], label_page_no: 2, label_regions: [label] });
    const jumps = pageJumps(cited, ["grand", "total", "the"]);
    expect(jumps.map((jump) => new URL(jump.href, "http://x").searchParams.get("words"))).toEqual(["grand total", "grand total"]);
    expect(pageJumps(cited).map((jump) => new URL(jump.href, "http://x").searchParams.has("words"))).toEqual([false, false]);
  });
});

describe("a question that could not be sent", () => {
  it("tells the person what to do, never a bare 'Something went wrong'", () => {
    expect(askErrorText(null)).toMatch(/Could not reach Kaagaz\. Check your internet connection/);
    expect(askErrorText({ status: 0, message: "Could not reach the server." })).toMatch(
      /Could not reach Kaagaz/,
    );
    expect(askErrorText({ status: 429, message: "Slow down" })).toBe(
      "Too many questions at once. Wait a minute, then try again.",
    );
    expect(askErrorText({ status: 400, message: "The question is too long." })).toBe(
      "The question is too long.",
    );
    expect(askErrorText({ status: 400, message: "Something went wrong." })).toBe(
      "Could not get an answer right now. Try again in a minute.",
    );
    // Changed on purpose (Ask hotfix, 15 Nov 2026): a 503 never says "busy". Ask shows it in the
    // answer's place (unfinishedText, with the first name); the question still goes back in the box.
    expect(askErrorText({ status: 503, message: "Upstream down" })).toBe(
      "Sorry, I couldn't finish that just now. Please try again in a moment.",
    );
    expect(askErrorText({ status: 502, message: "Bad gateway" })).toBe(
      "Could not get an answer right now. Try again in a minute.",
    );
  });
});

describe("the open conversation in the URL", () => {
  const id = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b";

  it("reads only a UUID-shaped id from ?c=", () => {
    expect(conversationFromSearch(`?c=${id}`)).toBe(id);
    expect(conversationFromSearch(`?c=${id.toUpperCase()}`)).toBe(id);
    for (const search of ["", "?c=", "?c=abc", "?c=../admin", `?c=${id}x`, `?x=${id}`]) {
      expect(conversationFromSearch(search)).toBeNull();
    }
  });

  it("builds the chat address for a conversation or the empty chat", () => {
    expect(chatHref(id)).toBe(`/chat?c=${id}`);
    expect(chatHref(null)).toBe("/chat");
  });
});
