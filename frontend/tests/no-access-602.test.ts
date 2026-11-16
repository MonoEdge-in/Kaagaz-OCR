// PRD 7 (#602): a saved answer that names a file the person can no longer see comes back from the
// server replaced whole (status "no_access"): one line, no sources, no rating, no pick; on the
// Admin's Answer quality page, "Source not visible to you" and no question or reason.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AnswerQualityView, QUALITY_TEXT } from "@/components/admin/AnswerQualityPanel";
import { NO_ACCESS_TEXT, answerStatusText } from "@/components/chat/answer";
import { AnswerView } from "@/components/chat/AnswerView";
import type { Answer, AnswerQualityItem, Message } from "@/lib/types";

const replaced: Answer = {
  status: "no_access",
  label: null,
  sentences: [{ text: NO_ACCESS_TEXT, citations: [] }],
  citations: [],
  closest_documents: [],
  route: "search",
  normalized_question: "",
  model_used: false,
};

const noop = () => undefined;

describe("an answer the person may no longer see", () => {
  it("says the server's line, word for word", () => {
    const views = readFileSync(join(__dirname, "../../backend/kaagaz/api/views.py"), "utf8");
    const found = /NO_ACCESS_LINE = \(\n((?:\s+".*"\n)+)\)/.exec(views);
    expect(found).not.toBeNull();
    const joined = (found?.[1] ?? "")
      .split("\n")
      .map((line) => line.trim().replace(/^"|"$/g, ""))
      .join("");
    expect(joined).toBe(NO_ACCESS_TEXT);
    expect(answerStatusText(replaced)).toBe(NO_ACCESS_TEXT);
  });

  it("shows only that line: no rating, no sources, no pick", () => {
    const message: Message = {
      id: "m1",
      role: "assistant",
      text: NO_ACCESS_TEXT,
      created_at: "2026-11-14T05:00:00Z",
      answer: replaced,
      feedback: "up",
      pin: null,
    };
    const html = renderToStaticMarkup(createElement(AnswerView, { message, question: "What is the total?" }));
    expect(html).toContain(NO_ACCESS_TEXT);
    expect(html).not.toMatch(/Useful|Sources|Most relevant/);
  });

  it("is 'Source not visible to you' on Answer quality, with no question or reason", () => {
    const item: AnswerQualityItem = {
      id: "f1",
      message_id: "m1",
      asked_by: "Ravi Sharma",
      question: null,
      answer: replaced,
      reason_codes: [],
      reason: null,
      given_at: "2026-10-22T05:00:00Z",
      handled_at: null,
    };
    const html = renderToStaticMarkup(
      createElement(AnswerQualityView, {
        status: "open",
        weeks: "4",
        offset: 0,
        reply: { from: "2026-09-28", weeks: [], items: [item], total: 1 },
        error: null,
        onChoose: noop,
        onHandle: noop,
        onRetry: noop,
      }),
    );
    expect(html).toContain(QUALITY_TEXT.hidden);
    expect(html).not.toContain(NO_ACCESS_TEXT);
    expect(html).not.toContain(">Question<");
  });
});
