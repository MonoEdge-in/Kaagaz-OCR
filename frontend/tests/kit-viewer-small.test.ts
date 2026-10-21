// PageThumb, Paper and Mark, Zoom, KeyValueList, Reading, Kbd, Skeleton,
// Avatar, NavItem and PhoneTabBar, against Components.html sections 15 and 16
// and the sidebar and tab bar in desktop/Home.html and phone/PhoneHome.html.

import { createElement as h } from "react";
import { describe, expect, it } from "vitest";
import { Avatar, initials } from "@/components/ui/Avatar/Avatar";
import { Kbd } from "@/components/ui/Kbd/Kbd";
import { KeyValueList } from "@/components/ui/KeyValueList/KeyValueList";
import { NavItem, NavLabel, PhoneTabBar } from "@/components/ui/NavItem/NavItem";
import { PageThumb, PageThumbs, thumbName } from "@/components/ui/PageThumb/PageThumb";
import { Mark, MarkLegend, Paper, PaperRule, Viewer } from "@/components/ui/Paper/Paper";
import { Reading } from "@/components/ui/Reading/Reading";
import { Skeleton } from "@/components/ui/Skeleton/Skeleton";
import { Zoom } from "@/components/ui/Zoom/Zoom";
import { render } from "./kit-render";

const noop = () => {};

describe("PageThumb", () => {
  it("marks the current page with words, not only the accent edge", () => {
    const html = render(PageThumb, { page: 1, current: true, onSelect: noop });
    expect(html).toMatch(/^<button type="button" class="_thumb_\w+ _active_\w+" aria-label="Page 1, current" aria-current="page"><span class="_page_\w+"><\/span><span aria-hidden="true">1<\/span><\/button>$/);
  });

  it("flags a page that couldn't be read, and one with values to review", () => {
    const unreadable = render(PageThumb, { page: 3, flag: "unreadable", onSelect: noop });
    expect(unreadable).toContain('aria-label="Page 3, could not be read"');
    expect(unreadable).toMatch(/<span class="_flag_\w+" aria-hidden="true">!<\/span>/);
    const review = render(PageThumb, { page: 4, flag: "review", toReview: 2, onSelect: noop });
    expect(review).toContain('aria-label="Page 4, 2 values to verify"');
    expect(review).toMatch(/<span class="_flag_\w+ _flagReview_\w+" aria-hidden="true">\?<\/span>/);
  });

  it("names every case", () => {
    expect(thumbName(2)).toBe("Page 2");
    expect(thumbName(4, false, "review", 1)).toBe("Page 4, 1 value to verify");
    expect(thumbName(4, false, "review")).toBe("Page 4, values to verify");
    expect(thumbName(1200, true, "unreadable")).toBe("Page 1,200, current, could not be read");
  });

  it("lines the pages up in a named strip", () => {
    expect(render(PageThumbs, { label: "Pages" }, "x")).toMatch(/^<nav class="_thumbs_\w+" aria-label="Pages">x<\/nav>$/);
  });
});

describe("Paper and Mark", () => {
  it("puts the paper on the sunken viewer", () => {
    expect(render(Viewer, null, h(Paper, null, h("p", null, "LEAVE AND LICENCE"), h(PaperRule)))).toMatch(
      /^<div class="_viewer_\w+"><div class="_paper_\w+"><p>LEAVE AND LICENCE<\/p><div class="_rule_\w+" aria-hidden="true"><\/div><\/div><\/div>$/,
    );
  });

  it.each([
    ["cited", "Cited"],
    ["unsure", "Unsure word"],
    ["unreadable", "Couldn’t be read"],
    ["unconfirmed", "Spot not confirmed"],
  ] as const)("draws the %s mark and says what it is", (kind, words) => {
    expect(render(Mark, { kind }, "Rs. 45,000")).toMatch(
      new RegExp(`^<mark class="_mark_\\w+ _${kind}_\\w+"><span class="visually-hidden">${words}: </span>Rs. 45,000</mark>$`),
    );
  });

  it("names the four marks in a legend", () => {
    const html = render(MarkLegend);
    expect(html.match(/<li>/g)).toHaveLength(4);
    expect(html).toMatch(/<li><i class="_cited_\w+"><\/i>Cited<\/li>/);
    expect(render(MarkLegend, { kinds: ["unsure"] }).match(/<li>/g)).toHaveLength(1);
  });
});

describe("Zoom", () => {
  it("draws out, the size, in and Fit width", () => {
    const html = render(Zoom, { percent: 125, onZoomOut: noop, onZoomIn: noop, onFit: noop });
    expect(html).toMatch(/^<div class="_zoom_\w+" role="group" aria-label="Zoom"><button type="button" aria-label="Zoom out"><svg/);
    expect(html).toMatch(/<span class="_percent_\w+" aria-live="polite">125%<\/span><button type="button" aria-label="Zoom in">/);
    expect(html).toMatch(/<button type="button" class="_fit_\w+">Fit width<\/button><\/div>$/);
  });

  it("stops at the ends", () => {
    const html = render(Zoom, { percent: 400, onZoomOut: noop, onZoomIn: noop, onFit: noop, canZoomIn: false });
    expect(html).toContain('<button type="button" aria-label="Zoom in" disabled="">');
  });
});

describe("KeyValueList", () => {
  it("lists names and values", () => {
    expect(
      render(KeyValueList, {
        items: [
          { key: "type", name: "Type", value: "Rent agreement" },
          { key: "date", name: "Document date", value: "1 Oct 2026" },
        ],
      }),
    ).toMatch(/^<dl class="_kv_\w+"><dt>Type<\/dt><dd>Rent agreement<\/dd><dt>Document date<\/dt><dd>1 Oct 2026<\/dd><\/dl>$/);
  });
});

describe("Reading", () => {
  it("shows the picked reading with a check and the word Chosen", () => {
    const html = render(Reading, { label: "First reading", value: "₹45,000", note: "matches page 1 summary", picked: true });
    expect(html).toMatch(/^<div class="_reading_\w+ _picked_\w+"><span class="_head_\w+"><span class="k-eyebrow">First reading<\/span><\/span><span class="_value_\w+">₹45,000<\/span>/);
    expect(html).toMatch(/<span class="k-small k-muted _note_\w+"><svg[^>]*>.*<\/svg>Chosen · matches page 1 summary<\/span><\/div>$/);
  });

  it("shows another reading with its note", () => {
    const html = render(Reading, { label: "Second reading", value: "₹46,000", note: "The 5 is smudged" });
    expect(html).toMatch(/<span class="k-small k-muted">The 5 is smudged<\/span><\/div>$/);
    expect(html).not.toContain("Chosen");
  });

  it("is the choice itself in Review, with its key", () => {
    const html = render(Reading, { label: "First reading", value: "₹45,000", shortcut: "1", onPick: noop });
    expect(html).toMatch(/^<button type="button" class="_reading_\w+" aria-pressed="false" aria-keyshortcuts="1">/);
    expect(html).toMatch(/<kbd class="_kbd_\w+">1<\/kbd>/);
  });
});

describe("Kbd, Skeleton and Avatar", () => {
  it("draws a key", () => {
    expect(render(Kbd, null, "J")).toMatch(/^<kbd class="_kbd_\w+">J<\/kbd>$/);
  });

  it("draws a loading shape the screen sizes", () => {
    expect(render(Skeleton, { width: "62%", height: 12 })).toMatch(/^<span class="_skel_\w+" style="width:62%;height:12px" aria-hidden="true"><\/span>$/);
    expect(render(Skeleton, { width: 22, height: 28, radius: 4 })).toContain('style="width:22px;height:28px;border-radius:4px"');
  });

  it("draws initials, large when asked", () => {
    expect(render(Avatar, { name: "Priya Kulkarni" })).toMatch(/^<span class="_avatar_\w+" aria-hidden="true">PK<\/span>$/);
    expect(render(Avatar, { name: "Anita", size: "lg" })).toMatch(/class="_avatar_\w+ _lg_\w+" aria-hidden="true">AN</);
    expect(initials("  rahul  ")).toBe("RA");
    expect(initials("Suresh Baburao Shinde")).toBe("SS");
    expect(initials("")).toBe("?");
  });
});

describe("NavItem and PhoneTabBar", () => {
  it("draws a sidebar link, the current one marked", () => {
    const html = render(NavItem, { href: "/home", label: "Home", icon: "home", active: true });
    expect(html).toMatch(/^<a class="_item_\w+ _active_\w+" aria-current="page" href="\/home"><svg[^>]*>.*<\/svg><span>Home<\/span><\/a>$/);
  });

  it("shows work waiting as a count said in words, and nothing at 0", () => {
    const html = render(NavItem, { href: "/review", label: "Review", icon: "review", count: 23, countWords: "to review" });
    expect(html).toMatch(/<span>Review<\/span><span class="_count_\w+">23<span class="visually-hidden"> to review<\/span><\/span><\/a>$/);
    expect(render(NavItem, { href: "/review", label: "Review", icon: "review", count: 0 })).not.toContain("_count_");
  });

  it("heads a group", () => {
    expect(render(NavLabel, null, "Workspace")).toMatch(/^<span class="_label_\w+">Workspace<\/span>$/);
  });

  it("draws the phone's tabs with the current one filled", () => {
    const html = render(PhoneTabBar, {
      places: [
        { href: "/home", label: "Home", icon: "home", active: true },
        { href: "/review", label: "Review", icon: "review", count: 23, countWords: "to review" },
      ],
    });
    expect(html).toMatch(/^<nav class="_tabbar_\w+" aria-label="Main"><a class="_tab_\w+ _tabActive_\w+" aria-current="page" href="\/home">/);
    expect(html).toMatch(/<span class="_tabCount_\w+">23<span class="visually-hidden"> to review<\/span><\/span><\/span>Review<\/a><\/nav>$/);
  });
});
