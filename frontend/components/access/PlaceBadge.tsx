// The people icon (PRD 7 PR 11b, #604; plan section 3): it means only Department only and
// Private. PlaceIcon sits after a folder's name in the tree, the tiles and the Move picker, read
// as words (", private"); PlaceBadge is the icon with its words, under the place heading. The icon
// itself is hidden from screen readers (Icon); its words never are.

import { Icon } from "@/components/ui/Icon/Icon";
import type { Folder } from "@/lib/types";
import { placeKind } from "./places";
import type { PlaceKind } from "./places";
import { PLACE_WORDS } from "./words";
import styles from "./Access.module.css";

export function PlaceIcon({ folder }: { folder: Pick<Folder, "kind" | "parent_id" | "department_only" | "private"> }) {
  const kind = placeKind(folder);
  if (kind === null) return null;
  return (
    <span className={styles.placeIcon} title={PLACE_WORDS[kind]}>
      <Icon name="people" size="sm" />
      <span className="visually-hidden">{`, ${PLACE_WORDS[kind].toLowerCase()}`}</span>
    </span>
  );
}

export function PlaceBadge({ kind }: { kind: PlaceKind }) {
  return (
    <span className={styles.placeBadge}>
      <Icon name="people" size="sm" />
      {PLACE_WORDS[kind]}
    </span>
  );
}
