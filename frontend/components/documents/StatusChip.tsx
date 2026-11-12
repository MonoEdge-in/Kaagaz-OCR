// A document's status: a square outlined chip with a dot, in its own style so
// it is never mistaken for a Verified / Needs review label. The word is always
// there, so the colour and the dot are never the only signal. ``text`` gives a
// document being read its step ("Scanning") and a read one "Scan complete".

import { statusChip } from "@/lib/format";
import type { DetectedType, DocumentStatus } from "@/lib/types";

export function StatusChip({ status, text, detectedType }: { status: DocumentStatus; text?: string; detectedType?: DetectedType }) {
  const chip = statusChip(status, detectedType);
  return (
    <span className={chip.className} data-tone={chip.tone}>
      {text ?? chip.text}
    </span>
  );
}
