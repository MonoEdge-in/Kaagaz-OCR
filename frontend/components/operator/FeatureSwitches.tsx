"use client";

// Feature switches, a tab of /operator (spec 17 Oct 2026): one switch per product feature
// turns it on or off for every organisation at once, with how many use it now. Every flip
// asks first in ConfirmDialog ("Turn off Ask for all 12 organisations?") with a fresh code
// (step-up). Below it, the test model (Tushar, 17 Oct 2026): the whole platform scans and
// answers with one model until undone; while it runs, TestModelBanner sits at the top of
// the console with its figures. Super admin only: model names and real cost are fine here.

import { useEffect, useState } from "react";
import libraryStyles from "@/components/documents/Library.module.css";
import { Toast } from "@/components/ui/Toast";
import {
  getFeatureSwitches,
  getOperatorModels,
  listOrgs,
  setFeatureSwitch,
  startTestModel,
  stopTestModel,
} from "@/lib/api";
import { formatDateTime, formatNumber, formatUsd } from "@/lib/format";
import type { FeatureSwitch, OperatorModel, TestModel } from "@/lib/types";
import { ConfirmDialog } from "./ConfirmDialog";
import type { Confirm } from "./ConfirmDialog";
import { LoadFailed } from "./LoadFailed";
import { afterDone, countText, operatorErrorMessage } from "./logic";
import admin from "@/components/admin/Admin.module.css";
import styles from "./Operator.module.css";

/** Plain names; an id the server adds later shows as it is. */
export const FEATURE_NAMES: Record<string, string> = {
  scan_ai: "Scanning with AI",
  ask: "Ask",
  ensemble: "Ensemble",
  autofile: "Auto-filing",
  cost_saving: "Cost saving",
  review: "Review",
  my_words: "My words",
  self_signup: "Self sign-up",
};

export const featureName = (feature: string) => FEATURE_NAMES[feature] ?? feature;
const switchId = (feature: string) => `feature-switch-${feature.replace(/[^A-Za-z0-9_-]/g, "-")}`;

/** Flipping one switch for every organisation: always asked first, always with a fresh code. */
export function switchConfirm(each: FeatureSwitch, orgs: number): Confirm {
  const name = featureName(each.feature);
  const on = !each.enabled;
  const all = `all ${countText(orgs, "organisation", "organisations")}`;
  return {
    title: `Turn ${on ? "on" : "off"} ${name} for ${all}?`,
    effect: on
      ? `${name} comes back for every organisation, within each one's own settings.`
      : `${name} disappears from every organisation's screens. They see "This is turned off for now." Work already queued waits and is not lost.`,
    action: on ? "Turn on" : "Turn off",
    done: `${name} is ${on ? "on" : "off"} for every organisation.`,
    danger: !on,
    stepUp: true,
    focusId: switchId(each.feature),
    run: (code) => setFeatureSwitch(each.feature, on, code),
  };
}

export function FeatureSwitchesView({ testModel, onTestModel }: { testModel: TestModel | null; onTestModel(): void }) {
  const [attempt, setAttempt] = useState(0);
  const [switches, setSwitches] = useState<FeatureSwitch[] | null>(null);
  const [orgs, setOrgs] = useState<number | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);
  const [changing, setChanging] = useState<Confirm | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getFeatureSwitches().then(
      (result) => !cancelled && setSwitches(result),
      (caught: unknown) => !cancelled && setError({ attempt, message: operatorErrorMessage(caught) }),
    );
    listOrgs().then(
      (result) => !cancelled && setOrgs(result.items.length),
      () => undefined, // the question then counts the organisations using it
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const failed = error?.attempt === attempt ? error.message : null;
  return (
    <>
      <section className={admin.section} aria-labelledby="feature-switches-heading">
        <h2 id="feature-switches-heading" className={admin.sectionHeading}>
          Feature switches
        </h2>
        <p className={styles.summary}>Each switch turns a feature on or off for every organisation at once.</p>
        {failed && !switches && <LoadFailed what="the feature switches" message={failed} onRetry={() => setAttempt((n) => n + 1)} />}
        {!switches && !failed && (
          <p className="visually-hidden" role="status">
            Loading the feature switches…
          </p>
        )}
        {switches && (
          <SwitchList
            switches={switches}
            onFlip={(each) => setChanging(switchConfirm(each, orgs ?? each.organisations_using))}
          />
        )}
        {changing && (
          <ConfirmDialog
            confirm={changing}
            onClose={() => setChanging(null)}
            onDone={(message) => {
              setChanging((open) => afterDone(open, changing));
              setToast(message);
              setAttempt((n) => n + 1);
            }}
          />
        )}
      </section>
      <TestModelSection testModel={testModel} orgs={orgs} onChanged={onTestModel} onDone={setToast} />
      <Toast message={toast} onClose={() => setToast(null)} />
    </>
  );
}

/** One row per feature: a real checkbox with the switch role and its name as the label. */
export function SwitchList({ switches, onFlip }: { switches: FeatureSwitch[]; onFlip(each: FeatureSwitch): void }) {
  return (
    <ul className="stack" role="list">
      {switches.map((each) => (
        <li key={each.feature}>
          <label className={libraryStyles.check}>
            <input
              id={switchId(each.feature)}
              type="checkbox"
              role="switch"
              checked={each.enabled}
              aria-describedby={`${switchId(each.feature)}-count`}
              // Controlled: nothing changes until the question is answered (and the code is right).
              onChange={() => onFlip(each)}
            />
            {featureName(each.feature)}
          </label>
          <span id={`${switchId(each.feature)}-count`} className={styles.statusNote}>
            {countText(each.organisations_using, "organisation uses", "organisations use")} this
          </span>
        </li>
      ))}
    </ul>
  );
}

// --- Test model ---------------------------------------------------------------------

const START_ID = "test-model-start";
const UNDO_ID = "test-model-undo";

/** "a and b", or one name when both are the same or one is not set. */
const modelsText = (scan: string | null, chat: string | null) =>
  scan && chat && scan !== chat ? `${scan} and ${chat}` : (scan ?? chat ?? "");

/** Start (or replace) the test model: asked first, with a fresh code. */
export function startConfirm(body: { scan_model: string | null; chat_model: string | null; note: string }, orgs: number | null): Confirm {
  const models = modelsText(body.scan_model, body.chat_model);
  const who = orgs === null ? "All organisations" : `All ${countText(orgs, "organisation", "organisations")}`;
  return {
    title: "Run the whole platform on this model?",
    effect: `${who} will scan and answer with ${models} until you undo. Their own settings are kept.`,
    action: "Run on this model",
    done: `The whole platform now runs on ${models}.`,
    danger: false,
    stepUp: true,
    focusId: START_ID,
    run: (code) => startTestModel(body, code),
  };
}

export function undoConfirm(): Confirm {
  return {
    title: "Undo the test model?",
    effect: "Every organisation goes back to its own settings.",
    action: "Undo",
    done: "Every organisation is back on its own settings.",
    danger: true,
    stepUp: true,
    focusId: UNDO_ID,
    run: (code) => stopTestModel(code),
  };
}

/** "Test model running since …: X · 120 pages · 30 questions · $1.20 · 4.1 s/page". */
export function bannerText(model: TestModel): string {
  const { since } = model;
  const head = `Test model running since ${model.started_at ? formatDateTime(model.started_at) : "now"}: ${modelsText(model.scan_model, model.chat_model)}`;
  if (since === null) return head;
  return [
    head,
    countText(since.pages, "page", "pages"),
    countText(since.questions, "question", "questions"),
    since.cost_usd === null ? null : formatUsd(since.cost_usd), // null: nothing reported yet
    since.avg_seconds_per_page === null ? null : `${formatNumber(Math.round(since.avg_seconds_per_page * 10) / 10)} s/page`,
  ]
    .filter((part) => part !== null)
    .join(" · ");
}

/** At the top of the console while the test model runs, with Undo (asked first). */
export function TestModelBanner({ testModel, onChanged }: { testModel: TestModel | null; onChanged(): void }) {
  const [changing, setChanging] = useState<Confirm | null>(null);
  if (!testModel?.active) return null;
  return (
    <div className={`card row ${styles.testBanner}`} role="status">
      <p className={admin.customer}>
        <strong>{bannerText(testModel)}</strong>
      </p>
      <button id={UNDO_ID} type="button" className="btn" onClick={() => setChanging(undoConfirm())}>
        Undo — back to each organisation&apos;s settings
      </button>
      {changing && (
        <ConfirmDialog
          confirm={changing}
          onClose={() => setChanging(null)}
          onDone={() => {
            setChanging(null);
            onChanged();
          }}
        />
      )}
    </div>
  );
}

function TestModelSection(props: { testModel: TestModel | null; orgs: number | null; onChanged(): void; onDone(message: string): void }) {
  const { testModel, orgs, onChanged, onDone } = props;
  const [models, setModels] = useState<OperatorModel[]>([]);
  const [scan, setScan] = useState("");
  const [chat, setChat] = useState("");
  const [note, setNote] = useState("");
  const [changing, setChanging] = useState<Confirm | null>(null);

  useEffect(() => {
    let cancelled = false;
    getOperatorModels().then(
      (result) => !cancelled && setModels(result.items.filter((model) => model.enabled_at !== null)),
      () => undefined, // no catalogue: nothing to pick, the button stays off
    );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <section className={admin.section} aria-labelledby="test-model-heading">
      <h2 id="test-model-heading" className={admin.sectionHeading}>
        Test model for the whole platform
      </h2>
      <p className={styles.summary}>
        {testModel?.active ? "A test model is running: it is shown at the top. Starting another replaces it." : "Every organisation scans and answers with the model you pick, until you undo."}
      </p>
      <div className="field">
        <label htmlFor="test-model-scan">Scanning model</label>
        <select id="test-model-scan" className="input" value={scan} onChange={(event) => setScan(event.target.value)}>
          <option value="">Keep each organisation&apos;s</option>
          {models.filter((model) => model.eligible).map((model) => (
            <option key={model.id} value={model.id}>
              {model.name}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="test-model-chat">Answering model</label>
        <select id="test-model-chat" className="input" value={chat} onChange={(event) => setChat(event.target.value)}>
          <option value="">Keep each organisation&apos;s</option>
          {models.filter((model) => model.chat_eligible).map((model) => (
            <option key={model.id} value={model.id}>
              {model.name}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="test-model-note">Note (optional)</label>
        <input id="test-model-note" className="input" type="text" value={note} onChange={(event) => setNote(event.target.value)} />
      </div>
      <div>
        <button
          id={START_ID}
          type="button"
          className="btn btn-primary"
          disabled={!scan && !chat}
          onClick={() => setChanging(startConfirm({ scan_model: scan || null, chat_model: chat || null, note: note.trim() }, orgs))}
        >
          Run the whole platform on this model
        </button>
      </div>
      {changing && (
        <ConfirmDialog
          confirm={changing}
          onClose={() => setChanging(null)}
          onDone={(message) => {
            setChanging(null);
            onDone(message);
            onChanged();
          }}
        />
      )}
    </section>
  );
}
