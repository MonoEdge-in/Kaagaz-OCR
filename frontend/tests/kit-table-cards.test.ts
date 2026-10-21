// Table, BulkBar, Card, StatCard, LinkCard and BarChart, against Components.html
// sections 10 and 11.

import { createElement as h } from "react";
import { describe, expect, it } from "vitest";
import { BarChart, barWidth } from "@/components/ui/BarChart/BarChart";
import { Button } from "@/components/ui/Button/Button";
import { Card, CardHead, Divider, LinkCard, StatCard } from "@/components/ui/Card/Card";
import { BulkBar, Cell, NameCell, Row, SortHeader, SubLine, Table } from "@/components/ui/Table/Table";
import { ProblemFlag } from "@/components/ui/VersionChip/VersionChip";
import { render } from "./kit-render";

const noop = () => {};

describe("Table", () => {
  it("scrolls inside its own box", () => {
    expect(render(Table, null, h("tbody"))).toMatch(/^<div class="_wrap_\w+"><table class="_table_\w+"><tbody><\/tbody><\/table><\/div>$/);
  });

  it("sorts by a column header button; the sorted one has aria-sort and an arrow", () => {
    const off = render(SortHeader, { label: "Name", onSort: noop });
    expect(off).toMatch(/^<th><button type="button" class="_sort_\w+">Name<\/button><\/th>$/);
    const down = render(SortHeader, { label: "Document date", sort: "descending", onSort: noop });
    expect(down).toMatch(/^<th aria-sort="descending"><button type="button" class="_sort_\w+ _sorted_\w+">Document date<svg/);
    expect(down).not.toContain("_up_");
    expect(render(SortHeader, { label: "Name", sort: "ascending", onSort: noop })).toMatch(/<svg class="_up_\w+"/);
  });

  it("marks a selected row and right-aligns figures", () => {
    expect(render(Row, { selected: true })).toMatch(/^<tr class="_selected_\w+"><\/tr>$/);
    expect(render(Row, null)).toBe("<tr></tr>");
    expect(render(Cell, { num: true }, "1,310")).toMatch(/^<td class="_num_\w+">1,310<\/td>$/);
    expect(render(Cell, null, "Anita")).toBe("<td>Anita</td>");
  });

  it("draws a name with its file icon, and a line under it", () => {
    expect(render(NameCell, { ext: "PDF", name: "Rent Agreement Shinde.pdf" })).toMatch(
      /^<span class="_name_\w+"><span class="_file_\w+" data-ext="PDF" aria-hidden="true"><\/span><span>Rent Agreement Shinde.pdf<\/span><\/span>$/,
    );
    const deva = render(NameCell, { ext: "PDF", name: "7/12 Survey 44 Wakad.pdf", sub: h(SubLine, { deva: true, children: "सातबारा उतारा" }) });
    expect(deva).toMatch(/<span class="_nameText_\w+"><span>7\/12 Survey 44 Wakad.pdf<\/span><span class="_sub_\w+ k-deva">सातबारा उतारा<\/span>/);
    const problem = render(NameCell, { ext: "PDF", name: "Sale Deed.pdf", sub: h(ProblemFlag, null, "2 pages unreadable") });
    expect(problem).toContain("2 pages unreadable");
  });
});

describe("BulkBar", () => {
  it("is a named region with the count, the actions and Clear selection last", () => {
    const html = render(BulkBar, { count: "1 selected", onClear: noop }, h(Button, { size: "sm", icon: "move" }, "Move"));
    expect(html).toMatch(/^<div class="_bulk_\w+" role="region" aria-label="Selection actions"><strong>1 selected<\/strong><button/);
    expect(html).toMatch(/Move<\/button><button type="button" class="_btn_\w+ _sm_\w+ _clear_\w+">Clear selection<\/button><\/div>$/);
  });
});

describe("Card", () => {
  it("draws the default, flush and dashed cards", () => {
    expect(render(Card, null, "x")).toMatch(/^<div class="_card_\w+">x<\/div>$/);
    expect(render(Card, { variant: "flush" }, "x")).toMatch(/class="_card_\w+ _flush_\w+"/);
    expect(render(Card, { variant: "dashed" }, "x")).toMatch(/class="_card_\w+ _dashed_\w+"/);
  });

  it("draws a heading row, with its own band in a flush card", () => {
    expect(render(CardHead, null, "Recent")).toMatch(/^<div class="_head_\w+">Recent<\/div>$/);
    expect(render(CardHead, { bar: true }, "Move 3 files")).toMatch(/class="_head_\w+ _bar_\w+"/);
  });

  it("draws a number with its name and its change", () => {
    const html = render(StatCard, { label: "Documents", value: "2,560", delta: "+148 this week" });
    expect(html).toMatch(/<span class="k-eyebrow">Documents<\/span><span class="k-num">2,560<\/span><span class="_delta_\w+"><svg/);
    expect(html).toContain("+148 this week</span></div>");
    expect(render(StatCard, { label: "Departments", value: "5" })).not.toContain("_delta_");
  });

  it("draws a card that links somewhere, dashed when it is a place to sort", () => {
    const html = render(LinkCard, { href: "/documents?folder=2", icon: "department", title: "Land & Legal", meta: "420 files · 3 folders" });
    expect(html).toMatch(/^<a class="_card_\w+" href="\/documents\?folder=2"><span class="_linkTitle_\w+"><svg/);
    expect(html).toContain('<strong>Land &amp; Legal</strong></span><span class="k-small k-muted">420 files · 3 folders</span></a>');
    expect(render(LinkCard, { href: "/x", icon: "file", title: "Not in a department", meta: "312 files", dashed: true })).toMatch(
      /class="_card_\w+ _dashed_\w+"/,
    );
  });

  it("draws a divider", () => {
    expect(render(Divider)).toMatch(/^<hr class="_divider_\w+"\/>$/);
  });
});

describe("BarChart", () => {
  it("is a list of names and numbers, the bars only drawn", () => {
    const html = render(BarChart, {
      label: "Documents by type",
      bars: [
        { label: "GST invoice", value: 820 },
        { label: "7/12 extract", value: 410 },
      ],
    });
    expect(html).toMatch(/^<dl class="_bars_\w+" aria-label="Documents by type">/);
    expect(html).toMatch(/<dt>GST invoice<\/dt><dd><span class="_track_\w+" aria-hidden="true"><span class="_fill_\w+" style="width:100%"><\/span><\/span><span class="_value_\w+">820<\/span><\/dd>/);
    expect(html).toContain('style="width:50%"');
  });

  it("sizes each bar against the biggest, never past the track", () => {
    expect(barWidth(410, 820)).toBe(50);
    expect(barWidth(1, 3)).toBe(33.3);
    expect(barWidth(0, 820)).toBe(0);
    expect(barWidth(5, 0)).toBe(0);
    expect(barWidth(900, 820)).toBe(100);
  });
});
