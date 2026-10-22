// EmptyState, against Components.html section 16.

import { createElement as h } from "react";
import { describe, expect, it } from "vitest";
import { Button } from "@/components/ui/Button/Button";
import { EmptyState, Ledger } from "@/components/ui/EmptyState/EmptyState";
import { render } from "./kit-render";

describe("EmptyState", () => {
  it("draws two ledger sheets, what is missing, what to do and the action", () => {
    const html = render(
      EmptyState,
      { title: "No documents in this folder yet", action: h(Button, { variant: "primary", icon: "upload" }, "Upload to Survey 44, Wakad") },
      "Drop bills, agreements or 7/12 extracts here.",
    );
    expect(html).toMatch(/^<div class="_empty_\w+"><div class="_stack_\w+"><span class="_ledger_\w+ _back_\w+" aria-hidden="true"><\/span><span class="_ledger_\w+ _front_\w+" aria-hidden="true"><\/span><\/div>/);
    expect(html).toContain('<h3 class="k-h3">No documents in this folder yet</h3><p>Drop bills, agreements or 7/12 extracts here.</p><button');
  });

  it("draws the ledger on its own", () => {
    expect(render(Ledger)).toMatch(/^<span class="_ledger_\w+" aria-hidden="true"><\/span>$/);
  });
});
