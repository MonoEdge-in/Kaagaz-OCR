// One inline SVG icon. It draws in the text colour (currentColor), so it
// follows the theme, and it is hidden from screen readers: the button or text
// next to it always carries the words.

import { ICONS } from "./icons";
import type { IconName } from "./icons";

export type { IconName } from "./icons";

export function Icon({
  name,
  size = 18,
  className,
}: {
  name: IconName;
  size?: number;
  className?: string;
}) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {ICONS[name].map((shape, index) => {
        switch (shape.kind) {
          case "path":
            return <path key={index} d={shape.d} />;
          case "circle":
            return <circle key={index} cx={shape.cx} cy={shape.cy} r={shape.r} />;
          case "rect":
            return (
              <rect
                key={index}
                x={shape.x}
                y={shape.y}
                width={shape.width}
                height={shape.height}
                rx={shape.rx}
              />
            );
        }
      })}
    </svg>
  );
}
