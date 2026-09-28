// A document's status: a square outlined chip with a dot, in its own style so
// it is never mistaken for a Verified / Needs review label. The word is always
// there, so the colour and the dot are never the only signal.

import { statusChip } from "@/lib/format";
import type { DocumentStatus } from "@/lib/types";

export function StatusChip({ status }: { status: DocumentStatus }) {
  const chip = statusChip(status);
  return (
    <span className={chip.className} data-tone={chip.tone}>
      {chip.text}
    </span>
  );
}
