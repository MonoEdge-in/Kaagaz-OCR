// Issue 376 (27 Oct 2026): the admin Departments tab says one error at a time, in the word
// "department", on a line of its own under the add form, so the Add button never moves.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { NameForm } from "@/components/admin/DepartmentsPanel";
import { departmentNameProblem, libraryErrorText } from "@/components/documents/library";
import { ApiError } from "@/lib/api";

const TAKEN = "A department with this name already exists. Choose another name.";

describe("the Departments tab's words", () => {
  it("says department, never folder", () => {
    expect(libraryErrorText(new ApiError(409, "conflict", "Conflict."), "department")).toBe(TAKEN);
    expect(departmentNameProblem(" ")).toBe("Enter a name for the department.");
    expect(libraryErrorText(new ApiError(409, "conflict", "Conflict."), "folder")).toContain("folder"); // elsewhere unchanged
  });
});

describe("the add form's error", () => {
  const form = (error: string | null) =>
    renderToStaticMarkup(
      createElement(NameForm, { start: "", label: "New department", submitLabel: "Add department", busy: false, error, onSubmit: () => undefined }),
    );

  it("sits after the field and the button, on its own line, not inside the field", () => {
    const html = form(TAKEN);
    const field = html.slice(html.indexOf('<div class="field">'), html.indexOf("</div>"));
    expect(field).not.toContain("error-text");
    expect(html.indexOf("error-text")).toBeGreaterThan(html.indexOf(">Add department</button>"));
    expect(html).toMatch(/class="error-text [^"]*departmentError[^"]*" role="alert">A department with this name/);
    expect(html.match(/error-text/g)).toHaveLength(1);
    expect(html).toContain('aria-invalid="true"');
  });

  it("is not there with nothing to say", () => {
    const html = form(null);
    expect(html).not.toContain("error-text");
    expect(html).not.toContain("aria-invalid");
  });

  it("takes a whole line of the form, and the panel keeps an add's refusal off its top", () => {
    const css = readFileSync(join(__dirname, "..", "components/files/Files.module.css"), "utf8");
    expect(css).toMatch(/\.departmentError \{\s*flex-basis: 100%;/);
    const panel = readFileSync(join(__dirname, "..", "components/admin/DepartmentsPanel.tsx"), "utf8");
    expect(panel).toMatch(/error=\{addError\}\s*onEdit=\{\(\) => setAddError\(null\)\}/);
    expect(panel).toContain('libraryErrorText(caught, "department")');
  });
});
