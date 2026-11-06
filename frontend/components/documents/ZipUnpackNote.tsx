"use client";

// #522: a zip the server unpacked into a folder, in the upload tray (UploadArea.tsx): the
// server's line, its folder once nothing waits, and each file it skipped with why (the first the
// server lists, then how many more). The words are the server's (zipUnpack.ts).

import Link from "next/link";
import { ALL_DOCUMENTS, libraryHref } from "./libraryQuery";
import { WrapName } from "./WrapName";
import { moreSkippedText } from "./zipUnpack";
import type { ZipNote } from "./zipUnpack";
import styles from "./Documents.module.css";

export function ZipUnpackNote({ note }: { note: ZipNote }) {
  const more = moreSkippedText(note.more);
  return (
    <>
      <p className={styles.small}>
        {note.line}
        {note.done && note.folderId && (
          <>
            {" "}
            <Link href={libraryHref({ ...ALL_DOCUMENTS, folderId: note.folderId })}>Open the folder</Link>
          </>
        )}
      </p>
      {note.skipped.length > 0 && (
        <ul className={styles.small} aria-label="Files not unpacked">
          {note.skipped.map((file, index) => (
            <li key={`${index}-${file.name}`}>
              <WrapName name={file.name} />: {file.reason}
            </li>
          ))}
          {more && <li className="muted">{more}</li>}
        </ul>
      )}
    </>
  );
}
