// Owner, 29 Oct 2026 (#454): dragging or scrolling the PDF glitched, blinked and bled. Found in a
// browser trace on a made-up 8-page scan with 48 flags: the line under the viewer came and went
// with the spot, which resized the pages' scroll area, and the engine (its fit-width zoom) answers
// a resize by setting the scroll again, stopping a drag, a wheel scroll or the #446 centring
// mid-way; the viewer draws only the pages near the view, so a page scrolled back to was drawn
// anew and its CSS entrances (the spot's settle, the words' sweep) played again; and nothing kept
// what is drawn on a page inside it.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const wrapper = read("components/viewer/PdfDocumentViewer.tsx");
const between = (text: string, from: string, to: string) => text.slice(text.indexOf(from), text.indexOf(to, text.indexOf(from)));

describe("the PDF viewer stays smooth while it moves (#454)", () => {
  it("keeps everything drawn on a page inside the page", () => {
    const page = between(read("components/extend/pdf-viewer.tsx"), "data-pdf-viewer-page={pageNumber}", "pageClassName?.(pageNumber)");
    expect(page).toMatch(/className=\{cn\(\s*"relative overflow-hidden /);
  });

  it("never resizes the pages' scroll area as the spot comes and goes: its line is always laid out", () => {
    expect(wrapper).not.toMatch(/\{spot && showHighlights \? \(\s*<p/);
    expect(wrapper).toContain('style={spot && showHighlights ? undefined : { visibility: "hidden" }}');
  });

  it("plays no CSS entrance on a mark the viewer draws again, and settles a new spot once from toSpot", () => {
    const overlay = between(wrapper, "const overlay = useCallback(", "return (\n    <div");
    expect(overlay).not.toContain("styles.pulse");
    expect(overlay).toContain('style={{ ...place(region.box), animation: "none" }}'); // the words' sweep
    const toSpot = between(wrapper, "const toSpot = useEffectEvent(", "const toPage = useEffectEvent(");
    expect(toSpot).toContain("play(mark, SPOT_IN, MOTION_MS.slow);");
    // The same settle as PageViewer's spotIn: from faded and 15% larger, in --t-slow.
    expect(wrapper).toContain('const SPOT_IN: Frame[] = [{ opacity: 0, transform: "scale(1.15)" }, { opacity: 1, transform: "none" }];');
    const css = read("components/viewer/PageViewer.module.css");
    expect(css).toMatch(/@keyframes spotIn \{\s*from \{\s*opacity: 0;\s*transform: scale\(1\.15\);\s*\}\s*\}/);
    expect(css).toMatch(/\.pulse \{\s*animation: spotIn var\(--t-slow\) var\(--ease\);/);
  });

  it("stops aiming at the spot as soon as the user scrolls, drags or presses a key", () => {
    const effect = between(wrapper, "const go = (tries: number) =>", "}, [spotKey, open]);");
    expect(effect).toContain('const input = ["wheel", "pointerdown", "touchstart", "keydown"] as const;');
    expect(effect).toContain("area?.addEventListener(type, stop, { passive: true })");
    expect(effect).toContain("area?.removeEventListener(type, stop)");
    const stop = between(effect, "const stop = () => {", "const input");
    expect(stop).toContain("cancelAnimationFrame(frame);");
    // A smooth scroll still on its way to the spot is halted where it is, and page sync resumes.
    expect(stop).toMatch(/if \(area && performance\.now\(\) < steeringUntil\.current\) \{\s*area\.scrollTo\(\{ left: area\.scrollLeft, top: area\.scrollTop, behavior: "instant" \}\);/);
    expect(stop).toContain("steeringUntil.current = 0;");
  });

  it("pans the hand once a frame, reading no layout on a move", () => {
    const move = between(wrapper, "const move = (event: PointerEvent) => {", "const end = ");
    expect(move).toContain("to = panTo(press, event.clientX, event.clientY);");
    expect(move).toMatch(/frame \|\|= requestAnimationFrame\(\(\) => \{\s*frame = 0;\s*if \(to\) area\.scrollTo\(\{ \.\.\.to, behavior: "instant" \}\);/);
    expect(move).not.toMatch(/getBoundingClientRect|scrollTop|scrollLeft|offset(Top|Left|Width|Height)|client(Width|Height)/);
    expect(between(wrapper, "// The hand (#419)", "const overlay = useCallback(")).toMatch(/return \(\) => \{\s*cancelAnimationFrame\(frame\);/);
  });
});
