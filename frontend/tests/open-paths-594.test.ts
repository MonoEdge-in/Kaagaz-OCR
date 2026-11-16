// #594: a restore whose folder is gone says where the file or folder went instead, and a folder
// that came back private says why and who sees it.

import { describe, expect, it } from "vitest";
import { SEALED, folderBackMessage, folderRestoredMessage, placedMessage } from "@/components/documents/trash";
import type { Folder } from "@/lib/types";

const folder = (name: string) => ({ id: "f1", name, parent_id: null, created_at: "2026-01-01T00:00:00Z" }) as Folder;
const back = (more: { location?: string[] | null; relocated?: boolean; sealed?: boolean } = {}) => ({
  folder: folder("2025"),
  location: more.location === undefined ? ["Finance"] : more.location,
  renamed: false,
  relocated: more.relocated ?? true,
  sealed: more.sealed ?? false,
});

describe("a file restored after its folder was deleted", () => {
  it("says it is held, or the folder it went into", () => {
    expect(placedMessage({ held: true, folder: null })).toBe(
      "Its folder no longer exists. Only you and Management can see it until it's filed.",
    );
    expect(placedMessage({ held: false, folder: folder("Finance") })).toBe("Its folder no longer exists: it's in “Finance”.");
    expect(placedMessage({ held: false, folder: null })).toBe("Its folder no longer exists, so it went into the folder above it.");
  });
});

describe("a folder restored after its folder was deleted", () => {
  it("names the department or private folder it went under", () => {
    expect(folderRestoredMessage(back())).toBe("Restored to Finance, because its folder no longer exists.");
    expect(folderBackMessage(back())).toBe("Its folder is back in Finance, because the folder it was in no longer exists.");
    expect(folderBackMessage(back({ location: [] }))).toBe(
      "Its folder is back at Top level, because the folder it was in no longer exists.",
    );
  });

  it("says it came back private, and who sees it", () => {
    expect(folderRestoredMessage(back({ location: [], sealed: true }))).toBe(
      `Restored to Top level, because its folder no longer exists. ${SEALED}`,
    );
    expect(folderBackMessage(back({ sealed: true }))).toBe(
      `Its folder is back in Finance, because the folder it was in no longer exists. ${SEALED}`,
    );
    expect(SEALED).toBe(
      "It came back private, because a department or private folder it was in no longer exists. Only Management can see it until an admin names people.",
    );
  });
});
