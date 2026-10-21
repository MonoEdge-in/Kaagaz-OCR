// Field, TextInput, Select, TextArea, Checkbox, Switch and CodeInput, against
// Components.html section 07: default, filled, disabled and error, with an
// icon, the select's chevron, the code boxes, and the switch's words. Field
// takes its control as a function, so it is rendered with createElement.

import { createElement as h } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Checkbox } from "@/components/ui/Checkbox/Checkbox";
import { CodeInput } from "@/components/ui/CodeInput/CodeInput";
import { digitsOf, eraseAt, typeAt, typedInto } from "@/components/ui/CodeInput/codeDigits";
import { Field, Select, TextArea, TextInput } from "@/components/ui/Field/Field";
import { Switch } from "@/components/ui/Switch/Switch";
import { render } from "./kit-render";


describe("Field", () => {
  it("labels its control", () => {
    const html = renderToStaticMarkup(
      h(Field, {
        id: "party",
        label: "Party name",
        children: (control) => h(TextInput, { ...control, placeholder: "e.g. Mehta Textiles" }),
      }),
    );
    expect(html).toMatch(/<label class="_label_\w+" for="party">Party name<\/label><input id="party"/);
    expect(html).not.toContain("aria-describedby");
    expect(html).not.toContain("aria-invalid");
  });

  it("ties the help line to the control", () => {
    const html = renderToStaticMarkup(
      h(Field, {
        id: "date",
        label: "Document date",
        help: "Day Month Year",
        children: (control) => h(TextInput, { ...control, icon: "calendar" }),
      }),
    );
    expect(html).toContain('aria-describedby="date-help"');
    expect(html).toMatch(/<span class="_help_\w+" id="date-help">Day Month Year<\/span>/);
  });

  it("shows an error with an icon and words, read before the help", () => {
    const html = renderToStaticMarkup(
      h(Field, {
        id: "gstin",
        label: "GSTIN",
        help: "15 characters",
        error: "Enter a 15-character GSTIN, like 27AABCM1234F1Z5",
        children: (control) => h(TextInput, { ...control, mono: true, value: "27AABCM1234F1", readOnly: true }),
      }),
    );
    expect(html).toContain('aria-describedby="gstin-error gstin-help"');
    expect(html).toContain('aria-invalid="true"');
    expect(html).toMatch(/id="gstin-error"><svg[^>]*>.*<\/svg>Enter a 15-character GSTIN/);
  });
});

describe("TextInput", () => {
  it("is a plain input without an icon", () => {
    expect(render(TextInput, { "aria-label": "Party" })).toMatch(/^<input aria-label="Party" class="_input_\w+"\/>$/);
  });

  it("puts an icon at the start", () => {
    const html = render(TextInput, { icon: "search", "aria-label": "Search inside documents" });
    expect(html).toMatch(/^<span class="_inputWrap_\w+"><svg[^>]*><path d="M20 20l-4\.5-4\.5"/);
  });

  it("is disabled and mono when asked", () => {
    const html = render(TextInput, { disabled: true, mono: true, "aria-label": "GSTIN" });
    expect(html).toContain('disabled=""');
    expect(html).toMatch(/class="_input_\w+ _mono_\w+"/);
  });
});

describe("Select and TextArea", () => {
  it("draws a chevron after the select", () => {
    const html = render(Select, { "aria-label": "Department" }, h("option", null, "Accounts"));
    expect(html).toMatch(/^<span class="_selectWrap_\w+"><select aria-label="Department" class="_select_\w+"><option>Accounts<\/option><\/select><svg[^>]*><path d="M6 9l6 6 6-6"/);
  });

  it("draws a text area", () => {
    expect(render(TextArea, { "aria-label": "Note" })).toMatch(/^<textarea aria-label="Note" class="_textarea_\w+"><\/textarea>$/);
  });
});

describe("Checkbox", () => {
  it("puts the words beside the box, the whole line clickable", () => {
    const html = render(Checkbox, { label: "Remember this correction", defaultChecked: true });
    expect(html).toMatch(/^<label class="_check_\w+"><input type="checkbox" checked=""\/>Remember this correction<\/label>$/);
  });

  it("is faint when disabled", () => {
    const html = render(Checkbox, { label: "Read handwriting", disabled: true });
    expect(html).toMatch(/class="_check_\w+ _disabled_\w+"/);
    expect(html).toContain('disabled=""');
  });
});

describe("Switch", () => {
  it("says On or Off in words beside the track", () => {
    const on = render(Switch, { label: "File new uploads into folders on their own", checked: true, onChange: () => {} });
    expect(on).toContain('type="checkbox" role="switch" checked=""');
    expect(on).toMatch(/aria-hidden="true">On<\/span><\/label>$/);
    const off = render(Switch, { label: "Two-step sign-in for members", checked: false, onChange: () => {} });
    expect(off).not.toContain("checked");
    expect(off).toMatch(/aria-hidden="true">Off<\/span><\/label>$/);
  });
});

describe("CodeInput", () => {
  it("draws one named box per digit in a labelled group", () => {
    const html = render(CodeInput, { labelledBy: "code-label", value: "482", onChange: () => {} });
    expect(html).toMatch(/^<div class="_code_\w+" role="group" aria-labelledby="code-label">/);
    expect(html.match(/<input /g)).toHaveLength(6);
    expect(html).toContain('aria-label="Digit 1" inputMode="numeric" autoComplete="one-time-code"');
    expect(html).toContain('aria-label="Digit 6" inputMode="numeric" autoComplete="off"');
    expect(html.match(/value="(\d?)"/g)).toEqual(['value="4"', 'value="8"', 'value="2"', 'value=""', 'value=""', 'value=""']);
  });

  it("marks the group invalid and disables every box", () => {
    const html = render(CodeInput, { labelledBy: "l", value: "", onChange: () => {}, invalid: true, disabled: true });
    expect(html.match(/aria-invalid="true"/g)).toHaveLength(6);
    expect(html.match(/disabled=""/g)).toHaveLength(6);
  });

  it("keeps the digits with no gaps", () => {
    expect(digitsOf("482 913", 6)).toBe("482913");
    expect(digitsOf("4829131", 6)).toBe("482913");
    expect(typeAt("48", 2, "2", 6)).toEqual({ value: "482", focus: 3 });
    expect(typeAt("48", 5, "2", 6)).toEqual({ value: "482", focus: 3 });
    expect(typeAt("482", 1, "9", 6)).toEqual({ value: "492", focus: 2 });
    expect(typeAt("", 0, "482913", 6)).toEqual({ value: "482913", focus: 5 });
    expect(typeAt("48", 2, "x", 6)).toEqual({ value: "48", focus: 2 });
    expect(typedInto("4", "49")).toBe("9");
    expect(typedInto("4", "94")).toBe("9");
    expect(typedInto("", "7")).toBe("7");
    expect(eraseAt("482", 2)).toEqual({ value: "48", focus: 2 });
    expect(eraseAt("482", 3)).toEqual({ value: "48", focus: 2 });
    expect(eraseAt("", 0)).toEqual({ value: "", focus: 0 });
  });
});
