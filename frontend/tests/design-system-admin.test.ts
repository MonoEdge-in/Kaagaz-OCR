// The MonoEdge design system on the admin, operator, review and My words screens (Tushar,
// 16 Oct 2026): the tabs spec, flat square styles, tables in their own sideways scroller,
// buttons in the order [Destructive] ... [Cancel] [Primary], and figures that say how fresh
// they are. Each guard reads the source or renders in node, so an old habit coming back fails.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MAX_PHONE_TABS, Tabs } from "@/components/admin/Tabs";
import { MoreRows, ROW_CAP } from "@/components/admin/RowCap";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const noComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "");

const CSS_MODULES = [
  "components/admin/Admin.module.css",
  "components/operator/Operator.module.css",
  "components/operator/Insights.module.css",
  "components/operator/Platform.module.css",
  "components/review/Review.module.css",
  "components/words/Words.module.css",
];

const SEVEN = [
  { id: "a", label: "Overview" },
  { id: "b", label: "Quality" },
  { id: "c", label: "Organisations", count: 1234 },
  { id: "d", label: "Models" },
  { id: "e", label: "AI spend" },
  { id: "f", label: "Activity" },
  { id: "g", label: "Health" },
] as const;

const tabs = (items: readonly { id: string; label: string; count?: number }[], active: string) =>
  renderToStaticMarkup(
    // Changed on purpose (#468 review, UX1): onChange says whether the tab opened.
    // eslint-disable-next-line react/no-children-prop -- Tabs types children as a required prop
    createElement(Tabs<string>, { prefix: "t", label: "Sections", tabs: items, active, onChange: () => true, children: "Body" }),
  );

describe("the tabs spec", () => {
  const html = tabs(SEVEN, "b");

  it("wires tablist, tab and tabpanel to each other", () => {
    expect(html).toMatch(/<div role="tablist" aria-label="Sections"/);
    expect(html.match(/role="tab"/g)).toHaveLength(7);
    expect(html).toMatch(/<button[^>]*role="tab" id="t-tab-b" aria-selected="true" aria-controls="t-panel-b" tabindex="0"/);
    expect(html).toMatch(/<div role="tabpanel" id="t-panel-b" aria-labelledby="t-tab-b" tabindex="0"/);
  });

  it("keeps only the selected tab in the Tab order (roving tabindex)", () => {
    expect(html.match(/role="tab"[^>]*tabindex="0"/g)).toHaveLength(1);
    expect(html.match(/role="tab"[^>]*tabindex="-1"/g)).toHaveLength(6);
  });

  it("mounts only the open panel's content, every panel present", () => {
    expect(html.match(/role="tabpanel"/g)).toHaveLength(7);
    expect(html.match(/hidden=""/g)?.length).toBeGreaterThanOrEqual(6);
    expect(html.match(/>Body</g)).toHaveLength(1);
  });

  it("reserves each tab's bold width with its label, and counts in figures", () => {
    expect(html).toContain('data-label="Organisations">Organisations</span>');
    expect(html).toContain(">1,234</span>");
  });

  it("turns more than six tabs into a labelled select for phones; six stay tabs", () => {
    expect(MAX_PHONE_TABS).toBe(6);
    expect(html).toMatch(/<label for="t-tab-select">Sections<\/label><select id="t-tab-select"/);
    expect(html).toContain('<option value="c">Organisations (1,234)</option>');
    expect(tabs(SEVEN.slice(0, 6), "a")).not.toContain("<select");
  });

  it("hides the mouse-only edge arrows from screen readers and the Tab order", () => {
    expect(html.match(/<button[^>]*data-side="(start|end)"[^>]*tabindex="-1"[^>]*aria-hidden="true"/g)).toHaveLength(2);
  });

  it("is used by the admin screen and the operator console", () => {
    expect(read("components/admin/AdminView.tsx")).toContain('<Tabs<TabId> prefix="admin"');
    expect(read("components/operator/OperatorConsole.tsx")).toContain('<Tabs<TabId> prefix="operator"');
    const css = noComments(read("components/admin/Admin.module.css"));
    expect(css).toMatch(/\.tab\[aria-selected="true"\]::after \{[^}]*position: absolute;[^}]*height: 2px;[^}]*background: var\(--accent\);/);
    expect(css).toMatch(/\.tabLabel::before \{[^}]*content: attr\(data-label\)[^}]*font-weight: 700;/);
    expect(css).toMatch(/\.tabCount \{[^}]*min-width:[^}]*font-variant-numeric: tabular-nums;/);
    expect(css).toMatch(/\.panel \{[^}]*min-height: 70svh;/);
  });
});

describe("flat, square, hairline", () => {
  // Changed on purpose (UI rebuild, 21 Oct 2026): the handoff's soft corners replace the 2px square ones.
  it("has no gradient, shadow or glow, and every corner is a handoff radius token", () => {
    const found = CSS_MODULES.flatMap((file) => {
      // Changed on purpose (UI rebuild, 21 Oct 2026): the handoff's raised cards and pressed
      // pills use its own shadow tokens (kaagaz.css --k-shadow-1, -2, -page); nothing else.
      const css = noComments(read(file)).replace(/box-shadow:\s*var\(--k-shadow-(1|2|page)\);/g, "");
      const bad = [
        ...css.matchAll(/gradient\(|box-shadow|text-shadow|filter:\s*drop-shadow|backdrop-filter/g),
        ...[...css.matchAll(/border-radius:\s*([^;]+);/g)].filter(
          (m) => !/^(0|var\(--k-r-(\d|pill)\)|2px 2px 0 0)$/.test(m[1] ?? ""),
        ),
      ];
      return bad.map((m) => `${file}: ${m[0]}`);
    });
    expect(found).toEqual([]);
  });

  it("uses MonoEdge tokens only, never the old names", () => {
    // Changed on purpose (Tushar, 16 Oct 2026, one control height / centred icons): --control-h is
    // now THE control height (36px, 44px on touch), not an old alias, so screens use it.
    const old = /var\(--(fg|fg-muted|surface|surface-2|accent-soft|accent-fg|brand-lal|danger-bg|danger-fg|review-bg|review-fg|review-solid|verified-[a-z]+|r-sm|r-md|r-full|e-1|e-2|space-5|space-7)\)/g;
    const found = CSS_MODULES.flatMap((file) => [...noComments(read(file)).matchAll(old)].map((m) => `${file}: ${m[0]}`));
    expect(found).toEqual([]);
  });

  // Changed on purpose (readable type, 25 Oct 2026): labels were 11px bold capitals, 0.08em apart.
  it("writes labels in sentence case, 14px, medium, in the secondary text colour", () => {
    const css = noComments(read("components/admin/Admin.module.css"));
    expect(css).toMatch(/\.tileLabel,\s*\.usageTileHead \{\s*font-size: var\(--fs-base\);\s*font-weight: 500;\s*color: var\(--text-secondary\);\s*\}/);
  });
});

describe("no bleeding", () => {
  const TABLE_VIEWS = [
    "components/admin/ActivityPanel.tsx",
    "components/admin/FailedPanel.tsx",
    "components/admin/UsagePanel.tsx",
    "components/admin/UsersPanel.tsx",
    "components/operator/ActivityView.tsx",
    "components/operator/HealthView.tsx",
    "components/operator/ModelBoard.tsx",
    "components/operator/ModelsView.tsx",
    "components/operator/OrganisationsView.tsx",
    "components/operator/OverviewView.tsx",
    "components/operator/QualityView.tsx",
    "components/operator/SpendView.tsx",
  ];

  it("puts every table in its own sideways scroller", () => {
    for (const file of TABLE_VIEWS) {
      const source = read(file);
      const wraps = source.match(/table\.reflowWrap/g)?.length ?? 0;
      expect(wraps, file).toBeGreaterThan(0);
      expect(source.match(/table\.reflowWrap\} \$\{(styles|admin)\.tableScroll\}/g)?.length, file).toBe(wraps);
    }
    const css = noComments(read("components/admin/Admin.module.css"));
    expect(css).toMatch(/\.tableScroll \{[^}]*position: relative;[^}]*overflow-x: auto;[^}]*overscroll-behavior-x: contain;/);
    expect(css).toMatch(/\.chart \{[^}]*overflow-x: auto;[^}]*overscroll-behavior-x: contain;/);
  });

  it("keeps Compare models' and Quality's name column in view while their columns scroll", () => {
    for (const view of ["ModelBoard", "QualityView"]) {
      expect(read(`components/operator/${view}.tsx`)).toContain("${styles.nameWide} ${styles.compare}");
    }
    expect(noComments(read("components/operator/Operator.module.css"))).toMatch(
      /\.compare th:first-child,\s*\.compare td:first-child \{\s*position: sticky;\s*inset-inline-start: 0;/,
    );
  });

  it("cuts a long operator e-mail with an ellipsis, its title holding all of it", () => {
    expect(read("components/operator/OperatorShell.tsx")).toContain("className={styles.who} title={operator.email}");
    expect(noComments(read("components/operator/Operator.module.css"))).toMatch(/\.who \{[^}]*min-width: 0;[^}]*text-overflow: ellipsis;/);
  });

  it("stacks the two readings below 720px", () => {
    expect(read("components/review/Review.module.css")).toContain("@media (min-width: 720px)");
  });

  it("draws at most 100 rows of a long table, then offers the next ones", () => {
    expect(ROW_CAP).toBe(100);
    const html = renderToStaticMarkup(createElement(MoreRows, { shown: 100, total: 240, what: "users", onMore: () => undefined }));
    expect(html).toContain("Showing 100 of 240 users.");
    expect(html).toMatch(/<button type="button" class="btn">Show 100 more<\/button>/);
    expect(renderToStaticMarkup(createElement(MoreRows, { shown: 5, total: 5, what: "users", onMore: () => undefined }))).toBe("");
    for (const file of ["components/admin/UsersPanel.tsx", "components/operator/OrganisationsView.tsx", "components/operator/QualityView.tsx"]) {
      expect(read(file), file).toMatch(/\.slice\(0, cap\.limit\)/);
    }
  });
});

describe("misclicks", () => {
  /** The words in a source, in order: true when each comes after the one before. */
  const inOrder = (source: string, ...parts: string[]) => {
    let at = -1;
    return parts.every((part) => {
      const next = source.indexOf(part, at + 1);
      at = next;
      return next >= 0;
    });
  };

  it("orders buttons [Cancel] [Primary], the primary last", () => {
    expect(inOrder(read("components/operator/ConfirmDialog.tsx"), "Cancel", 'type="submit"')).toBe(true);
    expect(inOrder(read("components/operator/CreateDialog.tsx"), ">\n              Cancel", 'type="submit"')).toBe(true);
    expect(inOrder(read("components/operator/PlatformDefaults.tsx"), "Cancel\n", 'type="submit"')).toBe(true);
    // Changed on purpose (MFA required, 16 Oct 2026): the Two-step panel has no buttons now.
    // Changed on purpose (UI audit, 16 Oct 2026): AD2, the row's confirms in a dialog; AD3, the Add user panel.
    expect(inOrder(read("components/admin/UsersPanel.tsx"), "Cancel\n", "{CONFIRM_BUTTON[confirming]}")).toBe(true);
    expect(inOrder(read("components/admin/UsersPanel.tsx"), "onClick={askClose}>\n              Cancel", 'type="submit"')).toBe(true);
    expect(inOrder(read("components/chat/ConversationList.tsx"), "Cancel\n", ">\n              Delete\n")).toBe(true);
    expect(inOrder(read("components/words/MyWordsScreen.tsx"), ">Cancel</button>", 'type="submit" className={`btn ${row.mode')).toBe(true);
  });

  it("puts Replace (it takes a meaning away) first, apart, then Cancel, then Add too", () => {
    const source = read("components/words/WordParts.tsx");
    expect(inOrder(source, "styles.destructive", "Replace", ">Cancel<", "Add too")).toBe(true);
  });

  it("gives the actions that take something away their own row, 16px from the rest", () => {
    // Changed on purpose (owner, 16 Oct 2026): the organisations' actions are one Manage menu,
    // the status changes after a separator 16px from the rest (tests/operator-manage-menu.test.ts).
    expect(read("components/operator/OrganisationsView.tsx")).toContain("<ManageMenu");
    expect(noComments(read("components/operator/Operator.module.css"))).toMatch(
      /\.menuSeparator \{[^}]*margin: var\(--space-2\) 0;/,
    );
    // Changed on purpose (UI audit, 16 Oct 2026): AD2, a row's menu lists the destructive action last, under a hairline, 16px down.
    // Changed on purpose (plain words, 25 Oct 2026): each item has a line saying what it does.
    // Changed on purpose (#711): the menu is smaller, so the destructive action sits 8px from the rest.
    expect(read("components/admin/UsersPanel.tsx")).toContain(
      '{ label: "Disable", description: USER_MENU_HELP.disable, onSelect: () => setConfirming("disable"), destructive: true }',
    );
    expect(noComments(read("components/admin/Admin.module.css"))).toMatch(
      /\.rowMenuDanger \{\s*margin-top: var\(--space-2\);\s*border-top: 1px solid var\(--border\);/,
    );
  });

  it("keeps a busy button's width: the same words, a spinner over them", () => {
    expect(noComments(read("components/admin/Admin.module.css"))).toMatch(
      /\.busy \{\s*position: relative;\s*-webkit-text-fill-color: transparent;\s*\}\s*\.busy > :global\(\.spinner\) \{\s*position: absolute;/,
    );
    for (const file of ["components/admin/UsersPanel.tsx", "components/admin/SettingsPanel.tsx", "components/operator/CreateDialog.tsx"]) {
      expect(read(file), file).not.toMatch(/Adding…|Saving…|Resetting…|Creating…/);
    }
  });

  it("never sends twice from Enter while a save runs", () => {
    expect(read("components/admin/UsersPanel.tsx")).toContain("if (busy) return; // Enter in a field while adding");
    expect(read("components/admin/SettingsPanel.tsx")).toContain("if (!settings || busy) return;");
    expect(read("components/review/ReviewCard.tsx")).toContain("if (busy) return;");
  });

  it("asks before the org AI settings close with unsaved changes, Keep editing focused", () => {
    const dialog = read("components/operator/OrgAiSettings.tsx");
    expect(dialog).toContain("onClose={() => (unchanged ? onClose() : setLeaving(true))}");
    expect(inOrder(dialog, "Close without saving\n", "ref={focusKeep}", "Keep editing")).toBe(true);
  });

  it("never changes the budget on the mouse wheel", () => {
    expect(read("components/operator/OrgAiSettings.tsx")).toMatch(/type="number"[\s\S]{0,400}onWheel=\{\(event\) => event\.currentTarget\.blur\(\)\}/);
  });
});

describe("figures that can be old say when they are from", () => {
  it("shows the time the figures arrived and a Refresh on Usage, AI spend, Quality and Compare models", () => {
    expect(read("components/admin/UsagePanel.tsx")).toMatch(/Figures as of \{formatDateTime\(new Date\(current\.at\)/);
    for (const view of ["SpendView", "QualityView", "ModelBoard", "OverviewView"]) {
      expect(read(`components/operator/${view}.tsx`), view).toContain("<Freshness loadedAt=");
    }
    expect(read("components/operator/OverviewView.tsx")).toMatch(/Figures as of \{formatDateTime\(new Date\(loadedAt\)/);
  });

  it("right-aligns the spend and quality figures", () => {
    expect(read("components/operator/SpendView.tsx")).toContain('data-label="Tokens out" className="num"');
    expect(read("components/operator/QualityView.tsx")).toContain('data-label={ORG_COLUMNS[index + 1]} className="num"');
  });
});
