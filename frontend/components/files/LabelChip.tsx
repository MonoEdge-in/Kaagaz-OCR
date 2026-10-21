// A sensitivity label as a chip (O2): its name, in its colour tokens (globals.css,
// .label-chip), with its description as the title. Read out as "Label: Confidential".

import type { Sensitivity } from "@/lib/types";
import { LABEL_DESCRIPTIONS, labelName } from "./labels";

export function LabelChip({ label }: { label: Sensitivity }) {
  return (
    <span className="label-chip" data-label={label} title={LABEL_DESCRIPTIONS[label]}>
      <span className="visually-hidden">Label: </span>
      {labelName(label)}
    </span>
  );
}
