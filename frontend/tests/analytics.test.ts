// Google Analytics on the public pages only, after the visitor accepts it (#457 phase 4):
// the measurement id's shape, the page path without its query or fragment, each page's fixed
// title, the remembered choice, what gtag is told, where the component is mounted, and the
// content security policy's new hosts (on the pages that load it only).

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { getPathMatch } from "next/dist/shared/lib/router/utils/path-match";
import { NextRequest } from "next/server";
import { describe, expect, it } from "vitest";
import {
  BANNER_TEXT,
  CONSENT_KEY,
  PAGE_TITLES,
  type GtagDocument,
  gaId,
  pagePath,
  readConsent,
  saveConsent,
  startGtag,
  viewFor,
} from "@/components/analytics/ga";
import nextConfig from "@/next.config";
import { proxy } from "@/proxy";

const root = join(__dirname, "..");
const source = (path: string) => readFileSync(join(root, path), "utf8");

function files(dir: string): string[] {
  return readdirSync(join(root, dir)).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(join(root, path)).isDirectory()) return files(path);
    return /\.(ts|tsx)$/.test(name) ? [path.replaceAll("\\", "/")] : [];
  });
}

function memoryStorage(): Storage {
  const data = new Map<string, string>();
  return {
    get length() {
      return data.size;
    },
    clear: () => data.clear(),
    getItem: (key) => data.get(key) ?? null,
    key: (index) => [...data.keys()][index] ?? null,
    removeItem: (key) => void data.delete(key),
    setItem: (key, value) => void data.set(key, value),
  };
}

// Private mode, or storage switched off: every call throws.
const blockedStorage = (): Storage => {
  throw new DOMException("blocked", "SecurityError");
};

describe("gaId", () => {
  it("takes a GA4 measurement id", () => {
    expect(gaId("G-ABC123XYZ9")).toBe("G-ABC123XYZ9");
    expect(gaId("  G-ABC123  ")).toBe("G-ABC123");
  });

  it("counts anything else as unset", () => {
    for (const raw of [undefined, "", "   ", "G-", "UA-12345-1", "g-ABC", "G-ABC 123", "G-ABC\"><script>", "G-ABC&x=1", "AW-123"]) {
      expect(gaId(raw), String(raw)).toBeNull();
    }
  });
});

describe("pagePath", () => {
  it("drops the query string and the fragment", () => {
    expect(pagePath("/reset-password#t=SECRET")).toBe("/reset-password");
    expect(pagePath("/login?next=/documents/d-1")).toBe("/login");
    expect(pagePath("/invite?x=1#t=SECRET")).toBe("/invite");
    expect(pagePath("/email/confirm")).toBe("/email/confirm");
    expect(pagePath("")).toBe("/");
  });
});

describe("the page titles", () => {
  it("are fixed, one per public page", () => {
    // Changed on purpose (#457): the emailed links' pages hold a token or a new password, so
    // they are no longer tracked.
    expect(PAGE_TITLES).toEqual({
      "/login": "Sign in",
      "/signup": "Sign up",
      "/forgot-password": "Forgot password",
    });
  });
});

// Pages that hold a link's token, a new password or a sign-in code while open.
const NEVER_TRACKED = ["/reset-password", "/invite", "/email/confirm", "/login?step=code", "/login?next=/documents&step=code"];

describe("the banner", () => {
  it("says plainly what is asked", () => {
    expect(BANNER_TEXT).toBe(
      "We'd like to use Google Analytics to see how people find the sign-in pages. Nothing you type is sent.",
    );
    const banner = source("components/analytics/Analytics.tsx");
    // Changed on purpose (#457): the buttons say what they accept or decline.
    expect(banner).toContain(">Accept analytics</Button>");
    expect(banner).toContain(">Decline analytics</Button>");
    // Equal weight: neither button is the primary one.
    expect(banner).not.toMatch(/variant="primary"/);
  });
});

describe("viewFor", () => {
  it("shows nothing at all with no measurement id", () => {
    for (const consent of [null, "accepted", "declined"] as const) {
      expect(viewFor(null, "/login", consent)).toBe("off");
    }
  });

  it("asks first, and loads only after Accept", () => {
    expect(viewFor("G-ABC123", "/login", null)).toBe("ask");
    expect(viewFor("G-ABC123", "/login", "accepted")).toBe("on");
    expect(viewFor("G-ABC123", "/login", "declined")).toBe("off");
  });

  it("is off on every page without a fixed title: the app, the admin and the operator", () => {
    for (const path of ["/", "/documents", "/documents/d-1", "/search", "/admin", "/operator", "/operator/login"]) {
      expect(viewFor("G-ABC123", path, "accepted"), path).toBe("off");
      expect(viewFor("G-ABC123", path, null), path).toBe("off");
    }
  });

  it("is off on the emailed links' pages and on the code step after Google, even after Accept", () => {
    for (const path of NEVER_TRACKED) {
      expect(viewFor("G-ABC123", path, "accepted"), path).toBe("off");
      expect(viewFor("G-ABC123", path, null), path).toBe("off");
    }
    expect(viewFor("G-ABC123", "/login?next=/documents/abc", "accepted")).toBe("on");
  });

  it("is given the query string by the banner, so it can see the code step", () => {
    const banner = source("components/analytics/Analytics.tsx");
    expect(banner).toContain("window.location.search");
  });
});

describe("the remembered choice", () => {
  it("is nothing until the visitor chooses", () => {
    expect(readConsent(memoryStorage)).toBeNull();
  });

  it("remembers Decline, which loads nothing", () => {
    const storage = memoryStorage();
    saveConsent(() => storage, "declined");
    expect(storage.getItem(CONSENT_KEY)).toBe("declined");
    expect(readConsent(() => storage)).toBe("declined");
    expect(viewFor("G-ABC123", "/login", readConsent(() => storage))).toBe("off");
  });

  it("remembers Accept", () => {
    const storage = memoryStorage();
    saveConsent(() => storage, "accepted");
    expect(viewFor("G-ABC123", "/signup", readConsent(() => storage))).toBe("on");
  });

  it("ignores anything else stored under its key", () => {
    const storage = memoryStorage();
    storage.setItem(CONSENT_KEY, "yes");
    expect(readConsent(() => storage)).toBeNull();
  });

  it("works in private mode, and simply asks again", () => {
    expect(() => saveConsent(blockedStorage, "accepted")).not.toThrow();
    expect(readConsent(blockedStorage)).toBeNull();
    expect(viewFor("G-ABC123", "/login", readConsent(blockedStorage))).toBe("ask");
  });

  it("loads Google's script only once the view is on", () => {
    const banner = source("components/analytics/Analytics.tsx");
    expect(banner.match(/startGtag\(/g)).toHaveLength(1);
    expect(banner).toMatch(/if \(view === "on"\) startGtag\(/);
  });
});

function fakeBrowser(referrer = "") {
  const appended: { async: boolean; src: string }[] = [];
  const win: Parameters<typeof startGtag>[0] = { location: { origin: "https://kaagaz.example" } };
  const doc = {
    referrer,
    head: { appendChild: (node: { async: boolean; src: string }) => void appended.push(node) },
    createElement: () => ({ async: false, src: "" }),
  } as unknown as GtagDocument;
  // What gtag queues, as plain arrays (gtag pushes its arguments object).
  const calls = () => (win.dataLayer ?? []).map((entry) => Array.from(entry as ArrayLike<unknown>));
  return { win, doc, appended, calls };
}

describe("startGtag", () => {
  it("adds Google's script once, and sends one page view with the path and the fixed title only", () => {
    const { win, doc, appended, calls } = fakeBrowser();
    // Changed on purpose (#457): /reset-password is no longer tracked, and the page is set for
    // every event before the config.
    startGtag(win, doc, "G-ABC123", "/forgot-password");
    expect(appended).toEqual([{ async: true, src: "https://www.googletagmanager.com/gtag/js?id=G-ABC123" }]);
    const page = {
      page_location: "https://kaagaz.example/forgot-password",
      page_title: "Forgot password",
      page_referrer: "",
    };
    expect(calls().slice(1)).toEqual([
      ["set", page],
      [
        "config",
        "G-ABC123",
        { send_page_view: false, allow_google_signals: false, allow_ad_personalization_signals: false, ...page },
      ],
      ["event", "page_view", page],
    ]);
    expect(calls()[0]?.[0]).toBe("js");

    startGtag(win, doc, "G-ABC123", "/login");
    expect(appended).toHaveLength(1);
    expect(calls().at(-1)).toEqual([
      "event",
      "page_view",
      { page_location: "https://kaagaz.example/login", page_title: "Sign in", page_referrer: "" },
    ]);
  });

  it("never sends the query string, the fragment or the address the visitor came from", () => {
    // Changed on purpose (#457): /reset-password is no longer tracked, so a sign-in address.
    const { win, doc, calls } = fakeBrowser("https://kaagaz.example/documents/d-1?q=gst#p=3");
    startGtag(win, doc, "G-ABC123", "/login?next=/documents/abc#t=SECRET");
    const sent = JSON.stringify(calls());
    expect(sent).not.toContain("SECRET");
    expect(sent).not.toContain("next=");
    expect(sent).not.toContain("documents");
    expect(sent).not.toContain("d-1");
    expect(sent).not.toContain("gst");
    const page = {
      page_location: "https://kaagaz.example/login",
      page_title: "Sign in",
      page_referrer: "https://kaagaz.example/",
    };
    // Set before the config, so whatever Google's script sends uses this page, not the address bar.
    expect(calls()[1]).toEqual(["set", page]);
    expect(calls().at(-1)).toEqual(["event", "page_view", page]);
  });

  it("sets the page again before every page view it sends", () => {
    const { win, doc, calls } = fakeBrowser();
    startGtag(win, doc, "G-ABC123", "/login?next=/documents/abc");
    startGtag(win, doc, "G-ABC123", "/signup?ref=x");
    const sets = calls().filter(([kind]) => kind === "set");
    expect(sets.map(([, page]) => (page as { page_location: string }).page_location)).toEqual([
      "https://kaagaz.example/login",
      "https://kaagaz.example/signup",
    ]);
    expect(JSON.stringify(calls())).not.toContain("?");
  });

  it("does nothing on the emailed links' pages or the code step after Google", () => {
    for (const path of NEVER_TRACKED) {
      const { win, doc, appended, calls } = fakeBrowser();
      startGtag(win, doc, "G-ABC123", path);
      expect(appended, path).toEqual([]);
      expect(calls(), path).toEqual([]);
    }
  });

  it("does nothing on a page without a fixed title", () => {
    const { win, doc, appended, calls } = fakeBrowser();
    startGtag(win, doc, "G-ABC123", "/documents/d-1");
    expect(appended).toEqual([]);
    expect(calls()).toEqual([]);
  });
});

describe("where it is mounted", () => {
  it("only from the public pages' frame, read from GA_ID, never for the operator", () => {
    const importers = [...files("app"), ...files("components")].filter(
      (path) => !path.startsWith("components/analytics/") && /analytics\/Analytics["']/.test(source(path)),
    );
    expect(importers).toEqual(["app/login/AuthFrame.tsx"]);
    const frame = source("app/login/AuthFrame.tsx");
    expect(frame).toContain("gaId(process.env.GA_ID)");
    expect(frame).toMatch(/variant === "member" && gaMeasurementId && <Analytics id=\{gaMeasurementId\} \/>/);
  });

  it("never from the app, its shells, the admin, the operator or the root layout", () => {
    const signedIn = [
      ...files("app/(app)"),
      ...files("app/admin"),
      ...files("app/operator"),
      ...files("components/shell"),
      ...files("components/admin"),
      ...files("components/operator"),
      "app/layout.tsx",
    ];
    expect(signedIn.length).toBeGreaterThan(20);
    for (const path of signedIn) {
      expect(source(path), path).not.toMatch(/components\/analytics|googletagmanager|gtag/);
    }
  });

  it("is off on the test server: .env.example leaves the id empty and commented", () => {
    const env = readFileSync(join(root, "..", ".env.example"), "utf8");
    expect(env).toMatch(/^# GA_ID=$/m);
    expect(env).not.toMatch(/^GA_ID=/m);
  });

  it("reaches the build, which is where Next.js reads a NEXT_PUBLIC_ value", () => {
    expect(source("Dockerfile")).toMatch(/ARG GA_ID=\n/);
    expect(source("Dockerfile")).toContain("GA_ID=${GA_ID}");
    expect(readFileSync(join(root, "..", "docker-compose.yml"), "utf8")).toContain(
      "GA_ID: ${GA_ID:-}",
    );
  });
});

describe("the content security policy", () => {
  // The rules as Next.js applies them: every matching rule in order, a later header replacing
  // an earlier one, and a rule skipped when the query has a value it lists as missing.
  async function headersFor(address: string): Promise<Record<string, string>> {
    const url = new URL(address, "https://kaagaz.example");
    const out: Record<string, string> = {};
    for (const rule of (await nextConfig.headers?.()) ?? []) {
      const match = getPathMatch(rule.source, { strict: true, removeUnnamedParams: true });
      if (match(url.pathname) === false) continue;
      const missing = rule.missing ?? [];
      if (missing.some((m) => m.type === "query" && url.searchParams.get(m.key) === m.value)) continue;
      for (const { key, value } of rule.headers) out[key] = value;
    }
    return out;
  }

  // The policy proxy.ts sends with a page (#481), its nonce written as 'nonce-…' (it changes
  // on every request; csp-nonce.test.ts checks it).
  function directives(address: string): Record<string, string> {
    const response = proxy(new NextRequest(new URL(address, "https://kaagaz.example")));
    const policy = (response.headers.get("Content-Security-Policy-Report-Only") ?? "").replace(
      /'nonce-[^']+'/,
      "'nonce-…'",
    );
    return Object.fromEntries(
      policy.split("; ").map((part) => {
        const [name, ...values] = part.split(" ");
        return [name, values.join(" ")];
      }),
    );
  }

  const collect = "https://*.google-analytics.com https://*.analytics.google.com https://*.googletagmanager.com";
  const base = {
    "default-src": "'self'",
    "script-src": "'self' 'nonce-…' 'strict-dynamic' 'wasm-unsafe-eval'",
    "style-src": "'self' 'unsafe-inline'",
    "worker-src": "'self' blob:",
    "img-src": "'self' data: blob:",
    "frame-src": "'self' blob:",
    "font-src": "'self' data:",
    "connect-src": "'self'",
    "object-src": "'none'",
    "frame-ancestors": "'none'",
    "base-uri": "'self'",
    "form-action": "'self'",
  };

  it("allows Google's script and its collection hosts on the pages that load it, and nothing else new", () => {
    for (const path of ["/login", "/signup", "/forgot-password", "/login?next=/documents/abc"]) {
      expect(directives(path), path).toEqual({
        ...base,
        "script-src": "'self' 'nonce-…' 'strict-dynamic' 'wasm-unsafe-eval' https://www.googletagmanager.com",
        "img-src": `'self' data: blob: ${collect}`,
        "connect-src": `'self' ${collect}`,
      });
    }
  });

  it("allows no Google host anywhere else: the app, the admin, the operator, the emailed links, the code step", async () => {
    for (const path of [
      "/documents",
      "/documents/d-1",
      "/chat",
      "/admin",
      "/operator",
      "/operator/login",
      "/reset-password",
      "/invite",
      "/email/confirm",
      "/login?step=code",
    ]) {
      expect(directives(path), path).toEqual(base);
    }
    // Next.js's files and the /api calls get no policy from the proxy (csp-nonce.test.ts), and
    // no Google host from next.config.ts either.
    for (const path of ["/login", "/documents", "/api/documents", "/_next/static/chunks/main.js"]) {
      expect(JSON.stringify(await headersFor(path)), path).not.toMatch(/google/);
    }
  });
});
