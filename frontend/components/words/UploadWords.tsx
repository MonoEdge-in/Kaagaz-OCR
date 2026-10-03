"use client";

// "Words in these files" on the upload screen (optional, collapsed). When a batch
// of uploads settles, the pairs still to save go to My words, one POST each, linked
// to that batch's files: a separate call after the upload (the council's choice),
// so the upload never waits or fails for them. Each pair says what became of it;
// one whose word has another meaning asks the My words question.

import Link from "next/link";
import { useEffect, useEffectEvent, useRef, useState } from "react";
import type { UploadEntry } from "@/components/documents/uploadQueue";
import { addMyWord } from "@/lib/api";
import { useSession } from "@/lib/session";
import { DuplicateDialog, WordFields } from "./WordParts";
import { addBody, failedState, pairsSummary, pairsToSave, retryBody, savedState, uploadedIds } from "./words";
import type { Choice, NewWordBody, SaveState, UploadPair } from "./words";
import styles from "./Words.module.css";

const blank = (key: string): UploadPair => ({ key, word: "", meaning: "", state: { kind: "draft" } });

export function UploadWords({ entries, settled }: { entries: UploadEntry[]; settled: number }) {
  const { me } = useSession();
  const nextKey = useRef(1);
  const linked = useRef<Set<string>>(new Set());
  const heard = useRef(settled);
  const [pairs, setPairs] = useState([blank("pair-0")]);
  const [asking, setAsking] = useState<string | null>(null);
  const [status, setStatus] = useState("");

  const setState = (key: string, state: UploadPair["state"], sent?: NewWordBody) =>
    setPairs((now) => now.map((pair) => (pair.key === key ? { ...pair, state, sent: sent ?? pair.sent } : pair)));

  async function send(key: string, body: NewWordBody): Promise<SaveState> {
    setState(key, { kind: "saving" }, body);
    const state = await addMyWord(body).then(savedState, failedState);
    setState(key, state);
    return state;
  }

  // A batch settled: save the waiting pairs with the files it uploaded, one after another.
  const onSettled = useEffectEvent(async () => {
    const ids = uploadedIds(entries, linked.current);
    ids.forEach((id) => linked.current.add(id)); // a batch's files are linked to the words typed before it only
    const waiting = pairsToSave(pairs);
    if (ids.length === 0 || waiting.length === 0) return;
    const states: SaveState[] = [];
    for (const pair of waiting) states.push(await send(pair.key, addBody(pair, me.user.id, ids)));
    setStatus(pairsSummary(states));
  });

  useEffect(() => {
    if (settled === heard.current) return;
    heard.current = settled;
    void onSettled();
  }, [settled]);

  async function choose(key: string, sent: NewWordBody | undefined, choice: Choice) {
    setAsking(null);
    const body = sent && retryBody(sent, choice);
    if (body) setStatus(pairsSummary([await send(key, body)]));
    else setState(key, { kind: "refused", message: "Not added." });
  }

  const askingPair = pairs.find((pair) => pair.key === asking);
  const askingState = askingPair?.state.kind === "ask" ? askingPair.state : null;

  // The live region and the dialog sit outside <details>, which hides what it holds while closed.
  return (
    <>
      <p className="visually-hidden" role="status" aria-live="polite">{status}</p>
      <details className={`card ${styles.block} ${styles.uploadWords}`}>
        <summary>Words in these files (optional)</summary>
        <div className="stack">
          <p className="hint">
            Words the way they are written in these files, and what they mean in English (malak → owner). They are
            saved to <Link href="/words">My words</Link> once the files upload, and help chat with every question. Only
            you see them.
          </p>
          {pairs.map((pair, index) => (
            <div key={pair.key} className={styles.fields}>
              <WordFields
                idPrefix={pair.key}
                draft={pair}
                labelSuffix={` ${index + 1}`}
                checked={false}
                onChange={(draft) => setPairs((now) => now.map((p) => (p.key === pair.key ? { ...p, ...draft, state: { kind: "draft" } } : p)))}
              />
              <button type="button" className={`btn btn-quiet ${styles.target}`} onClick={() => setPairs((now) => now.filter((p) => p.key !== pair.key))}>
                Remove<span className="visually-hidden">{` word ${index + 1}`}</span>
              </button>
              <PairState pair={pair} onChoose={() => setAsking(pair.key)} />
            </div>
          ))}
          <button
            type="button"
            className={`btn ${styles.target}`}
            onClick={() => {
              const key = `pair-${nextKey.current++}`; // outside the updater, which React may run twice
              setPairs((now) => [...now, blank(key)]);
            }}
          >
            Add another word
          </button>
        </div>
      </details>
      {askingPair && askingState && (
        <DuplicateDialog
          draft={askingPair.sent ?? askingPair}
          existing={askingState.existing}
          onChoose={(choice) => void choose(askingPair.key, askingPair.sent, choice)}
        />
      )}
    </>
  );
}

function PairState({ pair, onChoose }: { pair: UploadPair; onChoose(): void }) {
  const { state } = pair;
  if (state.kind === "draft") return null;
  if (state.kind !== "ask") {
    const text = state.kind === "saving" ? "Saving…" : state.message;
    return <p className={`${state.kind === "refused" ? "error-text" : "muted"} ${styles.pairState}`}>{text}</p>;
  }
  return (
    <p className={styles.pairState}>
      This word already has another meaning.{" "}
      <button type="button" className={`btn ${styles.target}`} onClick={onChoose}>Choose what to do</button>
    </p>
  );
}
