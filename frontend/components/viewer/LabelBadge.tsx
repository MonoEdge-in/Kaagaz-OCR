import { Icon } from "@/components/ui/Icon";
import { Tip } from "@/components/ui/Tip";
import type { Label, LabelReason } from "@/lib/types";
import { labelMarkIcon, labelText, labelTip } from "./labels";

/**
 * "Verified" or "Ready to verify" beside a value or an answer, never document status. A quiet
 * mark, not a pill (owner, 29 Oct 2026): a check seal or a small dot (#453), its word read out by
 * screen readers, and a tooltip on hover and focus with the word and, when known, the value's
 * reason. They differ by shape and word, never by colour alone. The Review queue keeps its pill.
 */
export function LabelBadge({ label, reason }: { label: Label; reason?: LabelReason }) {
  return (
    <Tip className={label === "verified" ? "label-mark label-mark-verified" : "label-mark"} text={labelTip(label, reason)}>
      <Icon name={labelMarkIcon(label)} size={16} />
      <span className="visually-hidden">{labelText(label)}</span>
    </Tip>
  );
}
