"use client";

// The 2FA set-up link drawn as a QR code, for Google or Microsoft
// Authenticator to scan. It is one inline SVG with one path, from our own
// encoder (lib/qr.ts). The link holds the secret key, so it goes only to the
// encoder: never into a URL, an attribute, a title or a log. Only the drawn
// squares reach the page.

import { useMemo } from "react";
import { encodeQr } from "@/lib/qr";
import type { QrMatrix } from "@/lib/qr";

const QUIET_ZONE = 4; // light modules round the code, as the standard asks
const MODULE_PX = 4; // whole pixels per module, so the edges stay sharp

/**
 * The dark modules as one SVG path, in module units, quiet zone included. Each
 * run of dark modules along a row is one 1-module-thick stroke: "M4 4.5h7" is
 * the 7 dark modules at the start of the top row.
 */
export function qrPath(matrix: QrMatrix): string {
  const { size, modules } = matrix;
  let path = "";
  for (let row = 0; row < size; row++) {
    let col = 0;
    while (col < size) {
      if (modules[row * size + col] !== 1) {
        col++;
        continue;
      }
      const start = col;
      while (col < size && modules[row * size + col] === 1) col++;
      path += `M${start + QUIET_ZONE} ${row + QUIET_ZONE + 0.5}h${col - start}`;
    }
  }
  return path;
}

export function QrCode({ value, label }: { value: string; label: string }) {
  const { path, side } = useMemo(() => {
    const matrix = encodeQr(value);
    return { path: qrPath(matrix), side: matrix.size + QUIET_ZONE * 2 };
  }, [value]);

  // Fixed black on white, not theme colours, even in dark mode and high
  // contrast: authenticator cameras read dark squares on a light background.
  return (
    <svg
      width={side * MODULE_PX}
      height={side * MODULE_PX}
      viewBox={`0 0 ${side} ${side}`}
      shapeRendering="crispEdges"
      role="img"
      aria-label={label}
      style={{ background: "#fff", forcedColorAdjust: "none", maxWidth: "100%", height: "auto" }}
    >
      <path d={path} fill="none" stroke="#000" />
    </svg>
  );
}
