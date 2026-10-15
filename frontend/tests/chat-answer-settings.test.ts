// Answer settings and the Ensemble answer line (features 25-27, council 15 Oct 2026, C12,
// with Tushar's override: models picked by name, thinking High / Medium / Low).

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { answerLabelText, modelName, NOT_AGREED_TEXT, teamLine } from "@/components/chat/answer";
import { AnswerSettings } from "@/components/chat/AnswerSettings";
import {
  allowedLevels,
  choiceBody,
  chooseAgent,
  defaultPick,
  hasChoices,
  pickSummary,
  toggleAgent,
} from "@/components/chat/logic";
import type { AnswerPick } from "@/components/chat/logic";
import type { Answer, ChatOptions, Citation } from "@/lib/types";

const FLASH = { id: "google/gemini-3.8-flash", name: "Google: Gemini 3.8 Flash", default: true };
const SONNET = { id: "anthropic/claude-sonnet-5.5", name: "Anthropic: Claude Sonnet 5.5", default: false };
const PRO = { id: "google/gemini-3.1-pro-preview", name: "Google: Gemini 3.1 Pro", default: false };
const FOURTH = { id: "google/gemini-4", name: "Gemini 4", default: false };

const options = (changes: Partial<ChatOptions> = {}): ChatOptions => ({
  agents: [FLASH, SONNET],
  levels: ["low", "medium", "high"],
  default_level: "low",
  ensemble: false,
  max_agents: 3,
  ...changes,
});

const MONEY = /[$₹]|\bRs\b|\bUSD\b|\bprice|\bcost|\bbudget|\bspen[dt]|\bpaid\b/i;

describe("answer settings", () => {
  it("are hidden when there is only one choice", () => {
    expect(hasChoices(null)).toBe(false);
    expect(hasChoices(options({ agents: [FLASH], levels: ["low"] }))).toBe(false);
    expect(hasChoices(options({ agents: [], levels: ["low"] }))).toBe(false);
    expect(hasChoices(options({ agents: [FLASH], levels: ["low", "medium"] }))).toBe(true);
    expect(hasChoices(options({ levels: ["low"] }))).toBe(true);
  });

  it("start from the organisation's default, which sends nothing", () => {
    const allowed = options({ default_level: "medium" });
    const pick = defaultPick(allowed);
    expect(pick).toEqual({ agents: [FLASH.id], level: "medium" });
    expect(choiceBody(allowed, pick)).toBeUndefined();
    expect(choiceBody(null, null)).toBeUndefined();
    expect(pickSummary(allowed, pick)).toBe("Gemini 3.8 Flash · Medium");
  });

  it("send the model by id and the level once changed", () => {
    const allowed = options();
    const pick = chooseAgent(allowed, defaultPick(allowed), SONNET.id);
    expect(choiceBody(allowed, pick)).toEqual({ agents: [SONNET.id], thinking: "low" });
    expect(choiceBody(allowed, { ...defaultPick(allowed), level: "high" })).toEqual({
      agents: [FLASH.id],
      thinking: "high",
    });
  });

  it("let Ensemble tick up to three models, never none, and drop High for more than one", () => {
    const allowed = options({ agents: [FLASH, SONNET, PRO, FOURTH], ensemble: true });
    let pick: AnswerPick = { ...defaultPick(allowed), level: "high" };
    pick = toggleAgent(allowed, pick, SONNET.id);
    expect(pick).toEqual({ agents: [FLASH.id, SONNET.id], level: "low" }); // the default level
    expect(allowedLevels(allowed, pick)).toEqual(["low", "medium"]);
    pick = toggleAgent(allowed, pick, PRO.id);
    expect(toggleAgent(allowed, pick, FOURTH.id)).toBe(pick); // the cap: three
    pick = toggleAgent(allowed, toggleAgent(allowed, pick, PRO.id), SONNET.id);
    expect(pick.agents).toEqual([FLASH.id]);
    expect(toggleAgent(allowed, pick, FLASH.id)).toBe(pick); // never none
    expect(allowedLevels(allowed, pick)).toEqual(["low", "medium", "high"]);
  });

  it("show one model as a select, by name, with plain words and no money", () => {
    const allowed = options();
    const html = renderToStaticMarkup(
      createElement(AnswerSettings, { options: allowed, pick: defaultPick(allowed), locked: false, onPick: () => {} }),
    );
    expect(html).toContain("<details");
    expect(html).toContain("Answer with:");
    expect(html).toContain(">Gemini 3.8 Flash<");
    expect(html).toContain(">Claude Sonnet 5.5<");
    expect(html).toContain(">High<");
    expect(html).not.toContain('type="checkbox"');
    expect(html).not.toMatch(MONEY);
    const shown = html.replace(/<[^>]*>/g, " "); // the text a person sees, not the ids it sends
    expect(shown).not.toMatch(/\bmodel\b|google\/|anthropic\//i); // names only, never ids or the word
  });

  it("show Ensemble as named checkboxes, the rest disabled with the reason at three", () => {
    const allowed = options({ agents: [FLASH, SONNET, PRO, FOURTH], ensemble: true });
    const pick = { agents: [FLASH.id, SONNET.id, PRO.id], level: "medium" as const };
    const html = renderToStaticMarkup(
      createElement(AnswerSettings, { options: allowed, pick, locked: false, onPick: () => {} }),
    );
    expect(html.match(/type="checkbox"/g)).toHaveLength(4);
    expect(html).toMatch(/<input type="checkbox" disabled="" aria-describedby="[^"]+-most"/);
    expect(html).toContain("Up to 3 at once.");
    expect(html).toContain("High works only when one answers alone.");
    expect(html).not.toContain(">High<");
    expect(html).toMatch(/<fieldset[^>]*><legend>Answer settings<\/legend>/);
  });

  it("are locked while a question is out", () => {
    const allowed = options();
    const html = renderToStaticMarkup(
      createElement(AnswerSettings, { options: allowed, pick: defaultPick(allowed), locked: true, onPick: () => {} }),
    );
    expect(html).toMatch(/<fieldset[^>]* disabled=""/);
  });
});

const citation = (label: Citation["label"]): Citation =>
  ({ n: 1, label, regions: [], page_no: 1, document_id: "d", document_name: "a.pdf" }) as unknown as Citation;

describe("the answer line", () => {
  it("is shown only when more than one model was asked", () => {
    expect(teamLine({})).toBeNull(); // an answer from before
    expect(teamLine({ agents: ["Gemini 3.8 Flash"], agents_asked: 1, agreement: null })).toBeNull();
  });

  it("says who answered and whether they agreed", () => {
    const two = { agents: ["Google: Gemini 3.8 Flash", "Sonnet 5.5"], agents_asked: 2 };
    expect(teamLine({ ...two, agreement: "agreed" })).toBe("Answered by Gemini 3.8 Flash and Sonnet 5.5. They agreed.");
    expect(teamLine({ ...two, agreement: "not_confirmed" })).toBe(
      `Answered by Gemini 3.8 Flash and Sonnet 5.5. ${NOT_AGREED_TEXT}`,
    );
    expect(teamLine({ agents: ["Gemini 3.8 Flash"], agents_asked: 3, agreement: null })).toBe(
      "Only Gemini 3.8 Flash answered.",
    );
    expect(teamLine({ agents: ["A", "B"], agents_asked: 3, agreement: "agreed" })).toBe(
      "Only A and B answered. They agreed.",
    );
  });

  it("explains a Needs review that only the other models caused", () => {
    const checked: Pick<Answer, "agreement" | "citations"> = {
      agreement: "not_confirmed",
      citations: [citation("verified")],
    };
    expect(answerLabelText("needs_review", checked).explanation).toBe(NOT_AGREED_TEXT);
    const unchecked = { ...checked, citations: [citation("needs_review")] };
    expect(answerLabelText("needs_review", unchecked).explanation).toMatch(/^At least one source/);
    expect(answerLabelText("needs_review").explanation).toMatch(/^At least one source/); // as before
  });

  it("names a model without its maker's prefix", () => {
    expect(modelName("Google: Gemini 3.8 Flash")).toBe("Gemini 3.8 Flash");
    expect(modelName("Gemini 4")).toBe("Gemini 4");
  });
});
