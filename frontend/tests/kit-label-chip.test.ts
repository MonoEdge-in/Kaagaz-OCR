// LabelChip, against Components.html section 08.

import { describe, expect, it } from "vitest";
import { LabelChip } from "@/components/ui/LabelChip/LabelChip";
import type { Sensitivity } from "@/components/ui/LabelChip/LabelChip";
import { render } from "./kit-render";

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
