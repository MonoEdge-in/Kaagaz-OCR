// Chip, Tag, LabelChip, StatusBadge, VersionChip and ProblemFlag, against
// Components.html section 08.

import { createElement as h } from "react";
import { describe, expect, it } from "vitest";
import { Chip, ChipRow, Tag } from "@/components/ui/Chip/Chip";
import { LabelChip } from "@/components/ui/LabelChip/LabelChip";
import type { Sensitivity } from "@/components/ui/LabelChip/LabelChip";
import { StatusBadge } from "@/components/ui/StatusBadge/StatusBadge";
import { ProblemFlag, VersionChip } from "@/components/ui/VersionChip/VersionChip";
import type { UiStatus } from "@/lib/status";
import { render } from "./kit-render";


describe("Chip", () => {
  it("is off by default: a button with no pressed state", () => {
    const html = render(Chip, { end: "menu" }, "Type");
    expect(html).toMatch(/^<button type="button" class="_chip_\w+">Type<svg[^>]*><path d="M6 9l6 6 6-6"/);
    expect(html).not.toContain("aria-pressed");
  });

  it("is on with its value and says pressing it removes the filter", () => {
    const html = render(Chip, { pressed: true, value: "Accounts", end: "remove" }, "Department: ");
    expect(html).toContain('aria-pressed="true"');
    expect(html).toMatch(/Department: <span class="_value_\w+">Accounts<\/span><svg[^>]*><path d="M6 6l12 12M18 6 6 18"/);
    expect(html).toContain('<span class="visually-hidden">, remove filter</span>');
  });

  it("is a toggle that can be pressed and released", () => {
    expect(render(Chip, { pressed: false }, "Needs attention")).toContain('aria-pressed="false"');
  });

  it("draws the dashed add chip with a plus first", () => {
    const html = render(Chip, { add: true }, "Party");
    expect(html).toMatch(/class="_chip_\w+ _add_\w+"><svg[^>]*><path d="M12 5v14M5 12h14"><\/path><\/svg>Party/);
  });

  it("lines chips up in a wrapping row", () => {
    expect(render(ChipRow, null, h(Chip, null, "A"))).toMatch(/^<div class="_row_\w+"><button/);
  });
});

describe("Tag", () => {
  it("is a quiet word, Devanagari in its own face", () => {
    expect(render(Tag, null, "GST")).toMatch(/^<span class="_tag_\w+">GST<\/span>$/);
    expect(render(Tag, { deva: true }, "सातबारा")).toMatch(/class="_tag_\w+ _deva_\w+">सातबारा/);
  });
});

describe("LabelChip", () => {
  it.each([
    ["public", "Public"],
    ["internal", "Internal"],
    ["confidential", "Confidential"],
    ["restricted", "Restricted"],
  ] as [Sensitivity, string][])("draws %s with its own shape and the word", (sensitivity, words) => {
    expect(render(LabelChip, { sensitivity })).toMatch(new RegExp(`^<span class="_lab_\\w+ _${sensitivity}_\\w+">${words}</span>$`));
  });
});

describe("StatusBadge", () => {
  it.each([
    ["verified", "Verified", "M5 12l5 5 9-10"],
    ["review", "Ready to verify", "M9.5 9.5a2.5"],
    ["from", "From documents", "M14 3H6v18h12V7zM14 3v4h4"],
    ["notfound", "Not found", "M8 12h8"],
    ["failed", "Failed", "M12 4 2.5 20h19z"],
  ] as [UiStatus, string, string][])("draws %s with its icon and words", (state, words, path) => {
    const html = render(StatusBadge, { state });
    expect(html).toMatch(new RegExp(`^<span class="_status_\\w+ _${state}_\\w+"><svg`));
    expect(html).toContain(`<path d="${path}`);
    expect(html).toMatch(new RegExp(`</svg>${words}</span>$`));
  });

  it("draws reading with breathing dots instead of an icon", () => {
    const html = render(StatusBadge, { state: "reading" }, "Reading 4 of 12");
    expect(html).toMatch(/_reading_\w+"><span class="_dots_\w+" aria-hidden="true"><i><\/i><i><\/i><i><\/i><\/span>Reading 4 of 12<\/span>$/);
    expect(html).not.toContain("<svg");
  });

  it("takes more exact words than the state's name", () => {
    expect(render(StatusBadge, { state: "verified" }, "Ready")).toMatch(/<\/svg>Ready<\/span>$/);
  });
});

describe("VersionChip and ProblemFlag", () => {
  it("draws plain, current and newer versions", () => {
    expect(render(VersionChip, null, "v1")).toMatch(/^<span class="_ver_\w+">v1<\/span>$/);
    expect(render(VersionChip, { kind: "current" }, "Current")).toMatch(/class="_ver_\w+ _current_\w+"/);
    expect(render(VersionChip, { kind: "newer" }, "v1 · newer?")).toMatch(/class="_ver_\w+ _newer_\w+">v1 · newer\?/);
  });

  it("flags a problem with an icon and words", () => {
    expect(render(ProblemFlag, null, "2 pages unreadable")).toMatch(/^<span class="_problem_\w+"><svg[^>]*>.*<\/svg>2 pages unreadable<\/span>$/);
  });
});
