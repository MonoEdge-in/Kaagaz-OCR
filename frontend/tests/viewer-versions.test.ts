import { describe, expect, it } from "vitest";
import {
  newerVersionText,
  sortedVersions,
  versionNote,
  versionPosition,
  versionText,
} from "@/components/viewer/versions";
import type { DocumentStatus, DocumentVersion } from "@/lib/types";

const version = (
  id: string,
  version_no: number,
  is_current: boolean,
  status: DocumentStatus = "ready",
): DocumentVersion => ({ id, version_no, status, is_current, created_at: "2026-09-29T10:00:00+05:30" });

const v1 = version("a", 1, false);
const v2 = version("b", 2, true);

describe("the version picker", () => {
  it("lists the versions oldest first, whatever order they came in", () => {
    expect(sortedVersions([v2, v1]).map((v) => v.version_no)).toEqual([1, 2]);
  });

  it('says "Version 2 of 2" for the one being viewed', () => {
    const position = versionPosition({ id: "b", versions: [v2, v1] });
    expect(position).toEqual({ number: 2, total: 2 });
    expect(position && versionText(position)).toBe("Version 2 of 2");
    expect(versionPosition({ id: "a", versions: [v2, v1] })).toEqual({ number: 1, total: 2 });
  });

  it("shows no picker for a document with one version, or one not in its list", () => {
    expect(versionPosition({ id: "a", versions: [version("a", 1, true)] })).toBeNull();
    expect(versionPosition({ id: "x", versions: [v1, v2] })).toBeNull();
  });
});

describe("the note under the picker", () => {
  it("says nothing for the version search and chat use", () => {
    expect(versionNote({ id: "b", versions: [v1, v2] })).toBeNull();
  });

  it("names the version in use when an older one is viewed", () => {
    expect(versionNote({ id: "a", versions: [v1, v2] })).toBe(
      "This is an older version. Search and chat use version 2.",
    );
  });

  it("says a newer version is still being read, and the older one is used until then", () => {
    const reading = [version("a", 1, true), version("b", 2, false, "clean")];
    expect(versionNote({ id: "b", versions: reading })).toBe(
      "This version is still being read. Search and chat use version 1 until it is ready.",
    );
  });

  it("says a newer version that did not work is not used", () => {
    const failed = [version("a", 1, true), version("b", 2, false, "failed")];
    expect(versionNote({ id: "b", versions: failed })).toBe(
      "This version isn't used. Search and chat use version 1.",
    );
  });

  it("says nothing when no version is in use yet", () => {
    expect(versionNote({ id: "a", versions: [version("a", 1, false, "received")] })).toBeNull();
  });
});

describe("the line for a newer version", () => {
  it("names the version and gives the server's status message as is", () => {
    expect(
      newerVersionText({ version_no: 3, status_message: "Reading the document… Try 2 of 5." }),
    ).toBe("Version 3: Reading the document… Try 2 of 5.");
  });
});
