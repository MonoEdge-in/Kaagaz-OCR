// Folder upload limits (#484): the super admin sets, for every organisation or for one, the most
// files, total size and folders one folder upload may have. Each field says whether the value is
// the organisation's own or comes from the platform (or the platform's default); every save asks
// first with a fresh code.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  FolderLimitsForm,
  inheritedLabel,
  limitsConfirm,
  parseLimit,
} from "@/components/operator/FolderUploadLimits";
import { OPERATOR_TABS } from "@/components/operator/OperatorConsole";
import { MENU_HELP } from "@/components/operator/OrganisationsView";
import { getFolderUploadLimits, getOrgFolderUploadLimits, setFolderUploadLimits, setOrgFolderUploadLimits } from "@/lib/api";
import type { FolderLimits, Organisation } from "@/lib/types";

const read = (path: string) => readFileSync(join(__dirname, "..", path), "utf8");
const DEFAULTS = { max_files: 50000, max_gb: 5, max_folders: 10000 };
const NONE: FolderLimits = { max_files: null, max_gb: null, max_folders: null };
const ORG = { id: "o1", name: "Sharma Traders" } as Organisation;

afterEach(() => vi.unstubAllGlobals());

describe("the Folder uploads tab and menu item", () => {
  it("is a tab of the console, before Feature switches, and an item of each organisation's Manage menu", () => {
    const labels = OPERATOR_TABS.map((tab) => tab.label);
    expect(labels.indexOf("Folder uploads")).toBe(labels.indexOf("Feature switches") - 1);
    expect(read("components/operator/OrganisationsView.tsx")).toContain(
      '{ label: "Folder upload limits", description: MENU_HELP.folderLimits, onSelect: onFolderLimits }',
    );
    expect(MENU_HELP.folderLimits).toBe("Set the most files, size and folders one folder upload may have.");
  });
});

describe("each field", () => {
  it("says where an empty organisation value comes from: the platform's, else its default", () => {
    expect(inheritedLabel("max_files", { max_files: 60000, max_gb: null, max_folders: null }, DEFAULTS)).toBe(
      "From the platform: 60,000 files",
    );
    expect(inheritedLabel("max_gb", NONE, DEFAULTS)).toBe("From the platform's default: 5 GB");
    expect(inheritedLabel("max_folders", NONE, DEFAULTS)).toBe("From the platform's default: 10,000 folders");
  });

  it("takes empty or a whole number from 1 to the ceiling", () => {
    expect(parseLimit("", "max_files")).toBeNull();
    expect(parseLimit(" 200000 ", "max_files")).toBe(200000);
    expect(parseLimit("1024", "max_gb")).toBe(1024);
    for (const bad of ["0", "200001", "1.5", "-1", "abc"]) expect(parseLimit(bad, "max_files")).toBeUndefined();
    expect(parseLimit("1025", "max_gb")).toBeUndefined();
    expect(parseLimit("50001", "max_folders")).toBeUndefined();
  });

  it("marks an organisation's own value Own and an empty one with where it comes from", () => {
    const html = renderToStaticMarkup(
      createElement(FolderLimitsForm, {
        saved: { max_files: 70000, max_gb: null, max_folders: null },
        inherited: { max_files: null, max_gb: 20, max_folders: null },
        defaults: DEFAULTS,
        org: true,
        onSave: () => undefined,
      }),
    );
    expect(html).toContain("Own");
    expect(html).toContain("From the platform: 20 GB");
    expect(html).toContain("From the platform&#x27;s default: 10,000 folders");
    expect(html).toMatch(/id="folder-limit-max_files"[^>]*value="70000"/);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>Save<\/button>/); // nothing changed yet
  });

  it("shows the platform's default under each platform field", () => {
    const html = renderToStaticMarkup(
      createElement(FolderLimitsForm, { saved: NONE, inherited: NONE, defaults: DEFAULTS, org: false, onSave: () => undefined }),
    );
    expect(html).toContain("Using the default: 50,000 files.");
    expect(html).toContain("Using the default: 5 GB.");
  });
});

describe("saving", () => {
  it("asks first with a fresh code, naming every field that changes", () => {
    const confirm = limitsConfirm(
      { max_files: 60000, max_gb: null, max_folders: 2000 },
      { max_files: null, max_gb: null, max_folders: 5000 },
      ORG,
    );
    expect(confirm).toMatchObject({
      title: "Save the folder upload limits for Sharma Traders?",
      done: "The folder upload limits for Sharma Traders are saved.",
      stepUp: true,
      danger: false,
    });
    expect(confirm.effect).toContain("Most files in one folder upload: 60,000");
    expect(confirm.effect).toContain("Most folders one upload may make: 2,000");
    expect(confirm.effect).not.toContain("size");
    expect(limitsConfirm({ ...NONE, max_gb: null }, { ...NONE, max_gb: 8 }).effect).toContain(
      "Most total size, in GB: from the platform's default",
    );
  });

  it("reads and saves in the agreed shapes", async () => {
    const sent: { url: string; method: string; body: unknown }[] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit = {}) => {
      sent.push({ url, method: init.method ?? "GET", body: init.body ? JSON.parse(String(init.body)) : undefined });
      return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
    });
    const limits = { max_files: 60000, max_gb: null, max_folders: null };
    await getFolderUploadLimits();
    await setFolderUploadLimits(limits, "123456");
    await getOrgFolderUploadLimits("o1");
    await setOrgFolderUploadLimits("o1", limits, "654321");
    expect(sent).toEqual([
      { url: "/api/operator/folder-upload-limits", method: "GET", body: undefined },
      { url: "/api/operator/folder-upload-limits", method: "PUT", body: { ...limits, code: "123456" } },
      { url: "/api/operator/orgs/o1/folder-upload-limits", method: "GET", body: undefined },
      { url: "/api/operator/orgs/o1/folder-upload-limits", method: "PUT", body: { ...limits, code: "654321" } },
    ]);
  });
});
