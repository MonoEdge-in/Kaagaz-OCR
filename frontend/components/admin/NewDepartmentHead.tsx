"use client";

// "Who heads Finance?" (PRD 7 PR 11a, #604), right after a department is added: Choose a person
// (then Make head), Me (the password step: it gives the admin themselves access), or Decide later.
// A Head may be anyone in the organisation (lead decision 13). Focus moves to its question when it
// opens, so a keyboard or screen reader is taken there.

import { useEffect, useId, useRef, useState } from "react";
import { asksFirst } from "@/components/access/passwordStep";
import { HEAD_STEP, accessErrorText, headExplained, headQuestion } from "@/components/access/words";
import access from "@/components/access/Access.module.css";
import { listPeople, putMembership } from "@/lib/api";
import type { Person } from "@/lib/types";

export function NewDepartmentHead({
  department,
  me,
  run,
  onDone,
}: {
  department: { id: string; name: string };
  me: Person;
  /** The panel's password step (usePasswordStep().run). */
  run<T>(what: string, send: (password?: string) => Promise<T>, first: boolean): Promise<T | null>;
  /** The head chosen, or null: Decide later. */
  onDone(head: Person | null): void;
}) {
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const [choosing, setChoosing] = useState(false);
  const [people, setPeople] = useState<Person[] | null>(null);
  const [chosen, setChosen] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => heading.current?.focus(), []);

  useEffect(() => {
    if (!choosing) return;
    let live = true;
    listPeople().then(
      (reply) => {
        if (live) setPeople(reply.items);
      },
      (caught: unknown) => {
        if (live) setError(accessErrorText(caught));
      },
    );
    return () => {
      live = false;
    };
  }, [choosing]);

  async function make(head: Person) {
    setBusy(true);
    setError(null);
    try {
      const first = asksFirst({ myId: me.id, targetId: head.id, on: true });
      const done = await run(department.name, (password) => putMembership(head.id, department.id, { head: true, password }), first);
      if (done !== null) onDone(head);
    } catch (caught) {
      setError(accessErrorText(caught));
    } finally {
      setBusy(false);
    }
  }

  const person = people?.find((each) => each.id === chosen);
  return (
    <section className={`card ${access.card}`} aria-labelledby={`${id}-heading`}>
      <h3 id={`${id}-heading`} ref={heading} tabIndex={-1}>
        {headQuestion(department.name)}
      </h3>
      <p className="muted">{headExplained(department.name)}</p>
      {choosing && (
        <div className={access.line}>
          <label htmlFor={`${id}-person`} className="visually-hidden">
            {HEAD_STEP.choose}
          </label>
          <select id={`${id}-person`} className="input" value={chosen} disabled={people === null} onChange={(event) => setChosen(event.target.value)}>
            <option value="">{people === null ? "Loading people…" : HEAD_STEP.choose}</option>
            {(people ?? []).map((each) => (
              <option key={each.id} value={each.id}>
                {each.name}
              </option>
            ))}
          </select>
          <button type="button" className="btn" disabled={busy || !person} onClick={() => person && void make(person)}>
            {HEAD_STEP.make}
          </button>
        </div>
      )}
      {error && (
        <p className="error-text" role="alert">
          {error}
        </p>
      )}
      <div className={access.line}>
        {!choosing && (
          <button type="button" className="btn" disabled={busy} onClick={() => setChoosing(true)}>
            {HEAD_STEP.choose}
          </button>
        )}
        <button type="button" className="btn" disabled={busy} onClick={() => void make(me)}>
          {HEAD_STEP.me}
        </button>
        <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => onDone(null)}>
          {HEAD_STEP.later}
        </button>
      </div>
    </section>
  );
}
