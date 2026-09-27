// A document's status in words and colour. The words are always there, so the
// colour is never the only signal.

import { statusChip } from "@/lib/format";
import type { DocumentStatus } from "@/lib/types";

export function StatusChip({ status }: { status: DocumentStatus }) {
  const chip = statusChip(status);
  return <span className={chip.className}>{chip.text}</span>;
}
