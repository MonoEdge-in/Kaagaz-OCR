// Issue 367 (27 Oct 2026): the department picker beside "All documents" picks where new
// uploads go, so it says "Upload to", never a bare "Department" that reads as a list filter.
// It stays in the upload controls, before "Read with" (tests/ui-audit-documents.test.ts).

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DepartmentPicker } from "@/components/files/DepartmentPicker";
import type { Folder } from "@/lib/types";

const LAND = { id: "f1", name: "Land & Legal", parent_id: null } as unknown as Folder;

describe("the upload's department picker", () => {
  it("is labelled Upload to, and offers each department", () => {
    const html = renderToStaticMarkup(
      createElement(DepartmentPicker, { departments: [LAND], value: "f1", onChange: () => undefined }),
    );
    expect(html).toMatch(/<label[^>]*>Upload to<\/label>/);
    expect(html).not.toContain(">Department<");
    expect(html).toContain(">Land &amp; Legal</option>");
    expect(html).toContain(">No department</option>");
  });
});
