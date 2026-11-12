// The handoff's 5 Nov 2026 changes, measured in the browser: the documents list fits its width
// with no column cut, every select's chevron stays inside its box, and Home's greeting wraps
// into even lines; at 1440 and 1280 wide, light and dark, in WebKit and Chromium. Each case
// also saves a screenshot to e2e-results/ to look at.

import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import { fakeApi } from "./fake-api";

const WIDTHS = [1440, 1280] as const;
const THEMES = ["light", "dark"] as const;

/** Nothing on the page scrolls sideways. */
async function expectNoSidewaysScroll(page: Page) {
  const { scroll, view } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    view: document.documentElement.clientWidth,
  }));
  expect(scroll, "the page's width").toBeLessThanOrEqual(view);
}

/** Every select's chevron sits inside its wrapper and its select, and the wrapper is at least 220px. */
async function expectChevronsInside(page: Page) {
  const boxes = await page.locator(".k-select-wrap").evaluateAll((wraps) =>
    wraps
      .filter((wrap) => (wrap as HTMLElement).offsetParent !== null)
      .map((wrap) => {
        const select = wrap.querySelector("select")!.getBoundingClientRect();
        const icon = wrap.querySelector("svg")!.getBoundingClientRect();
        const box = wrap.getBoundingClientRect();
        return { select, icon, box };
      }),
  );
  for (const { select, icon, box } of boxes) {
    expect(icon.left).toBeGreaterThanOrEqual(select.left);
    expect(icon.right).toBeLessThanOrEqual(select.right);
    expect(icon.top).toBeGreaterThanOrEqual(select.top);
    expect(icon.bottom).toBeLessThanOrEqual(select.bottom);
    expect(box.width).toBeGreaterThanOrEqual(220);
  }
  return boxes.length;
}

for (const width of WIDTHS) {
  for (const theme of THEMES) {
    test.describe(`${width}px, ${theme}`, () => {
      test.use({ viewport: { width, height: 900 }, colorScheme: theme });

      test("Documents: the list fits, no header cut, one 44px line per row", async ({ page }, info) => {
        await fakeApi(page);
        await page.goto("/documents");
        const table = page.locator("table[role='table']").first();
        await expect(table).toBeVisible();
        await expect(page.locator("tbody tr")).toHaveCount(6);
        await expectNoSidewaysScroll(page);
        const fit = await table.evaluate((t) => {
          const box = t.parentElement!;
          return { table: t.getBoundingClientRect().width, box: box.clientWidth, scroll: box.scrollWidth };
        });
        expect(fit.scroll, "the table's box scrolls sideways").toBeLessThanOrEqual(fit.box);
        expect(fit.table).toBeLessThanOrEqual(fit.box + 1);
        // No column is cut: every header's words fit, and every cell stays inside the table.
        const heads = await page.locator("thead th").evaluateAll((ths) =>
          ths
            .filter((th) => !th.querySelector(":scope > .visually-hidden")) // words for screen readers only
            .map((th) => {
              const words = th.querySelector("button") ?? th;
              return { text: th.textContent?.trim() ?? "", fits: words.scrollWidth <= th.clientWidth + 1 };
            }),
        );
        for (const head of heads) expect(head.fits, `header "${head.text}"`).toBe(true);
        const right = await table.evaluate((t) => t.getBoundingClientRect().right);
        const cells = await page.locator("tbody td").evaluateAll((tds) => tds.map((td) => td.getBoundingClientRect().right));
        for (const edge of cells) expect(edge).toBeLessThanOrEqual(right + 1);
        const heights = await page.locator("tbody tr").evaluateAll((rows) => rows.map((row) => Math.round(row.getBoundingClientRect().height)));
        for (const height of heights) expect(height).toBe(44);
        // Every name is one line (Chromium breaks at <wbr> even under nowrap unless it is hidden).
        const names = await page.locator("tbody td:nth-child(2) a").evaluateAll((links) =>
          links.map((link) => {
            const range = document.createRange();
            range.selectNodeContents(link);
            return new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size;
          }),
        );
        for (const lines of names) expect(lines, "lines in a name").toBe(1);
        expect(await page.locator(".k-file-ic").count()).toBe(0);
        await expect(page.locator(".k-check-link").first()).toHaveText(/19 to check/);
        expect(await expectChevronsInside(page)).toBeGreaterThanOrEqual(2); // Upload to and Read with
        await page.screenshot({ path: info.outputPath(`documents-${width}-${theme}.png`), fullPage: true });
      });

      test("Upload: one 56px circle in the drop zone, the formats with no HEIC", async ({ page }, info) => {
        await fakeApi(page, []);
        await page.goto("/documents");
        const circle = page.locator("text=No documents yet").locator("xpath=..").locator("span[aria-hidden='true']").first();
        await expect(circle).toBeVisible();
        const box = await circle.boundingBox();
        expect(Math.round(box!.width)).toBe(56);
        expect(Math.round(box!.height)).toBe(56);
        await expect(page.getByText(/PDF, JPG, PNG, TIFF, Excel, CSV/)).toBeVisible();
        expect(await page.getByText("HEIC").count()).toBe(0);
        await expectNoSidewaysScroll(page);
        expect(await expectChevronsInside(page)).toBeGreaterThanOrEqual(2);
        await page.screenshot({ path: info.outputPath(`upload-${width}-${theme}.png`), fullPage: true });
      });

      test("Home: the greeting wraps cleanly, the versions tile stacks its label", async ({ page }, info) => {
        await fakeApi(page);
        await page.goto("/dashboard");
        const heading = page.locator("#home-greeting");
        await expect(heading).toBeVisible();
        const shape = await heading.evaluate((h) => {
          const style = getComputedStyle(h);
          const range = document.createRange();
          range.selectNodeContents(h);
          const lines = new Set([...range.getClientRects()].map((r) => Math.round(r.top))).size;
          return { size: parseFloat(style.fontSize), wrap: style.textWrap || style.getPropertyValue("text-wrap-style"), lines };
        });
        expect(shape.size).toBeGreaterThanOrEqual(34);
        expect(shape.size).toBeLessThanOrEqual(46);
        expect(shape.lines).toBeLessThanOrEqual(2);
        const tile = page.locator("a[href='/documents?newer=1']");
        const label = await tile.locator(".k-eyebrow").boundingBox();
        const number = await tile.locator(".k-num").boundingBox();
        const chip = await tile.locator(".k-ver--newer").boundingBox();
        expect(number!.y).toBeGreaterThan(label!.y + label!.height - 1); // the number under the label
        expect(chip!.x).toBeGreaterThan(number!.x + number!.width); // the chip beside the number
        await expectNoSidewaysScroll(page);
        await page.screenshot({ path: info.outputPath(`home-${width}-${theme}.png`), fullPage: true });
      });
    });
  }
}
