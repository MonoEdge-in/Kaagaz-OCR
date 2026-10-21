// One icon from the handoff's set (./icons.ts): drawn in the text colour, so it
// follows the theme, and hidden from screen readers, since the button or text
// next to it always carries the words. Sizes are the handoff's .k-ic sizes.

import { K_ICONS } from "./icons";
import type { KIconName, KIconShape } from "./icons";

export type { KIconName } from "./icons";

const SIZES = { sm: 14, md: 18, lg: 22 } as const;

export type IconSize = keyof typeof SIZES;

export function Icon({ name, size = "md", className }: { name: KIconName; size?: IconSize; className?: string }) {
  const icon: KIconShape = K_ICONS[name];
  const px = SIZES[size];
  return (
    <svg
      className={className}
      width={px}
      height={px}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={icon.stroke ?? 1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d={icon.d} />
      {icon.circle ? <circle cx={icon.circle.cx} cy={icon.circle.cy} r={icon.circle.r} /> : null}
    </svg>
  );
}
