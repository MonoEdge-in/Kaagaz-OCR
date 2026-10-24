// Answer settings (features 25-27, council 15 Oct 2026, C12, with Tushar's override:
// models picked by name, thinking High / Medium / Low). Changed on purpose (Tushar,
// 16 Oct 2026, one model in Ask): one model answers, picked with radio buttons; no
// Ensemble checkboxes, no "Up to 3 answer together" or "High works only when one
// answers alone" lines, and no line under an answer on who answered or whether they agreed.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { modelName } from "@/components/chat/answer";
import { AnswerSettings } from "@/components/chat/AnswerSettings";
import { choiceBody, chooseAgent, defaultPick, hasChoices, pickSummary } from "@/components/chat/logic";
import type { ChatOptions } from "@/lib/types";

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
    // Changed on purpose (SoW 19 Oct 2026): Low is not offered in Ask, so Low and Swift alone are one choice.
    expect(hasChoices(options({ agents: [FLASH], levels: ["low", "medium"] }))).toBe(false);
    expect(hasChoices(options({ agents: [FLASH], levels: ["medium", "high"] }))).toBe(true);
    expect(hasChoices(options({ levels: ["low"] }))).toBe(true);
  });

  it("start from the organisation's default, which sends nothing", () => {
    const allowed = options({ default_level: "medium" });
    const pick = defaultPick(allowed);
    expect(pick).toEqual({ agents: [FLASH.id], level: "medium" });
    expect(choiceBody(allowed, pick)).toBeUndefined();
    expect(choiceBody(null, null)).toBeUndefined();
    // Changed on purpose (spec 17 Oct 2026): org users never see a model's name.
    // Changed on purpose (SoW 19 Oct 2026): Medium is Swift, and one model shows the engine alone.
    expect(pickSummary(allowed, pick)).toBe("Standard · Swift");
    expect(pickSummary(options({ agents: [FLASH] }), pick)).toBe("Swift");
  });

  // SoW 19 Oct 2026: Ask does not offer Low; an organisation defaulting to Low starts on Swift,
  // and Swift is then sent, since it is not the organisation's own level.
  it("start on Swift over a Low default, and send it", () => {
    const allowed = options({ default_level: "low" });
    const pick = defaultPick(allowed);
    expect(pick.level).toBe("medium");
    expect(choiceBody(allowed, pick)).toEqual({ agents: [FLASH.id], thinking: "medium" });
    expect(defaultPick(options({ default_level: "low", levels: ["low"] })).level).toBe("low");
  });

  it("send the model by id and the level once changed", () => {
    const allowed = options();
    const pick = chooseAgent(defaultPick(allowed), SONNET.id);
    expect(choiceBody(allowed, pick)).toEqual({ agents: [SONNET.id], thinking: "medium" });
    expect(choiceBody(allowed, { ...defaultPick(allowed), level: "high" })).toEqual({
      agents: [FLASH.id],
      thinking: "high",
    });
  });

  // Changed on purpose (Tushar, 16 Oct 2026, one model in Ask).
  it("pick one model at a time, keep High open, and send exactly one", () => {
    const allowed = options({ agents: [FLASH, SONNET, PRO, FOURTH], ensemble: true });
    const pick = chooseAgent(chooseAgent({ ...defaultPick(allowed), level: "high" }, SONNET.id), PRO.id);
    expect(pick).toEqual({ agents: [PRO.id], level: "high" });
    expect(choiceBody(allowed, pick)).toEqual({ agents: [PRO.id], thinking: "high" });
    // An older tab's pick of several still sends one.
    expect(choiceBody(allowed, { agents: [FLASH.id, SONNET.id], level: "low" })).toEqual({
      agents: [FLASH.id],
      thinking: "low",
    });
    expect(pickSummary(allowed, pick)).toBe("Careful 2 · Precision"); // spec 17 Oct 2026: tiers, never names
  });

  // Changed on purpose (chat composer, 16 Oct 2026): a quiet button beside Send opens a popover
  // panel (was a <details> chip "Answer with:" and a select); the models are named radio buttons.
  it("show one model as named radios in a popover panel, by name, with plain words and no money", () => {
    const allowed = options();
    const html = renderToStaticMarkup(
      createElement(AnswerSettings, { options: allowed, pick: defaultPick(allowed), locked: false, onPick: () => {} }),
    );
    expect(html).not.toContain("<details");
    expect(html).not.toContain("Answer with:");
    expect(html).toMatch(/<button type="button"[^>]* popoverTarget="([^"]+)" aria-haspopup="dialog"/);
    const target = /popoverTarget="([^"]+)"/.exec(html)?.[1];
    expect(html).toContain(`<div id="${target}" popover="auto" role="dialog" aria-label="Answer settings"`);
    expect(html).toContain('<span class="visually-hidden">Answer settings: </span>');
    // Changed on purpose (spec 17 Oct 2026): the models are tiers, never their names.
    expect(html).toContain(">Standard · Swift<"); // the button names the pick
    expect(html.match(/<input type="radio" name="[^"]+-model"/g)).toHaveLength(2);
    expect(html).toContain("/>Standard</label>");
    expect(html).toContain("/>Careful</label>");
    expect(html).not.toMatch(/Gemini|Claude|Sonnet|Flash/);
    expect(html).toContain(">Precision<");
    expect(html).not.toContain('type="checkbox"');
    expect(html).not.toMatch(MONEY);
    const shown = html.replace(/<[^>]*>/g, " "); // the text a person sees, not the ids it sends
    expect(shown).not.toMatch(/\bmodel\b|google\/|anthropic\//i); // names only, never ids or the word
  });

  // Changed on purpose (SoW 19 Oct 2026): "AI engine", Swift then Precision; no Low.
  // Changed on purpose (plain words, 25 Oct 2026): both hints are shown, saying what each is for.
  it("show the AI engine as Swift and Precision segments, each with its hint, both shown", () => {
    const allowed = options({ levels: ["low", "medium", "high"] });
    const html = renderToStaticMarkup(
      createElement(AnswerSettings, { options: allowed, pick: { agents: [FLASH.id], level: "medium" }, locked: false, onPick: () => {} }),
    );
    const segments = [...html.matchAll(/aria-describedby="([^"]+)" name="[^"]+-level"[^>]*\/>(\w+)<\/label>/g)];
    expect(segments.map((match) => match[2])).toEqual(["Swift", "Precision"]);
    for (const [, hint] of segments) expect(html).toContain(`<p id="${hint}"`);
    expect(html).toMatch(/<p id="[^"]+-high" class="[^"]+">Precision: slower, more careful answers for important questions\.<\/p>/);
    expect(html).toMatch(/<p id="[^"]+-medium" class="[^"]+">Swift: quick answers for everyday questions\.<\/p>/);
    expect(html).not.toMatch(/-low"|>Low</);
    expect(html).toMatch(/<legend>AI engine<\/legend>/);
  });

  // Changed on purpose (Tushar, 16 Oct 2026, one model in Ask): even with Ensemble allowed,
  // radios, one checked, every level shown, and no line about answering together.
  it("show radios even when the organisation allows Ensemble, with no agree wording", () => {
    const allowed = options({ agents: [FLASH, SONNET, PRO, FOURTH], ensemble: true });
    const html = renderToStaticMarkup(
      createElement(AnswerSettings, { options: allowed, pick: defaultPick(allowed), locked: false, onPick: () => {} }),
    );
    expect(html).not.toContain('type="checkbox"');
    expect(html.match(/<input type="radio" name="[^"]+-model"/g)).toHaveLength(4);
    expect(html.match(/name="[^"]+-model" checked=""/g)).toHaveLength(1);
    expect(html).toContain(">Precision<");
    expect(html).not.toMatch(/answer together|agree|answers alone|Up to \d/i);
    // Changed on purpose (chat composer, 16 Oct 2026): the panel is named, so its legend is for screen readers only.
    expect(html).toMatch(/<fieldset[^>]*><legend class="visually-hidden">Answer settings<\/legend>/);
  });

  it("are locked while a question is out", () => {
    const allowed = options();
    const html = renderToStaticMarkup(
      createElement(AnswerSettings, { options: allowed, pick: defaultPick(allowed), locked: true, onPick: () => {} }),
    );
    expect(html).toMatch(/<fieldset[^>]* disabled=""/);
  });
});

describe("the answer line", () => {
  // Changed on purpose (Tushar, 16 Oct 2026, one model in Ask): teamLine is gone.
  it("never says who answered or whether models agreed", () => {
    for (const name of ["components/chat/AnswerView.tsx", "components/chat/answer.ts", "components/chat/AnswerSettings.tsx"]) {
      const code = readFileSync(name, "utf8").replace(/^\s*(\/\/|\*|\/\*).*$/gm, "");
      expect(code, name).not.toMatch(/teamLine|They agreed|did not agree|answer together|answers alone/);
    }
  });

  it("names a model without its maker's prefix", () => {
    expect(modelName("Google: Gemini 3.8 Flash")).toBe("Gemini 3.8 Flash");
    expect(modelName("Gemini 4")).toBe("Gemini 4");
  });
});
