// Tushar, 16 Oct 2026: "Needs review" is shown as "Ready to verify", and a value's label
// shows its confidence on hover and focus, in a tooltip only (never by default).

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TO_VERIFY_TIP, kpiTiles } from "@/components/admin/logic";
import { canReview } from "@/components/review/review";
import { TIP_DELAY, hidesTip, placeTip } from "@/components/ui/Tip";
import { LabelBadge } from "@/components/viewer/LabelBadge";
import { confidenceTip, labelText } from "@/components/viewer/labels";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");

function sources(dir: string): string[] {
  return readdirSync(join(ROOT, dir)).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(join(ROOT, path)).isDirectory()) return sources(path);
    return /\.tsx?$/.test(name) ? [path] : [];
  });
}

/** The code without its comments, so only strings and JSX text are left to say words. */
const code = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");

describe("Ready to verify", () => {
  it("is the label's word, and Verified stays", () => {
    expect(labelText("needs_review")).toBe("Ready to verify");
    expect(labelText("verified")).toBe("Verified");
  });

  // Changed on purpose (Vrushit UAT 2, 27 Oct 2026, #416): Values found, and its Verify and
  // Verify all, are gone from the document page. A value is verified in Review: one at a time
  // (POST /review/{id}) or all the queue shows (POST /review/verify-all), for one document too.
  it("is verified in Review, by a reviewer or an admin, one value or all, for one document too", () => {
    expect([canReview("member"), canReview("reviewer"), canReview("admin")]).toEqual([false, true, true]);
    expect(read("app/(app)/documents/[id]/DocumentView.tsx")).not.toMatch(/resolveReview|Verify all|verifyAllValues/);
    expect(read("components/review/ReviewCard.tsx")).toContain(
      "const after = await resolveReview(value.id, check.choice, remember, check.value);",
    );
    const queue = read("components/review/ReviewQueue.tsx");
    expect(queue).toContain("await verifyAllReview(verifyAllFilters(documentId, reason));");
    expect(queue).toContain('const documentId = searchParams.get("document_id") || undefined;');
  });


  it("is never shown to people as Needs review (internal ids like needs_review stay)", () => {
    const files = [...sources("components"), ...sources("app"), ...sources("lib")];
    // "Needs a check" was the same label on answers; "the exact spot ... needs a check" is a page's problem, not a label.
    const saying = files.filter((path) => /needs review|["'`]needs a check["'`]/i.test(code(read(path))));
    expect(saying).toEqual([]);
  });

  it("names the Admin tile Ready to verify, with a one-line tooltip that names no number", () => {
    const tile = kpiTiles({
      documents_by_status: {},
      open_review_items: 146,
    } as unknown as Parameters<typeof kpiTiles>[0])[2];
    expect(tile).toEqual({ label: "Ready to verify", value: "146", note: "Values to verify in Review", tip: TO_VERIFY_TIP });
    expect(TO_VERIFY_TIP).toMatch(/Open Review to verify them\.$/);
    expect(TO_VERIFY_TIP).not.toMatch(/\d|%|couldn't|sorry|unfortunately|perfect|guaranteed/i);
    expect(read("components/admin/NumbersPanel.tsx")).toContain(
      "{tile.tip ? <Tip text={tile.tip}>{tile.label}</Tip> : tile.label}",
    );
  });
});

describe("the confidence tooltip", () => {
  it("rounds the 0 to 1 score, and never invents one", () => {
    expect(confidenceTip(0.716)).toBe("Confidence 72%");
    expect(confidenceTip(0.9)).toBe("Confidence 90%");
    expect(confidenceTip(1)).toBe("Confidence 100%");
    expect(confidenceTip(0)).toBe("Confidence 0%");
    expect(confidenceTip(null)).toBe("Confidence not measured");
    expect(confidenceTip(undefined)).toBe("Confidence not measured");
    expect(confidenceTip(Number.NaN)).toBe("Confidence not measured");
  });

  it("makes the badge focusable and described only when it has a tooltip", () => {
    const plain = renderToStaticMarkup(createElement(LabelBadge, { label: "needs_review" }));
    expect(plain).not.toContain("tabindex");
    expect(plain).not.toContain("role=\"tooltip\"");
    expect(plain).not.toContain("%");

    const tipped = renderToStaticMarkup(createElement(LabelBadge, { label: "needs_review", confidence: 0.716 }));
    const described = /tabindex="0" aria-describedby="([^"]+)"/.exec(tipped);
    expect(described).not.toBeNull();
    expect(tipped).toContain(`id="${described?.[1]}" role="tooltip"`);
    expect(tipped).toMatch(/role="tooltip" class="tip tabular" hidden="">Confidence 72%<\/span>/);
    // The percentage is only in the hidden tooltip, never shown by default.
    expect(tipped.replace(/<span[^>]*role="tooltip"[^>]*>[^<]*<\/span>/, "")).not.toContain("%");

    const unmeasured = renderToStaticMarkup(createElement(LabelBadge, { label: "verified", confidence: null }));
    expect(unmeasured).toContain(">Confidence not measured</span>");
    expect(unmeasured).toContain("Verified");
  });

  it("hides on Esc, shows after a short delay, on hover and on focus", () => {
    expect(hidesTip("Escape")).toBe(true);
    expect(hidesTip("Enter")).toBe(false);
    expect(TIP_DELAY).toBeGreaterThan(0);
    expect(TIP_DELAY).toBeLessThanOrEqual(500);
    const tip = read("components/ui/Tip.tsx");
    expect(tip).toContain('window.addEventListener("keydown", onKey)');
    expect(tip).toContain("onFocus={() => later(true, TIP_DELAY)}");
    expect(tip).toContain("onMouseEnter=");
    expect(tip).toContain("onBlur={hide}");
  });

  it("is placed under its anchor, above it near the bottom, and never past an edge", () => {
    const view = { width: 390, height: 800 };
    const tip = { width: 200, height: 30 };
    expect(placeTip({ top: 100, bottom: 124, left: 40 }, tip, view)).toEqual({ top: 128, left: 40 });
    // Near the right edge: pulled in, 8px clear.
    expect(placeTip({ top: 100, bottom: 124, left: 300 }, tip, view).left).toBe(390 - 8 - 200);
    // Near the bottom: above the anchor.
    expect(placeTip({ top: 770, bottom: 794, left: 40 }, tip, view).top).toBe(770 - 4 - 30);
    // Wider than the screen: never past the left edge.
    expect(placeTip({ top: 100, bottom: 124, left: 10 }, { width: 500, height: 30 }, view).left).toBe(8);
  });

  // Changed on purpose (UI rebuild, 21 Oct 2026): the handoff's raised surface, soft corner and shadow.
  it("looks like the handoff's floating card: soft corner, hairline edge, raised surface, small tabular text", () => {
    const css = read("app/globals.css");
    const rule = /\n\.tip \{([^}]*)\}/.exec(css)?.[1] ?? "";
    expect(rule).toContain("border-radius: var(--r-sm);");
    expect(rule).toContain("border: 1px solid var(--k-card-edge);");
    expect(rule).toContain("background: var(--k-raised);");
    expect(rule).toContain("box-shadow: var(--e-2);");
    expect(rule).toContain("font-size: var(--fs-sm);");
    expect(read("components/ui/Tip.tsx")).toContain('className="tip tabular"');
  });

  // Changed on purpose (Vrushit UAT 2, 27 Oct 2026, #416): the values table is gone.
  it("is on every value's badge that has a score: the review card", () => {
    expect(read("components/review/ReviewCard.tsx")).toContain("text={confidenceTip(value.confidence ?? null)}");
  });
});
