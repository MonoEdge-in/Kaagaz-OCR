"use client";

// #698: a document in Trash, where its page would be. The server serves no file and no page
// picture of a family in Trash (409 in_trash, File management T1), so no viewer asks for them:
// this says why and, for who may restore it (can_delete, as Trash decides), offers Restore, the
// same call as the menu's (DocumentMenu.tsx); else the way to Trash. Restored, the screen loads
// the detail again and says it (onRestored).

import Link from "next/link";
import { useState } from "react";
import { placedMessage, restoredMessage, TRASH_PREVIEW_TEXT } from "@/components/documents/trash";
import { ApiError, restoreDocument } from "@/lib/api";
import type { DocumentDetail } from "@/lib/types";
import styles from "./DocumentView.module.css";

export function TrashPreview({
  detail,
  onRestored,
}: {
  detail: Pick<DocumentDetail, "id" | "display_name" | "can_delete">;
  onRestored(message: string): void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function restore() {
    setBusy(true);
    setError(null);
    restoreDocument(detail.id).then(
      ({ placed }) => onRestored([restoredMessage(detail.display_name), placed && placedMessage(placed)].filter(Boolean).join(" ")),
      (caught: unknown) => {
        setBusy(false);
        setError(caught instanceof ApiError ? caught.message : "Check your internet connection, then try again.");
      },
    );
  }

  return (
    <div className={styles.trashPreview}>
      <p>{TRASH_PREVIEW_TEXT.why}</p>
      <p>{TRASH_PREVIEW_TEXT.fix}</p>
      {detail.can_delete ? (
        <button type="button" className="btn btn-primary" onClick={restore} disabled={busy}>
          Restore
        </button>
      ) : (
        <Link href="/documents?trash=1">Open Trash</Link>
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
