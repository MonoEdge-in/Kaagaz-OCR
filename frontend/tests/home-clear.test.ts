// Home made clear (Tushar, 24 Oct 2026: "I am not able to understand what to do"): a calm
// greeting that says what the page is for, Ask as the one main action, then one card per
// next step (Check values, Upload documents, Browse documents), each with a plain sentence
// and its own button; one entrance on load and a lift under the mouse, where motion is welcome.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { HomeActions, READY_LOOKS, ReadyQuestions } from "@/components/dashboard/Dashboard";
import { browseTitle, checkTitle, DASHBOARD_TEXT } from "@/components/dashboard/words";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
const HOME_CSS = "components/dashboard/Dashboard.module.css";
const cards = (over: Partial<Parameters<typeof HomeActions>[0]> = {}) =>
  renderToStaticMarkup(
    createElement(HomeActions, { reviewer: true, reviewTotal: 39, documents: 9, picker: null, onUpload: () => undefined, ...over }),
  );

describe("the home screen's next steps", () => {
  it("names each step in plain words, with its count", () => {
    expect(checkTitle(39)).toBe("Check 39 values");
    expect(checkTitle(1)).toBe("Check 1 value");
    expect(checkTitle(0)).toBe("Nothing to check");
    expect(checkTitle(null)).toBe("Check values");
    expect(browseTitle(9)).toBe("Browse 9 documents");
    expect(browseTitle(1)).toBe("Browse 1 document");
    expect(browseTitle(null)).toBe("Browse documents");
  });

  it("gives a reviewer three cards, each a sentence and a button that says where it goes", () => {
    const html = cards();
    expect(html).toContain("Check 39 values");
    expect(html).toContain(DASHBOARD_TEXT.checkLine);
    expect(html).toMatch(/<a class="[^"]*action[^"]*"[^>]*href="\/review"/);
    expect(html).toContain("Upload documents");
    expect(html).toMatch(/<button type="button" class="k-btn k-btn--primary[^"]*">[\s\S]*Choose files<\/button>/);
    expect(html).toContain("Browse 9 documents");
    expect(html).toMatch(/<a class="[^"]*action[^"]*"[^>]*href="\/documents"/);
    expect(html.match(/style="--i:\d"/g)).toEqual(['style="--i:0"', 'style="--i:1"', 'style="--i:2"']);
  });

  it("leaves Review out for a member, who has none", () => {
    const html = cards({ reviewer: false });
    expect(html).not.toContain('href="/review"');
    expect(html).toContain("Upload documents");
    expect(html).toContain("Browse 9 documents");
  });

  it("says once what the page is for, with no numbered decoration or second big number", () => {
    const home = read("components/dashboard/Dashboard.tsx");
    expect(home).toContain("{DASHBOARD_TEXT.lead}");
    expect(home).not.toContain("k-tagline");
    expect(home).not.toContain("waitingText");
    expect(css(HOME_CSS)).not.toMatch(/\.panelNumber/);
  });

  it("enters once and lifts under the mouse, only where motion is welcome", () => {
    const text = css(HOME_CSS);
    const calm = text.slice(text.indexOf("@media (prefers-reduced-motion: no-preference)"));
    expect(calm).toMatch(/\.action \{[^}]*animation: home-rise var\(--motion-calm\) var\(--ease-out\) both;/);
    expect(text).toMatch(/@keyframes home-rise \{\s*from \{\s*opacity: 0;\s*transform: translateY\(8px\);/);
    expect(text).toMatch(/@media \(hover: hover\) and \(pointer: fine\) \{[^@]*a\.action:hover \{[^}]*transform: translateY\(-2px\);/);
  });
});

// Changed on purpose (24 Oct 2026, later still): Home's questions sit under Ask as one-press
// recommendations, each offered only once its answer is ready (an answer in under a second);
// Home looks again until every one is ready.
describe("the ready questions under Ask", () => {
  const row = (questions: Parameters<typeof ReadyQuestions>[0]["questions"]) =>
    renderToStaticMarkup(createElement(ReadyQuestions, { questions }));

  it("offers only the questions whose answers are ready, each a link to Ask", () => {
    const html = row([
      { text: "What is the total for Shinde?", yours: true, ready: true },
      { text: "Who are the owners named in the land records?", yours: false, ready: false },
    ]);
    expect(html).toContain(">Your usual questions</span>");
    expect(html).toContain('href="/chat?q=What%20is%20the%20total%20for%20Shinde%3F&amp;send=1"');
    expect(html).not.toContain("owners named");
    expect(html.match(/style="--i:\d"/g)).toEqual(['style="--i:0"']);
  });

  it("says Ready to ask for library questions, and shows nothing while none is ready", () => {
    expect(row([{ text: "Who are the owners named in the land records?", yours: false, ready: true }])).toContain(
      ">Ready to ask</span>",
    );
    expect(row([{ text: "x", yours: false, ready: false }])).toBe("");
    expect(row(null)).toBe("");
  });

  it("looks again until every answer is ready, and sits under Ask, not in a card beside it", () => {
    expect(READY_LOOKS).toEqual([12_000, 40_000]);
    const home = read("components/dashboard/Dashboard.tsx");
    expect(home).toContain("getReadyQuestions()");
    expect(home).toMatch(/<\/form>\s*<ReadyQuestions questions=\{questions\} \/>\s*<\/section>/);
    expect(home).not.toContain("QuestionsCard");
    expect(read("components/dashboard/words.ts")).not.toContain("What did we upload");
    const text = css(HOME_CSS);
    const calm = text.slice(text.indexOf("@media (prefers-reduced-motion: no-preference)"));
    expect(calm).toMatch(/\.readyList > li \{\s*animation: home-pop var\(--motion-calm\) var\(--ease-out\) both;/);
  });
});
