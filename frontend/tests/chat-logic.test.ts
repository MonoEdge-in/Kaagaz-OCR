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
  NEAR_BOTTOM_PX,
  sourceAction,
  sourceMeta,
  sourcePlace,
  STILL_LOOKING_AFTER_MS,
} from "@/components/chat/logic";
import { formatDate } from "@/lib/format";
import type { Citation, ConversationSummary } from "@/lib/types";

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
    expect(sourcePlace(citation({ detected_type: "text" }))).toBe("Sheet 3");
  });

  it("writes the meta line as place and file kind", () => {
    expect(sourceMeta(citation())).toBe("Page 3 · PDF");
    expect(sourceMeta(citation({ detected_type: "zip", page_no: 2 }))).toBe("Sheet 2 · Spreadsheet");
  });

  it("opens a verified page, and asks to check one that needs review, saying why", () => {
    expect(sourceAction(citation())).toEqual({ text: "Open page 3", check: false, reason: null });
    const review = sourceAction(citation({ label: "needs_review", kind: "value" }));
    expect(review.text).toBe("Check page 3");
    expect(review.check).toBe(true);
    expect(review.reason).toMatch(/^This value was not fully checked/);
    expect(review.reason).toMatch(/Look at the page before you rely on it\.$/);
    const sheet = sourceAction(citation({ label: "needs_review", detected_type: "zip" }));
    expect(sheet.text).toBe("Check sheet 3");
    expect(sheet.reason).toMatch(/^This text was not fully checked.*Look at the sheet/);
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
    expect(askErrorText({ status: 503, message: "Upstream down" })).toBe(
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
