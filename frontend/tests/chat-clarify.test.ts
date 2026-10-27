// Clarifying questions in Ask (PRD 6 B2): the choices as a group named by the question
// (checkboxes in a fieldset when several may be picked), "Something else" sending nothing, the
// answered card as plain text, the composer's "Answering" line, and the chips' layout rules.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement, createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { answerStatusText } from "@/components/chat/answer";
import { answersClarify, chosenAfter, langOf, openClarify, pickedText } from "@/components/chat/clarify";
import { ClarifyView } from "@/components/chat/ClarifyView";
import { QuestionBox } from "@/components/chat/QuestionBox";
import { MODE_TEXTS } from "@/lib/chatMode";
import type { Answer, Clarify, Message } from "@/lib/types";

const pick = (n: number, label: string) => ({ id: `c${n}`, label, kind: "pick" as const, document_ids: ["d1"] });
const OTHER = { id: "other", label: "Something else", kind: "other" as const };
const ALL = { id: "all", label: "All of these", kind: "all" as const };

function clarifying(id: string, clarify: Partial<Clarify> = {}): Message {
  const asked: Clarify = { id: "x", question: "Did you mean Pune?", choices: [pick(1, "Pune"), OTHER], multi: false, turn: 1 };
  const answer = { status: "clarify", label: null, sentences: [], citations: [], closest_documents: [], route: "search" };
  return {
    id,
    role: "assistant",
    text: asked.question,
    created_at: "2026-10-24T06:30:00Z",
    answer: { ...answer, normalized_question: "", model_used: true, clarify: { ...asked, ...clarify } } as Answer,
    feedback: null,
  } as Message;
}

const user = (id: string, text: string) => ({ id, role: "user", text, answer: null }) as unknown as Message;

const view = (message: Message, open: boolean, busy = false, chosen?: string | null) =>
  renderToStaticMarkup(
    createElement(ClarifyView, { message, open, busy, chosen, onChoose: () => undefined, onOther: () => undefined }),
  );

describe("a clarifying question", () => {
  it("shows its choices as a group named by the question, and Something else", () => {
    const html = view(clarifying("m1"), true);
    const named = /<p id="([^"]+)"[^>]*>Did you mean Pune\?<\/p>/.exec(html);
    expect(named).not.toBeNull();
    expect(html).toContain(`role="group" aria-labelledby="${named?.[1]}"`);
    expect(html.match(/<button type="button" class="_chip/g)).toHaveLength(2);
    expect(html).toContain(">Pune</span>");
    expect(html).toContain(">Something else</button>");
    expect(html).not.toContain("btn");
  });

  it("lets several places be picked in a fieldset named by its legend, or All of these", () => {
    const places = { question: "Which places should I look in?", multi: true };
    const html = view(clarifying("m1", { ...places, choices: [pick(1, "Maval"), pick(2, "मावळ"), ALL, OTHER] }), true);
    expect(html).toMatch(/<fieldset[^>]*><legend[^>]*>Which places should I look in\?<\/legend>/);
    expect(html.match(/type="checkbox"/g)).toHaveLength(2);
    expect(html).toContain(">Look in these</button>");
    expect(html).toContain(">All of these</button>");
    expect(html).toContain('lang="mr">मावळ</span>');
  });

  it("is aria-disabled while a reply is on its way, and plain text once answered", () => {
    expect(view(clarifying("m1"), true, true).match(/aria-disabled="true"/g)).toHaveLength(2);
    const answered = view(clarifying("m1"), false, false, "Pune");
    expect(answered).toContain("You chose: ");
    expect(answered).not.toContain("<button");
    expect(view(clarifying("m1"), false, false, null)).not.toContain("You chose");
  });
});

describe("the thread around it", () => {
  it("is open only as the newest message, until left", () => {
    const asked = clarifying("m2");
    expect(openClarify([user("m1", "Land in Puny?"), asked], null)).toBe(asked);
    expect(openClarify([user("m1", "Land in Puny?"), asked], "m2")).toBeNull();
    expect(openClarify([asked, user("m3", "Pune")], null)).toBeNull();
  });

  it("knows what was picked, by the labels the server stored", () => {
    const places = clarifying("m2", { choices: [pick(1, "Maval"), pick(2, "Panvel"), ALL, OTHER], multi: true });
    expect(pickedText(places, ["all"])).toBe("Maval, Panvel");
    expect(pickedText(places, ["c2"])).toBe("Panvel");
    const chosen = chosenAfter([places, user("m3", "Maval, Panvel"), clarifying("m4"), user("m5", "Pimpri")]);
    expect([...chosen]).toEqual([
      ["m2", "Maval, Panvel"],
      ["m4", null],
    ]);
    expect([langOf("Pune"), langOf("पुणे")]).toEqual([undefined, "mr"]);
    // Review 3: typed words answer it only as a short name; a question or long text is a new question.
    expect([answersClarify("Pimpri"), answersClarify("Whose land is it?"), answersClarify("x ".repeat(31))]).toEqual([
      true,
      false,
      false,
    ]);
    // A new question typed without a question mark is never taken as the reply.
    expect([answersClarify("Yes, Pune"), answersClarify("Survey 45/2"), answersClarify("Total GST paid last month")]).toEqual([
      true,
      true,
      false,
    ]);
    expect(answerStatusText(clarifying("m2").answer)).toBe("Did you mean Pune?");
  });

  it("puts Answering and Ask something else above the composer", () => {
    const html = renderToStaticMarkup(
      createElement(QuestionBox, {
        pending: false,
        error: null,
        onAsk: async () => true,
        inputRef: createRef<HTMLTextAreaElement>(),
        texts: MODE_TEXTS.documents,
        answering: { question: "Did you mean Pune?", onLeave: () => undefined },
      }),
    );
    expect(html).toContain("Answering: <span>Did you mean Pune?</span></span>");
    expect(html).toContain(">Ask something else</button>");
    expect(html.indexOf("Answering:")).toBeLessThan(html.indexOf("<textarea"));
  });

  it("marks a Devanagari question as Marathi in the card, the legend and the composer", () => {
    const asked = "पुणे म्हणायचे होते का?"; // review 9
    expect(view(clarifying("m1", { question: asked }), true)).toMatch(/<p id="[^"]+" class="[^"]+" lang="mr">/);
    const places = { question: asked, multi: true, choices: [pick(1, "Maval"), pick(2, "Panvel"), ALL, OTHER] };
    expect(view(clarifying("m1", places), true)).toContain(`lang="mr">${asked}</legend>`);
    const box = renderToStaticMarkup(
      createElement(QuestionBox, {
        pending: false,
        error: null,
        onAsk: async () => true,
        inputRef: createRef<HTMLTextAreaElement>(),
        texts: MODE_TEXTS.documents,
        answering: { question: asked, onLeave: () => undefined },
      }),
    );
    expect(box).toContain(`Answering: <span lang="mr">${asked}</span>`);
  });
});

describe("the chips' layout (no overflow at 320 px)", () => {
  const css = readFileSync(join(__dirname, "..", "components/chat/Chat.module.css"), "utf8");
  const rule = (selector: string) => new RegExp(`\\n${selector} \\{([^}]*)\\}`).exec(css)?.[1] ?? "";

  it("wraps in a row with an 8px gap, each chip as wide as its words up to the card", () => {
    expect(rule("\\.choices")).toMatch(/flex-wrap: wrap;[\s\S]*gap: 8px;[\s\S]*min-width: 0;/);
    const chip = rule("\\.chip");
    for (const line of ["flex: 0 1 auto;", "max-width: 100%;", "min-width: 0;", "overflow-wrap: break-word;"]) {
      expect(chip).toContain(line);
    }
    expect(chip).toMatch(/font: inherit;[\s\S]*text-transform: none;/);
    // One control tall, so 44px on touch (alignment-guards.test.ts: --control-h grows there).
    expect([chip, rule("\\.answeringLeave")].every((body) => body.includes("min-height: var(--control-h);"))).toBe(true);
  });
});
