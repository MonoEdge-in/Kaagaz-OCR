"use client";

// A department's people (PRD 7 PR 11a, #604), opened from its row on the Departments tab: each with
// the Head tick (taking it away asks first) and Remove (asked first), and Add people (the picker,
// then one add per person: me first, so a company sign-in check comes before anyone is added; a person who
// can't be added is said, stays ticked, and the others are still added). Ticking yourself Head
// needs a fresh company sign-in. Read each time it opens (GET /admin/departments/{id}/members), never kept.

import { useEffect, useState } from "react";
import { addPeople } from "@/components/access/addPeople";
import { PeoplePicker } from "@/components/access/PeoplePicker";
import {
  ACCESS_REMOVED,
  ADD_PEOPLE,
  REMOVE_AS_HEAD,
  accessErrorText,
  addedPeopleText,
  headToast,
  loadingMembers,
  noMembers,
  removeDepartmentQuestion,
  removeHeadQuestion,
} from "@/components/access/words";
import access from "@/components/access/Access.module.css";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { listDepartmentMembers, putMembership, removeMembership } from "@/lib/api";
import { errorWords } from "@/lib/errorWords";
import type { DepartmentMember, Person } from "@/lib/types";
import type { Notice } from "./Toast";

const MAX_PEOPLE = 50;

interface Asking {
  member: DepartmentMember;
  kind: "remove" | "head";
}

export function DepartmentPeople({
  department,
  me,
  adding,
  onAdding,
  run,
  onChanged,
  onNotice,
}: {
  department: { id: string; name: string };
  me: Person;
  adding: boolean; // the picker is open (also from the dialog's and the warning's Add people)
  onAdding(adding: boolean): void;
  run<T>(send: () => Promise<T>): Promise<T | null>;
  onChanged(): void; // the department's counts changed
  onNotice(notice: Notice): void;
}) {
  const [attempt, setAttempt] = useState(0);
  const [members, setMembers] = useState<DepartmentMember[] | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [chosen, setChosen] = useState<Person[]>([]);
  const [asking, setAsking] = useState<Asking | null>(null);
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

  async function change(work: () => Promise<Notice | null>) {
    setBusy(true);
    try {
      const notice = await work();
      if (notice) onNotice(notice);
    } catch (caught) {
      onNotice({ problem: true, text: accessErrorText(caught) });
    } finally {
      setBusy(false);
      setAttempt((n) => n + 1);
      onChanged();
    }
  }

  const head = (member: DepartmentMember, on: boolean) =>
    change(async () => {
      const reply = await run(() => putMembership(member.id, department.id, { head: on }));
      return reply?.changed ? { text: headToast(member.name, department.name, on) } : null;
    });

  function add() {
    const people = chosen;
    void change(async () => {
      const result = await addPeople(
        people,
        me.id,
        (person) => run(() => putMembership(person.id, department.id)),
        accessErrorText,
      );
      if (result.cancelled) return null; // nobody was added; the picker stays as it was
      const kept = result.failed.map((each) => each.person);
      setChosen(kept); // who failed stays ticked, to try again
      if (kept.length === 0) onAdding(false);
      const text = addedPeopleText(result.added, department.name, result.failed);
      return text ? { text, problem: kept.length > 0 } : null;
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
                      if (event.target.checked) void head(member, true);
                      else setAsking({ member, kind: "head" });
                    }}
                  />
                  Head<span className="visually-hidden">{` of ${department.name}: ${member.name}`}</span>
                </label>
                <button type="button" className="btn btn-quiet" disabled={busy} onClick={() => setAsking({ member, kind: "remove" })}>
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
            scroll
          />
          <div className="actions">
            <button type="button" className="btn" onClick={() => onAdding(false)}>
              Cancel
            </button>
            <button type="button" className="btn" disabled={busy || chosen.length === 0} onClick={add}>
              {`Add to ${department.name}`}
            </button>
          </div>
        </div>
      ) : (
        <div>
          <button type="button" className="btn" disabled={busy} onClick={() => onAdding(true)}>
            {ADD_PEOPLE}
          </button>
        </div>
      )}
      {asking && (
        <ActionDialog title={asking.member.name} onClose={() => setAsking(null)}>
          <p>
            {asking.kind === "head"
              ? removeHeadQuestion(asking.member.name, department.name)
              : removeDepartmentQuestion(asking.member.name, department.name)}
          </p>
          <div className="actions">
            <button type="button" className="btn" data-autofocus onClick={() => setAsking(null)}>
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-danger"
              onClick={() => {
                const { member, kind } = asking;
                setAsking(null);
                if (kind === "head") void head(member, false);
                else
                  void change(async () => {
                    await removeMembership(member.id, department.id);
                    return { text: ACCESS_REMOVED };
                  });
              }}
            >
              {asking.kind === "head" ? REMOVE_AS_HEAD : `Remove from ${department.name}`}
            </button>
          </div>
        </ActionDialog>
      )}
    </div>
  );
}
