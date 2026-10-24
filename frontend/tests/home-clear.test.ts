// Home made clear (Tushar, 24 Oct 2026: "I am not able to understand what to do"): a calm
// greeting that says what the page is for, Ask as the one main action, then one card per
// next step (Check values, Upload documents, Browse documents), each with a plain sentence
// and its own button; one entrance on load and a lift under the mouse, where motion is welcome.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { HomeActions } from "@/components/dashboard/Dashboard";
import { browseTitle, checkTitle, DASHBOARD_TEXT, suggestedQuestions } from "@/components/dashboard/words";

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
    expect(text).toMatch(/@media \(hover: hover\) and \(pointer: fine\) \{\s*a\.action:hover \{[^}]*transform: translateY\(-2px\);/);
  });
});

// Changed on purpose (24 Oct 2026): "What did we upload this week?" was offered to Ask, which
// reads what documents say, not when they came, so it always answered "Nothing matches".
describe("the questions Home offers to Ask", () => {
  it("offers only questions about what documents say", () => {
    expect(suggestedQuestions([{ doc_type: "land_record" }, { doc_type: "invoice" }])).toEqual([
      "Who are the owners named in the land records?",
      "Which invoice had the largest total last month?",
    ]);
    expect(suggestedQuestions([])).toEqual([]);
    expect(read("components/dashboard/words.ts")).not.toContain("What did we upload");
  });
});
