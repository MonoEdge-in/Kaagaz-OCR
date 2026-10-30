// Google Analytics on the public pages only, after the visitor accepts it (#457 phase 4):
// the measurement id's shape, the page path without its query or fragment, each page's fixed
// title, the remembered choice, what gtag is told, where the component is mounted, and the
// content security policy's new hosts.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
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
    expect(PAGE_TITLES).toEqual({
      "/login": "Sign in",
      "/signup": "Sign up",
      "/forgot-password": "Forgot password",
      "/reset-password": "Reset password",
      "/invite": "Accept invite",
      "/email/confirm": "Confirm email",
    });
  });
});

describe("the banner", () => {
  it("says plainly what is asked", () => {
    expect(BANNER_TEXT).toBe(
      "We'd like to use Google Analytics to see how people find the sign-in pages. Nothing you type is sent.",
    );
    const banner = source("components/analytics/Analytics.tsx");
    expect(banner).toContain(">Accept</Button>");
    expect(banner).toContain(">Decline</Button>");
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
    startGtag(win, doc, "G-ABC123", "/reset-password");
    expect(appended).toEqual([{ async: true, src: "https://www.googletagmanager.com/gtag/js?id=G-ABC123" }]);
    const page = {
      page_location: "https://kaagaz.example/reset-password",
      page_title: "Reset password",
      page_referrer: "",
    };
    expect(calls().slice(1)).toEqual([
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
    const { win, doc, calls } = fakeBrowser("https://kaagaz.example/documents/d-1?q=gst#p=3");
    startGtag(win, doc, "G-ABC123", "/reset-password#t=SECRET");
    const sent = JSON.stringify(calls());
    expect(sent).not.toContain("SECRET");
    expect(sent).not.toContain("d-1");
    expect(sent).not.toContain("gst");
    expect(calls().at(-1)).toEqual([
      "event",
      "page_view",
      {
        page_location: "https://kaagaz.example/reset-password",
        page_title: "Reset password",
        page_referrer: "https://kaagaz.example/",
      },
    ]);
  });

  it("does nothing on a page without a fixed title", () => {
    const { win, doc, appended, calls } = fakeBrowser();
    startGtag(win, doc, "G-ABC123", "/documents/d-1");
    expect(appended).toEqual([]);
    expect(calls()).toEqual([]);
  });
});

describe("where it is mounted", () => {
  it("only from the public pages' frame, read from NEXT_PUBLIC_GA_ID, never for the operator", () => {
    const importers = [...files("app"), ...files("components")].filter(
      (path) => !path.startsWith("components/analytics/") && /analytics\/Analytics["']/.test(source(path)),
    );
    expect(importers).toEqual(["app/login/AuthFrame.tsx"]);
    const frame = source("app/login/AuthFrame.tsx");
    expect(frame).toContain("gaId(process.env.NEXT_PUBLIC_GA_ID)");
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
    expect(env).toMatch(/^# NEXT_PUBLIC_GA_ID=$/m);
    expect(env).not.toMatch(/^NEXT_PUBLIC_GA_ID=/m);
  });

  it("reaches the build, which is where Next.js reads a NEXT_PUBLIC_ value", () => {
    expect(source("Dockerfile")).toMatch(/ARG NEXT_PUBLIC_GA_ID=\n/);
    expect(source("Dockerfile")).toContain("NEXT_PUBLIC_GA_ID=${NEXT_PUBLIC_GA_ID}");
    expect(readFileSync(join(root, "..", "docker-compose.yml"), "utf8")).toContain(
      "NEXT_PUBLIC_GA_ID: ${NEXT_PUBLIC_GA_ID:-}",
    );
  });
});

describe("the content security policy", () => {
  async function directives(): Promise<Record<string, string>> {
    const rule = ((await nextConfig.headers?.()) ?? []).find((r) => r.source === "/:path*");
    const policy = rule?.headers.find((h) => h.key === "Content-Security-Policy-Report-Only")?.value ?? "";
    return Object.fromEntries(
      policy.split("; ").map((part) => {
        const [name, ...values] = part.split(" ");
        return [name, values.join(" ")];
      }),
    );
  }

  const collect = "https://*.google-analytics.com https://*.analytics.google.com https://*.googletagmanager.com";

  it("allows Google's script and its collection hosts, and nothing else new", async () => {
    expect(await directives()).toEqual({
      "default-src": "'self'",
      "script-src": "'self' 'unsafe-inline' 'wasm-unsafe-eval' https://www.googletagmanager.com",
      "style-src": "'self' 'unsafe-inline'",
      "worker-src": "'self' blob:",
      "img-src": `'self' data: blob: ${collect}`,
      "frame-src": "'self' blob:",
      "font-src": "'self' data:",
      "connect-src": `'self' ${collect}`,
      "object-src": "'none'",
      "frame-ancestors": "'none'",
      "base-uri": "'self'",
      "form-action": "'self'",
    });
  });
});
