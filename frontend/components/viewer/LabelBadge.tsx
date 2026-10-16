import { Icon } from "@/components/ui/Icon";
import type { Label } from "@/lib/types";
import { labelBadgeClass, labelIcon, labelText } from "./labels";

/**
 * "Verified" or "Needs review": for values and answers only, never document
 * status. They differ by icon, word and edge (solid or dashed, globals.css), never by
 * colour or shape alone.
 */
export function LabelBadge({ label }: { label: Label }) {
  return (
    <span className={labelBadgeClass(label)}>
      <Icon name={labelIcon(label)} size={16} />
      {labelText(label)}
    </span>
  );
}
