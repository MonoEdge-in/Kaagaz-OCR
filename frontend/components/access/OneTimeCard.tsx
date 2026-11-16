"use client";

// Home's one-time card (PRD 7 PR 11a, #604): "Everyone can see every file today." Shown to an admin
// session while no department is Department only and no folder is private (GET /admin/departments'
// keeps_files_to_some_people), until Not now. Only that "dismissed" flag is kept, per person in
// this browser; storage can be blocked, so every read and write is in try/catch. Nothing shows
// while it loads, on an error, or once dismissed.

import Link from "next/link";
import { useEffect, useState } from "react";
import { adminGate } from "@/components/admin/logic";
import { listAdminDepartments } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { Me } from "@/lib/types";
import { CARD } from "./words";
import styles from "./Access.module.css";

const key = (userId: string) => `kaagaz:access-card:${userId}`;

export function cardDismissed(userId: string): boolean {
  try {
    return localStorage.getItem(key(userId)) === "1";
  } catch {
    return false;
  }
}

export function dismissCard(userId: string): void {
  try {
    localStorage.setItem(key(userId), "1");
  } catch {
    // Storage blocked: it stays hidden for this visit only.
  }
}

/** `keeps`: whether anything is kept to some people yet; null until the server says. */
export const showCard = (me: Me, keeps: boolean | null) =>
  adminGate(me) === "allow" && keeps === false && !cardDismissed(me.user.id);

export function OneTimeCard() {
  const { me } = useSession();
  const allowed = adminGate(me) === "allow";
  const [keeps, setKeeps] = useState<boolean | null>(null);
  const [closed, setClosed] = useState(false);

  useEffect(() => {
    if (!allowed || cardDismissed(me.user.id)) return; // dismissed: not even asked
    let live = true;
    listAdminDepartments().then(
      (reply) => {
        if (live) setKeeps(reply.keeps_files_to_some_people);
      },
      () => undefined, // nothing shows on an error
    );
    return () => {
      live = false;
    };
  }, [allowed, me.user.id]);

  if (closed || !showCard(me, keeps)) return null;
  return (
    <OneTimeCardView
      onNotNow={() => {
        dismissCard(me.user.id);
        setClosed(true);
      }}
    />
  );
}

export function OneTimeCardView({ onNotNow }: { onNotNow(): void }) {
  return (
    <section className={`card ${styles.card}`} aria-labelledby="access-card-title">
      <h2 id="access-card-title">{CARD.title}</h2>
      <p>{CARD.text}</p>
      <ol>
        {CARD.steps.map((step) => (
          <li key={step}>{step}</li>
        ))}
      </ol>
      <div className="actions">
        <Link href="/admin?tab=departments" className="k-btn k-btn--primary">
          {CARD.setUp}
        </Link>
        <button type="button" className="btn btn-quiet" onClick={onNotNow}>
          {CARD.notNow}
        </button>
      </div>
    </section>
  );
}
