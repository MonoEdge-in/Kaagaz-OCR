import { describe, expect, it } from "vitest";
import { nameParts } from "@/components/documents/names";

describe("nameParts", () => {
  it("cuts after _ . - and @, keeping the extension on the last piece", () => {
    expect(nameParts("Ask_Brain_Eval_Report.pdf")).toEqual(["Ask_", "Brain_", "Eval_", "Report.pdf"]);
    expect(nameParts("land-record-7-12-extract.jpeg")).toEqual([
      "land-",
      "record-",
      "7-",
      "12-",
      "extract.jpeg",
    ]);
    expect(nameParts("tushar@monoedge.in")).toEqual(["tushar@", "monoedge.in"]);
  });

  it("cuts at a dot inside the name, but not before a short extension", () => {
    expect(nameParts("report.v2.final.xlsx")).toEqual(["report.", "v2.", "final.xlsx"]);
    expect(nameParts("archive.backup")).toEqual(["archive.", "backup"]);
  });

  it("keeps a run of marks together, and never starts with a lone mark", () => {
    expect(nameParts("a__b")).toEqual(["a__", "b"]);
    expect(nameParts("_draft_copy")).toEqual(["_draft_", "copy"]);
    expect(nameParts("end_")).toEqual(["end_"]);
  });

  it("leaves a name without marks whole, and always gives the name back when joined", () => {
    expect(nameParts("6a35a60b6764ef0c10a909d2622fcb27")).toEqual(["6a35a60b6764ef0c10a909d2622fcb27"]);
    expect(nameParts("Retail bill March.pdf")).toEqual(["Retail bill March.pdf"]);
    expect(nameParts("")).toEqual([""]);
    const long =
      "Sharma_Textiles_Tax_Invoice_GSTIN_27AAACS1234F1Z5_FY2025-26_April_to_September_consolidated_final_v3.pdf";
    expect(nameParts(long).join("")).toBe(long);
    expect(nameParts(long)).toContain("consolidated_");
  });
});
