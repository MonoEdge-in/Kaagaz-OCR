// Button (components/ui/Button), against Components.html section 06: five
// variants, three sizes, with an icon, icon only, disabled and loading.

import { createElement as h } from "react";
import { describe, expect, it } from "vitest";
import { Button, ButtonGroup, ButtonLink } from "@/components/ui/Button/Button";
import { render } from "./kit-render";

const classOf = (html: string) => /class="([^"]*)"/.exec(html)?.[1] ?? "";

describe("Button", () => {
  it("is a plain button by default, so it never submits a form by accident", () => {
    const html = render(Button, null, "Keep separate");
    expect(html).toMatch(/^<button type="button" class="_btn_\w+">Keep separate<\/button>$/);
  });

  it.each([
    ["primary", "_primary_"],
    ["quiet", "_quiet_"],
    ["danger", "_danger_"],
    ["danger-outline", "_dangerOutline_"],
  ] as const)("draws the %s variant", (variant, cls) => {
    expect(classOf(render(Button, { variant }, "Go"))).toContain(cls);
  });

  it("draws the three sizes", () => {
    expect(classOf(render(Button, { size: "sm" }, "Review"))).toContain("_sm_");
    expect(classOf(render(Button, null, "Upload"))).not.toMatch(/_sm_|_lg_/);
    expect(classOf(render(Button, { size: "lg" }, "Sign in"))).toContain("_lg_");
  });

  it("puts the icon before the words, small on a small button", () => {
    const html = render(Button, { variant: "primary", icon: "upload" }, "Upload");
    expect(html).toMatch(/<svg width="18" height="18"[^>]*aria-hidden="true"[^>]*>.*<\/svg>Upload<\/button>$/);
    expect(render(Button, { size: "sm", icon: "move" }, "Move")).toContain('width="14"');
  });

  it("names an icon-only button with its label", () => {
    const html = render(Button, { icon: "x", label: "Close", variant: "quiet", size: "sm" });
    expect(html).toContain('aria-label="Close"');
    expect(classOf(html)).toContain("_icon_");
  });

  it("is disabled with the disabled attribute", () => {
    expect(render(Button, { disabled: true }, "Keep separate")).toContain('disabled=""');
  });

  it("shows loading with a spinner and its words, and stays focusable", () => {
    const html = render(Button, { variant: "primary", loading: true, icon: "check" }, "Linking…");
    expect(classOf(html)).toContain("_loading_");
    expect(html).toContain('aria-busy="true"');
    expect(html).not.toContain("disabled");
    expect(html).not.toContain("<svg");
    expect(html).toContain("Linking…");
  });

  it("can submit a form when asked", () => {
    expect(render(Button, { type: "submit", variant: "primary" }, "Sign in")).toContain('type="submit"');
  });
});

describe("ButtonLink", () => {
  it("is a link that looks like a button", () => {
    const html = render(ButtonLink, { href: "/documents/1?page=2", size: "sm", icon: "open-spot" }, "Open page 2");
    expect(html).toMatch(/^<a class="_btn_\w+ _sm_\w+" href="\/documents\/1\?page=2">/);
    expect(html).toContain("Open page 2</a>");
  });
});

describe("ButtonGroup", () => {
  it("wraps its buttons in one row", () => {
    expect(render(ButtonGroup, null, h(Button, null, "A"), h(Button, null, "B"))).toMatch(/^<div class="_group_\w+"><button/);
  });
});
