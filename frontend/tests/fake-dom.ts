// A small DOM for rendering a component in node with react-dom's own createRoot and act()
// (there is no DOM library here, and none is to be added): elements, text, attributes, focus,
// "[attr=value]" lookups, and events that capture and bubble, enough for clicks, typing and
// keys. Used by clean-copy-edit-527.test.ts; install()
// stubs `document` and `window`, which must be in place before react-dom/client is first
// imported (it looks for a DOM as it loads).
// keys. Used by clean-copy-edit-527.test.ts and folder-delete-537.test.ts; install() stubs `document` and `window`, which must
// be in place before react-dom/client is first imported (it looks for a DOM as it loads).

import { vi } from "vitest";

type Listener = { fn: (event: FakeEvent) => void; capture: boolean };

export class FakeEvent {
  defaultPrevented = false;
  propagationStopped = false;
  target: FakeNode | null = null;
  currentTarget: FakeNode | null = null;
  eventPhase = 0;
  timeStamp = 0;
  isTrusted = true;
  readonly bubbles = true;
  readonly cancelable = true;
  constructor(
    readonly type: string,
    init: Record<string, unknown> = {},
  ) {
    Object.assign(this, init);
  }
  preventDefault() {
    this.defaultPrevented = true;
  }
  stopPropagation() {
    this.propagationStopped = true;
  }
}

export class FakeNode {
  parentNode: FakeNode | null = null;
  childNodes: FakeNode[] = [];
  nodeValue: string | null = null;
  private listeners = new Map<string, Listener[]>();
  constructor(
    readonly ownerDocument: FakeDocument | null,
    readonly nodeType: number,
    readonly nodeName: string,
  ) {}
  get firstChild(): FakeNode | null {
    return this.childNodes[0] ?? null;
  }
  get lastChild(): FakeNode | null {
    return this.childNodes.at(-1) ?? null;
  }
  get nextSibling(): FakeNode | null {
    const siblings = this.parentNode?.childNodes ?? [];
    return siblings[siblings.indexOf(this) + 1] ?? null;
  }
  get textContent(): string {
    return this.nodeType === 3 ? (this.nodeValue ?? "") : this.childNodes.map((child) => child.textContent).join("");
  }
  set textContent(value: string) {
    if (this.nodeType === 3) {
      this.nodeValue = value;
      return;
    }
    for (const child of [...this.childNodes]) this.removeChild(child);
    if (value) this.appendChild(this.document.createTextNode(value));
  }
  get document(): FakeDocument {
    return this.ownerDocument ?? (this as unknown as FakeDocument);
  }
  appendChild(child: FakeNode) {
    return this.insertBefore(child, null);
  }
  insertBefore(child: FakeNode, before: FakeNode | null) {
    child.parentNode?.removeChild(child);
    const at = before ? this.childNodes.indexOf(before) : -1;
    this.childNodes.splice(at < 0 ? this.childNodes.length : at, 0, child);
    child.parentNode = this;
    return child;
  }
  removeChild(child: FakeNode) {
    this.childNodes.splice(this.childNodes.indexOf(child), 1);
    child.parentNode = null;
    const doc = this.document;
    if (doc.activeElement && child.contains(doc.activeElement)) doc.activeElement = doc.body; // as a browser does
    return child;
  }
  contains(node: FakeNode | null): boolean {
    for (let at = node; at; at = at.parentNode) if (at === this) return true;
    return false;
  }
  /** Only `input[name="x"][type="radio"]`: react-dom asks it for a radio's group when one is checked (#537). */
  querySelectorAll(selector: string): FakeElement[] {
    const group = /^input\[name="([^"]*)"\]\[type="radio"\]$/.exec(selector);
    if (!group) throw new Error(`fake-dom: no selector ${selector}`);
    const walk = (node: FakeNode): FakeElement[] =>
      node.childNodes.flatMap((child) => (child instanceof FakeElement ? [child, ...walk(child)] : []));
    return walk(this).filter(
      (element) => element.tagName === "INPUT" && element.type === "radio" && element.getAttribute("name") === group[1],
    );
  }
  addEventListener(type: string, fn: (event: FakeEvent) => void, options?: boolean | { capture?: boolean }) {
    const capture = typeof options === "boolean" ? options : Boolean(options?.capture);
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), { fn, capture }]);
  }
  removeEventListener(type: string, fn: (event: FakeEvent) => void) {
    this.listeners.set(type, (this.listeners.get(type) ?? []).filter((listener) => listener.fn !== fn));
  }
  /** Capture from the top down, then bubble up, as a browser does; false when prevented. */
  dispatchEvent(event: FakeEvent): boolean {
    event.target = this;
    const path: FakeNode[] = [this];
    while (path.at(-1)?.parentNode) path.push(path.at(-1)!.parentNode!);
    const run = (node: FakeNode, capture: boolean) => {
      event.currentTarget = node;
      for (const listener of node.listeners.get(event.type) ?? []) if (listener.capture === capture) listener.fn(event);
    };
    for (const node of [...path].reverse()) if (!event.propagationStopped) run(node, true);
    for (const node of path) if (!event.propagationStopped) run(node, false);
    return !event.defaultPrevented;
  }
}

export class FakeElement extends FakeNode {
  readonly attributes = new Map<string, string>();
  readonly style: Record<string, unknown> & { setProperty(name: string, value: string): void } = {
    setProperty(name: string, value: string) {
      this[name] = value;
    },
  };
  // What react-dom sets as properties on a form control.
  value = "";
  defaultValue = "";
  type = "";
  checked = false;
  defaultChecked = false;
  selectionStart = 0;
  selectionEnd = 0;
  scrollLeft = 0;
  scrollTop = 0;
  constructor(
    ownerDocument: FakeDocument,
    readonly tagName: string,
    readonly namespaceURI: string,
  ) {
    super(ownerDocument, 1, tagName);
    if (tagName === "INPUT") this.type = "text"; // a browser's default, so react-dom sees a text box
  }
  setAttribute(name: string, value: unknown) {
    this.attributes.set(name, String(value));
  }
  getAttribute(name: string) {
    return this.attributes.get(name) ?? null;
  }
  hasAttribute(name: string) {
    return this.attributes.has(name);
  }
  removeAttribute(name: string) {
    this.attributes.delete(name);
  }
  focus() {
    this.document.activeElement = this;
  }
  blur() {
    if (this.document.activeElement === this) this.document.activeElement = this.document.body;
  }
  scrollIntoView() {}
  get isConnected() {
    return this.document.contains(this);
  }
  // A <dialog>'s own (ActionDialog).
  open = false;
  showModal() {
    this.open = true;
  }
  close() {
    this.open = false;
  }
  // A <select>'s options and an <option>'s choice (react-dom sets them; the Audience panel's).
  selected = false;
  defaultSelected = false;
  get options(): FakeElement[] {
    return this.all().filter((element) => element.tagName === "OPTION");
  }
  /** Every element under this one, in document order. */
  all(): FakeElement[] {
    return this.childNodes.flatMap((child) => (child instanceof FakeElement ? [child, ...child.all()] : []));
  }
  /** A list of `tag`, `[name]` or `[name=value]` (the value bare or quoted): what the code here asks for. */
  matches(selector: string): boolean {
    return selector.split(",").some((part) => {
      const one = part.trim();
      const found = /^\[([\w-]+)(?:=["']?([^"'\]]*)["']?)?\]$/.exec(one);
      if (found) {
        const [, name = "", value] = found;
        return this.getAttribute(name) !== null && (value === undefined || this.getAttribute(name) === value);
      }
      if (!/^[a-z]+$/i.test(one)) throw new Error(`fake-dom: no selector ${selector}`);
      return this.tagName === one.toUpperCase();
    });
  }
  querySelector(selector: string): FakeElement | null {
    return this.all().find((element) => element.matches(selector)) ?? null;
  }
  querySelectorAll(selector: string): FakeElement[] {
    return this.all().filter((element) => element.matches(selector));
  }
  closest(selector: string): FakeElement | null {
    if (this.matches(selector)) return this;
    return this.parentNode instanceof FakeElement ? this.parentNode.closest(selector) : null;
  }
  // No layout here: every box is empty at the top left (a menu's place, RowMenu).
  getBoundingClientRect() {
    return { top: 0, bottom: 0, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0 };
  }
}

export class FakeDocument extends FakeNode {
  activeElement: FakeElement | null = null;
  oninput = null; // react-dom, as it loads, takes the input event from this (else an old browser's way)
  readonly documentElement: FakeElement;
  readonly body: FakeElement;
  readonly defaultView: unknown = undefined;
  constructor() {
    super(null, 9, "#document");
    this.documentElement = this.appendChild(this.createElement("html")) as FakeElement;
    this.body = this.documentElement.appendChild(this.createElement("body")) as FakeElement;
  }
  createElement(tag: string) {
    return new FakeElement(this, tag.toUpperCase(), "http://www.w3.org/1999/xhtml");
  }
  createElementNS(namespace: string, tag: string) {
    return new FakeElement(this, tag, namespace);
  }
  getElementById(id: string) {
    return this.documentElement.all().find((element) => element.getAttribute("id") === id) ?? null;
  }
  createTextNode(text: string) {
    const node = new FakeNode(this, 3, "#text");
    node.nodeValue = text;
    return node;
  }
}

/** Stub `document` and `window` with a fresh FakeDocument, and tell React it runs in act(). */
export function install(): FakeDocument {
  const doc = new FakeDocument();
  vi.stubGlobal("IS_REACT_ACT_ENVIRONMENT", true);
  vi.stubGlobal("document", doc);
  vi.stubGlobal("HTMLElement", FakeElement);
  vi.stubGlobal("window", { document: doc, HTMLIFrameElement: class {}, addEventListener: () => undefined, removeEventListener: () => undefined });
  return doc;
}

/** The text a person sees in `element` (its text nodes, in order). */
export const textOf = (element: FakeNode) => element.textContent;

/** Fire `type` at `element` (a click, "input", "keydown" with `key`...). */
export function fire(element: FakeElement, type: string, init: Record<string, unknown> = {}) {
  return element.dispatchEvent(new FakeEvent(type, init));
}
