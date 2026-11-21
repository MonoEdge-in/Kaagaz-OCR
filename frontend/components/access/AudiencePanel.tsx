"use client";

// "Who can see this, and why" (PRD 7 PR 11b, #604; lead decision 1): one panel for a folder or a
// file. It lists who can see it by why (GET .../audience), how many people can't, and Check a
// person. For a folder an Admin may Make private or Make open again (each reads its preview
// first; the company sign-in starts when the server asks for it); on a
// private folder, the Admin or its department's Head edits the named list. Each change is a view
// of the same dialog, Cancel first and focused. The server
// checks every rule again; nothing here is kept anywhere but in this dialog's state.
// ponytail: a dialog, not the side Drawer the plan named: the Drawer closes itself from 1024 px.

import { useEffect, useId, useRef, useState } from "react";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { Icon } from "@/components/ui/Icon";
import {
  ApiError,
  addNamed,
  checkAudience,
  getAudience,
  listNamed,
  listPeople,
  privatePreview,
  removeNamed,
  setPrivate,
} from "@/lib/api";
import type { Audience, NamedList, Person, PlacePreview } from "@/lib/types";
import type { AccessAbilities } from "./abilities";
import { addPeople } from "./addPeople";
import { PeoplePicker } from "./PeoplePicker";
import type { FolderPlace } from "./places";
import { useProofStep } from "./proofStep";
import {
  ACCESS_REMOVED,
  ADD_PEOPLE,
  AUDIENCE_FORBIDDEN,
  AUDIENCE_LOADING,
  CHECK_PERSON,
  FOLD_AT,
  MAKE_OPEN,
  MAKE_PRIVATE,
  MAX_NAMED,
  NAMED_BEFORE_LEGEND,
  NAMED_HEADING,
  NAME_PEOPLE,
  NOT_FOUND_DOCUMENT,
  NOT_FOUND_FOLDER,
  NOT_FOUND_TEXT,
  NO_ONE_NAMED,
  PEOPLE_FAILED,
  PREVIEW_FAILED,
  PREVIEW_LOADING,
  TAKE_OFF,
  accessErrorText,
  addedPeopleText,
  audienceTitle,
  canNowSeeToast,
  checkAnswer,
  everyoneElseText,
  keepNamed,
  makeOpenQuestion,
  makePrivateQuestion,
  namedBeforeText,
  notTakenOff,
  openAgainText,
  privateLosing,
  privateRefusalText,
  privateText,
  privateToast,
  quoted,
  reasonHeading,
  removeFolderQuestion,
  showAll,
} from "./words";
import styles from "./Access.module.css";

export interface AudienceTarget {
  kind: "folder" | "document";
  id: string;
  name: string;
}

interface PanelProps {
  target: AudienceTarget;
  myId: string;
  abilities: AccessAbilities;
  /** A folder's: private or not, its department, why Make private is refused (places.ts). */
  place?: FolderPlace | null;
  onClose(): void;
  /** Shut: the screen puts focus back where it was (a row's menu button). */
  onClosed?(): void;
  /** A change was saved: the toast's words; the screen reads the folders again. */
  onChanged(message: string): void;
  /** #700: opened by the folder's Make private button: its step first, and its Cancel shuts the panel. */
  start?: "private";
}

/** A read that failed, in words: one not-found text for missing, hidden or locked; the 403 text;
 * else what went wrong, with Try again. */
export function audienceFailure(caught: unknown, kind: AudienceTarget["kind"]): { text: string; retry: boolean } {
  if (caught instanceof ApiError && caught.status === 404) {
    return { text: `${kind === "folder" ? NOT_FOUND_FOLDER : NOT_FOUND_DOCUMENT} ${NOT_FOUND_TEXT}`, retry: false };
  }
  if (caught instanceof ApiError && caught.status === 403) return { text: AUDIENCE_FORBIDDEN, retry: false };
  return { text: accessErrorText(caught), retry: true };
}

/** The places the groups name, for Check a person's answer. */
const placesOf = (audience: Audience | null) =>
  Object.fromEntries((audience?.groups ?? []).flatMap((group) => (group.place ? [[group.reason, group.place.name]] : [])));

const kindPath = (kind: AudienceTarget["kind"]) => (kind === "folder" ? "folders" : "documents");

type Mode = { kind: "view" } | { kind: "private" } | { kind: "open" } | { kind: "add" } | { kind: "remove"; person: Person };

export function AudiencePanel(props: PanelProps) {
  const { target, place, abilities, myId, onChanged } = props;
  const [attempt, setAttempt] = useState(0);
  const [audience, setAudience] = useState<Audience | null>(null);
  const [failed, setFailed] = useState<unknown>(null);
  const [named, setNamed] = useState<NamedList | null>(null);
  const [mode, setMode] = useState<Mode>({ kind: props.start ?? "view" });
  // #700: still on the step it was opened at (start): Cancel there shuts the panel, as the person
  // never asked for who can see it; once back on that view, every Cancel goes back to it.
  const [direct, setDirect] = useState(props.start !== undefined);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The last change's words, kept in the dialog as well as the toast (which sits behind it).
  const [notice, setNotice] = useState("");
  // Seeded from the tree, then from the server's reply to Make private / Make open again.
  const [isPrivate, setIsPrivate] = useState(place?.isPrivate === true);
  const proofStep = useProofStep();
  const withNamed = target.kind === "folder" && isPrivate && abilities.editNamed;

  useEffect(() => {
    let live = true;
    getAudience(kindPath(target.kind), target.id).then(
      (reply) => {
        if (!live) return;
        setAudience(reply);
        setFailed(null);
      },
      (caught: unknown) => {
        if (live) setFailed(caught);
      },
    );
    if (withNamed) {
      listNamed(target.id).then(
        (reply) => live && setNamed(reply),
        () => live && setNamed(null), // the list's own section says nothing it doesn't know
      );
    }
    return () => {
      live = false;
    };
  }, [target.kind, target.id, withNamed, attempt]);

  const back = () => {
    setDirect(false);
    setError(null);
    setMode({ kind: "view" });
  };

  /** Sends one change; null (the company sign-in was started) keeps the form open and says nothing. */
  async function send(change: () => Promise<string | null | undefined>) {
    setBusy(true);
    setError(null);
    try {
      const done = await change();
      if (done === undefined) return;
      if (done !== null) {
        onChanged(done);
        setNotice(done);
      }
      back();
      setAttempt((n) => n + 1);
    } catch (caught) {
      setError(accessErrorText(caught));
    } finally {
      setBusy(false);
    }
  }

  const switchPrivate = (on: boolean, chosen: readonly Person[], preview: PlacePreview, dropped: readonly Person[]) =>
    send(async () => {
      const ids = chosen.map((person) => person.id);
      const off = dropped.map((person) => person.id);
      const reply = await proofStep.run(() => setPrivate(target.id, on, ids, off));
      if (reply === null) return undefined;
      setIsPrivate(reply.private);
      // The people named before and unticked go in the same call (drop_named, PR 10c), in Make
      // private's own transaction. Fallback only: an older backend that ignores the field names
      // them still in its reply, so they are taken off one by one now it is private.
      const failed: string[] = [];
      for (const person of dropped.filter((each) => reply.named.some((named) => named.id === each.id))) {
        try {
          await removeNamed(target.id, person.id);
        } catch {
          failed.push(person.name);
        }
      }
      return [privateToast(target.name, on), ...(failed.length ? [notTakenOff(failed)] : [])].join(" ");
    });
  const addChosen = (chosen: readonly Person[]) =>
    send(async () => {
      const result = await addPeople(
        chosen,
        myId,
        // A Head names themselves with no check (the server asks it of an Admin only); a 403
        // keycloak_reauth_required starts the company sign-in.
        (person) => proofStep.run(() => addNamed(target.id, person.id)),
        accessErrorText,
      );
      if (result.cancelled && result.added === 0) return undefined;
      const [only] = chosen;
      if (chosen.length === 1 && only && result.added === 1) return canNowSeeToast(only.name, target.name);
      return addedPeopleText(result.added, quoted(target.name), result.failed);
    });
  const takeOff = (person: Person) =>
    send(async () => {
      await removeNamed(target.id, person.id);
      return ACCESS_REMOVED;
    });

  const title =
    mode.kind === "private" ? makePrivateQuestion(target.name) : mode.kind === "open" ? makeOpenQuestion(target.name) : audienceTitle(target.name);
  return (
    <>
      <ActionDialog title={title} onClose={props.onClose} onClosed={props.onClosed} canClose={() => !busy} wide>
        {mode.kind === "view" && (
          <AudienceView
            {...props}
            place={place ? { ...place, isPrivate } : null}
            audience={audience}
            failed={failed}
            named={withNamed ? named : null}
            onRetry={() => {
              setFailed(null);
              setAttempt((n) => n + 1);
            }}
            onMode={(next) => {
              setError(null);
              setMode(next);
            }}
          />
        )}
        {(mode.kind === "private" || mode.kind === "open") && (
          <PlaceChange
            target={target}
            on={mode.kind === "private"}
            department={place?.department?.name ?? null}
            busy={busy}
            error={error}
            onCancel={direct ? props.onClose : back}
            onConfirm={switchPrivate}
          />
        )}
        {mode.kind === "add" && (
          <AddNamed
            target={target}
            exclude={(named?.named ?? []).map((person) => person.id)}
            busy={busy}
            error={error}
            onCancel={back}
            onAdd={addChosen}
          />
        )}
        {mode.kind === "remove" && (
          <div className="stack">
            <p>{removeFolderQuestion(mode.person.name, target.name)}</p>
            {error && (
              <p className="error-text" role="alert">
                {error}
              </p>
            )}
            <div className="actions">
              <button ref={focusOnMount} type="button" className="btn" onClick={back} disabled={busy}>
                Cancel
              </button>
              <button type="button" className="btn btn-danger" onClick={() => void takeOff(mode.person)} disabled={busy}>
                {TAKE_OFF}
              </button>
            </div>
          </div>
        )}
        <p className="hint" role="status">
          {notice}
        </p>
      </ActionDialog>
    </>
  );
}

/** Cancel takes the focus when a form of the panel opens (never the action). */
const focusOnMount = (element: HTMLElement | null) => element?.focus();

/** The panel's first view, for the render tests: `audience` null while it loads. */
export function AudienceView({
  target,
  abilities,
  place = null,
  audience,
  failed,
  named,
  onRetry,
  onMode,
}: PanelProps & {
  audience: Audience | null;
  failed: unknown;
  named: NamedList | null;
  onRetry(): void;
  onMode(mode: Mode): void;
}) {
  const top = useRef<HTMLDivElement>(null);
  // Back from a form: focus comes to the top of the list, not lost on the page behind.
  useEffect(() => top.current?.focus(), []);
  if (failed) {
    const { text, retry } = audienceFailure(failed, target.kind);
    return (
      <div className="stack">
        <p className={retry ? "error-text" : undefined} role="alert">
          {text}
        </p>
        {retry && (
          <div>
            <button type="button" className="btn" onClick={onRetry}>
              <Icon name="refresh" />
              Try again
            </button>
          </div>
        )}
      </div>
    );
  }
  if (audience === null) {
    return (
      <p className="muted" role="status">
        {AUDIENCE_LOADING}
      </p>
    );
  }
  const admin = target.kind === "folder" && abilities.makePrivate ? (place ?? null) : null;
  return (
    <div ref={top} tabIndex={-1} className={`stack ${styles.audience}`}>
      {audience.groups.map((group) => {
        const heading = reasonHeading(group.reason, group.place?.name ?? null);
        return (
          heading && (
            <section key={group.reason} className={styles.group}>
              <h3>{heading}</h3>
              {group.people.length > FOLD_AT ? (
                // A large organisation's Everyone: folded, so the reasons above stay in view.
                <details className={styles.disclosure}>
                  <summary>{showAll(group.people.length)}</summary>
                  <NameList people={group.people} />
                </details>
              ) : (
                <NameList people={group.people} />
              )}
            </section>
          )
        );
      })}
      <p>{everyoneElseText(audience.everyone_else)}</p>
      <CheckPerson target={target} places={placesOf(audience)} />
      {named && <NamedSection named={named} onMode={onMode} />}
      {admin &&
        (admin.isPrivate ? (
          <div>
            <button type="button" className="btn" onClick={() => onMode({ kind: "open" })}>
              {MAKE_OPEN}
            </button>
          </div>
        ) : (
          <div className="stack">
            {/* Refused: it stays, does nothing, and says why in place of acting. */}
            <div>
              <button
                type="button"
                className="btn"
                aria-disabled={admin.refusal ? true : undefined}
                aria-describedby={admin.refusal ? `refusal-${target.id}` : undefined}
                onClick={() => !admin.refusal && onMode({ kind: "private" })}
              >
                {MAKE_PRIVATE}
              </button>
            </div>
            {admin.refusal && (
              <p id={`refusal-${target.id}`} className="hint">
                {privateRefusalText(admin.refusal)}
              </p>
            )}
          </div>
        ))}
    </div>
  );
}

const NameList = ({ people }: { people: readonly Person[] }) => (
  <ul className={styles.names}>
    {people.map((person) => (
      <li key={person.id}>{person.name}</li>
    ))}
  </ul>
);

/** A private folder's named list; [Add people] and [Remove] only when the server says `can_edit`. */
function NamedSection({ named, onMode }: { named: NamedList; onMode(mode: Mode): void }) {
  return (
    <section className={styles.group}>
      <h3>{NAMED_HEADING}</h3>
      {named.named.length === 0 ? (
        <p className="muted">{NO_ONE_NAMED}</p>
      ) : (
        <ul className={styles.list}>
          {named.named.map((person) => (
            <li key={person.id} className={styles.row}>
              <span className={styles.rowText}>{person.name}</span>
              {named.can_edit && (
                <button type="button" className="btn btn-quiet" onClick={() => onMode({ kind: "remove", person })}>
                  Remove<span className="visually-hidden">{` ${person.name}`}</span>
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {named.can_edit && (
        <div>
          <button type="button" className="btn" onClick={() => onMode({ kind: "add" })}>
            {ADD_PEOPLE}
          </button>
        </div>
      )}
    </section>
  );
}

/** Check a person: a select of the organisation's people, and a one-line answer, read out. */
function CheckPerson({ target, places }: { target: AudienceTarget; places: Readonly<Record<string, string>> }) {
  const id = useId();
  const [people, setPeople] = useState<Person[] | null>(null);
  const [answer, setAnswer] = useState("");
  const asked = useRef(0);
  useEffect(() => {
    let live = true;
    listPeople().then(
      (reply) => live && setPeople(reply.items),
      () => {
        if (!live) return;
        setPeople([]);
        setAnswer(PEOPLE_FAILED);
      },
    );
    return () => {
      live = false;
    };
  }, []);

  async function check(personId: string) {
    const turn = ++asked.current; // a slower earlier answer never replaces a later one
    const person = people?.find((each) => each.id === personId);
    if (!person) {
      setAnswer("");
      return;
    }
    try {
      const reply = await checkAudience(kindPath(target.kind), target.id, person.id);
      if (turn === asked.current) setAnswer(checkAnswer(person.name, reply.can_see, reply.reasons, places));
    } catch (caught) {
      if (turn === asked.current) setAnswer(accessErrorText(caught));
    }
  }

  return (
    <div className="field">
      <label htmlFor={id}>{CHECK_PERSON}</label>
      <select id={id} className="input" defaultValue="" disabled={people === null} onChange={(event) => void check(event.target.value)}>
        <option value="">Choose a person</option>
        {(people ?? []).map((person) => (
          <option key={person.id} value={person.id}>
            {person.name}
          </option>
        ))}
      </select>
      <p className="hint" role="status">
        {answer}
      </p>
    </div>
  );
}

/** Make private (`on`, naming people who keep it) or Make open again: the preview first, its
 * button waiting for it. Each change of the people named asks again (the preview counts them). */
function PlaceChange({
  target,
  on,
  department,
  busy,
  error,
  onCancel,
  onConfirm,
}: {
  target: AudienceTarget;
  on: boolean;
  department: string | null;
  busy: boolean;
  error: string | null;
  onCancel(): void;
  onConfirm(on: boolean, chosen: readonly Person[], preview: PlacePreview, dropped: readonly Person[]): void;
}) {
  const [chosen, setChosen] = useState<Person[]>([]);
  const [dropped, setDropped] = useState<string[]>([]); // the people named before, unticked
  const [attempt, setAttempt] = useState(0);
  const [preview, setPreview] = useState<{ key: string; reply: PlacePreview } | null>(null);
  const [failedKey, setFailedKey] = useState<string | null>(null);
  const ids = chosen.map((person) => person.id).join(",");
  const key = `${attempt}|${ids}`; // the preview shown is the one for these people
  useEffect(() => {
    let live = true;
    privatePreview(target.id, on, ids.split(",").filter(Boolean)).then(
      (reply) => live && setPreview({ key, reply }),
      () => live && setFailedKey(key),
    );
    return () => {
      live = false;
    };
  }, [target.id, on, ids, key]);
  return (
    <PlaceChangeView
      on={on}
      department={department}
      chosen={chosen}
      onChoose={setChosen}
      dropped={dropped}
      onDrop={setDropped}
      preview={preview?.key === key ? preview.reply : null}
      failed={failedKey === key}
      busy={busy}
      error={error}
      onRetry={() => setAttempt((n) => n + 1)}
      onCancel={onCancel}
      onConfirm={(reply) => onConfirm(on, chosen, reply, (reply.named_before ?? []).filter((person) => dropped.includes(person.id)))}
    />
  );
}

/** The form, for the render tests: `preview` null while the one for these people loads. */
export function PlaceChangeView({
  on,
  department,
  chosen,
  onChoose,
  dropped = [],
  onDrop = () => undefined,
  preview,
  failed,
  busy,
  error,
  onRetry,
  onCancel,
  onConfirm,
}: {
  on: boolean;
  department: string | null;
  chosen: readonly Person[];
  onChoose(chosen: Person[]): void;
  dropped?: readonly string[];
  onDrop?(dropped: string[]): void;
  preview: PlacePreview | null;
  failed: boolean;
  busy: boolean;
  error: string | null;
  onRetry(): void;
  onCancel(): void;
  onConfirm(preview: PlacePreview): void;
}) {
  const namedBefore = (on && preview?.named_before) || [];
  const losing = preview ? privateLosing(preview.losing, namedBefore, dropped) : [];
  const back = namedBefore.filter((person) => !dropped.includes(person.id));
  return (
    <div className="stack">
      {/* One live region: "Checking…", then who loses (or the failure) where it was. */}
      <div role="status">
        {failed ? (
          <p className="error-text">{PREVIEW_FAILED}</p>
        ) : preview === null ? (
          <p className="muted">{PREVIEW_LOADING}</p>
        ) : (
          <p>{on ? privateText(department, losing) : openAgainText(department, preview.files)}</p>
        )}
      </div>
      {failed && (
        <div>
          <button type="button" className="btn" onClick={onRetry}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      )}
      {on && losing.length > 3 && (
        <details className={styles.disclosure}>
          <summary>{showAll(losing.length)}</summary>
          <NameList people={losing} />
        </details>
      )}
      {/* Making it private again brings back who was named on it: each can be taken off first. */}
      {namedBefore.length > 0 && (
        <fieldset className={styles.picker}>
          <legend>{NAMED_BEFORE_LEGEND}</legend>
          {back.length > 0 && <p>{namedBeforeText(back)}</p>}
          <ul className={styles.pickerList}>
            {namedBefore.map((person) => (
              <li key={person.id}>
                <label className={styles.pickerRow}>
                  <input
                    type="checkbox"
                    checked={!dropped.includes(person.id)}
                    onChange={(event) =>
                      onDrop(event.target.checked ? dropped.filter((id) => id !== person.id) : [...dropped, person.id])
                    }
                  />
                  {keepNamed(person.name)}
                </label>
              </li>
            ))}
          </ul>
        </fieldset>
      )}
      {on && <PeoplePicker legend={NAME_PEOPLE} chosen={chosen} onChange={onChoose} cap={MAX_NAMED} />}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <div className="actions">
        <button ref={focusOnMount} type="button" className="btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button
          type="button"
          className={`btn ${on ? "btn-danger" : "btn-primary"}`}
          disabled={busy || preview === null}
          onClick={() => preview && onConfirm(preview)}
        >
          {on ? MAKE_PRIVATE : MAKE_OPEN}
        </button>
      </div>
    </div>
  );
}

/** Name more people on a private folder (at most MAX_NAMED chosen at once). */
function AddNamed({
  target,
  exclude,
  busy,
  error,
  onCancel,
  onAdd,
}: {
  target: AudienceTarget;
  exclude: readonly string[];
  busy: boolean;
  error: string | null;
  onCancel(): void;
  onAdd(chosen: readonly Person[]): void;
}) {
  const [chosen, setChosen] = useState<Person[]>([]);
  return (
    <div className="stack">
      <PeoplePicker legend={`${ADD_PEOPLE} to ${quoted(target.name)}`} chosen={chosen} onChange={setChosen} cap={MAX_NAMED} exclude={exclude} />
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <div className="actions">
        <button ref={focusOnMount} type="button" className="btn" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="button" className="btn btn-primary" disabled={busy || chosen.length === 0} onClick={() => onAdd(chosen)}>
          {`${ADD_PEOPLE} to ${quoted(target.name)}`}
        </button>
      </div>
    </div>
  );
}
