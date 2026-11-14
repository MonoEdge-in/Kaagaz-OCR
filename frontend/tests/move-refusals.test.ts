// PRD 7 PR 11b (#604; p1:194-204): a move that would change who can see the files, or a delete of
// a folder holding what the person can't see, is refused (PR 10c's 403s) in the PRD's words,
// wherever libraryErrorText says a move or a delete failed (the Move dialog, a drop on the tree,
// a folder's Move and Delete). Any other refusal keeps the server's words.

import { describe, expect, it } from "vitest";
import { libraryErrorText, movedMessage } from "@/components/documents/library";
import { ApiError } from "@/lib/api";

const MOVE = "You can't move this here, because it would change who can see it. Ask an admin.";
const DELETE = "You can't delete this folder. Ask an admin.";

describe("a move or a folder delete refused for who can see it", () => {
  it("is said in the PRD's words for a file or a folder, whatever the server's words", () => {
    for (const what of ["document", "folder"] as const) {
      expect(libraryErrorText(new ApiError(403, "move_changes_access", "Forbidden."), what)).toBe(MOVE);
    }
    expect(libraryErrorText(new ApiError(403, "hidden_inside", "Forbidden."), "folder")).toBe(DELETE);
  });

  it("names why in a bulk move's toast", () => {
    const why = libraryErrorText(new ApiError(403, "move_changes_access", "x"), "document");
    expect(movedMessage(1, 3, "Invoices", why)).toBe(`Moved 1 document to Invoices. Not moved: 2 documents. ${MOVE}`);
  });

  it("leaves every other refusal in the server's words", () => {
    expect(libraryErrorText(new ApiError(403, "forbidden", "Only an admin can delete a folder that holds files."), "folder")).toBe(
      "Only an admin can delete a folder that holds files.",
    );
    expect(libraryErrorText(new ApiError(409, "move_changes_access", "Not a 403."), "document")).toBe("Not a 403.");
  });
});
