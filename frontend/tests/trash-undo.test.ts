// Issue 372 (27 Oct 2026): Move to Trash and Restore say so in the documents screen's toast,
// with Undo, as Move to folder does; Delete forever cannot be undone and offers none.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { undoRemoval } from "@/components/documents/trash";

function calls() {
  const made: string[] = [];
  return {
    made,
    trash: async (id: string) => made.push(`trash ${id}`),
    restore: async (id: string) => made.push(`restore ${id}`),
  };
}

describe("undoing a removal from the documents list", () => {
  it("restores what was moved to Trash, one at a time", async () => {
    const api = calls();
    const undo = undoRemoval("trash", ["d1", "d2"], api);
    expect(undo?.done).toBe("Restored from Trash.");
    await undo?.run();
    expect(api.made).toEqual(["restore d1", "restore d2"]);
  });

  it("moves what was restored back to Trash", async () => {
    const api = calls();
    const undo = undoRemoval("restore", ["d1"], api);
    expect(undo?.done).toBe("Moved back to Trash.");
    await undo?.run();
    expect(api.made).toEqual(["trash d1"]);
  });

  it("offers none for Delete forever, Read again, or nothing done", () => {
    expect(undoRemoval("forever", ["d1"], calls())).toBeUndefined();
    expect(undoRemoval("read", ["d1"], calls())).toBeUndefined();
    expect(undoRemoval("trash", [], calls())).toBeUndefined();
  });
});

describe("the documents screen", () => {
  const read = (file: string) => readFileSync(join(__dirname, "..", file), "utf8");

  it("shows a removal that can be undone in its toast, not only to screen readers", () => {
    const view = read("components/documents/DocumentsView.tsx");
    expect(view).toMatch(/function afterRemoved\(message: string, undo\?: RemovalUndo\) \{\s*if \(undo\) setToast\(/);
    // Changed on purpose (#537, 7 Nov 2026): a deleted folder's toast is a note with no Undo.
    expect(view).toContain('onUndo={toastUndo ? () => undoMove(toastUndo, toast?.undone ?? "") : undefined}');
  });

  it("gives each row's Move to Trash and Restore, and the selection's, their Undo", () => {
    const table = read("components/documents/DocumentsTable.tsx");
    expect(table).toContain('undoRemoval("trash", [document.id])');
    expect(table).toContain('undoRemoval("restore", [document.id])');
    expect(table).toContain("undoRemoval(action, result.done)");
  });
});
