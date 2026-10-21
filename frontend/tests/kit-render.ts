// Renders one UI kit component to HTML for the kit-*.test.ts render tests, with
// its props checked: render(Button, { variant: "primary" }, "Upload").

import { createElement } from "react";
import type { FunctionComponent, ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";

export function render<P extends object>(
  type: FunctionComponent<P>,
  props?: Omit<P, "children"> | null,
  ...children: ReactNode[]
): string {
  return renderToStaticMarkup(createElement(type, props as P, ...children));
}
