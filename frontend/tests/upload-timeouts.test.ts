// Production QA (5 Nov 2026): an upload call that never answers (a server restart mid-request)
// left the row waiting for ever with no word. A part gives up after 2 minutes idle and a JSON call
// of the upload after its own bound, and each is then sent again as after a lost connection,
// the row saying "Waiting for connection" meanwhile.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { uploadDocument } from "@/lib/api";
import { settle } from "./fake-clock";

const SESSION = { id: "s1", part_size: 8 * 1024 * 1024, part_count: 1 };
const DOCUMENT = { id: "d1", display_name: "deed.pdf", status: "received" };

/** A stand-in API. `hangComplete`: the first `complete` never answers. `hangPart`: nor does the first part. */
function stubApi(options: { hangPart?: boolean; hangComplete?: boolean }) {
  const asked: string[] = [];
  vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
    const path = url.split("?")[0] ?? url;
    asked.push(path);
    const completes = asked.filter((each) => each.endsWith("/complete")).length;
    if (path.endsWith("/complete") && completes === 1 && options.hangComplete) {
      // A server that never answers: only the browser's abort ends the request.
      return new Promise<Response>((_, reject) =>
        init?.signal?.addEventListener("abort", () => reject(new DOMException("The operation was aborted.", "AbortError"))),
      );
    }
    const body = path.endsWith("/candidates")
      ? { candidates: [] }
      : path.endsWith("/complete")
        ? { document: DOCUMENT, duplicate: false, in_trash: false }
        : SESSION;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  let parts = 0;
  class FakeXhr {
    status = 204;
    responseText = "";
    upload = { onprogress: null };
    onload: (() => void) | null = null;
    onerror = null;
    onabort: (() => void) | null = null;
    withCredentials = false;
    open() {}
    setRequestHeader() {}
    getResponseHeader() {
      return null;
    }
    abort() {
      this.onabort?.(); // as a browser: abort() ends the request with its abort event
    }
    send() {
      parts += 1;
      if (parts === 1 && options.hangPart) {
        return; // never answers: only an abort() ends it

      }
      queueMicrotask(() => this.onload?.());
    }
  }
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  return { asked, parts: () => parts };
}

async function upload(): Promise<{ done: boolean; said: [boolean, string | undefined][] }> {
  const said: [boolean, string | undefined][] = [];
  let done = false;
  void uploadDocument(new File(["%PDF-1.7"], "deed.pdf"), "u1", () => {}, undefined, undefined, undefined, undefined, undefined, (waiting, why) =>
    said.push([waiting, why]),
  ).then(() => (done = true));
  await settle(() => done, 8_000, 5_000);
  return { done, said };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("an upload call that never answers", () => {
  it("a part that never answers is sent again, and the row shows the waiting state", async () => {
    const api = stubApi({ hangPart: true });
    const { done, said } = await upload();
    expect(done).toBe(true);
    expect(api.parts()).toBe(2);
    expect(said).toEqual([
      [true, "connection"],
      [false, undefined],
    ]);
  });

  it("a complete that never answers is asked again, and the row shows the waiting state", async () => {
    const api = stubApi({ hangComplete: true });
    const { done, said } = await upload();
    expect(done).toBe(true);
    expect(api.asked.filter((path) => path.endsWith("/complete"))).toHaveLength(2);
    expect(said).toEqual([
      [true, "connection"],
      [false, undefined],
    ]);
  });
});
