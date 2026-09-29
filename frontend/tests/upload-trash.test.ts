import { describe, expect, it } from "vitest";
import { shouldReloadAfterUploads, uploadStateText } from "@/components/documents/logic";
import type { UploadState } from "@/components/documents/logic";

const done: UploadState = { kind: "done", documentId: "d1" };
const duplicate = (inTrash: boolean): UploadState => ({
  kind: "duplicate",
  documentId: "d2",
  existingName: "bill.pdf",
  inTrash,
});

describe("the words for a duplicate that is in Trash", () => {
  it("says it is in Trash, then that it is restored", () => {
    expect(uploadStateText(duplicate(false))).toBe("Already uploaded");
    expect(uploadStateText(duplicate(true))).toBe("Already uploaded, in Trash");
    expect(
      uploadStateText({ kind: "restoring", documentId: "d2", existingName: "bill.pdf" }),
    ).toBe("Restoring");
    expect(
      uploadStateText({ kind: "restored", documentId: "d2", existingName: "bill.pdf" }),
    ).toBe("Restored from Trash");
  });
});

describe("shouldReloadAfterUploads", () => {
  it("waits until every upload has finished, and something new came of it", () => {
    expect(shouldReloadAfterUploads([])).toBe(false);
    expect(shouldReloadAfterUploads([done, { kind: "uploading", fraction: 0.5 }])).toBe(false);
    expect(shouldReloadAfterUploads([done, { kind: "waiting" }])).toBe(false);
    expect(shouldReloadAfterUploads([done])).toBe(true);
    expect(shouldReloadAfterUploads([duplicate(false)])).toBe(true);
    expect(shouldReloadAfterUploads([{ kind: "error", message: "No." }])).toBe(false);
  });

  it("keeps the page while a duplicate in Trash waits for Restore", () => {
    expect(shouldReloadAfterUploads([done, duplicate(true)])).toBe(false);
    expect(
      shouldReloadAfterUploads([{ kind: "restoring", documentId: "d2", existingName: "bill.pdf" }]),
    ).toBe(false);
    expect(
      shouldReloadAfterUploads([{ kind: "restored", documentId: "d2", existingName: "bill.pdf" }]),
    ).toBe(true);
  });
});
