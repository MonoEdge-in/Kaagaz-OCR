// Answer quality's Mark as handled (PRD 6 F2, review of 24 Oct 2026): held until its Undo has
// gone, so Undo sends nothing and never loses who handled it; a refusal is heard; the list
// before it is kept for Undo; and focus goes to Undo, since the card that had it is gone.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { deferred, withoutItem } from "@/components/admin/AnswerQualityPanel";
import { Toast } from "@/components/ui/Toast";
import type { AnswerQualityItem, AnswerQualityReply } from "@/lib/types";

const item = (id: string) => ({ id, given_at: "2026-10-22T05:00:00+00:00" }) as AnswerQualityItem;
const noop = () => undefined;

function recorder(result: Promise<unknown> = Promise.resolve()) {
  const sent: [string, boolean][] = [];
  const queue = deferred((each, handled) => {
    sent.push([each.id, handled]);
    return result;
  });
  return { sent, queue };
}

describe("Mark as handled and Undo", () => {
  it("sends nothing on Undo, even when the toast closes after it", () => {
    const { sent, queue } = recorder();
    queue.start(item("f1"), true, noop);
    queue.undo();
    queue.flush();
    expect(sent).toEqual([]);
  });

  it("sends once when Undo has gone, and the one before when another starts", () => {
    const { sent, queue } = recorder();
    queue.start(item("f1"), true, noop);
    queue.start(item("f2"), false, noop); // Open again on another: the first is sent now
    expect(sent).toEqual([["f1", true]]);
    queue.flush();
    queue.flush();
    expect(sent).toEqual([
      ["f1", true],
      ["f2", false],
    ]);
  });

  it("hands a refusal (rated again since, 409) back to put the answer back", async () => {
    const refusal = new Error("This answer was marked again. Reload to see the new reason.");
    const { queue } = recorder(Promise.reject(refusal));
    const heard: unknown[] = [];
    queue.start(item("f1"), true, (caught) => heard.push(caught));
    queue.flush();
    await new Promise((done) => setTimeout(done, 0));
    expect(heard).toEqual([refusal]);
  });

  it("takes the answer off the list and keeps the list before it for Undo", () => {
    const before: AnswerQualityReply = { from: "2026-09-28", weeks: [], items: [item("f1"), item("f2")], total: 2 };
    const after = withoutItem(before, "f1");
    expect(after.items.map((each) => each.id)).toEqual(["f2"]);
    expect(after.total).toBe(1);
    expect(before.items.map((each) => each.id)).toEqual(["f1", "f2"]); // Undo puts this back as it was
  });

  it("puts the focus on Undo when asked, and only then", () => {
    const toast = (focusUndo: boolean) =>
      renderToStaticMarkup(createElement(Toast, { message: "Marked as handled.", onUndo: noop, onClose: noop, focusUndo }));
    expect(toast(true)).toMatch(/<button[^>]*autofocus=""[^>]*>Undo<\/button>/);
    expect(toast(false)).not.toContain("autofocus");
  });
});
