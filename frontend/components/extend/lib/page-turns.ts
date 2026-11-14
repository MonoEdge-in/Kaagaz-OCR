// Changed for Kaagaz (#589): the page turn menu's arithmetic, and the plugin that lays the
// pages out as turned, kept out of the viewer so they are unit tested in node
// (tests/page-turns-589.test.ts). A person's turn of a page is a count of quarter turns to the
// right (1 to 3) on top of the page's own turn in the file (/Rotate); a page they have not
// turned has no entry.
import { BasePlugin, createEmitter, type PluginPackage } from "@embedpdf/core"
import type { PdfPageObject, Rotation } from "@embedpdf/models"

export type PageTurns = ReadonlyMap<number, Rotation>
/** A page as the engine gives it: its size before any turn, and its own turn in the file. */
export type TurnablePage = {
  size: { width: number; height: number }
  rotation: number
}

/** A count of quarter turns as 0 to 3. */
export function normalizeRotation(rotation: number): Rotation {
  return (((rotation % 4) + 4) % 4) as Rotation
}

/** Each of pageIndexes a quarter turn left (-1) or right (1), on top of the turn it has. */
export function turnPages(
  turns: PageTurns,
  pageIndexes: Iterable<number>,
  direction: -1 | 1
): Map<number, Rotation> {
  const next = new Map(turns)
  for (const pageIndex of pageIndexes) {
    const turn = normalizeRotation((turns.get(pageIndex) ?? 0) + direction)
    if (turn) next.set(pageIndex, turn)
    else next.delete(pageIndex)
  }
  return next
}

/**
 * "Make all pages wide" or "tall": a quarter turn right for each page shown the other way, by
 * its shape as shown (its own turn and the person's), not by the way its writing runs. A
 * square page is left as it is.
 */
export function turnPagesTo(
  turns: PageTurns,
  pages: readonly TurnablePage[],
  shape: "wide" | "tall"
): Map<number, Rotation> {
  const wrongShape = pages.flatMap((page, pageIndex) => {
    const sideways = (page.rotation + (turns.get(pageIndex) ?? 0)) % 2 === 1
    const width = sideways ? page.size.height : page.size.width
    const height = sideways ? page.size.width : page.size.height
    const shown = width > height ? "wide" : height > width ? "tall" : null
    return shown && shown !== shape ? [pageIndex] : []
  })
  return turnPages(turns, wrongShape, 1)
}

/** Every page on its own, its turn the file's and the person's together. */
export function turnedSpreadPages(
  pages: readonly PdfPageObject[],
  turns: PageTurns | undefined
): PdfPageObject[][] {
  return pages.map((page, pageIndex) => [
    {
      ...page,
      rotation: normalizeRotation(page.rotation + (turns?.get(pageIndex) ?? 0)),
    },
  ])
}

type LaidOutItem = { offset: number; height: number; pageNumbers: number[] }
/**
 * Where to scroll so the page being read stays put after the pages' sizes or the zoom changed:
 * the same share of it above the top of the view. Offsets and heights are the scroll plugin's,
 * unzoomed; a page's top is at gap + offset x zoom, where the plugin's scrollToPage puts it.
 */
export function scrollTopKeepingPage(
  pageNumber: number,
  scrollTop: number,
  gap: number,
  before: { items: readonly LaidOutItem[]; scale: number },
  after: { items: readonly LaidOutItem[]; scale: number }
): number | null {
  const was = before.items.find((item) => item.pageNumbers.includes(pageNumber))
  const now = after.items.find((item) => item.pageNumbers.includes(pageNumber))
  if (!was || !now) return null
  const share =
    (scrollTop - gap - was.offset * before.scale) / (was.height * before.scale)
  return gap + (now.offset + share * now.height) * after.scale
}

type PageTurnsCapability = {
  forDocument: (documentId: string) => { getSpreadPages: () => PdfPageObject[][] }
  onSpreadChange: (listener: (event: { documentId: string }) => void) => () => void
  setPageTurns: (documentId: string, turns: PageTurns) => void
}
/**
 * The engine's scroll plugin lays pages out by their own turn in the file only, unless a
 * "spread" plugin hands it the pages; so does its zoom plugin's fit to width. This one hands
 * them every page on its own, as they lay them out without it, turned as the person turned it,
 * so page offsets, the space above the drawn pages, the current page, go to page and fit to
 * width all go by the pages as shown. It is not the engine's spread plugin: pages side by side
 * are not used here. setPageTurns lays the document out again.
 */
export class PageTurnsPlugin extends BasePlugin<unknown, PageTurnsCapability> {
  static readonly id = "spread"
  private readonly turns = new Map<string, PageTurns>()
  private readonly turned = createEmitter<{ documentId: string }>()
  async initialize(): Promise<void> {}
  protected buildCapability(): PageTurnsCapability {
    return {
      forDocument: (documentId) => ({
        getSpreadPages: () =>
          turnedSpreadPages(
            this.coreState.core.documents[documentId]?.document?.pages ?? [],
            this.turns.get(documentId)
          ),
      }),
      onSpreadChange: (listener) => this.turned.on(listener),
      setPageTurns: (documentId, turns) => {
        this.turns.set(documentId, turns)
        this.turned.emit({ documentId })
      },
    }
  }
  protected onDocumentClosed(documentId: string): void {
    this.turns.delete(documentId)
  }
}
export const PageTurnsPluginPackage: PluginPackage<PageTurnsPlugin> = {
  manifest: {
    id: PageTurnsPlugin.id,
    name: "Page turns",
    version: "1.0.0",
    provides: ["spread"],
    requires: [],
    optional: [],
    defaultConfig: {},
  },
  create: (registry) => new PageTurnsPlugin(PageTurnsPlugin.id, registry),
  reducer: (state) => state,
  initialState: {},
}
