"use client";

// "5 files here are kept from you." (PRD 7 PR 11b, #604; p1:102): inside a folder, an Admin with an
// admin session sees how many of its files they can't open, and their names (GET .../kept-from-me,
// asked each time the folder opens, never kept). It shows nothing to anyone else, nothing while it
// loads, nothing on any failure and nothing for 0: never a "0", never a count to anyone but an
// Admin. Each name's menu: Give access, Who can see this, and Give myself access (a fresh sign-in).

import { useEffect, useRef, useState } from "react";
import { adminGate } from "@/components/admin/logic";
import { RowMenu } from "@/components/admin/RowMenu";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { formatDate, typeLabel } from "@/lib/format";
import { giveFile, keptFromMe } from "@/lib/api";
import type { DetectedType, KeptFromMe as Kept, Me } from "@/lib/types";
import { accessAbilities } from "./abilities";
import { AudiencePanel } from "./AudiencePanel";
import { GiveAccessDialog } from "./GiveAccessDialog";
import { useProofStep } from "./proofStep";
import {
  AUDIENCE_HELP,
  GIVE_HELP,
  GIVE_MYSELF,
  KEPT_HINT,
  MYSELF_HELP,
  SHOW_NAMES,
  WHO_CAN_SEE,
  accessErrorText,
  keptHeading,
  keptShowing,
  keptStripText,
  myselfToast,
} from "./words";
import styles from "./Access.module.css";

type KeptFile = Kept["items"][number];

export function KeptFromMe({
  folder,
  me,
  version,
  onChanged,
  onEmptied,
}: {
  folder: { id: string; name: string };
  me: Me;
  /** The screen's reload count: any access change there (Make private, a give) asks again. */
  version: number;
  /** Access given: the toast's words; the screen reads the list again (and `version` moves). */
  onChanged(message: string): void;
  /** The last kept file was given: the strip is gone, so focus goes to the place heading. */
  onEmptied?(): void;
}) {
  const admin = adminGate(me) === "allow";
  const [kept, setKept] = useState<{ folderId: string; reply: Kept } | null>(null);
  const [open, setOpen] = useState(false);
  const [acting, setActing] = useState<{ kind: "give" | "audience"; file: KeptFile } | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The last change's words, kept in the names list as well as the toast (which sits behind it).
  const [notice, setNotice] = useState("");
  const proofStep = useProofStep();

  useEffect(() => {
    if (!admin) return; // never asked for anyone else
    let live = true;
    keptFromMe(folder.id).then(
      (reply) => {
        if (!live) return;
        setKept({ folderId: folder.id, reply });
        if (reply.count === 0) setOpen(false); // never reopened by itself when files are kept again
      },
      () => live && setKept(null), // any failure: nothing at all
    );
    return () => {
      live = false;
    };
  }, [admin, folder.id, version]);

  const shown = admin && kept?.folderId === folder.id ? kept.reply : null;
  const count = shown?.count ?? null;
  const before = useRef<number | null>(null);
  useEffect(() => {
    // After the commit that took the names list away, so its dialog has handed focus back first.
    if (count === 0 && (before.current ?? 0) > 0) onEmptied?.();
    before.current = count;
  }, [count, onEmptied]);
  if (!shown || keptStripText(shown.count) === null) return null;

  const changed = (message: string) => {
    onChanged(message);
    setNotice(message);
  };
  async function giveMyself(file: KeptFile) {
    setError(null);
    try {
      const reply = await proofStep.run(() => giveFile(file.id, me.user.id));
      if (reply !== null) changed(myselfToast(file.name));
    } catch (caught) {
      setError(accessErrorText(caught));
    }
  }

  return (
    <>
      <KeptStripView count={shown.count} onShow={() => setOpen(true)} />
      {open && (
        <ActionDialog title={keptHeading(folder.name)} onClose={() => setOpen(false)} wide>
          <KeptNamesView
            kept={shown}
            error={error}
            notice={notice}
            onGive={(file) => setActing({ kind: "give", file })}
            onAudience={(file) => setActing({ kind: "audience", file })}
            onMyself={(file) => void giveMyself(file)}
          />
        </ActionDialog>
      )}
      {acting?.kind === "give" && (
        <GiveAccessDialog
          files={[{ id: acting.file.id, display_name: acting.file.name }]}
          onClose={() => setActing(null)}
          onGiven={(message) => {
            setActing(null);
            changed(message);
          }}
        />
      )}
      {acting?.kind === "audience" && (
        <AudiencePanel
          target={{ kind: "document", id: acting.file.id, name: acting.file.name }}
          myId={me.user.id}
          abilities={accessAbilities(me, { departmentId: null })}
          onClose={() => setActing(null)}
          onChanged={changed}
        />
      )}
    </>
  );
}

/** The strip, for the render tests: nothing for 0. */
export function KeptStripView({ count, onShow }: { count: number; onShow(): void }) {
  const text = keptStripText(count);
  if (text === null) return null;
  return (
    <p className={styles.kept}>
      {text}
      <button type="button" className="btn btn-quiet" aria-haspopup="dialog" onClick={onShow}>
        {SHOW_NAMES}
      </button>
    </p>
  );
}

/** The names list, for the render tests: Name, Kind, Uploaded by, Date, and each one's menu. */
export function KeptNamesView({
  kept,
  error,
  notice = "",
  onGive,
  onAudience,
  onMyself,
}: {
  kept: Kept;
  error: string | null;
  notice?: string;
  onGive(file: KeptFile): void;
  onAudience(file: KeptFile): void;
  onMyself(file: KeptFile): void;
}) {
  return (
    <div className="stack">
      <p className="hint">{KEPT_HINT}</p>
      {kept.items.length < kept.count && <p className="hint">{keptShowing(kept.items.length, kept.count)}</p>}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <p className="hint" role="status">
        {notice}
      </p>
      {/* No scroller of its own: the cells wrap, and the dialog's body scrolls. On a phone, Kind
          and Date go under the name (Access.module.css). */}
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Name</th>
            <th scope="col" className={styles.keptWide}>
              Kind
            </th>
            <th scope="col">Uploaded by</th>
            <th scope="col" className={styles.keptWide}>
              Date
            </th>
            <th scope="col">
              <span className="visually-hidden">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {kept.items.map((file) => (
            <tr key={file.id}>
              <td className={styles.keptName}>
                {file.name}
                <span className={styles.keptFacts}>{`${typeLabel(file.kind as DetectedType)} · ${formatDate(file.created_at)}`}</span>
              </td>
              <td className={styles.keptWide}>{typeLabel(file.kind as DetectedType)}</td>
              <td>{file.uploader ?? "—"}</td>
              <td className={styles.keptWide}>{formatDate(file.created_at)}</td>
              <td>
                <RowMenu
                  name={file.name}
                  compact
                  items={[
                    { label: "Give access", description: GIVE_HELP, onSelect: () => onGive(file) },
                    { label: WHO_CAN_SEE, description: AUDIENCE_HELP, onSelect: () => onAudience(file) },
                    { label: GIVE_MYSELF, description: MYSELF_HELP, onSelect: () => onMyself(file) },
                  ]}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
