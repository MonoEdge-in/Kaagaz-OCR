// Vrushit's second UAT of the document page (27 Oct 2026), one block an issue. The removal of
// "Your words in this document" (#412) is in page-words.test.ts.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { FIX_MEMBER_NOTE, FIX_NOTE, fixBox } from "@/components/viewer/scanned";
import { panTo } from "@/components/viewer/viewer";

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
    expect(details).toMatch(/<DetailsPanel[^>]*\/>\s*\{waiting > 0 && \([\s\S]*?\)\}\s*<RelatedPanel[^>]*\/>\s*<StoryPanel[^>]*\/>/);
    const code = view.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1"); // its comments say what went
    expect(code).not.toMatch(/Values found|values-heading|<table|Verify all|Show all|verifyValue|FIRST_VALUES/);
    expect(css(VIEW_CSS)).not.toMatch(/\.(values|valuesTable|valuesWrap|valuesNote|valuePage|showAll|activeRow|labelCell)\b/);
  });

  it("leaves the values reachable: the summary card's facts on the page, and Review to verify them", () => {
    expect(read("app/(app)/documents/[id]/SummaryCard.tsx")).toContain("onClick={() => onShow(item)}");
    expect(read(VIEW)).toContain("<SummaryCard detail={detail} active={selection?.region ?? null} onShow={showSpot} />");
    expect(read("components/shell/nav.ts")).toMatch(/href: "\/review"/);
    // Follow-up: Details links a reviewer or an admin to this document's values waiting in Review,
    // counted from the loaded detail (no request of its own), only when some wait.
    const view = read(VIEW);
    expect(view).toMatch(
      /const waiting =\s+canReview\(me\.user\.role\) && detail\.deleted_at === null\s+\? detail\.values\.filter\(\(value\) => value\.label === "needs_review"\)\.length\s+: 0;/,
    );
    expect(view).toContain("href={`/review?document_id=${encodeURIComponent(detail.id)}`}");
    expect(view).toContain('{`Review values (${waiting.toLocaleString("en-IN")} waiting)`}');
    expect(css(VIEW_CSS)).toMatch(/\.reviewValues \{\s*align-self: flex-start;\s*\}/);
    expect(read("components/review/ReviewQueue.tsx")).toContain('searchParams.get("document_id")');
    // Add meaning stays on the Text tab's lines, and My words says so (no value row offers it now).
    expect(read(VIEW)).toContain("onAddMeaning={addMeaning}");
    expect(read("components/words/ViewerWords.tsx")).toContain("Choose &quot;Add meaning&quot; by a line in Text.");
  });
});

describe("#418 My words first among the document's own tabs", () => {
  it("puts My words right after Text, so it is among the four tabs a wide panel shows", () => {
    const view = read(VIEW);
    const list = view.slice(view.indexOf("const tabs: TabItem[] = ["), view.indexOf("];", view.indexOf("const tabs: TabItem[] = [")));
    const ids = [...list.matchAll(/id: "(\w+)"/g)].map((match) => match[1]);
    expect(ids).toEqual(["overview", "attention", "scanned", "words", "questions", "clear", "versions", "activity"]);
    // At most four show (PanelTabs: tabs.slice(0, room)), Needs attention or not: My words is one.
    expect(ids.slice(0, 4)).toContain("words");
    expect(ids.filter((id) => id !== "attention").slice(0, 4)).toContain("words");
  });
});

describe("#417 a [?] in the Text view: type the word in its place, right there", () => {
  const panel = read("components/viewer/ScannedTextPanel.tsx");

  it("asks in plain words, empty for an unread [?], else starting from the word as it reads", () => {
    expect(fixBox({ text: "[?]" })).toEqual({ hint: "Type the word you see on the page in place of [?].", draft: "" });
    expect(fixBox({ text: "Krishn4" })).toEqual({
      hint: "Type the word you see on the page in place of 'Krishn4'.",
      draft: "Krishn4",
    });
    // Changed on purpose (#443): the box confirms a word as read too.
    expect(FIX_NOTE).toBe(
      "To type the right word in place of [?] or a marked word, or confirm it as read, press its line or Fix text.",
    );
    expect(panel).toMatch(
      /\{reader\?\.role === "first" && flags\.length > 0 && \(onFix \|\| member\) && \(\s*<p className=\{styles\.muted\}>\{onFix \? FIX_NOTE : FIX_MEMBER_NOTE\}<\/p>\s*\)\}/,
    );
  });

  it("tells a member, who cannot type a word, who can (follow-up)", () => {
    // Changed on purpose (#443): any marked word (a lone "." too), not only a [?].
    expect(FIX_MEMBER_NOTE).toBe("Only a reviewer or an admin can fix or confirm a marked word.");
    expect(read(VIEW)).toContain("member={review.status?.can_review === false}");
    // A member gets no Fix text and no box: onFix is a reviewer's or an admin's only.
    expect(read(VIEW)).toContain("onFix={review.status?.can_review && detail.deleted_at === null ? fixWord : undefined}");
    expect(panel).toContain("const toFix = first && onFix ?");
  });

  // Changed on purpose (#431): the box asks for the word Fix text works on (ask, the line as shown
  // for the line's own flag), and while it is open the row's Fix text steps aside (fix-text-431.test.ts).
  it("opens the box under the line from a press on the line or Fix text, labelled by its hint", () => {
    expect(panel).toMatch(/onShow\(\{ name: `line \$\{index \+ 1\}`, region: line\.region, label: null \}\);\s*if \(toFix && ask\) openFix\(toFix, ask\.draft\);/);
    expect(panel).toContain("aria-expanded={false}");
    expect(panel).toMatch(/<label className=\{styles\.fixLabel\} htmlFor=\{`\$\{boxId\}-text`\}>\s*\{ask\.hint\}/);
    expect(panel).toMatch(/<input\s+ref=\{fixInput\}\s+id=\{`\$\{boxId\}-text`\}/);
    // [Cancel] [Primary]; Esc in the field closes the box (not the panel); focus goes back to the line.
    expect(panel.indexOf(">\n                            Cancel")).toBeLessThan(panel.indexOf(">\n                            Save"));
    expect(panel).toContain("event.stopPropagation(); // Esc here closes the box, not the panel");
    expect(panel).toContain('rows.current[index]?.querySelector("button")?.focus();');
    // Changed on purpose (#443): Confirm (asRead) sends no typed text.
    expect(panel).toContain('if (typed === "" && !asRead) return setFixing({ ...fixing, error: "Type the word you see on the page." });');
  });

  it("saves through the review's own Edit (saveWord), then asks for the words again", () => {
    const view = read(VIEW);
    expect(view).toContain("const done = await saveWord(detail.id, flag, typed === flag.text ? null : typed);");
    // Changed on purpose (#443): with the word, so the whole document comes again only when it may
    // have kept its page as is (attention-settle-443.test.ts).
    expect(view).toContain("if (done.ok || done.reload) review.reload(flag);");
    expect(view).toContain("onFix={review.status?.can_review && detail.deleted_at === null ? fixWord : undefined}");
    const sheet = css("components/viewer/ScannedText.module.css");
    // Changed on purpose (#431, Tushar 28 Oct 2026): the field on its own line, Cancel and Save
    // together under it, so Save never drops onto a line of its own in a narrow panel.
    expect(sheet).toMatch(/\.fixInput \{[^}]*width: 100%;[^}]*min-width: 0;/);
    expect(sheet).toMatch(/\.fixActions \{[^}]*display: flex;/);
    expect(sheet).not.toMatch(/\.fixActions \{[^}]*flex-wrap/);
    const panel = read("components/viewer/ScannedTextPanel.tsx");
    expect(panel).toMatch(/<div className=\{styles\.fixActions\}>\s*<button type="button" className="btn"[\s\S]*?Cancel[\s\S]*?Save\s*<\/button>\s*<\/div>/);
  });
});

describe("#419 the PDF viewer's hand: drag a zoomed page around", () => {
  const viewer = read("components/viewer/PdfDocumentViewer.tsx");

  it("scrolls the other way the pointer moves, from where the drag began", () => {
    const press = { x: 100, y: 200, left: 50, top: 400 };
    expect(panTo(press, 100, 200)).toEqual({ left: 50, top: 400 });
    expect(panTo(press, 60, 150)).toEqual({ left: 90, top: 450 }); // dragged up and left: shows more right and below
    expect(panTo(press, 130, 260)).toEqual({ left: 20, top: 340 });
  });

  it("is a toolbar toggle a keyboard reaches, named and pressed", () => {
    expect(viewer).toMatch(/aria-pressed=\{hand\}\s+aria-label="Hand"/);
    expect(viewer).toContain("onClick={() => setHand((on) => !on)}");
    expect(viewer).toContain("const [hand, setHand] = useState(false);");
  });

  it("pans only while on, never animated, leaving marks pressable and selection to the engine when off", () => {
    const effect = viewer.slice(viewer.indexOf("// The hand (#419), on the viewer's own scroll area"), viewer.indexOf("const overlay = useCallback("));
    expect(effect).toContain("if (!hand || !open || !area) return;");
    expect(effect).toContain('event.target.closest("button, a")');
    expect(effect).toContain('area.scrollTo({ ...panTo(press, event.clientX, event.clientY), behavior: "instant" });');
    // The engine's exclusion rule keeps its text selection out while the hand is on, and back after.
    expect(effect).toContain('area.dataset.noInteraction = "";');
    expect(effect).toContain("delete area.dataset.noInteraction;");
    for (const type of ["pointerdown", "pointermove", "pointerup", "pointercancel"]) {
      expect(effect).toContain(`area.addEventListener("${type}"`);
      expect(effect).toContain(`area.removeEventListener("${type}"`);
    }
    expect(effect).not.toMatch(/pointerType/); // a mouse, a pen and a finger alike
    // A grab cursor while on, a grabbing one while dragged.
    expect(effect).toContain('area.dataset.hand = "grabbing";');
    expect(effect).toContain("delete area.dataset.hand;");
    expect(viewer).toContain("className={`extend-ui flex min-h-0 flex-1 flex-col ${hand ? styles.hand : \"\"}`}");
    const sheet = css("components/viewer/PageViewer.module.css");
    expect(sheet).toMatch(/\.hand \[data-hand\],\s*\.hand \[data-hand\] \*:not\(button\) \{\s*cursor: grab !important;/);
    expect(sheet).toMatch(/\.hand \[data-hand="grabbing"\],\s*\.hand \[data-hand="grabbing"\] \*:not\(button\) \{\s*cursor: grabbing !important;/);
  });
});
