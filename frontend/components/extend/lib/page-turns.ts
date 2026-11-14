// Changed for Kaagaz (#589): the page turn menu's arithmetic, kept out of the viewer so it is
// unit tested in node (tests/page-turns-589.test.ts). A person's turn of a page is a count of
// quarter turns to the right (1 to 3) on top of the page's own turn in the file (/Rotate); a
// page they have not turned has no entry.
import type { Rotation } from "@embedpdf/models"

export type PageTurns = ReadonlyMap<number, Rotation>
/** A page as the engine gives it: its size before any turn, and its own turn in the file. */
export type TurnablePage = {
  size: { width: number; height: number }
  rotation: number
}

const quarterTurns = (turns: number) => (((turns % 4) + 4) % 4) as Rotation

/** Each of pageIndexes a quarter turn left (-1) or right (1), on top of the turn it has. */
export function turnPages(
  turns: PageTurns,
  pageIndexes: Iterable<number>,
  direction: -1 | 1
): Map<number, Rotation> {
  const next = new Map(turns)
  for (const pageIndex of pageIndexes) {
    const turn = quarterTurns((turns.get(pageIndex) ?? 0) + direction)
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
