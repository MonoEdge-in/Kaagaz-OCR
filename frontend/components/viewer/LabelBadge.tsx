import type { Label } from "@/lib/types";
import { labelBadgeClass, labelText } from "./labels";

/** "Verified" or "Needs review", in words and colour. */
export function LabelBadge({ label }: { label: Label }) {
  return <span className={labelBadgeClass(label)}>{labelText(label)}</span>;
}
