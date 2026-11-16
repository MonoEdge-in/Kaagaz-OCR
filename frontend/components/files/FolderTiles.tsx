"use client";

// Inside a folder, its subfolders first, as tiles with their file counts (File management
// F5). Each count is the folder's documents with its subfolders, from the list's own total
// (one small request a tile). ponytail: one request per subfolder; a count in GET /folders
// when folders hold many subfolders.

import Link from "next/link";
import { useEffect, useState } from "react";
import { PlaceIcon } from "@/components/access/PlaceBadge";
import type { FolderNode } from "@/components/documents/folderTree";
import { libraryHref } from "@/components/documents/libraryQuery";
import type { LibraryState } from "@/components/documents/libraryQuery";
import { Icon } from "@/components/ui/Icon";
import { listDocuments } from "@/lib/api";
import { FILES_TEXT, tileCount } from "./words";
import styles from "./Files.module.css";

export function FolderTiles({
  folders,
  library,
  counts,
}: {
  folders: readonly FolderNode[];
  library: LibraryState;
  counts?: Readonly<Record<string, number>>; // given by the render test; counted otherwise
}) {
  const [counted, setCounted] = useState<Record<string, number>>({});
  const ids = folders.map((node) => node.folder.id).join(",");
  useEffect(() => {
    if (counts || ids === "") return;
    let cancelled = false;
    for (const id of ids.split(",")) {
      listDocuments({ folder_id: id, include_subfolders: true, limit: 1 }).then(
        (list) => {
          if (!cancelled) setCounted((before) => ({ ...before, [id]: list.total }));
        },
        () => undefined, // the tile still opens the folder; its count stays "…"
      );
    }
    return () => {
      cancelled = true;
    };
  }, [ids, counts]);
  if (folders.length === 0) return null;
  const shown = counts ?? counted;
  return (
    <section aria-labelledby="subfolders-heading">
      <h3 id="subfolders-heading" className="visually-hidden">
        {FILES_TEXT.subfolders}
      </h3>
      <ul className={styles.tiles}>
        {folders.map((node) => (
          <li key={node.folder.id}>
            <Link
              href={libraryHref({ ...library, folderId: node.folder.id, trash: false })}
              scroll={false}
              className={styles.tile}
            >
              <Icon name="documents" className={styles.tileIcon} />
              <span className={styles.tileName}>
                {node.folder.name} <PlaceIcon folder={node.folder} />
              </span>
              <span className={`${styles.tileCount} tabular`}>{tileCount(shown[node.folder.id])}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
