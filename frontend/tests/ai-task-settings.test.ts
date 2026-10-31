// AI settings by task, feature switches and the test model (spec 17 Oct 2026): the API
// calls, the admin and member rows, the caps where tasks are used, the feature-off note,
// and the operator's switches and test model. Org screens never show a model name or cost.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AiSettingsForm } from "@/components/admin/AiSettingsPanel";
import { TABS } from "@/components/admin/logic";
import { AnswerSettings } from "@/components/chat/AnswerSettings";
import { defaultPick } from "@/components/chat/logic";
import {
  OrgFeatureSwitchesDialog,
  SwitchList,
  TestModelBanner,
  bannerText,
  orgSwitchConfirm,
  startConfirm,
  switchConfirm,
  undoConfirm,
} from "@/components/operator/FeatureSwitches";
import {
  AI_LEVELS,
  READER_HINTS,
  READER_NAMES,
  SCAN_READERS,
  capChatOptions,
  capReaders,
  changedOnly,
  gateEnsemble,
  levelName,
  levelOfReader,
  mineDraftOf,
  orgDraftOf,
  readerOf,
  startReader,
} from "@/lib/aiSettings";
import {
  ApiError,
  FEATURE_OFF,
  getAiSettings,
  getFeatureSwitches,
  getOrgFeatureSwitches,
  isFeatureOff,
  putMyAiSettings,
  putOrgAiSettings,
  setFeatureSwitch,
  setOrgFeatureSwitch,
  startTestModel,
  stopTestModel,
} from "@/lib/api";
import type { AiSettings, AiTaskSetting, ChatOptions, OrgFeatureSwitch, Organisation, ReviewStatus, TestModel, UploadReader } from "@/lib/types";

const ROOT = join(__dirname, "..");
const read = (path: string) => readFileSync(join(ROOT, path), "utf8");
const MONEY = /[$₹]|\bprice|\bcost|\bbudget|\bspen[dt]/i;
const MODELS = /gemini|claude|sonnet|gpt|google\/|anthropic\//i;

const task = (changes: Partial<AiTaskSetting> & Pick<AiTaskSetting, "task">): AiTaskSetting => ({
  effective: "low",
  mine: null,
  organisation: null,
  locked: false,
  allowed: ["none", "low", "high", "max"],
  available: true,
  ...changes,
});

const SETTINGS: AiSettings = {
  tasks: [
    task({ task: "scan", effective: "high", organisation: "high", allowed: ["none", "low", "high"] }),
    task({ task: "ask", effective: "low", organisation: "low", locked: true }),
    task({ task: "ensemble", effective: "off", organisation: "off", allowed: ["off", "on"] }),
    task({ task: "autofile", effective: "off", allowed: ["off", "on"], available: false }),
  ],
  can_edit_organisation: true,
};

const form = (scope: "organisation" | "member", draft = scope === "organisation" ? orgDraftOf(SETTINGS) : mineDraftOf(SETTINGS)) =>
  renderToStaticMarkup(
    createElement(AiSettingsForm, { scope, settings: SETTINGS, draft, busy: false, error: null, onChange: () => {}, onSubmit: () => {} }),
  );

afterEach(() => vi.unstubAllGlobals());

/** Every request the calls make, with a reply. */
function capture(reply: unknown = { tasks: [], can_edit_organisation: false }, status = 200) {
  const sent: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    sent.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify(reply), { status, headers: { "Content-Type": "application/json" } });
  });
  return sent;
}

describe("the API calls", () => {
  it("read and save AI settings and feature switches in the agreed shapes", async () => {
    const sent = capture();
    await getAiSettings();
    await putMyAiSettings({ scan: "high", ask: null });
    await putOrgAiSettings({ scan: { level: "low", locked: true } });
    await getFeatureSwitches();
    await setFeatureSwitch("ask", false, "123456");
    await startTestModel({ scan_model: "a/b", chat_model: null, note: "trial" }, "123456");
    await stopTestModel("654321");
    expect(sent).toEqual([
      { url: "/api/ai-settings", method: "GET", body: undefined },
      { url: "/api/ai-settings/me", method: "PUT", body: { tasks: { scan: "high", ask: null } } },
      { url: "/api/ai-settings/organisation", method: "PUT", body: { tasks: { scan: { level: "low", locked: true } } } },
      { url: "/api/operator/feature-switches", method: "GET", body: undefined },
      { url: "/api/operator/feature-switches/ask", method: "PUT", body: { enabled: false, code: "123456" } },
      { url: "/api/operator/test-model", method: "POST", body: { scan_model: "a/b", chat_model: null, note: "trial", code: "123456" } },
      { url: "/api/operator/test-model", method: "DELETE", body: { code: "654321" } },
    ]);
  });

  it("turn a feature_off refusal into one calm sentence", async () => {
    capture({ error: { code: "feature_off", message: "Feature ask is disabled by platform switch" } }, 403);
    const caught = await getAiSettings().catch((error: unknown) => error);
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).message).toBe(FEATURE_OFF);
    expect(isFeatureOff(caught)).toBe(true);
    expect(isFeatureOff(new ApiError(403, "forbidden", "No."))).toBe(false);
  });
});

describe("the admin's AI settings tab", () => {
  it("is a tab of Admin", () => {
    expect(TABS.map((tab) => tab.label)).toContain("AI settings");
    // Changed on purpose (#468): it tells AdminView about unsaved changes (onUnsaved).
    expect(read("components/admin/AdminView.tsx")).toContain('<AiSettingsPanel scope="organisation" onUnsaved={setUnsaved} />');
  });

  it("shows one row per task by its job, with no model names or cost words", () => {
    const html = form("organisation");
    for (const name of ["Scanning", "Ask", "Ensemble", "Auto-filing"]) expect(html).toContain(`>${name}</legend>`);
    const shown = html.replace(/<[^>]*>/g, " ");
    expect(shown).not.toMatch(MODELS);
    expect(shown).not.toMatch(MONEY);
    expect(shown).not.toMatch(/needs review/i);
  });

  it("disables a level above the limit and says why", () => {
    const html = form("organisation");
    expect(html).toMatch(/<input type="radio" disabled=""[^>]*value="max"\/>/);
    expect(html).not.toMatch(/<input type="radio" disabled=""[^>]*value="high"\/>/);
    expect(html).toContain("Max: Not available for your organisation.");
  });

  it("gives each task a Members can change this checkbox, checked when not locked", () => {
    const html = form("organisation");
    const boxes = [...html.matchAll(/<input type="checkbox"( checked="")?\/>Members can change this/g)];
    expect(boxes.map((box) => box[1] !== undefined)).toEqual([true, false, true]); // scan open, ask locked, ensemble open
  });

  it("says a task switched off for everyone is turned off, instead of its controls", () => {
    const html = form("organisation");
    // Changed on purpose (plain words, 25 Oct 2026): a "What's this?" under each task's name.
    expect(html).toMatch(new RegExp(`>Auto-filing</legend><span[\\s\\S]*?</span></span><p class="muted">${FEATURE_OFF}</p>`));
  });

  it("shows the Save bar only when something changed, and saves only what changed", () => {
    expect(form("organisation")).not.toContain(">Save<");
    const draft = { ...orgDraftOf(SETTINGS), scan: { level: "low" as const, locked: true } };
    expect(form("organisation", draft)).toContain("Save</button>");
    expect(changedOnly(orgDraftOf(SETTINGS), draft)).toEqual({ scan: { level: "low", locked: true } });
  });
});

describe("the member's AI settings", () => {
  it("opens from the account menu as its own page", () => {
    expect(read("components/shell/AccountMenu.tsx")).toContain('href="/settings/ai"');
    const page = read("app/(app)/settings/ai/page.tsx");
    expect(page).toContain('export const metadata = { title: "AI settings" };');
    expect(page).toContain('<AiSettingsPanel scope="member" />');
  });

  it("shows a locked task's value with Set by your admin., and the others can use the organisation's setting", () => {
    const html = form("member");
    // Changed on purpose (plain words, 25 Oct 2026): a "What's this?" under each task's name.
    expect(html).toMatch(/>Ask<\/legend><span[\s\S]*?<\/span><\/span><p[^>]*><strong>Low<\/strong><\/p><p class="hint">Set by your admin\.<\/p>/);
    // mine null: the organisation's setting is used, and the levels wait.
    expect(html).toMatch(/<input type="checkbox" checked=""\/>Use the organisation&#x27;s setting/);
    expect(html).toMatch(/<input type="radio" disabled=""[^>]*checked="" value="high"\/>/);
    const draft = { ...mineDraftOf(SETTINGS), scan: "low" as const };
    expect(changedOnly(mineDraftOf(SETTINGS), draft)).toEqual({ scan: "low" });
    expect(changedOnly(mineDraftOf(SETTINGS), mineDraftOf(SETTINGS))).toEqual({});
  });
});

// #398: every account chooses who reads its scans, in the Scanning row.
describe("the Scanning reader choice", () => {
  const withScan = (scan: Partial<AiTaskSetting>): AiSettings => ({
    ...SETTINGS,
    tasks: [task({ task: "scan", allowed: ["none", "save", "low", "high", "max"], ...scan }), ...SETTINGS.tasks.slice(1)],
  });
  const render = (scope: "organisation" | "member", settings: AiSettings) =>
    renderToStaticMarkup(
      createElement(AiSettingsForm, {
        scope,
        settings,
        draft: scope === "organisation" ? orgDraftOf(settings) : mineDraftOf(settings),
        busy: false,
        error: null,
        onChange: () => {},
        onSubmit: () => {},
      }),
    );
  const scanRow = (html: string) => /<legend[^>]*>Scanning<\/legend>[\s\S]*?<\/fieldset>/.exec(html)?.[0] ?? "";
  const radios = (html: string, name: "reader" | "level") =>
    [...scanRow(html).matchAll(/<input type="radio"[^>]*\/>/g)]
      .map((match) => match[0])
      .filter((input) => input.includes(`-${name}"`));
  const value = (input: string) => /value="([^"]+)"/.exec(input)?.[1];

  it("offers three readers in one control, AI reads every page by default, with plain hints", () => {
    const html = render("organisation", withScan({ effective: "low" }));
    const row = scanRow(html);
    expect(row).toContain('aria-label="Scanning reader"');
    expect(radios(html, "reader").map(value)).toEqual(["ai", "save", "local"]);
    for (const name of ["AI reads every page", "AI for harder pages", "Local reader only"]) expect(row).toContain(`/>${name}</label>`);
    expect(radios(html, "reader").find((input) => input.includes('checked=""'))).toContain('value="ai"');
    expect(row).toContain(READER_HINTS.ai);
    expect(radios(html, "level").map(value)).toEqual(["low", "high", "max"]); // AI's care, no None
    expect(READER_HINTS).toEqual({
      ai: "Every page is read by AI and checked by the local reader.",
      save: "Clean printed pages are read on our server; harder pages by AI. Values from the server reader need a quick review.",
      local: "Pages are read on our server only. Handwriting and Hindi/Marathi read less well.",
    });
  });

  it("shows only the reader's hint for the other two, and maps each reader to a scan level", () => {
    const save = scanRow(render("organisation", withScan({ effective: "save", organisation: "save" })));
    expect(save).toContain(READER_HINTS.save);
    expect(save).not.toContain('value="low"');
    const local = scanRow(render("organisation", withScan({ effective: "none", organisation: "none" })));
    expect(local).toContain(READER_HINTS.local);
    expect([levelOfReader("local", "high"), levelOfReader("save", "high"), levelOfReader("ai", "none"), levelOfReader("ai", "max")]).toEqual([
      "none",
      "save",
      "low",
      "max",
    ]);
    expect(SCAN_READERS.map((each) => readerOf(levelOfReader(each, "low")))).toEqual(["ai", "save", "local"]);
  });

  it("disables the reader the platform has not allowed, with a plain note", () => {
    const html = render("organisation", withScan({ allowed: ["none", "low", "high", "max"] }));
    expect(radios(html, "reader").find((input) => input.includes('value="save"'))).toContain('disabled=""');
    expect(radios(html, "reader").find((input) => input.includes('value="local"'))).not.toContain('disabled=""');
    expect(scanRow(html)).toContain("AI for harder pages: Not available for your organisation.");
  });

  it("shows a locked reader with Set by your admin. to a member, and an open one waits on the organisation's setting", () => {
    const locked = scanRow(render("member", withScan({ effective: "save", organisation: "save", locked: true })));
    expect(locked).toMatch(/<strong>AI for harder pages<\/strong><\/p><p class="hint">Set by your admin\.<\/p>/);
    expect(locked).not.toContain('type="radio"');
    const lockedAi = scanRow(render("member", withScan({ effective: "high", organisation: "high", locked: true })));
    expect(lockedAi).toContain("<strong>AI reads every page · High</strong>");
    const open = render("member", withScan({ effective: "none", organisation: "none" }));
    expect(radios(open, "reader").every((input) => input.includes('disabled=""'))).toBe(true);
    expect(radios(open, "reader").find((input) => input.includes('checked=""'))).toContain('value="local"');
  });

  it("names no model and no money in any reader, hint or level", () => {
    const texts = [...Object.values(READER_NAMES), ...Object.values(READER_HINTS), ...AI_LEVELS.map((level) => levelName("scan", level))];
    for (const settings of [withScan({ effective: "save", organisation: "save" }), withScan({ allowed: ["none"] })]) {
      texts.push(render("organisation", settings).replace(/<[^>]*>/g, " "), render("member", settings).replace(/<[^>]*>/g, " "));
    }
    for (const text of texts) {
      expect(text).not.toMatch(MODELS);
      expect(text).not.toMatch(MONEY);
    }
  });

  it("Read with offers Automatic only under AI for harder pages", () => {
    expect(capReaders(READERS, "save").map((each) => each.title)).toEqual(["Automatic"]);
  });
});

const reader = (id: string, title: string, changes: Partial<UploadReader> = {}): UploadReader => ({
  id,
  title,
  hint: "",
  available: true,
  reason: null,
  ...changes,
});
const READERS = [reader("dynamic", "Automatic", { default: true }), reader("m1", "Standard"), reader("m2", "Careful")];

describe("where tasks are used", () => {
  it("Read with offers nothing above the effective scan level, and starts from it", () => {
    expect(capReaders(READERS, undefined).map((each) => each.title)).toEqual(["Automatic", "Standard", "Careful"]);
    expect(capReaders(READERS, "max").map((each) => each.title)).toEqual(["Automatic", "Standard", "Careful"]);
    expect(capReaders(READERS, "high").map((each) => each.title)).toEqual(["Automatic", "Standard"]);
    expect(capReaders(READERS, "low").map((each) => each.title)).toEqual(["Automatic"]);
    expect(capReaders(READERS, "none")).toEqual([]);
    expect(startReader(capReaders(READERS, "high"), "high")).toBe("m1");
    expect(startReader(capReaders(READERS, "max"), "max")).toBe("m2");
    expect(startReader(READERS, undefined)).toBeNull();
    expect(read("components/documents/UploadArea.tsx")).toContain("capReaders(loadedLimits.readers, scanLevel)");
  });

  const OPTIONS: ChatOptions = {
    agents: [
      { id: "google/gemini-3.8-flash", name: "Google: Gemini 3.8 Flash", default: true },
      { id: "anthropic/claude-sonnet-5.5", name: "Anthropic: Claude Sonnet 5.5", default: false },
    ],
    levels: ["low", "medium", "high"],
    default_level: "medium",
    ensemble: false,
    max_agents: 1,
  };

  it("Ask's levels start from and never pass the effective Ask level", () => {
    expect(capChatOptions(OPTIONS, "low")).toMatchObject({ levels: ["low"], default_level: "low" });
    expect(capChatOptions(OPTIONS, "high")).toMatchObject({ levels: ["low", "medium", "high"], default_level: "high" });
    expect(capChatOptions(OPTIONS, "max")).toMatchObject({ default_level: "high" });
    expect(capChatOptions(OPTIONS, "none")).toMatchObject({ agents: [], levels: [] });
    expect(capChatOptions(OPTIONS, undefined)).toBe(OPTIONS);
  });

  it("Ask's settings never name a model to an org user", () => {
    const options = capChatOptions(OPTIONS, "high");
    const html = renderToStaticMarkup(
      createElement(AnswerSettings, { options, pick: defaultPick(options), locked: false, onPick: () => {} }),
    );
    expect(html.replace(/<[^>]*>/g, " ")).not.toMatch(MODELS);
    expect(html).not.toMatch(/gemini|claude/i); // not even in an attribute
    // Changed on purpose (Tushar, 25 Oct 2026): Ask starts on Swift, whatever the default level.
    // Changed on purpose (24 Oct 2026): the chip is the engine alone.
    // Changed on purpose (Vrushit UAT, 25 Oct 2026): the Ask bar's quality picker is gone.
    expect(html).toContain(">Swift<");
  });

  it("Ensemble's controls go when the effective Ensemble is off", () => {
    const status = { ensemble: { allowed: true, reason: null, run: null } } as ReviewStatus;
    expect(gateEnsemble(status, "off").ensemble.allowed).toBe(false);
    expect(gateEnsemble(status, "on")).toBe(status);
    expect(gateEnsemble(status, undefined)).toBe(status);
  });

  it("shows This is turned off for now. where Ask, Review and My words were", () => {
    expect(read("components/chat/ChatScreen.tsx")).toMatch(/askOff \|\| askError === FEATURE_OFF/);
    expect(read("components/review/ReviewQueue.tsx")).toContain("failed === FEATURE_OFF");
    expect(read("components/words/MyWordsScreen.tsx")).toContain("isFeatureOff(caught)");
  });
});

describe("the operator's feature switches", () => {
  const ASK = { feature: "ask", enabled: true, organisations_using: 12 };
  const SIGNUP = { feature: "self_signup", enabled: false, organisations_using: 1 };
  const SWITCHES = [ASK, SIGNUP];

  it("lists each feature by a plain name, as a labelled switch, with how many use it", () => {
    const html = renderToStaticMarkup(createElement(SwitchList, { switches: SWITCHES, onFlip: () => {} }));
    expect(html).toMatch(/<input id="feature-switch-ask" type="checkbox" role="switch"[^>]* checked=""\/>Ask<\/label>/);
    expect(html).toMatch(/<input id="feature-switch-self_signup" type="checkbox" role="switch" aria-describedby="[^"]+"\/>Self sign-up<\/label>/);
    expect(html).toContain("12 organisations use this");
    expect(html).toContain("1 organisation uses this");
  });

  it("asks before turning one off, with a fresh code", () => {
    const off = switchConfirm(ASK, 12);
    expect(off.title).toBe("Turn off Ask for all 12 organisations?");
    expect(off).toMatchObject({ stepUp: true, danger: true, action: "Turn off" });
    expect(switchConfirm(SIGNUP, 12)).toMatchObject({ title: "Turn on Self sign-up for all 12 organisations?", stepUp: true });
  });
});

describe("one organisation's feature switches (0039)", () => {
  const ORG = { id: "org-1", name: "Sharma Traders" } as Organisation;
  const ASK_ON: OrgFeatureSwitch = { feature: "ask", enabled: true, platform_enabled: true };
  const REVIEW_OFF: OrgFeatureSwitch = { feature: "review", enabled: false, platform_enabled: true };
  const ROWS = [ASK_ON, REVIEW_OFF, { feature: "my_words", enabled: true, platform_enabled: false }];

  it("reads and flips one organisation's switches in the agreed shapes", async () => {
    const sent = capture([]);
    await getOrgFeatureSwitches("org-1");
    await setOrgFeatureSwitch("org-1", "ask", false, "123456");
    expect(sent).toEqual([
      { url: "/api/operator/orgs/org-1/feature-switches", method: "GET", body: undefined },
      { url: "/api/operator/orgs/org-1/feature-switches/ask", method: "PUT", body: { enabled: false, code: "123456" } },
    ]);
  });

  it("opens from the organisation's Manage menu, saying it is for this organisation only", () => {
    // Changed on purpose (plain words, 25 Oct 2026): each Manage item has a line saying what it does.
    expect(read("components/operator/OrganisationsView.tsx")).toContain(
      '{ label: "Feature switches", description: MENU_HELP.switches, onSelect: onSwitches }',
    );
    const html = renderToStaticMarkup(createElement(OrgFeatureSwitchesDialog, { org: ORG, onClose: () => {} }));
    expect(html).toContain("Feature switches for Sharma Traders");
    expect(html).toContain("Each switch turns a feature on or off for this organisation only.");
  });

  it("shows a feature off for every organisation as off and disabled, with no count", () => {
    const html = renderToStaticMarkup(createElement(SwitchList<OrgFeatureSwitch>, { switches: ROWS, onFlip: () => {} }));
    expect(html).toMatch(/<input id="feature-switch-ask" type="checkbox" role="switch" checked=""\/>Ask<\/label>/);
    expect(html).toMatch(/<input id="feature-switch-review" type="checkbox" role="switch"\/>Review<\/label>/);
    expect(html).toMatch(/<input id="feature-switch-my_words" type="checkbox" role="switch" disabled="" aria-describedby="[^"]+"\/>My words<\/label>/);
    expect(html).toContain("Off for every organisation");
    expect(html).not.toMatch(/organisations? uses?/);
  });

  it("asks before turning one off for this organisation, with a fresh code", () => {
    expect(orgSwitchConfirm(ORG, ASK_ON)).toMatchObject({
      title: "Turn off Ask for Sharma Traders?",
      done: "Ask is off for Sharma Traders.",
      stepUp: true,
      danger: true,
    });
    expect(orgSwitchConfirm(ORG, REVIEW_OFF)).toMatchObject({ title: "Turn on Review for Sharma Traders?", danger: false });
  });
});

describe("the operator's test model", () => {
  const RUNNING: TestModel = {
    active: true,
    scan_model: "google/gemini-3.1-pro-preview",
    chat_model: null,
    started_at: "2026-10-17T13:30:00Z",
    since: { pages: 120, questions: 30, cost_usd: 1.2, avg_seconds_per_page: 4.06 },
    note: null,
  };

  it("shows only who runs and since when while no numbers came back", () => {
    expect(bannerText({ ...RUNNING, since: null })).not.toContain("pages");
    const fresh = bannerText({ ...RUNNING, since: { pages: 0, questions: 0, cost_usd: null, avg_seconds_per_page: null } });
    expect(fresh).toMatch(/0 pages · 0 questions$/);
  });

  it("asks before starting, saying who it reaches and that their settings are kept", () => {
    const confirm = startConfirm({ scan_model: "google/gemini-3.1-pro-preview", chat_model: null, note: "" }, 12);
    expect(confirm.effect).toBe(
      "All 12 organisations will scan and answer with google/gemini-3.1-pro-preview until you undo. Their own settings are kept.",
    );
    expect(confirm.stepUp).toBe(true);
  });

  it("shows a banner with its figures while it runs, and none otherwise", () => {
    expect(bannerText(RUNNING)).toMatch(/^Test model running since .+: google\/gemini-3.1-pro-preview · 120 pages · 30 questions · \$1\.20 · 4\.1 s\/page$/);
    const html = renderToStaticMarkup(createElement(TestModelBanner, { testModel: RUNNING, onChanged: () => {} }));
    expect(html).toContain("Undo — back to each organisation&#x27;s settings");
    expect(renderToStaticMarkup(createElement(TestModelBanner, { testModel: { ...RUNNING, active: false }, onChanged: () => {} }))).toBe("");
    expect(read("components/operator/OperatorConsole.tsx")).toContain("<TestModelBanner");
  });

  it("undoes with DELETE and a fresh code", async () => {
    const sent = capture({ ...RUNNING, active: false });
    await undoConfirm().run("111222", "");
    expect(sent).toEqual([{ url: "/api/operator/test-model", method: "DELETE", body: { code: "111222" } }]);
  });
});
