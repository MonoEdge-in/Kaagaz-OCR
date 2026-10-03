// The viewer's version picker (PRD 1, F6). Each version is its own document id,
// and every version's detail lists the whole family, so any version can be
// opened. The one search and chat use is the highest version that is ready
// (PROVISIONAL 7.3). Pure functions, unit tested in node
// (tests/viewer-versions.test.ts).

import type { DocumentDetail, DocumentStatus, DocumentVersion, NewerVersion } from "@/lib/types";

type WithVersions = Pick<DocumentDetail, "id" | "versions">;

const STILL_READING: readonly DocumentStatus[] = ["received", "clean"];

/** Oldest first. O(V log V) for V versions. */
export function sortedVersions(versions: readonly DocumentVersion[]): DocumentVersion[] {
  return [...versions].sort((a, b) => a.version_no - b.version_no);
}

/** Where the viewed version stands; null when there is nothing to pick from. */
export function versionPosition(detail: WithVersions): { number: number; total: number } | null {
  if (detail.versions.length < 2) return null;
  const viewed = detail.versions.find((version) => version.id === detail.id);
  return viewed ? { number: viewed.version_no, total: detail.versions.length } : null;
}

/** "Version 2 of 2". */
export function versionText(position: { number: number; total: number }): string {
  return `Version ${position.number} of ${position.total}`;
}

/** Which version search and chat use, when it is not the one being viewed; null otherwise. */
export function versionNote(detail: WithVersions): string | null {
  const viewed = detail.versions.find((version) => version.id === detail.id);
  const inUse = detail.versions.find((version) => version.is_current);
  if (!viewed || !inUse || viewed.id === inUse.id) return null;
  if (viewed.version_no < inUse.version_no) {
    return `This is an older version. Search and chat use version ${inUse.version_no}.`;
  }
  return STILL_READING.includes(viewed.status)
    ? `This version is still being read. Search and chat use version ${inUse.version_no} until it is ready.`
    : `This version isn't used. Search and chat use version ${inUse.version_no}.`;
}

/**
 * Whether chat can use the viewed version, so the viewer offers "Ask about
 * this document" (PRD 3 H2): it is ready, the version in use, and not in Trash.
 */
export function usedInChat(
  detail: WithVersions & Pick<DocumentDetail, "status" | "deleted_at">,
): boolean {
  const viewed = detail.versions.find((version) => version.id === detail.id);
  return detail.status === "ready" && detail.deleted_at === null && viewed?.is_current === true;
}

/**
 * The line for a newer version that is still being read, or failed, shown
 * under the current one in the list and the viewer: "Version 3: Reading the
 * document… Try 2 of 5." The server's status message is shown as is.
 */
export function newerVersionText(newer: Pick<NewerVersion, "version_no" | "status_message">): string {
  return `Version ${newer.version_no}: ${newer.status_message}`;
}
