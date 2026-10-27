// Answer settings (features 25-27, council 15 Oct 2026, C12, with Tushar's override:
// models picked by name, thinking High / Medium / Low). Changed on purpose (Tushar,
// 16 Oct 2026, one model in Ask): one model answers, picked with radio buttons; no
// Ensemble checkboxes, no "Up to 3 answer together" or "High works only when one
// answers alone" lines, and no line under an answer on who answered or whether they agreed.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { modelName } from "@/components/chat/answer";
import { AnswerSettings } from "@/components/chat/AnswerSettings";
import * as logic from "@/components/chat/logic";
import { choiceBody, defaultPick, hasChoices, pickSummary } from "@/components/chat/logic";
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
    // Changed on purpose (Vrushit UAT, 25 Oct 2026): the engine is the only choice; more than the
    // usual model adds none.
    expect(hasChoices(options({ levels: ["low"] }))).toBe(false);
  });

  it("start from the organisation's default, which sends nothing", () => {
    const allowed = options({ default_level: "medium" });
    const pick = defaultPick(allowed);
    // Changed on purpose (Vrushit UAT, 25 Oct 2026): the pick is the engine alone, no model.
    expect(pick).toEqual({ level: "medium" });
    expect(choiceBody(allowed, pick)).toBeUndefined();
    expect(choiceBody(null, null)).toBeUndefined();
    // Changed on purpose (spec 17 Oct 2026): org users never see a model's name.
    // Changed on purpose (SoW 19 Oct 2026): Medium is Swift, and one model shows the engine alone.
    expect(pickSummary(allowed, pick)).toBe("Swift");
  });

  // SoW 19 Oct 2026: Ask does not offer Low; an organisation defaulting to Low starts on Swift,
  // and Swift is then sent, since it is not the organisation's own level.
  it("start on Swift over a Low default, and send it", () => {
    const allowed = options({ default_level: "low" });
    const pick = defaultPick(allowed);
    expect(pick.level).toBe("medium");
    // Changed on purpose (Vrushit UAT, 25 Oct 2026): no model is sent; the server's default answers.
    expect(choiceBody(allowed, pick)).toEqual({ thinking: "medium" });
    expect(defaultPick(options({ default_level: "low", levels: ["low"] })).level).toBe("low");
  });

  // Changed on purpose (Vrushit UAT, 25 Oct 2026: "give only one option, Swift and Precision, and
  // make sure there is a difference"): Precision always sends High and names no model, so the
  // server answers on the strongest model the organisation allows (chat.py _choice).
  it("send Precision as High with no model, even when High is the organisation's own level", () => {
    const allowed = options({ agents: [FLASH, SONNET, PRO, FOURTH], ensemble: true });
    expect(choiceBody(allowed, { level: "high" })).toEqual({ thinking: "high" });
    expect(choiceBody(options({ default_level: "high" }), { level: "high" })).toEqual({ thinking: "high" });
    expect(choiceBody(options({ default_level: "medium" }), { level: "medium" })).toBeUndefined();
    expect(pickSummary(allowed, { level: "high" })).toBe("Precision");
  });

  // Vrushit UAT (25 Oct 2026): "why are there two options given": the Ask bar's answer quality
  // picker (Off, High, Extra high, Ultra) is gone; the composer's AI engine is the one choice.
  it("offer no answer quality picker, and no Ask bar on wide screens", () => {
    expect(existsSync("components/chat/QualityPicker.tsx")).toBe(false);
    for (const gone of ["hasQualityChoice", "qualityChoices", "qualityOf", "chooseQuality", "chooseAgent"]) {
      expect(logic, gone).not.toHaveProperty(gone);
    }
    const screen = readFileSync("components/chat/ChatScreen.tsx", "utf8");
    expect(screen).not.toMatch(/QualityPicker|hasQualityChoice|subbarEnd|subbarList|subbarNew/);
    const css = readFileSync("components/chat/Chat.module.css", "utf8");
    expect(css).not.toMatch(/\.quality\b|\.qualityWords|\.subbarEnd/);
    const wide = css.slice(css.indexOf("@media (min-width: 1024px) {\n  .screen {"));
    const block = wide.slice(0, wide.indexOf("\n}\n"));
    expect(block).toMatch(/grid-template-areas: "pane thread";/);
    expect(block).toMatch(/\.subbar \{\s*display: none;/);
    const panel = renderToStaticMarkup(
      createElement(AnswerSettings, { options: options(), pick: defaultPick(options()), locked: false, onPick: () => {} }),
    );
    expect(panel).not.toMatch(/Answer quality|Extra high|Ultra|>Off</);
    expect(readFileSync("components/chat/AnswerSettings.tsx", "utf8")).not.toMatch(/-model|options\.agents/);
  });

  // Changed on purpose (SoW 19 Oct 2026): "AI engine", Swift then Precision; no Low.
  // Changed on purpose (plain words, 25 Oct 2026): both hints are shown, saying what each is for.
  // Changed on purpose (Vrushit UAT, 25 Oct 2026): each hint says what it really does differently.
  // Changed on purpose (#411, 27 Oct 2026): only what the code does (AnswerSettings.tsx LEVEL_HINTS):
  // the AI, how long it thinks, and reading more only with many or long documents.
  it("show the AI engine as Swift and Precision segments, each with its hint, both shown", () => {
    const allowed = options({ levels: ["low", "medium", "high"] });
    const html = renderToStaticMarkup(
      createElement(AnswerSettings, { options: allowed, pick: { level: "medium" }, locked: false, onPick: () => {} }),
    );
    const segments = [...html.matchAll(/aria-describedby="([^"]+)" name="[^"]+-level"[^>]*\/>(\w+)<\/label>/g)];
    expect(segments.map((match) => match[2])).toEqual(["Swift", "Precision"]);
    for (const [, hint] of segments) expect(html).toContain(`<p id="${hint}"`);
    expect(html).toMatch(
      /<p id="[^"]+-high" class="[^"]+">Precision: the strongest AI your organisation allows, thinking longer; with many or long documents it reads more of them\. Slower\.<\/p>/,
    );
    expect(html).toMatch(
      /<p id="[^"]+-medium" class="[^"]+">Swift: your organisation&#x27;s usual AI, thinking briefly\. Quicker, for everyday questions\.<\/p>/,
    );
    expect(html).not.toMatch(/takes more care|most relevant parts/); // #411: Precision searches and checks the same way
    expect(html).not.toMatch(/-low"|>Low</);
    expect(html).toMatch(/<legend>AI engine<\/legend>/);
    expect(html).not.toMatch(/Gemini|Claude|Sonnet|Flash/);
    expect(html).not.toMatch(MONEY);
    expect(html.replace(/<[^>]*>/g, " ")).not.toMatch(/\bmodel\b|google\/|anthropic\//i);
  });

  // Changed on purpose (Tushar, 16 Oct 2026, one model in Ask; Vrushit UAT, 25 Oct 2026, the
  // engine alone): even with Ensemble allowed, radios, one checked, and no line about answering together.
  it("show radios even when the organisation allows Ensemble, with no agree wording", () => {
    const allowed = options({ agents: [FLASH, SONNET, PRO, FOURTH], ensemble: true });
    const panel = renderToStaticMarkup(
      createElement(AnswerSettings, { options: allowed, pick: defaultPick(allowed), locked: false, onPick: () => {} }),
    );
    expect(panel).not.toContain('type="checkbox"');
    expect(panel.match(/<input type="radio"[^>]* name="[^"]+-level"/g)).toHaveLength(2);
    expect(panel.match(/name="[^"]+-level" checked=""/g)).toHaveLength(1);
    expect(panel).not.toMatch(/answer together|agree|answers alone|Up to \d/i);
    expect(panel).toContain(">Precision<");
    // Changed on purpose (chat composer, 16 Oct 2026): the panel is named, so its legend is for screen readers only.
    expect(panel).toMatch(/<fieldset[^>]*><legend class="visually-hidden">Answer settings<\/legend>/);
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
