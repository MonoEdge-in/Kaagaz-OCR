// Tabs, Breadcrumbs, Segmented, RadioPill and Tree, against Components.html
// section 09.

import { createElement as h } from "react";
import { describe, expect, it } from "vitest";
import { Breadcrumbs } from "@/components/ui/Breadcrumbs/Breadcrumbs";
import { RadioPill, Segmented } from "@/components/ui/Segmented/Segmented";
import { tabAfterKey, tabPanelIds, Tabs } from "@/components/ui/Tabs/Tabs";
import { Tree, TreeGroup, TreeItem } from "@/components/ui/Tree/Tree";
import { render } from "./kit-render";

const noop = () => {};

describe("Tabs", () => {
  const tabs = [
    { id: "details", label: "Details" },
    { id: "questions", label: "Questions", count: 4 },
    { id: "values", label: "Values", count: "3 to check", countTone: "review" as const },
  ];
  const html = render(Tabs, { label: "Document sections", idPrefix: "doc", tabs, selected: "details", onSelect: noop });

  it("is a named tab list", () => {
    expect(html).toMatch(/^<div class="_tabs_\w+" role="tablist" aria-label="Document sections">/);
  });

  it("selects one tab, the only one in the Tab order, tied to its panel", () => {
    expect(html).toContain('role="tab" id="doc-details-tab" aria-selected="true" aria-controls="doc-details-panel" tabindex="0"');
    expect(html).toContain('id="doc-questions-tab" aria-selected="false" aria-controls="doc-questions-panel" tabindex="-1"');
    expect(tabPanelIds("doc", "details")).toEqual({ tab: "doc-details-tab", panel: "doc-details-panel" });
  });

  it("shows counts, work to do in review colours", () => {
    expect(html).toMatch(/Questions<span class="_count_\w+">4<\/span>/);
    expect(html).toMatch(/Values<span class="_count_\w+ _countReview_\w+">3 to check<\/span>/);
    expect(html).toMatch(/Details<\/button>/);
  });

  it("moves with the arrow keys, Home and End, wrapping round", () => {
    expect(tabAfterKey("ArrowRight", 0, 3)).toBe(1);
    expect(tabAfterKey("ArrowRight", 2, 3)).toBe(0);
    expect(tabAfterKey("ArrowLeft", 0, 3)).toBe(2);
    expect(tabAfterKey("Home", 2, 3)).toBe(0);
    expect(tabAfterKey("End", 0, 3)).toBe(2);
    expect(tabAfterKey("Enter", 0, 3)).toBeNull();
  });
});

describe("Breadcrumbs", () => {
  const html = render(Breadcrumbs, {
    items: [
      { label: "All documents", href: "/documents" },
      { label: "Land & Legal", href: "/documents?folder=1" },
      { label: "Survey 44, Wakad" },
    ],
  });

  it("links each level up, with a chevron between", () => {
    expect(html).toMatch(/^<nav class="_crumbs_\w+" aria-label="Breadcrumb"><a href="\/documents">All documents<\/a><svg/);
    expect(html.match(/<svg/g)).toHaveLength(2);
  });

  it("marks the last one as the current page, not a link", () => {
    expect(html).toMatch(/<span class="_current_\w+" aria-current="page">Survey 44, Wakad<\/span><\/nav>$/);
  });
});

describe("Segmented", () => {
  it("is a named group of pressed buttons", () => {
    const html = render(Segmented, {
      label: "View",
      options: [
        { value: "list", label: "List", icon: "list" },
        { value: "grid", label: "Grid", icon: "grid" },
      ],
      value: "list",
      onChange: noop,
    });
    expect(html).toMatch(/^<div class="_seg_\w+" role="group" aria-label="View">/);
    expect(html).toMatch(/<button type="button" aria-pressed="true"><svg[^>]*>.*?<\/svg>List<\/button>/);
    expect(html).toMatch(/<button type="button" aria-pressed="false"><svg[^>]*>.*?<\/svg>Grid<\/button>/);
  });
});

describe("RadioPill", () => {
  it("is a radio group with the chosen one raised", () => {
    const html = render(RadioPill, {
      label: "Search in",
      name: "scope",
      options: [
        { value: "inside", label: "Inside documents" },
        { value: "names", label: "Names" },
      ],
      value: "inside",
      onChange: noop,
    });
    expect(html).toMatch(/^<div class="_radio_\w+" role="radiogroup" aria-label="Search in">/);
    expect(html).toMatch(/<label class="_on_\w+"><input type="radio" name="scope" checked="" value="inside"\/>Inside documents<\/label>/);
    expect(html).toMatch(/<label><input type="radio" name="scope" value="names"\/>Names<\/label>/);
  });
});

describe("Tree", () => {
  it("is a named navigation with group headings", () => {
    expect(render(Tree, { label: "Folders" }, h(TreeGroup, null, "Departments"))).toMatch(
      /^<nav class="_tree_\w+" aria-label="Folders"><span class="_group_\w+">Departments<\/span><\/nav>$/,
    );
  });

  it("indents by level, with the icon, name and count", () => {
    const html = render(TreeItem, { href: "/f/2", label: "Invoices", level: 1, icon: "folder", count: "820" });
    expect(html).toMatch(/^<div style="--lvl:1" class="_item_\w+"><span class="_caret_\w+"><\/span><a class="_link_\w+" href="\/f\/2"><svg/);
    expect(html).toMatch(/<span class="_label_\w+">Invoices<\/span><span class="_count_\w+">820<\/span><\/a><\/div>$/);
  });

  it("marks the current folder", () => {
    const html = render(TreeItem, { href: "/f/3", label: "Survey 44, Wakad", level: 2, active: true });
    expect(html).toMatch(/class="_item_\w+ _active_\w+"/);
    expect(html).toContain('aria-current="page"');
  });

  it("draws a caret: open, closed, and a button when the screen can toggle it", () => {
    expect(render(TreeItem, { href: "/f/1", label: "Accounts", expanded: true })).toContain('<path d="M6 9l6 6 6-6">');
    expect(render(TreeItem, { href: "/f/4", label: "HR", expanded: false })).toContain('<path d="M9 6l6 6-6 6">');
    const button = render(TreeItem, { href: "/f/4", label: "HR", expanded: false, onToggle: noop });
    expect(button).toMatch(/<button type="button" class="_caret_\w+" aria-expanded="false" aria-label="Open HR">/);
  });
});
