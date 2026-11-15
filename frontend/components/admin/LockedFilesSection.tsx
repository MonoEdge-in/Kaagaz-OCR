"use client";

// A person's Access page, "Locked files" (PRD 7 section 8): the locked files they may open
// (GET /admin/users/{id}/access, `locked`), each with Remove, which takes back their permission on
// every version (PR 12's DELETE /documents/{id}/lock-grants/{user}). [Change] on a department or
// private folder opens LockedFilesPicker for that place.

import { useState } from "react";
import { lockFailureText, lockedFileLabel } from "@/components/viewer/locked";
import { revokeLock } from "@/lib/api";
import type { LockedGrant } from "@/lib/types";

export function LockedFilesSection({
  userId,
  items,
  onChanged,
}: {
  userId: string;
  items: readonly LockedGrant[];
  onChanged(message: string): void; // "Access removed.": say it and load the page again
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function remove(id: string) {
    setBusy(id);
    setError(null);
    revokeLock(id, userId).then(
      () => {
        setBusy(null);
        onChanged("Access removed.");
      },
      (caught: unknown) => {
        setBusy(null);
        setError(lockFailureText(caught));
      },
    );
  }

  return (
    <section aria-labelledby="locked-files-heading">
      <h2 id="locked-files-heading">Locked files</h2>
      {items.length === 0 ? (
        <p className="muted">{"They can't open any locked file."}</p>
      ) : (
        <ul>
          {items.map((item) => (
            <li key={item.id}>
              {lockedFileLabel(item)}{" "}
              <button type="button" className="btn btn-quiet" onClick={() => remove(item.id)} disabled={busy !== null}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
