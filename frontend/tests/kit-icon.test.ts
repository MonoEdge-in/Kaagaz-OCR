// The handoff's icon set (components/ui/Icon): every icon in Main.html, drawn at
// a 1.6 stroke in the three .k-ic sizes.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Icon } from "@/components/ui/Icon/Icon";
import type { KIconShape } from "@/components/ui/Icon/icons";
import { K_ICON_NAMES, K_ICONS } from "@/components/ui/Icon/icons";

const MAIN = readFileSync(
  join(__dirname, "..", "..", "design", "kaagaz-ui-handoff", "screens", "system", "Main.html"),
  "utf8",
);

describe("the icon set", () => {
  it("has every icon in the handoff, path for path, and nothing else", () => {
    const handoff = Array.from(MAIN.matchAll(/ic\('([a-z-]+)', '([^']+)'(?:, \[([\d., ]+)\])?\)/g), (m) => ({
      name: m[1]!,
      d: m[2]!,
      circle: m[3]?.split(",").map((n) => Number(n.trim())),
    }));
    expect(handoff).toHaveLength(52);
    expect(K_ICON_NAMES.slice(0, 52)).toEqual(handoff.map((icon) => icon.name));
    for (const icon of handoff) {
      const ours: KIconShape = K_ICONS[icon.name as keyof typeof K_ICONS];
      expect(ours.d, icon.name).toBe(icon.d);
      const circle = ours.circle ? [ours.circle.cx, ours.circle.cy, ours.circle.r] : undefined;
      expect(circle, icon.name).toEqual(icon.circle);
    }
  });

  it("copies the ten icons the screens draw but the sheet leaves out, from those screens", () => {
    const files = ["system/Components.html", "desktop/DocumentPage.html", "phone/PhoneHome.html", "desktop/Home.html", "desktop/Admin.html"];
    const screens = files
      .map((file) => readFileSync(join(__dirname, "..", "..", "design", "kaagaz-ui-handoff", "screens", file), "utf8"))
      .join("\n")
      // The handoff sometimes splits one icon into two paths; ours is one.
      .replaceAll('"></path><path d="', "");
    const extra = K_ICON_NAMES.slice(52);
    expect(extra).toEqual(["more", "file", "chev-left", "menu", "zoom-out", "external", "calendar", "sun", "moon", "unlock"]);
    for (const name of extra) expect(screens, name).toContain(`d="${K_ICONS[name].d}"`);
  });

  it("draws at a 1.6 stroke, hidden from screen readers, at 14, 18 and 22", () => {
    const sizes = { sm: 14, md: 18, lg: 22 } as const;
    for (const [size, px] of Object.entries(sizes)) {
      const html = renderToStaticMarkup(createElement(Icon, { name: "search", size: size as keyof typeof sizes }));
      expect(html).toContain(`width="${px}" height="${px}"`);
      expect(html).toContain('stroke-width="1.6"');
      expect(html).toContain('aria-hidden="true"');
      expect(html).toContain('<circle cx="11" cy="11" r="6"></circle>');
    }
    expect(renderToStaticMarkup(createElement(Icon, { name: "home" }))).not.toContain("<circle");
    // The handoff draws the three dots at a 3 stroke, or they would be specks.
    expect(renderToStaticMarkup(createElement(Icon, { name: "more" }))).toContain('stroke-width="3"');
  });
});
