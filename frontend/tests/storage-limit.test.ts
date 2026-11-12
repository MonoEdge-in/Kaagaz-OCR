// Storage limit per organisation (#516): the super admin sets how much an organisation may
// store in all (50 GB by default, 1 to 10,000 GB), for every organisation (the platform's
// default, on the Folder uploads tab) or for one (its Manage menu); every save asks first with a
// fresh code. An org admin sees one line in Settings: "Storage: 12.4 GB of 50 GB used".

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StorageLine } from "@/components/admin/SettingsPanel";
import { MENU_HELP } from "@/components/operator/OrganisationsView";
import { StorageLimitForm, parseStorageGb, storageConfirm } from "@/components/operator/StorageLimit";
import { getAdminStorage, getStorageLimit, setStorageLimit } from "@/lib/api";
import { storageUsedText } from "@/lib/format";
import type { Organisation, StorageLimit } from "@/lib/types";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");
const GB = 1024 ** 3;
const ORG = { id: "o1", name: "Sharma Traders" } as Organisation;
/** An organisation with no limit of its own, on a platform with none either: the default, 50 GB. */
const FOLLOWS: StorageLimit = { storage_gb: null, platform_gb: null, effective_gb: 50, default_gb: 50, most_gb: 10000, used_bytes: 12.4 * GB };
const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

/** Every request sent, with its method and JSON body; each answers `{}`. */
function recordRequests() {
  const sent: { url: string; method: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
    sent.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
    return json({});
  });
  return sent;
}

const form = (value: StorageLimit, org: boolean) =>
  renderToStaticMarkup(createElement(StorageLimitForm, { value, org, onSave: () => undefined }));

afterEach(() => vi.unstubAllGlobals());

describe("the API calls", () => {
  it("reads and saves the platform's and an organisation's limit, and the admin's usage, in the agreed shapes", async () => {
    const sent = recordRequests();
    await getStorageLimit();
    await setStorageLimit(null, 80, "123456");
    await setStorageLimit(null, null, "123457");
    await getStorageLimit("o 1");
    await setStorageLimit("o 1", 120, "654321");
    await setStorageLimit("o 1", null, "654322");
    await getAdminStorage();
    expect(sent).toEqual([
      { url: "/api/operator/storage-limit", method: "GET", body: undefined },
      { url: "/api/operator/storage-limit", method: "PUT", body: { storage_gb: 80, code: "123456" } },
      { url: "/api/operator/storage-limit", method: "PUT", body: { storage_gb: null, code: "123457" } },
      { url: "/api/operator/orgs/o%201/storage-limit", method: "GET", body: undefined },
      { url: "/api/operator/orgs/o%201/storage-limit", method: "PUT", body: { storage_gb: 120, code: "654321" } },
      { url: "/api/operator/orgs/o%201/storage-limit", method: "PUT", body: { storage_gb: null, code: "654322" } },
      { url: "/api/admin/storage", method: "GET", body: undefined },
    ]);
  });
});

describe("the words for what is used", () => {
  it("says the size used with the repo's byte formatter, then the limit in whole GB", () => {
    expect(storageUsedText(12.4 * GB, 50)).toBe("12.4 GB of 50 GB used");
    expect(storageUsedText(0, 50)).toBe("0 B of 50 GB used");
    expect(storageUsedText(300 * 1024 ** 2, 10000)).toBe("300 MB of 10,000 GB used");
    expect(storageUsedText(60.25 * GB, 50)).toBe("60.3 GB of 50 GB used"); // over a lowered limit: said as it is
  });
});

describe("the Storage (GB) field", () => {
  it("takes empty or a whole number from 1 to 10,000", () => {
    expect(parseStorageGb("", 10000)).toBeNull();
    expect(parseStorageGb("   ", 10000)).toBeNull();
    expect(parseStorageGb("1", 10000)).toBe(1);
    expect(parseStorageGb(" 10000 ", 10000)).toBe(10000);
    for (const bad of ["0", "10001", "1.5", "-1", "abc", "1e3", "5 0"]) expect(parseStorageGb(bad, 10000)).toBeUndefined();
  });

  it("shows an organisation's own value, what empty follows, what is used, and no Save until something changes", () => {
    const html = form({ ...FOLLOWS, storage_gb: 120, effective_gb: 120 }, true);
    expect(html).toContain('<label for="storage-limit-gb">Storage (GB)</label>');
    expect(html).toMatch(/id="storage-limit-gb"[^>]*value="120"/);
    expect(html).toMatch(/id="storage-limit-gb"[^>]*aria-describedby="storage-limit-gb-hint"/);
    expect(html).toContain('id="storage-limit-gb-hint" class="hint">Empty: follows the platform default (50 GB)</span>');
    expect(html).toContain("12.4 GB of 120 GB used");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Save<\/button>/);
  });

  it("is empty when the organisation follows the platform, and names the platform's own default when it has one", () => {
    const html = form({ ...FOLLOWS, platform_gb: 200, effective_gb: 200 }, true);
    expect(html).toMatch(/id="storage-limit-gb"[^>]*value=""/);
    expect(html).toContain("Empty: follows the platform default (200 GB)");
    expect(html).toContain("12.4 GB of 200 GB used");
  });

  it("on the platform, says empty uses Kaagaz's default and shows no usage", () => {
    const html = form({ ...FOLLOWS, platform_gb: 80, storage_gb: 80, effective_gb: 80, used_bytes: 0 }, false);
    expect(html).toMatch(/id="storage-limit-gb"[^>]*value="80"/);
    expect(html).toContain("Empty: uses the default (50 GB)");
    expect(html).not.toContain(" used");
  });
});

describe("saving", () => {
  it("asks first with a fresh code, and saves with that code", async () => {
    const confirm = storageConfirm(120, FOLLOWS, ORG);
    expect(confirm).toMatchObject({
      title: "Set the storage limit for Sharma Traders to 120 GB?",
      action: "Save",
      done: "The storage limit for Sharma Traders is 120 GB.",
      stepUp: true,
      danger: false,
    });
    expect(confirm.effect).toBe(
      "New uploads that would take Sharma Traders past 120 GB are refused. Nothing already stored is touched.",
    );
    const sent = recordRequests();
    await confirm.run("246810", "");
    expect(sent).toEqual([{ url: "/api/operator/orgs/o1/storage-limit", method: "PUT", body: { storage_gb: 120, code: "246810" } }]);
  });

  it("empty makes an organisation follow the platform, and the platform go back to the default", async () => {
    const org = storageConfirm(null, { ...FOLLOWS, storage_gb: 120, platform_gb: 200 }, ORG);
    expect(org.title).toBe("Set the storage limit for Sharma Traders to follow the platform (200 GB)?");
    expect(org.done).toBe("The storage limit for Sharma Traders follows the platform (200 GB).");
    const platform = storageConfirm(null, { ...FOLLOWS, storage_gb: 80, platform_gb: 80 });
    expect(platform.title).toBe("Set the platform's storage limit to the default (50 GB)?");
    expect(platform.effect).toBe(
      "New uploads that would take an organisation without its own limit past 50 GB are refused. Nothing already stored is touched.",
    );
    const sent = recordRequests();
    await org.run("111111", "");
    await platform.run("222222", "");
    expect(sent.map((each) => [each.url, each.body])).toEqual([
      ["/api/operator/orgs/o1/storage-limit", { storage_gb: null, code: "111111" }],
      ["/api/operator/storage-limit", { storage_gb: null, code: "222222" }],
    ]);
  });
});

describe("where the super admin finds it", () => {
  it("is an item of each organisation's Manage menu, after Folder upload limits, opening its dialog", () => {
    const view = read("components/operator/OrganisationsView.tsx");
    const folder = '{ label: "Folder upload limits", description: MENU_HELP.folderLimits, onSelect: onFolderLimits },';
    const storage = '{ label: "Storage limit", description: MENU_HELP.storage, onSelect: onStorage },';
    expect(view).toContain(`${folder}\n                    ${storage}`);
    expect(view).toContain("onStorage={() => setStorageOf(org)}");
    // Its words go to the page's Toast too (onDone), as the folder upload limits' do on Testing-Env.
    expect(view).toContain(
      "{storageOf && <OrgStorageLimitDialog org={storageOf} onClose={() => setStorageOf(null)} onDone={setDone} />}",
    );
    expect(MENU_HELP.storage).toBe("Set how much this organisation may store in all, in GB.");
  });

  it("sets the platform's default on the Folder uploads tab, under the folder upload limits", () => {
    const console = read("components/operator/OperatorConsole.tsx");
    expect(console).toMatch(/active === "folders" && \(\s*<>\s*<FolderUploadLimitsView \/>\s*<StorageLimitView \/>\s*<\/>\s*\)/);
  });

  it("titles the dialog with the organisation, and labels the platform's section", () => {
    const source = read("components/operator/StorageLimit.tsx");
    expect(source).toContain("title={`Storage limit for ${org.name}`}");
    expect(source).toContain('aria-labelledby="storage-limit-heading"');
  });
});

describe("the org admin's line in Settings", () => {
  const line = (props: Parameters<typeof StorageLine>[0]) => renderToStaticMarkup(createElement(StorageLine, props));

  it("says what is used of the limit", () => {
    expect(line({ storage: { used_bytes: 12.4 * GB, limit_bytes: 50 * GB, limit_gb: 50 }, failed: false })).toMatch(
      /^<p[^>]*>Storage: <strong>12.4 GB of 50 GB used<\/strong><\/p>$/,
    );
  });

  it("says so when it is loading or could not be loaded", () => {
    expect(line({ storage: null, failed: false })).toContain("Storage: <span");
    expect(line({ storage: null, failed: false })).toContain("Loading…");
    expect(line({ storage: null, failed: true })).toContain("Could not load it. Reload the page to try again.");
  });

  it("is loaded on the Settings tab, under the workspace's name", () => {
    const panel = read("components/admin/SettingsPanel.tsx");
    expect(panel).toContain("getAdminStorage()");
    expect(panel).toMatch(/Workspace: <strong>\{settings\.customer_name\}<\/strong>\s*<\/p>\s*<StorageLine /);
  });
});
