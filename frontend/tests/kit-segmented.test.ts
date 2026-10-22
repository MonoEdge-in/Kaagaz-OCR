// Segmented and RadioPill, against Components.html section 09.

import { describe, expect, it } from "vitest";
import { RadioPill, Segmented } from "@/components/ui/Segmented/Segmented";
import { render } from "./kit-render";

const noop = () => {};

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
