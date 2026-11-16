"use client";

// Possible versions (#648, V17, V18; the lead's flexible group, 16 Nov 2026), for Admins and
// Management only: anyone else sees one plain line and nothing is asked of the server.
// Each group the version finder made shows its files in its order (oldest first), each with a
// tick (all ticked to start), why it is there in plain words ("94% same text as X · same name")
// and a link to open it; any two of them can be compared side by side. "Link as versions" opens
// the link dialog at its order step with the ticked files; "None of these are versions" keeps
// the whole group apart, with Undo. After a link, each unticked file that is no longer in a
// smaller group asks "Not a version of these?" or "Decide later". The server shows a group only
// when the person can see every file in it, and only the files it sends are named.
// Below, Kept apart (V18): pairs someone kept separate, each linked again from here, and files
// marked "Not versions", each with Undo.

import Link from "next/link";
import { useEffect, useId, useState } from "react";
import { EmptyState } from "@/components/ui/EmptyState";
import { Icon } from "@/components/ui/Icon";
import { Toast } from "@/components/ui/Toast";
import { decideVersionGroup, getDocumentSummary, getVersionGroup, listKeptApart, listVersionGroups } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useSession } from "@/lib/session";
import type { KeptApart, VersionGroup } from "@/lib/types";
import { LinkDialog } from "./LinkDialog";
import type { Linked } from "./LinkDialog";
import {
  LINK_TEXT,
  MAX_LINK,
  MIN_LINK,
  byDate,
  fromSummary,
  groupLink,
  linkErrorText,
  mayLinkVersions,
  reasonLine,
  shownIn,
  withAsked,
  withoutAsked,
} from "./link";
import type { Asked, LinkFile } from "./link";
import { compareHref } from "./VersionChoice";
import styles from "./Link.module.css";

interface Named {
  id: string;
  name: string;
}

interface Linking {
  files: LinkFile[];
  latest: string | null;
  groupId?: string;
  unticked: Named[];
}

export function PossibleVersions() {
  const { me } = useSession();
  const allowed = mayLinkVersions(me);
  const [groups, setGroups] = useState<{ items: VersionGroup[]; more: boolean } | null>(null);
  const [kept, setKept] = useState<KeptApart | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [toast, setToast] = useState<{ message: string; undo?: () => Promise<unknown>; undone?: string } | null>(null);
  const [linking, setLinking] = useState<Linking | null>(null);
  const [asking, setAsking] = useState<Asked[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!allowed) return;
    let cancelled = false;
    Promise.all([listVersionGroups(), listKeptApart()]).then(
      ([found, apart]) => {
        if (cancelled) return;
        setGroups(found);
        setKept(apart);
        setFailed(null);
      },
      (caught: unknown) => {
        if (!cancelled) setFailed(linkErrorText(caught));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [allowed, reload]);

  if (!allowed) {
    return (
      <div className="page">
        <EmptyState icon="alert" title={LINK_TEXT.possible} headingLevel={1}>
          {LINK_TEXT.onlyAdmins}
        </EmptyState>
      </div>
    );
  }

  /** One decision, said in the toast; the lists are asked for again. */
  async function act(work: () => Promise<unknown>, said: string, undo?: () => Promise<unknown>, undone?: string) {
    setBusy(true);
    setError(null);
    try {
      await work();
      setToast({ message: said, undo, undone });
      setReload((n) => n + 1);
    } catch (caught) {
      setError(linkErrorText(caught));
    } finally {
      setBusy(false);
    }
  }

  function noneAre(group: VersionGroup) {
    const ids = group.files.map((file) => file.id);
    void act(
      () => decideVersionGroup(group.id, "keep-apart", ids),
      LINK_TEXT.keptApart(ids.length),
      () => decideVersionGroup(group.id, "keep-apart/undo", ids),
      LINK_TEXT.notMarked(ids.length),
    );
  }

  /** Undo of Not versions for one file. A file no group shows afterwards (its matches were all
   * linked) is asked about again, so it is never left on no screen (reviews of #659). */
  async function undoNotVersion(groupId: string, file: Named) {
    const { group } = await decideVersionGroup(groupId, "keep-apart/undo", [file.id]);
    if (!shownIn(group, file.id)) setAsking((before) => withAsked(before, [{ groupId, file }]));
  }

  async function linkPair(pair: KeptApart["pairs"][number]) {
    setError(null);
    try {
      const both = await Promise.all([getDocumentSummary(pair.document.id), getDocumentSummary(pair.other.id)]);
      const files = byDate(both.map(fromSummary));
      setLinking({ files, latest: files.at(-1)?.id ?? null, unticked: [] });
    } catch (caught) {
      setError(linkErrorText(caught));
    }
  }

  async function linked(done: Linked) {
    const from = linking;
    setLinking(null);
    const unticked = from?.unticked.map((file) => file.id) ?? [];
    // Undo of the link puts the files back in their group: the question about them goes.
    const undo = async () => {
      await done.undo();
      setAsking((before) => withoutAsked(before, unticked));
    };
    setToast({ message: done.message, undo, undone: LINK_TEXT.undone });
    setReload((n) => n + 1);
    const groupId = from?.groupId;
    if (!groupId || unticked.length === 0) return;
    // Unticked files that still match each other stay as a smaller group; the others are asked about.
    let still = new Set<string>();
    try {
      still = new Set((await getVersionGroup(groupId)).files.map((file) => file.id));
    } catch {
      // 404: the group is gone, so every unticked file is asked about.
    }
    const left = (from?.unticked ?? []).filter((file) => !still.has(file.id));
    setAsking((before) => withAsked(before, left.map((file) => ({ groupId, file }))));
  }

  function answer(asked: Asked, decision: "keep-apart" | "later") {
    const { groupId, file } = asked;
    void act(
      async () => {
        await decideVersionGroup(groupId, decision, [file.id]);
        setAsking((before) => withoutAsked(before, [file.id]));
      },
      decision === "later" ? `${LINK_TEXT.laterNote}.` : LINK_TEXT.keptApart(1),
      decision === "later" ? undefined : () => undoNotVersion(groupId, file),
      LINK_TEXT.notMarked(1),
    );
  }

  function undoToast(undo: () => Promise<unknown>, undone: string) {
    setError(null);
    undo().then(
      () => {
        setToast({ message: undone });
        setReload((n) => n + 1);
      },
      (caught: unknown) => {
        setError(linkErrorText(caught));
        setReload((n) => n + 1);
      },
    );
  }

  const items = groups?.items ?? [];
  const toastUndo = toast?.undo;
  return (
    <div className="page">
      <Link href="/documents" className={styles.back}>
        <Icon name="chevron-left" />
        Back to Documents
      </Link>
      <header className="page-header">
        <div>
          <h1>{LINK_TEXT.possible}</h1>
          <p>{LINK_TEXT.possibleIntro}</p>
        </div>
      </header>

      {failed && (
        <div className={styles.problem} role="alert">
          <p>
            {LINK_TEXT.loadFailed} {failed}
          </p>
          <button type="button" className="btn" onClick={() => setReload((n) => n + 1)}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      {groups === null && !failed && (
        <p className="muted delayed" role="status">
          {LINK_TEXT.loading}
        </p>
      )}

      {asking.length > 0 && (
        <section className={styles.asking} aria-label={LINK_TEXT.notLinkedHeading}>
          <ul className={styles.fileList}>
            {asking.map((asked) => (
              <li key={asked.file.id} className={styles.askRow}>
                <p className={styles.fileName}>{LINK_TEXT.notLinked(asked.file.name)}</p>
                <div className={styles.groupButtons}>
                  <button type="button" className="btn" disabled={busy} onClick={() => answer(asked, "keep-apart")}>
                    {LINK_TEXT.notVersion}
                  </button>
                  <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => answer(asked, "later")}>
                    {LINK_TEXT.decideLater}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      {groups !== null && items.length === 0 && (
        <EmptyState icon="file" title={LINK_TEXT.noGroups} headingLevel={2}>
          {LINK_TEXT.noGroupsText}
        </EmptyState>
      )}
      <div className={styles.groups}>
        {items.map((group) => (
          <GroupCard
            key={`${group.id}:${group.files.map((file) => file.id).join(",")}`}
            group={group}
            busy={busy}
            onLink={(files, latest, unticked) => setLinking({ files, latest, groupId: group.id, unticked })}
            onNoneAre={() => noneAre(group)}
          />
        ))}
      </div>
      {groups?.more && <p className="muted">{LINK_TEXT.more}</p>}

      {kept && (
        <KeptApartList
          kept={kept}
          busy={busy}
          onLink={(pair) => void linkPair(pair)}
          onUndo={(file) =>
            void act(() => undoNotVersion(file.group_id, { id: file.id, name: file.name }), LINK_TEXT.notMarked(1))
          }
        />
      )}

      <Toast
        message={toast?.message ?? null}
        onUndo={toastUndo ? () => undoToast(toastUndo, toast?.undone ?? LINK_TEXT.undone) : undefined}
        onClose={() => setToast(null)}
      />
      {linking && (
        <LinkDialog
          files={linking.files}
          start="order"
          latest={linking.latest}
          onClose={() => setLinking(null)}
          onLinked={(done) => void linked(done)}
        />
      )}
    </div>
  );
}

/** One group: its files with ticks and reasons, Compare, Link as versions and None of these are
 * versions. Exported for the render test. */
export function GroupCard({
  group,
  busy,
  onLink,
  onNoneAre,
}: {
  group: VersionGroup;
  busy: boolean;
  onLink(files: LinkFile[], latest: string | null, unticked: Named[]): void;
  onNoneAre(): void;
}) {
  const id = useId();
  const files = [...group.files].sort((a, b) => a.position - b.position);
  const [ticked, setTicked] = useState<ReadonlySet<string>>(() => new Set(files.map((file) => file.id)));
  const [left, setLeft] = useState(files[0]?.id ?? "");
  const [right, setRight] = useState(files.at(-1)?.id ?? "");
  const names = new Map(files.map((file) => [file.id, file.name]));
  const nameOf = (other: string) => names.get(other) ?? "another file";
  const chosen = files.filter((file) => ticked.has(file.id));
  const linkable = chosen.length >= MIN_LINK && chosen.length <= MAX_LINK;

  function toggle(file: string) {
    setTicked((before) => {
      const next = new Set(before);
      if (next.has(file)) next.delete(file);
      else next.add(file);
      return next;
    });
  }

  return (
    <article className={styles.group} aria-labelledby={`${id}-heading`}>
      <h2 id={`${id}-heading`} className={styles.groupHeading}>
        {LINK_TEXT.groupHeading(files.length)}
      </h2>
      <ul className={styles.fileList}>
        {files.map((file, at) => (
          <li key={file.id} className={styles.groupFile}>
            <input
              id={`${id}-${at}`}
              type="checkbox"
              className={styles.tick}
              checked={ticked.has(file.id)}
              onChange={() => toggle(file.id)}
              aria-describedby={`${id}-${at}-why`}
            />
            <span className={styles.fileWords}>
              <label htmlFor={`${id}-${at}`} className={styles.fileName}>
                {file.name}
              </label>
              <span className={styles.fileFacts}>
                {[
                  formatDate(file.document_date ?? file.uploaded_at),
                  file.page_count ? `${file.page_count} ${file.page_count === 1 ? "page" : "pages"}` : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
                {file.state === "later" && (
                  <>
                    {" "}
                    <span className="status-chip" data-tone="neutral">
                      {LINK_TEXT.laterNote}
                    </span>
                  </>
                )}
              </span>
              <span id={`${id}-${at}-why`} className={styles.reason}>
                <span className="visually-hidden">{`${LINK_TEXT.why}: `}</span>
                {reasonLine(file.reasons, nameOf)}
              </span>
            </span>
            <Link href={`/documents/${encodeURIComponent(file.id)}`} className="btn btn-quiet">
              Open<span className="visually-hidden">{` ${file.name}`}</span>
            </Link>
          </li>
        ))}
      </ul>
      {files.length > 1 && (
        <div className={styles.compare}>
          <label htmlFor={`${id}-left`}>{LINK_TEXT.compareFirst}</label>
          <span className="k-select-wrap">
            <select id={`${id}-left`} className="k-select" value={left} onChange={(event) => setLeft(event.target.value)}>
              {files.map((file) => (
                <option key={file.id} value={file.id}>
                  {file.name}
                </option>
              ))}
            </select>
          </span>
          <label htmlFor={`${id}-right`}>{LINK_TEXT.compareWith}</label>
          <span className="k-select-wrap">
            <select id={`${id}-right`} className="k-select" value={right} onChange={(event) => setRight(event.target.value)}>
              {files.map((file) => (
                <option key={file.id} value={file.id}>
                  {file.name}
                </option>
              ))}
            </select>
          </span>
          {left !== right ? (
            <Link className="btn" href={compareHref(right, left)}>
              {LINK_TEXT.compare}
            </Link>
          ) : (
            <span className="muted">{LINK_TEXT.pickTwo}</span>
          )}
        </div>
      )}
      <div className={styles.groupButtons}>
        <button
          type="button"
          className="btn btn-primary"
          disabled={busy || !linkable}
          onClick={() => {
            const link = groupLink(files, ticked, group.suggested_latest_id);
            onLink(link.files, link.latest, link.unticked);
          }}
        >
          {LINK_TEXT.bulk(chosen.length)}
        </button>
        <button type="button" className="btn" disabled={busy} onClick={onNoneAre}>
          {LINK_TEXT.noneAre}
        </button>
      </div>
    </article>
  );
}

/** V18: pairs kept separate (Link as versions) and files marked not versions (Undo). Exported for the render test. */
export function KeptApartList({
  kept,
  busy,
  onLink,
  onUndo,
}: {
  kept: KeptApart;
  busy: boolean;
  onLink(pair: KeptApart["pairs"][number]): void;
  onUndo(file: KeptApart["groups"][number]): void;
}) {
  const empty = kept.pairs.length === 0 && kept.groups.length === 0;
  return (
    <section className={styles.kept} aria-labelledby="kept-apart-heading">
      <h2 id="kept-apart-heading" className={styles.groupHeading}>
        {LINK_TEXT.keptApartHeading}
      </h2>
      <p className="muted">{empty ? LINK_TEXT.noKeptApart : LINK_TEXT.keptApartIntro}</p>
      {!empty && (
        <ul className={styles.fileList}>
          {kept.pairs.map((pair) => (
            <li key={pair.id} className={styles.fileRow}>
              <span className={styles.fileWords}>
                <span className={styles.fileName}>{LINK_TEXT.pair(pair.document.name, pair.other.name)}</span>
                <span className={styles.fileFacts}>
                  {pair.separated ? LINK_TEXT.separated : LINK_TEXT.keptSeparate}
                  {pair.decided_at ? ` · ${formatDate(pair.decided_at)}` : ""}
                </span>
              </span>
              <button type="button" className="btn" disabled={busy} onClick={() => onLink(pair)}>
                {LINK_TEXT.link}
                <span className="visually-hidden">{`: ${LINK_TEXT.pair(pair.document.name, pair.other.name)}`}</span>
              </button>
            </li>
          ))}
          {kept.groups.map((file) => (
            <li key={`${file.group_id}:${file.id}`} className={styles.fileRow}>
              <span className={styles.fileWords}>
                <span className={styles.fileName}>{file.name}</span>
                <span className={styles.fileFacts}>{LINK_TEXT.notVersions}</span>
              </span>
              <button type="button" className="btn" disabled={busy} onClick={() => onUndo(file)}>
                {LINK_TEXT.undo}
                <span className="visually-hidden">{`: ${file.name}`}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
