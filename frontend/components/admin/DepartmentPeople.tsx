"use client";

// A department's people (PRD 7 PR 11a, #604), opened from its row on the Departments tab: each with
// the Head tick and Remove (asked first), and Add people (the picker, then one add per person).
// Adding yourself, or ticking yourself Head, goes through the password step. Read each time it
// opens (GET /admin/departments/{id}/members), never kept.

import { useEffect, useState } from "react";
import { PeoplePicker } from "@/components/access/PeoplePicker";
import {
  ACCESS_REMOVED,
  ADD_PEOPLE,
  accessErrorText,
  addedPeopleToast,
  headToast,
  loadingMembers,
  noMembers,
  removeDepartmentQuestion,
} from "@/components/access/words";
import access from "@/components/access/Access.module.css";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { listDepartmentMembers, putMembership, removeMembership } from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import type { DepartmentMember, Person } from "@/lib/types";
import type { Notice } from "./Toast";

const MAX_PEOPLE = 50;

export function DepartmentPeople({
  department,
  me,
  adding: startAdding,
  run,
  onChanged,
  onNotice,
}: {
  department: { id: string; name: string };
  me: Person;
  adding: boolean; // open with the picker (the dialog's and the warning's Add people)
  run<T>(what: string, send: (password?: string) => Promise<T>, first: boolean): Promise<T | null>;
  onChanged(): void; // the department's counts changed
  onNotice(notice: Notice): void;
}) {
  const [attempt, setAttempt] = useState(0);
  const [members, setMembers] = useState<DepartmentMember[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [adding, setAdding] = useState(startAdding);
  const [chosen, setChosen] = useState<Person[]>([]);
  const [removing, setRemoving] = useState<DepartmentMember | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    listDepartmentMembers(department.id).then(
      (reply) => {
        if (live) setMembers(reply.items);
      },
      (caught: unknown) => {
        if (live) setFailed(errorWords(caught));
      },
    );
    return () => {
      live = false;
    };
  }, [department.id, attempt]);

  async function change(work: () => Promise<string | null>) {
    setBusy(true);
    try {
      const done = await work();
      if (done) onNotice({ text: done });
    } catch (caught) {
      onNotice({ problem: true, text: accessErrorText(caught) });
    } finally {
      setBusy(false);
      setAttempt((n) => n + 1);
      onChanged();
    }
  }

  const join = (person: Person, head?: boolean) =>
    run(department.name, (password) => putMembership(person.id, department.id, { head, password }), person.id === me.id && head !== false);

  function add() {
    const people = chosen;
    setAdding(false);
    setChosen([]);
    void change(async () => {
      let added = 0;
      for (const person of people) if ((await join(person)) !== null) added += 1;
      return added ? addedPeopleToast(added, department.name) : null;
    });
  }

  return (
    <div className={access.members}>
      {failed && <p className="error-text">{failed}</p>}
      {!failed && members === null && (
        <p className="muted" role="status">
          {loadingMembers(department.name)}
        </p>
      )}
      {members?.length === 0 && <p className="muted">{noMembers(department.name)}</p>}
      {members && members.length > 0 && (
        <ul className={access.list}>
          {members.map((member) => (
            <li key={member.id} className={access.row}>
              <span className={access.rowText}>{member.name}</span>
              <span className={access.rowControls}>
                <label className={access.check}>
                  <input
                    type="checkbox"
                    checked={member.head}
                    disabled={busy}
                    onChange={(event) => {
                      const on = event.target.checked;
                      void change(async () => ((await join(member, on)) === null ? null : headToast(member.name, department.name, on)));
                    }}
                  />
                  Head<span className="visually-hidden">{` of ${department.name}: ${member.name}`}</span>
                </label>
                <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => setRemoving(member)}>
                  Remove
                  <span className="visually-hidden">{` ${member.name}`}</span>
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}
      {adding ? (
        <div className="stack">
          <PeoplePicker
            legend={`${ADD_PEOPLE} to ${department.name}`}
            chosen={chosen}
            onChange={setChosen}
            cap={MAX_PEOPLE}
            exclude={(members ?? []).map((member) => member.id)}
          />
          <div className="actions">
            <button type="button" className="btn" onClick={() => setAdding(false)}>
              Cancel
            </button>
            <button type="button" className="btn" disabled={busy || chosen.length === 0} onClick={add}>
              {`Add to ${department.name}`}
            </button>
          </div>
        </div>
      ) : (
        <div>
          <button type="button" className="btn" disabled={busy} onClick={() => setAdding(true)}>
            {ADD_PEOPLE}
          </button>
        </div>
      )}
      {removing && (
        <ActionDialog title={removing.name} onClose={() => setRemoving(null)}>
          <p>{removeDepartmentQuestion(removing.name, department.name)}</p>
          <div className="actions">
            <button type="button" className="btn" data-autofocus onClick={() => setRemoving(null)}>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-danger"
              onClick={() => {
                const member = removing;
                setRemoving(null);
                void change(async () => {
                  await removeMembership(member.id, department.id);
                  return ACCESS_REMOVED;
                });
              }}
            >
              {`Remove from ${department.name}`}
            </button>
          </div>
        </ActionDialog>
      )}
    </div>
  );
}
