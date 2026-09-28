import { Icon } from "@/components/ui/Icon";
import type { Label } from "@/lib/types";
import { labelBadgeClass, labelIcon, labelText } from "./labels";

/**
 * "Verified" or "Needs review": for values and answers only, never document
 * status. They differ by shape, edge, icon and word, not colour alone.
 */
export function LabelBadge({ label }: { label: Label }) {
  return (
    <span className={labelBadgeClass(label)}>
      <Icon name={labelIcon(label)} size={16} />
      {labelText(label)}
    </span>
  );
}
