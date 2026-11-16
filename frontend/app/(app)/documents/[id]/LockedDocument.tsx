"use client";

// A locked file's screen (PRD 7 section 6, PR 13), in place of the document screen: its name, and
// only its page pictures (LockedViewer.tsx) once this browser's 10-minute check is live, else the
// password (or Google) prompt (LockedOpenPrompt.tsx). No Ask, download, clean copy, panels or
// menu: an Admin has Unlock only. A locked file still being prepared, or one in Trash, says so.

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import type { ReactNode, Ref } from "react";
import { UnlockDialog } from "@/components/documents/LockDialog";
import { Icon } from "@/components/ui/Icon";
import { LOCK_TEXT, LOCKED_TEXT } from "@/components/viewer/locked";
import { LockedOpenPrompt } from "@/components/viewer/LockedOpenPrompt";
import { LockedViewer } from "@/components/viewer/LockedViewer";
import { getLockedOpen } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { DocumentDetail, LockedOpen } from "@/lib/types";
import styles from "./DocumentView.module.css";

export function LockedDocument({
  detail,
  back,
  openError,
  onChanged,
  headingRef,
  children,
}: {
  detail: DocumentDetail;
  back: { href: string; name: string };
  openError: string | null; // ?open_error= after the Google confirm
  onChanged(message: string): void; // unlocked: say it and load the document again
  headingRef?: Ref<HTMLHeadingElement>; // the screen's heading, focused after a lock or unlock
  children?: ReactNode; // the screen's toast
}) {
  const { me } = useSession();
  const [state, setState] = useState<LockedOpen | null>(null);
  const [failed, setFailed] = useState(false);
  const [unlocking, setUnlocking] = useState(false);
  const [asks, setAsks] = useState(0);
  const admin = me.user.role === "admin" && me.session.scope === "admin";
  const ready = detail.deleted_at === null && detail.status === "locked";

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    getLockedOpen().then(
      (found) => {
        if (!cancelled) setState(found);
      },
      () => {
        if (!cancelled) setFailed(true);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [ready, asks]);

  const ended = useCallback(
    () => setState((now) => (now ? { ...now, open: false, until: null, seconds: null } : now)),
    [],
  );

  let body: ReactNode;
  if (detail.deleted_at !== null) body = <p>{LOCKED_TEXT.inTrash}</p>;
  else if (!ready) body = <p>{LOCKED_TEXT.preparing}</p>;
  else if (failed) {
    body = (
      <>
        <p className="error-text" role="alert">
          {LOCKED_TEXT.checkFailed}
        </p>
        <button
          type="button"
          className="btn"
          onClick={() => {
            setFailed(false);
            setAsks((n) => n + 1);
          }}
        >
          {LOCKED_TEXT.tryAgain}
        </button>
      </>
    );
  } else if (state === null) body = <p className="muted">{LOCKED_TEXT.checking}</p>;
  else if (state.open && state.seconds !== null) {
    body = (
      <LockedViewer
        documentId={detail.id}
        name={detail.display_name}
        pages={detail.pages}
        turns={detail.page_turns}
        seconds={state.seconds}
        onEnded={ended}
      />
    );
  } else {
    body = (
      <LockedOpenPrompt
        via={state.via}
        next={`/documents/${detail.id}`}
        openError={openError}
        onOpened={setState}
      />
    );
  }

  return (
    <div className="page page-wide">
      {children}
      <Link href={back.href} className={styles.back}>
        <Icon name="chevron-left" />
        Back to {back.name}
      </Link>
      <header className={styles.headActions}>
        <h1 ref={headingRef} tabIndex={-1}>
          {detail.display_name}
        </h1>
        {admin && (
          <button type="button" className="btn" onClick={() => setUnlocking(true)}>
            {LOCK_TEXT.unlockMenu}
          </button>
        )}
      </header>
      {body}
      {unlocking && (
        <UnlockDialog
          document={detail}
          onClose={() => setUnlocking(false)}
          onUnlocked={(message) => {
            setUnlocking(false);
            onChanged(message);
          }}
        />
      )}
    </div>
  );
}
