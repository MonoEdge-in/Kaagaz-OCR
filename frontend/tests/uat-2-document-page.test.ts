// Vrushit's second UAT of the document page (27 Oct 2026), one block an issue. The removal of
// "Your words in this document" (#412) is in page-words.test.ts.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const css = (path: string) => read(path).replace(/\/\*[\s\S]*?\*\//g, "");
const VIEW = "app/(app)/documents/[id]/DocumentView.tsx";
const VIEW_CSS = "app/(app)/documents/[id]/DocumentView.module.css";

describe("#415 the summary card's Show the full summary sits inside the card", () => {
  it("keeps the button's own side padding, so its pill never runs past the card's text edge", () => {
    const rule = /\n\.expand \{([^}]*)\}/.exec(css(VIEW_CSS))?.[1] ?? "";
    expect(rule).toContain("align-self: flex-start;");
    expect(rule).not.toMatch(/padding/);
    expect(rule).not.toMatch(/margin[^:]*:\s*-/);
    expect(read("app/(app)/documents/[id]/SummaryCard.tsx")).toContain("className={`btn btn-quiet ${styles.expand}`}");
  });
});

describe("#416 no Values found in the Details tab", () => {
  it("draws Details as its panels only: no values table, Verify, Verify all, Add meaning or Show all", () => {
    const view = read(VIEW);
    const panels = view.slice(view.indexOf("<PanelTabs"), view.indexOf("</PanelTabs>"));
    const details = panels.slice(panels.indexOf('shownTab === "overview"'), panels.indexOf('shownTab === "attention"'));
    expect(details).toMatch(/<DetailsPanel[^>]*\/>\s*<RelatedPanel[^>]*\/>\s*<StoryPanel[^>]*\/>/);
    const code = view.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1"); // its comments say what went
    expect(code).not.toMatch(/Values found|values-heading|<table|Verify all|Show all|verifyValue|FIRST_VALUES/);
    expect(css(VIEW_CSS)).not.toMatch(/\.(values|valuesTable|valuesWrap|valuesNote|valuePage|showAll|activeRow|labelCell)\b/);
  });

  it("leaves the values reachable: the summary card's facts on the page, and Review to verify them", () => {
    expect(read("app/(app)/documents/[id]/SummaryCard.tsx")).toContain("onClick={() => onShow(item)}");
    expect(read(VIEW)).toContain("<SummaryCard detail={detail} active={selection?.region ?? null} onShow={showSpot} />");
    expect(read("components/shell/nav.ts")).toMatch(/href: "\/review"/);
    // Add meaning stays on the Text tab's lines, and My words says so (no value row offers it now).
    expect(read(VIEW)).toContain("onAddMeaning={addMeaning}");
    expect(read("components/words/ViewerWords.tsx")).toContain("Choose &quot;Add meaning&quot; by a line in Text.");
  });
});
