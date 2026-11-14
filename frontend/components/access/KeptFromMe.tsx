"use client";

// "5 files here are kept from you." (PRD 7 PR 11b, #604; p1:102): inside a folder, an Admin with an
// admin session sees how many of its files they can't open, and their names (GET .../kept-from-me,
// asked each time the folder opens, never kept). It shows nothing to anyone else, nothing while it
// loads, nothing on any failure and nothing for 0: never a "0", never a count to anyone but an
// Admin. Each name's menu: Give access, Who can see this, and Give myself access (my password).

import { useEffect, useState } from "react";
import { adminGate } from "@/components/admin/logic";
import { RowMenu } from "@/components/admin/RowMenu";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { formatDate, typeLabel } from "@/lib/format";
import { giveFile, keptFromMe } from "@/lib/api";
import type { DetectedType, KeptFromMe as Kept, Me } from "@/lib/types";
import { accessAbilities } from "./abilities";
import { AudiencePanel } from "./AudiencePanel";
import { GiveAccessDialog } from "./GiveAccessDialog";
import { usePasswordStep } from "./SelfPasswordDialog";
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
  quoted,
} from "./words";
import styles from "./Access.module.css";

type KeptFile = Kept["items"][number];

export function KeptFromMe({
  folder,
  me,
  onChanged,
}: {
  folder: { id: string; name: string };
  me: Me;
  /** Access given: the toast's words; the screen reads the list again. */
  onChanged(message: string): void;
}) {
  const admin = adminGate(me) === "allow";
  const [kept, setKept] = useState<{ folderId: string; reply: Kept } | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [open, setOpen] = useState(false);
  const [acting, setActing] = useState<{ kind: "give" | "audience"; file: KeptFile } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const passwordStep = usePasswordStep();

  useEffect(() => {
    if (!admin) return; // never asked for anyone else
    let live = true;
    keptFromMe(folder.id).then(
      (reply) => live && setKept({ folderId: folder.id, reply }),
      () => live && setKept(null), // any failure: nothing at all
    );
    return () => {
      live = false;
    };
  }, [admin, folder.id, attempt]);

  const shown = admin && kept?.folderId === folder.id ? kept.reply : null;
  if (!shown || keptStripText(shown.count) === null) return null;

  const changed = (message: string) => {
    onChanged(message);
    setAttempt((n) => n + 1);
  };
  async function giveMyself(file: KeptFile) {
    setError(null);
    try {
      const reply = await passwordStep.run(quoted(file.name), (password) => giveFile(file.id, me.user.id, password), true);
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
            onGive={(file) => setActing({ kind: "give", file })}
            onAudience={(file) => setActing({ kind: "audience", file })}
            onMyself={(file) => void giveMyself(file)}
          />
        </ActionDialog>
      )}
      {acting?.kind === "give" && (
        <GiveAccessDialog
          files={[{ id: acting.file.id, display_name: acting.file.name }]}
          myId={me.user.id}
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
      {passwordStep.dialog}
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
  onGive,
  onAudience,
  onMyself,
}: {
  kept: Kept;
  error: string | null;
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
      <div className={styles.tableWrap}>
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Kind</th>
              <th scope="col">Uploaded by</th>
              <th scope="col">Date</th>
              <th scope="col">
                <span className="visually-hidden">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {kept.items.map((file) => (
              <tr key={file.id}>
                <td>{file.name}</td>
                <td>{typeLabel(file.kind as DetectedType)}</td>
                <td>{file.uploader ?? "—"}</td>
                <td>{formatDate(file.created_at)}</td>
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
    </div>
  );
}
