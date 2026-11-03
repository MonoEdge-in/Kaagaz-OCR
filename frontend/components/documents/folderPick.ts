// Reading a whole folder the person picked (<input webkitdirectory>) or dropped
// (DataTransferItem.webkitGetAsEntry) for "Upload a folder" (#484), then the manifest the
// server plans the folders from. Unit tested in node (tests/folder-pick.test.ts).
// - Memory: a pick of 50,000 files keeps 50,000 File handles (the browser reads no bytes until
//   one is sent) and their paths, in the caller's ref, never in React state.
// - A drop is walked folder by folder, readEntries until it gives [] (it gives about 100 at a
//   time), saying the count as it goes. It sees empty folders; a folder pick can't (a browser fact).
// - Hidden and system files are skipped, never sent and not counted against the limits; a
//   hidden folder takes everything inside it. Each is listed once (the first SKIPPED_SHOWN).
// - Names are kept in one Unicode form (NFC), so a Mac's copy and a PC's copy are one path.

/** A name is skipped when it starts with "." or "~$", or is one of these, in any letter case. */
const SYSTEM_NAMES = new Set([
  "thumbs.db",
  "ehthumbs.db",
  "desktop.ini",
  "icon\r",
  "$recycle.bin",
  "system volume information",
  "__macosx",
]);

/** How many skipped names are kept to show; the count goes on past it. */
const SKIPPED_SHOWN = 100;

/** The longest folder name Kaagaz keeps (the server's FOLDER_NAME_MAX). */
const FOLDER_NAME_MAX = 100;

export function isSkipped(name: string): boolean {
  return name.startsWith(".") || name.startsWith("~$") || SYSTEM_NAMES.has(name.toLowerCase());
}

/** The server's rule: control, format, surrogate, private-use, unassigned, line and paragraph
 * separator characters, and "/" or "\". The Devanagari joiners (ZWNJ, ZWJ) are allowed. */
const HIDDEN_CHARACTERS = /(?![\u200C\u200D])[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}/\\]/u;

/** Why a folder name can't be kept: "long" (over 100 characters), "hidden" (a character above, or "." or ".."), or null. */
export function nameProblem(name: string): "long" | "hidden" | null {
  if (HIDDEN_CHARACTERS.test(name) || name === "." || name === ".." || name.trim() === "") return "hidden";
  return [...name].length > FOLDER_NAME_MAX ? "long" : null;
}

/** The name without its hidden characters, to show in a message. */
export const visibleName = (name: string) => name.replace(new RegExp(HIDDEN_CHARACTERS.source, "gu"), "");

export interface Picked {
  rootName: string;
  files: File[]; // kept by the caller in a ref, never in React state
  paths: string[]; // each file's path from the root, root first, "/"-joined, NFC
  folders: string[]; // every folder seen, root first (a drop's empty folders too)
  skipped: string[]; // the first SKIPPED_SHOWN hidden or system names left out, as paths
  skippedCount: number;
  seesEmptyFolders: boolean; // false for a folder pick: the browser lists files only
}

const nfc = (name: string) => name.normalize("NFC");

function empty(rootName: string, seesEmptyFolders: boolean): Picked {
  return { rootName, files: [], paths: [], folders: [rootName], skipped: [], skippedCount: 0, seesEmptyFolders };
}

function skip(pick: Picked, path: string) {
  pick.skippedCount += 1;
  if (pick.skipped.length < SKIPPED_SHOWN) pick.skipped.push(path);
}

export const NO_FOLDER_PICKER = "This browser can’t pick folders. Use a computer.";

/** A pick of files without their folder paths: a phone's picker ignores webkitdirectory. */
export const noFolderPaths = (list: ArrayLike<File>) => list.length > 0 && !list[0]!.webkitRelativePath;

/** A folder pick's files (each has webkitRelativePath "A/B/c.pdf"); null for an empty pick. O(n). */
export function fromFileList(list: ArrayLike<File>): Picked | null {
  const first = list[0];
  if (!first) return null;
  const pick = empty(nfc(first.webkitRelativePath.split("/")[0] ?? first.name), false);
  const skippedFolders = new Set<string>();
  for (let index = 0; index < list.length; index++) {
    const file = list[index]!;
    const parts = (file.webkitRelativePath || file.name).split("/").map(nfc);
    // The root itself is never skipped: the person chose it.
    const hidden = parts.findIndex((part, at) => at > 0 && isSkipped(part));
    if (hidden === -1) {
      pick.files.push(file);
      pick.paths.push(parts.join("/"));
      continue;
    }
    const path = parts.slice(0, hidden + 1).join("/");
    if (hidden === parts.length - 1) skip(pick, path);
    else if (!skippedFolders.has(path)) {
      skippedFolders.add(path);
      skip(pick, path);
    }
  }
  return pick;
}

const readBatch = (reader: FileSystemDirectoryReader) =>
  new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));

const fileOf = (entry: FileSystemFileEntry) => new Promise<File>((resolve, reject) => entry.file(resolve, reject));

/** A dropped folder, walked folder by folder; onCount hears the files found so far after each batch. */
export async function walkDrop(root: FileSystemDirectoryEntry, onCount?: (files: number) => void): Promise<Picked> {
  const pick = empty(nfc(root.name), true);
  async function walk(dir: FileSystemDirectoryEntry, path: string): Promise<void> {
    const reader = dir.createReader();
    for (let batch = await readBatch(reader); batch.length > 0; batch = await readBatch(reader)) {
      const files: { entry: FileSystemFileEntry; path: string }[] = [];
      const folders: { entry: FileSystemDirectoryEntry; path: string }[] = [];
      for (const entry of batch) {
        const at = `${path}/${nfc(entry.name)}`;
        if (isSkipped(entry.name)) skip(pick, at);
        else if (entry.isDirectory) folders.push({ entry: entry as FileSystemDirectoryEntry, path: at });
        else if (entry.isFile) files.push({ entry: entry as FileSystemFileEntry, path: at });
      }
      const handles = await Promise.all(files.map(({ entry }) => fileOf(entry)));
      handles.forEach((file, index) => {
        pick.files.push(file);
        pick.paths.push(files[index]!.path);
      });
      onCount?.(pick.files.length);
      for (const folder of folders) {
        pick.folders.push(folder.path);
        await walk(folder.entry, folder.path);
      }
    }
  }
  await walk(root, pick.rootName);
  return pick;
}

/**
 * A drop's folders and loose files. Called in the drop handler itself: the browser empties
 * the drop's items once the handler returns.
 */
export function droppedItems(items: DataTransferItemList): { folders: FileSystemDirectoryEntry[]; files: File[] } {
  const folders: FileSystemDirectoryEntry[] = [];
  const files: File[] = [];
  for (let index = 0; index < items.length; index++) {
    const item = items[index]!;
    if (item.kind !== "file") continue;
    const entry = item.webkitGetAsEntry();
    if (entry?.isDirectory) folders.push(entry as FileSystemDirectoryEntry);
    else {
      const file = item.getAsFile();
      if (file) files.push(file);
    }
  }
  return { folders, files };
}

export interface Manifest {
  folders: { name: string; parent: number | null }[]; // parents first; entry 0 is the root
  fileFolder: Int32Array; // per file of the pick: the index of its folder
  fileCount: number;
  totalBytes: number;
  height: number; // levels, the root counted
  badName: { name: string; problem: "long" | "hidden" } | null; // the first folder name Kaagaz can't keep
}

/**
 * The folders and each file's folder, from a pick. O(n + folders). Given the `earlier` manifest
 * of the same upload (a re-plan of the folder picked again), its folders come first, in the same
 * order, and new folders after: the server reuses each earlier index's folder.
 */
export function buildManifest(pick: Picked, earlier?: Manifest): Manifest {
  const folders: Manifest["folders"] = [];
  const depths: number[] = [];
  let height = 0;
  const index = new Map<string, number>();
  let badName: Manifest["badName"] = null;
  const folderOf = (path: string): number => {
    const known = index.get(path);
    if (known !== undefined) return known;
    const cut = path.lastIndexOf("/");
    const parent = cut === -1 ? null : folderOf(path.slice(0, cut));
    const name = cut === -1 ? path : path.slice(cut + 1);
    const problem = nameProblem(name);
    if (problem && !badName) badName = { name, problem };
    folders.push({ name, parent });
    depths.push(parent === null ? 1 : depths[parent]! + 1);
    height = Math.max(height, depths.at(-1)!);
    index.set(path, folders.length - 1);
    return folders.length - 1;
  };
  folderOf(pick.rootName);
  const earlierPaths: string[] = [];
  earlier?.folders.forEach(({ name, parent }, at) => {
    earlierPaths[at] = parent === null ? pick.rootName : `${earlierPaths[parent]}/${name}`;
    folderOf(earlierPaths[at]!);
  });
  for (const path of pick.folders) folderOf(path);
  const fileFolder = new Int32Array(pick.paths.length);
  let totalBytes = 0;
  pick.paths.forEach((path, at) => {
    fileFolder[at] = folderOf(path.slice(0, path.lastIndexOf("/")));
    totalBytes += pick.files[at]?.size ?? 0;
  });
  return { folders, fileFolder, fileCount: pick.paths.length, totalBytes, height, badName };
}

/** A file's id within its upload: the SHA-256 (hex) of its NFC path, root first. */
export async function pathKey(path: string): Promise<string> {
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(path.normalize("NFC"))));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}
