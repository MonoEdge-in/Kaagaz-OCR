// Banner, Toast, InlineConfirm, Dialog, Drawer and Menu, against Components.html
// sections 11 and 12.

import { createElement as h } from "react";
import { describe, expect, it } from "vitest";
import { Banner } from "@/components/ui/Banner/Banner";
import type { BannerTone } from "@/components/ui/Banner/Banner";
import { Button } from "@/components/ui/Button/Button";
import { Dialog, DialogPanel } from "@/components/ui/Dialog/Dialog";
import { Drawer, DrawerPanel } from "@/components/ui/Drawer/Drawer";
import { InlineConfirm } from "@/components/ui/InlineConfirm/InlineConfirm";
import { itemAfterKey, Menu, MenuHeader, MenuItem, MenuItems, MenuRow } from "@/components/ui/Menu/Menu";
import { Toast } from "@/components/ui/Toast/Toast";
import { render } from "./kit-render";

const noop = () => {};

describe("Banner", () => {
  it.each([
    ["info", "M12 11v5M12 8v.5"],
    ["warning", "M12 4 2.5 20h19z"],
    ["danger", "M12 4 2.5 20h19z"],
    ["version", "M12 3 3 8l9 5 9-5z"],
    ["quiet", "M12 11v5M12 8v.5"],
  ] as [BannerTone, string][])("draws the %s tone with its icon", (tone, path) => {
    const html = render(Banner, { tone, title: "Still reading 5 files." }, "You can keep working.");
    expect(html).toMatch(new RegExp(`^<div class="_banner_\\w+ _${tone}_\\w+"><svg`));
    expect(html).toContain(`<path d="${path}`);
  });

  it("says the headline in bold, then the detail, then the actions", () => {
    const html = render(
      Banner,
      { tone: "warning", title: "Glare covers the lower half of page 3.", actions: h(Button, { size: "sm" }, "Read this page again") },
      "6 values on this page couldn’t be checked.",
    );
    expect(html).toMatch(/<div class="_body_\w+"><span><strong>Glare covers the lower half of page 3.<\/strong> 6 values on this page couldn’t be checked.<\/span><button/);
  });

  it("puts one short action on the same line, with its own icon", () => {
    const html = render(Banner, { tone: "quiet", icon: "link", inline: true, actions: h(Button, { variant: "quiet", size: "sm", icon: "undo" }, "Undo") }, "Linked as version 3.");
    expect(html).toMatch(/class="_body_\w+ _inline_\w+"/);
    expect(html).toContain('<path d="M10 14a4 4 0 0 0 5.7 0');
  });

  it("is slim inside a card, and live only when asked", () => {
    expect(render(Banner, { tone: "version", compact: true, title: "4 files look like new versions." })).toMatch(/class="_banner_\w+ _version_\w+ _compact_\w+"/);
    expect(render(Banner, { tone: "danger", title: "x" })).not.toContain("role=");
    expect(render(Banner, { tone: "danger", title: "x", role: "alert" })).toContain('role="alert"');
  });
});

describe("Toast", () => {
  it("is a dark note with an icon, and an Undo when given one", () => {
    const html = render(Toast, { action: { label: "Undo", onClick: noop } }, "Moved 3 files to Land & Legal");
    expect(html).toMatch(/^<div class="_toast_\w+"><svg[^>]*><path d="M5 12l5 5 9-10"/);
    expect(html).toMatch(/<span class="_message_\w+">Moved 3 files to Land &amp; Legal<\/span><button type="button" class="_btn_\w+ _quiet_\w+ _sm_\w+">Undo<\/button><\/div>$/);
  });

  it("has no button without an action, and takes another icon", () => {
    const html = render(Toast, { icon: "trash" }, "Moved to Trash");
    expect(html).not.toContain("<button");
    expect(html).toContain('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"');
  });
});

describe("InlineConfirm", () => {
  const props = { label: "Confirm move to Trash", question: "Move 3 files to Trash?", detail: "You can restore them for 30 days.", confirmLabel: "Move to Trash", busyLabel: "Moving…", onConfirm: noop, onCancel: noop };

  it("asks in the page, the action before Cancel", () => {
    const html = render(InlineConfirm, props);
    expect(html).toMatch(/^<div class="_confirm_\w+" role="alertdialog" aria-label="Confirm move to Trash"><svg/);
    expect(html).toMatch(/<strong>Move 3 files to Trash\?<\/strong> You can restore them for 30 days.<\/span>/);
    expect(html).toMatch(/_danger_\w+ _sm_\w+">Move to Trash<\/button><button type="button" class="_btn_\w+ _sm_\w+">Cancel<\/button>/);
  });

  it("shows the action running and holds Cancel", () => {
    const html = render(InlineConfirm, { ...props, busy: true });
    expect(html).toMatch(/aria-busy="true">Moving…<\/button>/);
    expect(html).toMatch(/<button disabled="" type="button" class="_btn_\w+ _sm_\w+">Cancel<\/button>/);
  });
});

describe("Dialog", () => {
  const props = {
    id: "make-current",
    title: "Make version 2 the current one?",
    onClose: noop,
    actions: [h(Button, { key: "c" }, "Cancel"), h(Button, { key: "a", variant: "primary" }, "Make version 2 current")],
  };

  it("draws the card: title, Close, body, then the actions on the right", () => {
    const html = render(DialogPanel, props, h("p", { className: "k-muted" }, "Version 1 stays in Versions."));
    expect(html).toMatch(/^<div class="_dialog_\w+" role="dialog" aria-labelledby="make-current-title"><div class="_head_\w+"><h3 class="k-h3" id="make-current-title">/);
    expect(html).toContain('aria-label="Close"');
    expect(html).toMatch(/<\/p><div class="_group_\w+ _actions_\w+"><button[^>]*>Cancel<\/button><button[^>]*>Make version 2 current<\/button><\/div><\/div>$/);
  });

  it("is a native dialog, empty while closed", () => {
    const closed = render(Dialog, { ...props, open: false });
    expect(closed).toMatch(/^<dialog class="_native_\w+" aria-labelledby="make-current-title"><\/dialog>$/);
    expect(render(Dialog, { ...props, open: true })).toMatch(/^<dialog class="_native_\w+" aria-labelledby="make-current-title"><div class="_dialog_\w+">/);
  });
});

describe("Drawer", () => {
  const props = { id: "move", title: "Move 3 files", onClose: noop, footer: h(Button, { variant: "primary" }, "Move here") };

  it("draws the head bar, the body and the footer", () => {
    const html = render(DrawerPanel, { ...props, width: 300 }, "tree");
    expect(html).toMatch(/^<aside class="_drawer_\w+" aria-labelledby="move-title" style="width:300px"><div class="_head_\w+"><h3 class="k-h4" id="move-title">Move 3 files<\/h3><button/);
    expect(html).toMatch(/<div class="_body_\w+">tree<\/div><div class="_foot_\w+"><button[^>]*>Move here<\/button><\/div><\/aside>$/);
  });

  it("is a native dialog, empty while closed", () => {
    expect(render(Drawer, { ...props, open: false }, "tree")).toMatch(/^<dialog class="_native_\w+" aria-labelledby="move-title"><\/dialog>$/);
    expect(render(Drawer, { ...props, open: true }, "tree")).toContain("tree");
  });
});

describe("Menu", () => {
  it("keeps the header and the theme row out of the list of actions", () => {
    const html = render(
      Menu,
      null,
      h(MenuHeader, { title: "Priya Kulkarni", sub: "Admin · Wakad Realty" }),
      h(MenuRow, { label: "Theme", children: "SEG" }),
      h(MenuItems, {
        label: "Account",
        children: [
          h(MenuItem, { key: "p", icon: "user", onSelect: noop, children: "Your profile" }),
          h(MenuItem, { key: "s", icon: "lock", danger: true, onSelect: noop, children: "Sign out everywhere" }),
        ],
      }),
    );
    expect(html).toMatch(/^<div class="_menu_\w+"><div class="_header_\w+"><strong>Priya Kulkarni<\/strong><span class="k-small k-muted">Admin · Wakad Realty<\/span><\/div>/);
    expect(html).toMatch(/<div class="_row_\w+"><span class="k-small k-muted">Theme<\/span>SEG<\/div><div class="_items_\w+" role="menu" aria-label="Account">/);
    expect(html).toMatch(/<button type="button" role="menuitem" class="_item_\w+"><svg[^>]*>.*?<\/svg>Your profile<\/button>/);
    expect(html).toMatch(/class="_item_\w+ _danger_\w+"><svg[^>]*>.*?<\/svg>Sign out everywhere<\/button>/);
  });

  it("moves with the arrow keys, Home and End, wrapping round", () => {
    expect(itemAfterKey("ArrowDown", 0, 3)).toBe(1);
    expect(itemAfterKey("ArrowDown", 2, 3)).toBe(0);
    expect(itemAfterKey("ArrowUp", 0, 3)).toBe(2);
    expect(itemAfterKey("ArrowUp", -1, 3)).toBe(1);
    expect(itemAfterKey("Home", 2, 3)).toBe(0);
    expect(itemAfterKey("End", 0, 3)).toBe(2);
    expect(itemAfterKey("a", 0, 3)).toBeNull();
  });
});
