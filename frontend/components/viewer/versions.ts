// The viewer's version picker (PRD 1, F6). Each version is its own document id,
// and every version's detail lists the whole family, so any version can be
// opened. The one search and chat use is the highest version that is ready
// (PROVISIONAL 7.3). Pure functions, unit tested in node
// (tests/viewer-versions.test.ts).

import type { DocumentDetail, DocumentStatus, DocumentVersion } from "@/lib/types";

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
