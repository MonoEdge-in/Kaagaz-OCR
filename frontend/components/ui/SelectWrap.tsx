// A native select in the handoff's wrapper (styles/kaagaz.css section 21): the chevron is an
// icon inside the wrapper, never a background image, so Safari cannot draw its own chrome
// over it and it always stays inside the box. The wrapper is at least 220px and never
// shrinks under the words beside it.

import type { ReactNode } from "react";
import { cx } from "./cx";
import { Icon } from "./Icon/Icon";

export function SelectWrap({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span className={cx("k-select-wrap", className)}>
      {children}
      <Icon name="chev-down" size="sm" className="k-ic" />
    </span>
  );
}
