"use client";

// /words: my own words and what they mean ("malak" means owner), only mine (the
// server sees to it). Add at the top; the list is filtered in the browser (at
// most 500); each meaning can be changed or deleted (asked first). An add or a
// change that meets another meaning asks first (DuplicateDialog). Results go to
// one polite live region; problems are alerts by the form or in the dialog.
// UI audit (16 Oct 2026): Add waits until both boxes are filled, and Enter adds (W1);
// the search shows from SEARCH_FROM words, and Download CSV saves the list (W2).

import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import {
  FEATURE_OFF, addMyWord, changeMyWord, decideOrganizationWord, deleteMyWord, isFeatureOff,
  listMyWords, listOrganizationWords, suggestOrganizationWord,
} from "@/lib/api";
import { useSession } from "@/lib/session";
import type { MyWord, OrganizationWord } from "@/lib/types";
import { DuplicateDialog, WordFields } from "./WordParts";
import {
  addBody,
  changeBody,
  draftFilled,
  draftOk,
  failedState,
  limitNote,
  retryBody,
  savedState,
  SEARCH_FROM,
  SOURCE_TEXT,
  textProblem,
  wordGroups,
  wordsCsv,
} from "./words";
import type { ChangeBody, Choice, Existing, NewWordBody, WordDraft } from "./words";
import styles from "./Words.module.css";

const EMPTY: WordDraft = { word: "", meaning: "" };
type RowDialog = { mode: "change" | "delete"; item: MyWord; draft: WordDraft; error: string | null };
// One save: an add (POST), or a change (PATCH of wordId). draft is what the question names.
type Job = { draft: WordDraft } & ({ body: NewWordBody; wordId?: undefined } | { body: ChangeBody; wordId: string });

/** W2: the list as a file the browser saves; nothing leaves the device. */
function downloadCsv(items: MyWord[]) {
  const url = URL.createObjectURL(new Blob([wordsCsv(items)], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = "my-words.csv";
  link.click();
  URL.revokeObjectURL(url);
}

/** After a refused draft, focus the field to fix (once its message has rendered). */
const focusProblem = (idPrefix: string, draft: WordDraft) =>
  requestAnimationFrame(() => document.getElementById(`${idPrefix}-${textProblem(draft.word, "word") ? "word" : "meaning"}`)?.focus());

export function MyWordsScreen() {
  const { me } = useSession();
  const userId = me.user.id;
  const headingRef = useRef<HTMLHeadingElement>(null);
  const deleted = useRef(false); // then focus goes to the heading, as the row is gone
  const [attempt, setAttempt] = useState(0);
  const [items, setItems] = useState<MyWord[] | null>(null);
  const [organizationItems, setOrganizationItems] = useState<OrganizationWord[]>([]);
  const [organizationDraft, setOrganizationDraft] = useState(EMPTY);
  const [organizationBusy, setOrganizationBusy] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [off, setOff] = useState(false); // My words switched off for the whole product
  const [filter, setFilter] = useState("");
  const [draft, setDraft] = useState(EMPTY);
  const [tried, setTried] = useState(false);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [asking, setAsking] = useState<(Job & { existing: Existing[] }) | null>(null);
  const [row, setRow] = useState<RowDialog | null>(null);
  const [status, setStatus] = useState("");

  useEffect(() => {
    let cancelled = false;
    listMyWords().then(
      (result) => !cancelled && setItems(result.items),
      (caught: unknown) => !cancelled && (isFeatureOff(caught) ? setOff(true) : setLoadError(true)),
    );
    listOrganizationWords().then(
      (result) => !cancelled && setOrganizationItems(result.items),
      () => !cancelled && setOrganizationItems([]),
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  /** Busy while it runs; a refusal goes to onRefused in the server's words, the 409 question to the dialog. */
  async function run(work: () => Promise<void>, onRefused: (message: string) => void, job?: Job) {
    setBusy(true);
    try {
      await work();
    } catch (caught) {
      const state = failedState(caught);
      if (state.kind === "ask" && job) setAsking({ ...job, existing: state.existing });
      else onRefused(state.kind === "refused" ? state.message : "Not saved.");
    } finally {
      setBusy(false);
    }
  }

  const save = (job: Job, onRefused: (message: string) => void) =>
    run(
      async () => {
        const reply = job.wordId === undefined ? await addMyWord(job.body) : await changeMyWord(job.wordId, job.body);
        const { id } = reply.word;
        setItems((now) => [...(now ?? []).filter((it) => it.id !== id && it.id !== job.body.replace_id), reply.word]);
        if (job.body.replace_id) setAttempt((n) => n + 1); // and ask the server which rows a replace left
        setStatus(savedState(reply).message);
        if (job.wordId === undefined) {
          setDraft(EMPTY);
          setTried(false);
        }
      },
      onRefused,
      job,
    );

  function submitAdd(event: FormEvent) {
    event.preventDefault();
    setTried(true);
    setProblem(null);
    const body = addBody(draft, userId);
    if (!draftOk(draft)) focusProblem("add", draft);
    else if (!busy) void save({ body, draft: body }, setProblem);
  }

  async function submitOrganizationWord(event: FormEvent) {
    event.preventDefault();
    if (!draftOk(organizationDraft) || organizationBusy) return;
    setOrganizationBusy(true);
    try {
      const { item } = await suggestOrganizationWord(organizationDraft.word, organizationDraft.meaning, userId);
      setOrganizationItems((current) => [...current, item]);
      setOrganizationDraft(EMPTY);
      setStatus("Suggestion sent to your organization for review.");
    } catch {
      setStatus("This suggestion could not be saved. It may already be pending or approved.");
    } finally {
      setOrganizationBusy(false);
    }
  }

  async function decideOrganization(item: OrganizationWord, decision: "approve" | "reject") {
    try {
      const { item: decided } = await decideOrganizationWord(item.id, decision, userId);
      setOrganizationItems((current) => current.map((row) => row.id === item.id ? decided : row));
      setStatus(`Suggestion ${decision === "approve" ? "approved" : "rejected"}.`);
    } catch {
      setStatus("This suggestion could not be updated. Reload and try again.");
    }
  }

  function choose(choice: Choice) {
    if (!asking) return;
    setAsking(null);
    const body = retryBody(asking.body, choice);
    if (body) void save({ ...asking, body } as Job, setProblem);
    else setStatus("Not saved.");
  }

  function submitRow(event: FormEvent) {
    event.preventDefault();
    if (!row || busy) return;
    const { item } = row;
    const fail = (error: string) => setRow({ ...row, error });
    if (row.mode === "delete") {
      void run(async () => {
        await deleteMyWord(item.id, userId);
        setItems((now) => (now ?? []).filter((it) => it.id !== item.id));
        setStatus(`Deleted '${item.word}' as '${item.meaning}'.`);
        deleted.current = true;
        setRow(null);
      }, fail);
      return;
    }
    if (!draftOk(row.draft)) {
      focusProblem("edit", row.draft);
      return;
    }
    const body = changeBody(item, row.draft, userId);
    setRow(null); // a question, if one comes, takes its place; a refusal opens it again
    if (body) void save({ body, wordId: item.id, draft: { word: body.word ?? item.word, meaning: body.meaning ?? item.meaning } }, fail);
  }

  // With the search hidden (SEARCH_FROM words or fewer), a search typed earlier no longer filters.
  const groups = items ? wordGroups(items, items.length > SEARCH_FROM ? filter : "") : [];
  const note = items ? limitNote(items.length) : null;
  const spinner = busy && <span className="spinner" aria-hidden="true" />;
  const busyClass = busy ? styles.busy : "";
  const alert = (text: string | null) => text && <p className="error-text" role="alert">{text}</p>;

  if (off) {
    return (
      <div className="page">
        <header className="page-header"><h1>My words</h1></header>
        <EmptyState icon="words" title={FEATURE_OFF} />
      </div>
    );
  }

  return (
    <div className="page">
      <header className="page-header"><div>
        <h1 ref={headingRef} tabIndex={-1}>My words</h1>
        <p>Your words stay private. You can also suggest words for your organization; a reviewer or admin must approve them before everyone can use them.</p>
      </div></header>
      <p className="visually-hidden" role="status" aria-live="polite">{status}</p>
      {alert(problem)}

      <form className={`card stack ${styles.block}`} onSubmit={submitAdd} noValidate aria-labelledby="add-word-heading">
        <h2 id="add-word-heading" className={styles.subheading}>Add a word</h2>
        <div className={styles.fields}>
          <WordFields idPrefix="add" draft={draft} onChange={setDraft} checked={tried} />
          <button
            type="submit"
            className={`btn btn-primary ${styles.target} ${busyClass}`}
            disabled={!draftFilled(draft)}
            aria-disabled={busy || undefined}
            aria-describedby={draftFilled(draft) ? undefined : "add-word-why"}
          >
            {spinner}Add
          </button>
        </div>
        {!draftFilled(draft) && (
          <p id="add-word-why" className="hint">
            Type a word and its meaning to add it.
          </p>
        )}
        {note && <p className="hint">{note}</p>}
      </form>

      <form className={`card stack ${styles.block}`} onSubmit={submitOrganizationWord} noValidate aria-labelledby="suggest-word-heading">
        <h2 id="suggest-word-heading" className={styles.subheading}>Suggest an organization word</h2>
        <div className={styles.fields}>
          <WordFields idPrefix="organization" draft={organizationDraft} onChange={setOrganizationDraft} checked={false} />
          <button type="submit" className={`btn ${styles.target}`} disabled={!draftFilled(organizationDraft) || organizationBusy}>
            {organizationBusy ? <span className="spinner" aria-hidden="true" /> : null}Suggest
          </button>
        </div>
        <p className="hint">Approved words are shared with everyone in your organization.</p>
      </form>

      {organizationItems.length > 0 && (
        <section aria-labelledby="organization-words-heading" className={`stack ${styles.block}`}>
          <h2 id="organization-words-heading" className={styles.subheading}>Organization words and suggestions</h2>
          <ul className={styles.list}>
            {organizationItems.map((item) => (
              <li key={item.id} className={styles.group}>
                <span className={styles.word}>{item.word}</span><span className={styles.text}>→ {item.meaning}</span>
                <span className="muted">{item.status === "pending" ? "Awaiting review" : item.status}</span>
                {item.status === "pending" && (me.user.role === "reviewer" || me.user.role === "admin") && item.submitted_by !== userId && (
                  <>
                    <button type="button" className="btn btn-quiet" onClick={() => void decideOrganization(item, "approve")}>Approve</button>
                    <button type="button" className="btn btn-quiet" onClick={() => void decideOrganization(item, "reject")}>Reject</button>
                  </>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {items === null && !loadError && <p className="muted">Loading your words…</p>}
      {loadError && (
        <div role="alert" className={styles.fields}>
          <p>Your words could not be loaded. Check your internet connection, then try again.</p>
          <button type="button" className={`btn ${styles.target}`} onClick={() => { setLoadError(false); setAttempt((n) => n + 1); }}>Try again</button>
        </div>
      )}
      {items?.length === 0 && (
        <EmptyState icon="words" title="No words yet">
          <p>
            Add a word the way you type it, and its meaning in English. For example: <b>malak</b> → <b>owner</b>.
            Then &quot;malak kon ahe?&quot; also finds the owner.
          </p>
        </EmptyState>
      )}

      {items !== null && items.length > 0 && (
        <section aria-labelledby="words-heading" className={`stack ${styles.block}`}>
          <div className={styles.fields}>
            <h2 id="words-heading" className={styles.subheading}>Your words ({items.length})</h2>
            {items.length > SEARCH_FROM && (
              <div className={`field ${styles.field}`}>
                <label htmlFor="words-filter">Search your words</label>
                <input id="words-filter" type="search" className="input" value={filter} autoComplete="off" onChange={(event) => setFilter(event.target.value)} />
              </div>
            )}
            <button type="button" className={`btn ${styles.target}`} onClick={() => downloadCsv(items)}>
              Download CSV
            </button>
          </div>
          <p className="muted" role="status">{groups.length === 0 ? "No words match this search." : ""}</p>
          <ul className={styles.list}>
            {groups.map((group) => (
              <li key={group.key} className={styles.group}>
                <span className={styles.word}>{group.word}</span>
                <ul className={styles.list}>
                  {group.meanings.map((item) => (
                    <li key={item.id} className="row">
                      <span className={styles.text}>
                        <span aria-hidden="true">→ </span><span className="visually-hidden">means </span>{item.meaning}
                      </span>
                      <span className="muted">{SOURCE_TEXT[item.source]}</span>
                      {item.built_in && <span className="badge badge-review">Chat also knows this word</span>}
                      {(["Change", "Delete"] as const).map((verb) => (
                        <button key={verb} type="button" className={`btn btn-quiet ${styles.target}`} aria-label={`${verb} '${item.word}' as '${item.meaning}'`}
                          onClick={() => setRow({ mode: verb === "Change" ? "change" : "delete", item, draft: item, error: null })}>{verb}</button>
                      ))}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </section>
      )}

      {asking && <DuplicateDialog draft={asking.draft} existing={asking.existing} onChoose={choose} />}
      {row && (
        <ActionDialog
          title={row.mode === "change" ? `Change '${row.item.word}'` : "Delete this word?"}
          onClose={() => setRow(null)}
          onClosed={() => {
            if (deleted.current) headingRef.current?.focus();
            deleted.current = false;
          }}
        >
          <form onSubmit={submitRow} noValidate className="stack">
            {row.mode === "change" ? (
              <WordFields idPrefix="edit" draft={row.draft} onChange={(next) => setRow({ ...row, draft: next })} checked />
            ) : (
              <p>Delete &apos;{row.item.word}&apos; as &apos;{row.item.meaning}&apos;? Chat stops using it.</p>
            )}
            {alert(row.error)}
            {/* [Cancel] [Save or Delete]: the action last; Delete starts on Cancel. */}
            <div className={styles.dialogButtons}>
              <button type="button" className={`btn ${styles.target}`} data-autofocus={row.mode === "delete" || undefined} onClick={() => setRow(null)}>Cancel</button>
              <button type="submit" className={`btn ${row.mode === "change" ? "btn-primary" : "btn-danger"} ${styles.target} ${busyClass}`} aria-disabled={busy || undefined}>
                {spinner}{row.mode === "change" ? "Save" : "Delete"}
              </button>
            </div>
          </form>
        </ActionDialog>
      )}
    </div>
  );
}
