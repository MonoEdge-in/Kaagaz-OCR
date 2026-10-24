// Useful and Not useful (PRD 6 F1): the words on the buttons, the Not useful form (what
// went wrong, the count, the admin notice above Send), the reasons in the server's order,
// and the reason chips' no-overflow rules (PRD 6 build spec, Layout).

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FEEDBACK_NOTICE, REASONS } from "@/components/chat/answer";
import { FeedbackControls, ReasonForm } from "@/components/chat/FeedbackControls";

const ROOT = join(__dirname, "..");
const noop = () => undefined;

const form = (reason = "", codes: (typeof REASONS)[number]["code"][] = []) =>
  renderToStaticMarkup(
    createElement(ReasonForm, {
      id: "reason-a1",
      codes,
      reason,
      busy: false,
      onCodes: noop,
      onReason: noop,
      onCancel: noop,
      onSend: noop,
    }),
  );

describe("Useful and Not useful", () => {
  it("replace Was this helpful, Yes and No", () => {
    const html = renderToStaticMarkup(createElement(FeedbackControls, { messageId: "a1", initial: "down" }));
    expect(html).toContain(">Useful<");
    expect(html).toContain(">Not useful<");
    expect(html).toMatch(/aria-pressed="true"[^>]*>(<svg[\s\S]*?<\/svg>)<span>Not useful<\/span>/);
    expect(html).not.toMatch(/helpful\?<|>Yes<|>No</);
  });

  it("asks what went wrong with a box for each reason, the server's codes in its order", () => {
    const html = form();
    expect(html).toMatch(/<fieldset[^>]*><legend[^>]*>What went wrong\?<\/legend>/);
    expect([...html.matchAll(/type="checkbox"/g)]).toHaveLength(5);
    expect(REASONS.map((reason) => reason.label)).toEqual([
      "Wrong documents",
      "A document is missing",
      "Wrong figure",
      "Didn't understand me",
      "Other",
    ]);
    for (const reason of REASONS) expect(html).toContain(`<span>${reason.label.replace("'", "&#x27;")}</span>`);
    const backend = readFileSync(join(ROOT, "..", "backend", "kaagaz", "db", "chat.py"), "utf8");
    const codes = /^REASONS = \(([^)]*)\)/m.exec(backend)?.[1] ?? "";
    expect([...codes.matchAll(/"([a-z_]+)"/g)].map((match) => match[1])).toEqual(REASONS.map((reason) => reason.code));
    expect(form("", ["wrong_figure"])).toMatch(/checked=""/);
  });

  it("counts the characters out of 500 where everyone can see it", () => {
    expect(form()).toContain("0 of 500 characters");
    const html = form("Wrong March bill");
    expect(html).toContain("16 of 500 characters");
    expect(html).toMatch(/maxLength="500"|maxlength="500"/);
    expect(html).toMatch(/aria-describedby="reason-a1-count"/);
  });

  it("says who can see it, word for word, above Send", () => {
    expect(FEEDBACK_NOTICE).toBe(
      "Your company admin can see this answer, your question, your reason and your name.",
    );
    const html = form();
    const notice = html.indexOf(FEEDBACK_NOTICE);
    expect(notice).toBeGreaterThan(html.indexOf("What went wrong?"));
    expect(notice).toBeLessThan(html.indexOf("Send feedback"));
  });

  it("shows a typed reason as plain text, never as markup", () => {
    const html = form('<img src=x onerror="alert(1)">');
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });

  it("draws the reasons as chips that wrap, never as buttons that overflow", () => {
    expect(form()).not.toMatch(/<label[^>]*class="[^"]*\bbtn\b/);
    const css = readFileSync(join(ROOT, "components", "chat", "Chat.module.css"), "utf8");
    const chip = /\.reasonChip \{([^}]*)\}/.exec(css)?.[1] ?? "";
    expect(chip).toMatch(/flex: 0 1 auto;/);
    expect(chip).toMatch(/max-width: 100%;/);
    expect(chip).toMatch(/min-width: 0;/);
    expect(chip).toMatch(/overflow-wrap: break-word;/);
    expect(chip).toMatch(/min-height: var\(--control-min\);/); // 44px on touch (globals.css)
    expect(chip).not.toMatch(/white-space: nowrap|text-transform/);
    const row = /\.reasonChips \{([^}]*)\}/.exec(css)?.[1] ?? "";
    expect(row).toMatch(/flex-wrap: wrap;/);
    expect(row).toMatch(/gap: var\(--space-2\);/); // 8px
  });
});
