import { Icon } from "@/components/ui/Icon";
import { Tip } from "@/components/ui/Tip";
import type { Label } from "@/lib/types";
import { confidenceTip, labelBadgeClass, labelIcon, labelText } from "./labels";

/**
 * "Verified" or "Ready to verify": for values and answers only, never document
 * status. They differ by icon, word and edge (solid or dashed, globals.css), never by
 * colour or shape alone. A value's badge passes its `confidence` (null: not measured)
 * and gets a tooltip, "Confidence 72%" (Tushar, 16 Oct 2026); without it, no tooltip.
 */
export function LabelBadge({ label, confidence }: { label: Label; confidence?: number | null }) {
  const body = (
    <>
      <Icon name={labelIcon(label)} size={16} />
      {labelText(label)}
    </>
  );
  if (confidence === undefined) return <span className={labelBadgeClass(label)}>{body}</span>;
  return (
    <Tip className={labelBadgeClass(label)} text={confidenceTip(confidence)}>
      {body}
    </Tip>
  );
}
