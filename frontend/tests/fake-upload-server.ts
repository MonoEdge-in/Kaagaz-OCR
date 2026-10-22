// A stand-in for the resumable upload API that lib/api.ts's uploadDocument and uploadVersion
// call: POST /documents/upload-sessions/candidates, POST /documents/upload-sessions, one PUT a
// part (XMLHttpRequest, for its progress), then POST .../complete. It keeps what was sent.

import { vi } from "vitest";

type Reply = { status: number; body: unknown };

export const UPLOADED: Reply = { status: 201, body: { document: { id: "d1" }, duplicate: false, in_trash: false } };

const json = ({ status, body }: Reply) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

export class FakePart {
  method = "";
  url = "";
  body: unknown = null;
  status = 0;
  responseText = "";
  withCredentials = false;
  headers: Record<string, string> = {};
  upload: { onprogress: ((event: { lengthComputable: boolean; loaded: number }) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }
  send(body: unknown) {
    this.body = body;
    server.parts.push(this);
    if (server.autoParts) queueMicrotask(() => this.reply());
  }
  /** The part is stored: 204. */
  reply(status = 204) {
    this.status = status;
    this.onload?.();
  }
}

const server = {
  starts: [] as Record<string, unknown>[], // the bodies of POST /documents/upload-sessions
  parts: [] as FakePart[],
  completes: 0,
  autoParts: true,
};

/**
 * Stubs fetch and XMLHttpRequest. `candidates` answers the first call (a 409 there is how the
 * server refuses a file chosen by someone else); `complete` answers each complete (a function
 * for a different answer each time, or to throw a lost connection). With autoParts false a test
 * sends each part's progress and reply itself.
 */
export function fakeUploadServer({
  candidates = { status: 200, body: { candidates: [] } } as Reply,
  complete = (() => UPLOADED) as Reply | ((attempt: number) => Reply),
  autoParts = true,
} = {}) {
  server.starts = [];
  server.parts = [];
  server.completes = 0;
  server.autoParts = autoParts;
  vi.stubGlobal("XMLHttpRequest", FakePart);
  vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
    const body = init.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
    if (url === "/api/documents/upload-sessions/candidates") return json(candidates);
    if (url === "/api/documents/upload-sessions") {
      server.starts.push(body);
      const parts = Math.max(1, Math.ceil(Number(body.file_size) / (8 * 1024 * 1024)));
      return json({ status: 200, body: { id: `s${server.starts.length}`, part_size: 8 * 1024 * 1024, part_count: parts } });
    }
    if (url.endsWith("/complete")) {
      server.completes += 1;
      return json(typeof complete === "function" ? complete(server.completes) : complete);
    }
    throw new Error(`unexpected request ${url}`);
  });
  return server;
}

/** The part being sent, once the file's parts are fingerprinted and the session is open. */
export async function nextPart(index = 0): Promise<FakePart> {
  await vi.waitFor(() => {
    if (!server.parts[index]) throw new Error("no part sent yet");
  });
  return server.parts[index]!;
}
