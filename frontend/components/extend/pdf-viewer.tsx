"use client"


// Copied from extend-hq/ui (https://github.com/extend-hq/ui), MIT licence: see LICENSE in
// this folder. Changed only to fit this app: import paths, lucide-react icons in place of the
// registry's icon placeholder, the radix-ui package, and the PDF engine served by this app.
import * as React from "react"
import { createPluginRegistration, refreshPages } from "@embedpdf/core"
import { EmbedPDF, useRegistry } from "@embedpdf/core/react"
import type {
  PdfDocumentObject,
  PdfEngine,
  Rect,
  Rotation,
} from "@embedpdf/models"
import {
  DocumentManagerPluginPackage,
  useActiveDocument,
  useDocumentManagerCapability,
} from "@embedpdf/plugin-document-manager/react"
import {
  GlobalPointerProvider,
  InteractionManagerPluginPackage,
  PagePointerProvider,
} from "@embedpdf/plugin-interaction-manager/react"
import { RenderLayer, RenderPluginPackage } from "@embedpdf/plugin-render/react"
import { Rotate, RotatePluginPackage } from "@embedpdf/plugin-rotate/react"
import {
  ScrollPluginPackage,
  ScrollStrategy,
  useScroll,
  useScrollPlugin,
  type PageLayout,
  type ScrollerLayout,
} from "@embedpdf/plugin-scroll/react"
import {
  SearchLayer,
  SearchPluginPackage,
  useSearch,
} from "@embedpdf/plugin-search/react"
import {
  CopyToClipboard,
  SelectionPluginPackage,
  useSelectionCapability,
  useSelectionPlugin,
} from "@embedpdf/plugin-selection/react"
import {
  ThumbImg,
  ThumbnailPluginPackage,
  useThumbnailCapability,
  useThumbnailPlugin,
  type ThumbMeta,
} from "@embedpdf/plugin-thumbnail/react"
import { TilingLayer, TilingPluginPackage } from "@embedpdf/plugin-tiling/react"
import {
  useIsViewportGated,
  useViewportCapability,
  useViewportElement,
  useViewportRef,
  ViewportElementContext,
  ViewportPluginPackage,
} from "@embedpdf/plugin-viewport/react"
import {
  useZoom,
  ZoomMode,
  ZoomPluginPackage,
} from "@embedpdf/plugin-zoom/react"
import { flushSync } from "react-dom"

import {
  holdScrollTop,
  normalizeRotation,
  type PageTurns,
  PageTurnsPlugin,
  PageTurnsPluginPackage,
  scrollTopKeepingPage,
  searchHitTarget,
  type TurnablePage,
  turnPages,
  turnPagesTo,
} from "@/components/extend/lib/page-turns"
import { loadSharedPdfEngine } from "@/components/extend/lib/pdf-thumbnail-utils"
import { cn } from "@/components/extend/lib/utils"
import { Button } from "@/components/extend/ui/button"
import { Input } from "@/components/extend/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/extend/ui/popover"
import { ScrollArea } from "@/components/extend/ui/scroll-area"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/extend/ui/tooltip"
import {
  DocumentViewerSidebarSkeleton,
  DocumentViewerThumbnailSidebar,
  useElementWidth,
  useInlineThumbnailSidebar,
} from "@/components/extend/document-viewer-sidebar"
import {
  ArrowRightIcon,
  ChevronLeftIcon,
  DownloadIcon,
  LoaderCircleIcon,
  MinusIcon,
  PanelLeftIcon,
  PlusIcon,
  RotateCcwIcon,
  RotateCwIcon,
  ScanIcon,
  SearchIcon,
} from "lucide-react"

export type PDFViewerPageOverlayProps = {
  pageNumber: number
  pageWidth: number
  pageHeight: number
  scale: number
  rotation: number
  // Changed for Kaagaz (#442): the page's own turn in the file (/Rotate), in degrees. The
  // overlay is drawn before it, so marks stored on the page as shown are turned back first.
  fileRotation: number
}
export type PDFViewerHandle = {
  scrollToPage: (pageNumber: number, options?: ScrollIntoViewOptions) => void
  scrollToPageArea: (
    pageNumber: number,
    area: {
      top: number
      left?: number
      width?: number
      height?: number
    },
    options?: ScrollToOptions
  ) => void
  getViewportElement: () => HTMLDivElement | null
}
export type PDFViewerScrollAreaViewportResolver = (
  container: HTMLDivElement
) => HTMLDivElement | null
export type PDFViewerZoomLevel = number | "fit-page" | "fit-width" | "automatic"
export type PDFViewerProps = {
  className?: string
  defaultZoom?: PDFViewerZoomLevel
  fileName?: string
  resolveScrollAreaViewport?: PDFViewerScrollAreaViewportResolver
  showDownload?: boolean
  showToolbar?: boolean
  showRotateControls?: boolean
  showUpload?: boolean
  src?: string
  toolbarActions?: React.ReactNode
  // Changed for Kaagaz (#589): the person's turns to open with (page index to quarter turns
  // right), and each change of them, so the screen can keep them.
  pageRotations?: ReadonlyMap<number, number>
  onPageRotationsChange?: (pageRotations: ReadonlyMap<number, number>) => void
  // Changed for Kaagaz (#600): the turn menu waits while the turns are being saved.
  turnMenuDisabled?: boolean
  // Changed for Kaagaz (#709): "Make all pages wide" and "tall", and "Straighten all pages with
  // AI", handed to the screen with the pages (it asks the orientation check), and its status line.
  turnWithAi?: {
    onPick: (choice: TurnWithAiChoice, pages: readonly TurnablePage[]) => void
    status: React.ReactNode
  }
  pageClassName?: (pageNumber: number) => string | undefined
  renderPageOverlay?: (props: PDFViewerPageOverlayProps) => React.ReactNode
  onActivePageChange?: (pageNumber: number) => void
  onDocumentLoadSuccess?: (numPages: number) => void
  onPdfUpload?: (file: File) => void
  onPagePointerDown?: (
    event: React.PointerEvent<HTMLDivElement>,
    pageNumber: number
  ) => void
  onPagePointerMove?: (
    event: React.PointerEvent<HTMLDivElement>,
    pageNumber: number
  ) => void
  onPagePointerUp?: (
    event: React.PointerEvent<HTMLDivElement>,
    pageNumber: number
  ) => void
  onPagePointerCancel?: (
    event: React.PointerEvent<HTMLDivElement>,
    pageNumber: number
  ) => void
}
const DEFAULT_ZOOM = 1
const MIN_ZOOM = 0.1
const MAX_ZOOM = 2
const ZOOM_OPTIONS = [MIN_ZOOM, 0.25, 0.5, 0.75, 1, 1.25, 1.5, MAX_ZOOM]
const PAGE_GAP = 24
const THUMBNAIL_PAGE_WIDTH = 92
const THUMBNAIL_IMAGE_PADDING = 8
const THUMBNAIL_WIDTH = THUMBNAIL_PAGE_WIDTH + THUMBNAIL_IMAGE_PADDING * 2
const THUMBNAIL_LABEL_HEIGHT = 24
const THUMBNAIL_GAP = 12
const THUMBNAIL_PANE_PADDING_Y = 16
const THUMBNAIL_SIDEBAR_WIDTH_CLASS = "w-40"
const THUMBNAIL_SIDEBAR_CLOSED_CLASS = "-ml-40"
const PAGE_BASE_RENDER_MAX_SCALE = 1
const PAGE_BASE_RENDER_DPR = 1
const PDF_SEARCH_DEBOUNCE_MS = 300
const TEXT_SELECTION_BACKGROUND = "rgba(59, 130, 246, 0.14)"
const THUMBNAIL_FOCUS_RING_CLASS =
  "group-focus-visible/pdf-thumbnail-sidebar:ring-2 group-focus-visible/pdf-thumbnail-sidebar:ring-ring group-focus-visible/pdf-thumbnail-sidebar:ring-offset-1 group-focus-visible/pdf-thumbnail-sidebar:ring-offset-background"
const DEFAULT_SCROLL_AREA_VIEWPORT_SELECTOR =
  '[data-slot="scroll-area-viewport"]'
function toZoomLevel(level: PDFViewerZoomLevel): ZoomMode | number {
  if (level === "fit-page") return ZoomMode.FitPage
  if (level === "fit-width") return ZoomMode.FitWidth
  if (level === "automatic") return ZoomMode.Automatic
  return level
}
function resolveDefaultScrollAreaViewport(container: HTMLDivElement) {
  return container.querySelector<HTMLDivElement>(
    DEFAULT_SCROLL_AREA_VIEWPORT_SELECTOR
  )
}
const PDFViewerScrollAreaResolverContext =
  React.createContext<PDFViewerScrollAreaViewportResolver>(
    resolveDefaultScrollAreaViewport
  )
type PageRotationDeltas = Map<number, Rotation>
// Changed for Kaagaz (#589): a choice in the page turn menu. #600: or turns handed in after it
// opened (a save's reply, with others' turns), shown the way a choice is.
type PageTurnChange =
  | { pages: "picked" | "all"; direction: -1 | 1 }
  | "wide"
  | "tall"
  | "reset"
  | { set: ReadonlyMap<number, number> }
  | { ai: TurnWithAiChoice }
type TurnWithAiChoice = "straighten" | "wide" | "tall"
type ThumbnailSelectionMode = "replace" | "toggle" | "range"
function getPageIndexRange(from: number, to: number): Set<number> {
  const start = Math.min(from, to)
  const end = Math.max(from, to)
  const range = new Set<number>()
  for (let pageIndex = start; pageIndex <= end; pageIndex += 1) {
    range.add(pageIndex)
  }
  return range
}
function arePageIndexSetsEqual(left: Set<number>, right: Set<number>) {
  if (left.size !== right.size) return false
  for (const value of left) {
    if (!right.has(value)) return false
  }
  return true
}
function useSharedPdfEngine() {
  const [engine, setEngine] = React.useState<PdfEngine | null>(null)
  const [error, setError] = React.useState<Error | null>(null)
  React.useEffect(() => {
    let cancelled = false
    loadSharedPdfEngine().then(
      (loadedEngine) => {
        if (!cancelled) setEngine(loadedEngine)
      },
      (loadError: Error) => {
        if (!cancelled) setError(loadError)
      }
    )
    return () => {
      cancelled = true
    }
  }, [])
  return { engine, error }
}
function rotationToDegrees(rotation: Rotation) {
  return (rotation as number) * 90
}
function normalizeDegrees(rotation: number) {
  return ((rotation % 360) + 360) % 360
}
function ensurePdfExtension(fileName: string) {
  return fileName.toLowerCase().endsWith(".pdf") ? fileName : `${fileName}.pdf`
}
function getPdfDownloadFileName(fileName: string | undefined, src: string) {
  if (fileName?.trim()) return ensurePdfExtension(fileName.trim())
  const pathname = src.split(/[?#]/)[0] ?? ""
  const rawName = pathname.split("/").pop() || "document.pdf"
  try {
    return ensurePdfExtension(decodeURIComponent(rawName))
  } catch {
    return ensurePdfExtension(rawName)
  }
}
function getRotatedPdfDownloadFileName(fileName: string) {
  return fileName.replace(/\.pdf$/i, "-rotated.pdf")
}
function downloadBlob(blob: Blob, fileName: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = fileName
  anchor.rel = "noopener"
  document.body.append(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}
async function downloadPdfWithPageRotations({
  fileName,
  pageRotationDeltas,
  src,
}: {
  fileName: string
  pageRotationDeltas: PageRotationDeltas
  src: string
}) {
  const response = await fetch(src)
  if (!response.ok) {
    throw new Error(`Failed to download PDF (${response.status})`)
  }
  if (pageRotationDeltas.size === 0) {
    downloadBlob(await response.blob(), fileName)
    return
  }
  const [{ PDFDocument, degrees }, pdfBytes] = await Promise.all([
    import("pdf-lib"),
    response.arrayBuffer(),
  ])
  const pdfDocument = await PDFDocument.load(pdfBytes)
  pdfDocument.getPages().forEach((page, pageIndex) => {
    const rotationDelta = pageRotationDeltas.get(pageIndex)
    if (!rotationDelta) return
    page.setRotation(
      degrees(
        normalizeDegrees(
          page.getRotation().angle + rotationToDegrees(rotationDelta)
        )
      )
    )
  })
  const nextPdfBytes = await pdfDocument.save()
  const nextPdfBuffer = new ArrayBuffer(nextPdfBytes.byteLength)
  new Uint8Array(nextPdfBuffer).set(nextPdfBytes)
  downloadBlob(
    new Blob([nextPdfBuffer], { type: "application/pdf" }),
    getRotatedPdfDownloadFileName(fileName)
  )
}
function getThumbnailMetaForPage({
  page,
  pageIndex,
  rotation,
  width,
  imagePadding,
  labelHeight,
  top,
}: {
  page: PdfDocumentObject["pages"][number]
  pageIndex: number
  rotation: Rotation
  width: number
  imagePadding: number
  labelHeight: number
  top: number
}): ThumbMeta {
  const innerWidth = Math.max(1, width - imagePadding * 2)
  const pageWidth = rotation % 2 === 1 ? page.size.height : page.size.width
  const pageHeight = rotation % 2 === 1 ? page.size.width : page.size.height
  const imageHeight = Math.round(innerWidth * (pageHeight / pageWidth))
  const wrapperHeight = imagePadding + imageHeight + imagePadding + labelHeight
  return {
    pageIndex,
    width: innerWidth,
    height: imageHeight,
    wrapperHeight,
    top,
    labelHeight,
    padding: imagePadding,
  }
}
function buildThumbnailLayout({
  basePageRotations,
  pageRotationDeltas,
  pdfDocument,
  width,
  gap,
  imagePadding,
  labelHeight,
  paddingY,
}: {
  basePageRotations: Rotation[]
  pageRotationDeltas: PageRotationDeltas
  pdfDocument: PdfDocumentObject | null
  width: number
  gap: number
  imagePadding: number
  labelHeight: number
  paddingY: number
}) {
  if (!pdfDocument) return null
  let top = paddingY
  const items = pdfDocument.pages.map((page, pageIndex) => {
    const basePageRotation =
      basePageRotations[pageIndex] ?? normalizeRotation(page.rotation)
    const pageRotation = normalizeRotation(
      basePageRotation + (pageRotationDeltas.get(pageIndex) ?? 0)
    )
    const meta = getThumbnailMetaForPage({
      page,
      pageIndex,
      rotation: pageRotation,
      width,
      imagePadding,
      labelHeight,
      top,
    })
    top += meta.wrapperHeight + gap
    return meta
  })
  return {
    items,
    totalHeight: items.length ? top - gap + paddingY : paddingY * 2,
  }
}
function getVisibleThumbnailItems({
  buffer,
  clientHeight,
  items,
  scrollTop,
}: {
  buffer: number
  clientHeight: number
  items: ThumbMeta[]
  scrollTop: number
}) {
  if (items.length === 0) return []
  if (clientHeight <= 0)
    return items.slice(0, Math.min(items.length, buffer * 2))
  const viewportBottom = scrollTop + clientHeight
  let start = items.findIndex(
    (item) => item.top + item.wrapperHeight >= scrollTop
  )
  if (start === -1) start = items.length - 1
  let end = start
  while (end < items.length && (items[end]?.top ?? Infinity) <= viewportBottom) {
    end += 1
  }
  return items.slice(
    Math.max(0, start - buffer),
    Math.min(items.length, end + buffer)
  )
}
function PDFViewerLoadingSkeleton({
  sidebarOpen,
  sidebarInline,
}: {
  sidebarOpen: boolean
  sidebarInline: boolean
}) {
  return (
    <div className="absolute inset-0 z-20 flex bg-muted/30">
      {sidebarOpen ? (
        <DocumentViewerSidebarSkeleton
          className={THUMBNAIL_SIDEBAR_WIDTH_CLASS}
          inline={sidebarInline}
        />
      ) : null}
      <div className="grid min-w-0 flex-1 place-items-center">
        <InlineSpinner className="size-4" />
      </div>
    </div>
  )
}
// Rendered while the engine or document is not ready: same frame and controls
// as the full viewer, with document-dependent controls disabled.
function PDFViewerFallbackShell({
  className,
  defaultZoom,
  errorMessage = "Unable to load the PDF preview.",
  showDownload,
  showRotateControls,
  showToolbar,
  showUpload,
  sidebarOpen,
  state,
  toolbarActions,
  onUploadFile,
}: {
  className?: string
  defaultZoom: PDFViewerZoomLevel
  errorMessage?: string
  showDownload: boolean
  showRotateControls: boolean
  showToolbar: boolean
  showUpload: boolean
  sidebarOpen: boolean
  state: "loading" | "error" | "empty"
  toolbarActions?: React.ReactNode
  onUploadFile?: (file: File) => void
}) {
  return (
    <div
      data-slot="pdf-viewer"
      className={cn(
        "flex h-full max-h-full min-h-0 w-full flex-col overflow-hidden bg-background",
        className
      )}
    >
      {showToolbar ? (
        <PDFViewerToolbar
          activePage={1}
          controlsDisabled
          currentZoomLevel={
            typeof defaultZoom === "number" ? defaultZoom : DEFAULT_ZOOM
          }
          zoomLevel={toZoomLevel(defaultZoom)}
          numPages={0}
          searchControl={
            <ToolbarTooltip label="Search text">
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label="Search text"
                disabled
              >
                <SearchIcon
                  className="size-4"
                />
              </Button>
            </ToolbarTooltip>
          }
          sidebarOpen={sidebarOpen}
          showDownload={showDownload}
          showRotateControls={showRotateControls}
          showUpload={showUpload}
          toolbarActions={toolbarActions}
          onPageChange={() => undefined}
          onRotate={() => undefined}
          onToggleSidebar={() => undefined}
          onUploadFile={onUploadFile}
          onZoomChange={() => undefined}
        />
      ) : null}
      <div className="relative flex min-h-0 flex-1 overflow-hidden bg-muted/30">
        {state === "loading" ? (
          <PDFViewerLoadingSkeleton sidebarInline sidebarOpen={sidebarOpen} />
        ) : null}
        {state === "error" ? (
          <div className="absolute inset-0 z-20 grid place-items-center bg-background p-6 text-sm text-muted-foreground">
            {errorMessage}
          </div>
        ) : null}
        {state === "empty" ? (
          <div className="absolute inset-0 z-20 grid place-items-center bg-background p-6 text-center text-sm text-muted-foreground">
            <div className="max-w-sm space-y-3">
              <div className="font-medium text-foreground">
                Upload a PDF to preview
              </div>
              <div>
                Pass a PDF URL with the <code>src</code> prop or use the upload
                control.
              </div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  )
}
function ToolbarTooltip({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">{children}</span>
      </TooltipTrigger>
      <TooltipContent side="bottom">{label}</TooltipContent>
    </Tooltip>
  )
}
function PDFViewerPageNumberControl({
  activePage,
  controlsDisabled,
  numPages,
  onPageChange,
}: {
  activePage: number
  controlsDisabled: boolean
  numPages: number
  onPageChange: (pageNumber: number) => void
}) {
  const inputRef = React.useRef<HTMLInputElement>(null)
  const displayPage = numPages ? activePage : 1
  const [isEditing, setIsEditing] = React.useState(false)
  const [draftPage, setDraftPage] = React.useState(() => String(displayPage))
  React.useEffect(() => {
    if (!isEditing) return
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [isEditing])
  const applyPageDraft = React.useCallback(
    (value: string) => {
      const trimmedValue = value.trim()
      if (!trimmedValue) return
      const parsedPage = Number(trimmedValue)
      if (!Number.isInteger(parsedPage)) return
      onPageChange(Math.min(Math.max(parsedPage, 1), Math.max(numPages, 1)))
    },
    [numPages, onPageChange]
  )
  return (
    <div className="flex items-center gap-1.5 text-sm whitespace-nowrap">
      {isEditing ? (
        <Input
          ref={inputRef}
          aria-label="Page number"
          inputMode="numeric"
          pattern="[0-9]*"
          value={draftPage}
          onBlur={() => setIsEditing(false)}
          onChange={(event: React.ChangeEvent<HTMLInputElement>) => {
            const nextValue = event.target.value
            setDraftPage(nextValue)
            applyPageDraft(nextValue)
          }}
          onKeyDown={(event: React.KeyboardEvent<HTMLInputElement>) => {
            if (event.key === "Enter" || event.key === "Escape") {
              event.currentTarget.blur()
            }
          }}
          className="h-7 w-12 min-w-12 rounded-full border-0 bg-viewer-bar-field px-1 text-center text-viewer-bar-ink"
        />
      ) : (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 min-w-9 rounded-full bg-viewer-bar-field px-2 font-normal tabular-nums"
          aria-label={`Current page ${displayPage}. Edit page number`}
          disabled={controlsDisabled || !numPages}
          onClick={() => {
            setDraftPage(String(displayPage))
            setIsEditing(true)
          }}
        >
          {displayPage}
        </Button>
      )}
      <span className="tabular-nums">/ {numPages || "–"}</span>
    </div>
  )
}
// Changed for Kaagaz (#589): a hit on a page the person turned goes to its page's top
// (searchHitTarget).
function PDFViewerSearchControl({
  documentId,
  controlsDisabled,
  pageTurns,
}: {
  documentId: string
  controlsDisabled: boolean
  pageTurns: PageTurns
}) {
  const { state, provides } = useSearch(documentId)
  const { provides: scroll } = useScroll(documentId)
  const pageTurnsRef = React.useRef(pageTurns)
  React.useEffect(() => {
    pageTurnsRef.current = pageTurns
  }, [pageTurns])
  const [searchDraft, setSearchDraft] = React.useState("")
  const [searchQuery, setSearchQuery] = React.useState("")
  const [isSearching, setIsSearching] = React.useState(false)
  const providesRef = React.useRef(provides)
  const scrollRef = React.useRef(scroll)
  const searchRequestIdRef = React.useRef(0)
  const hasActiveQuery = Boolean(searchQuery.trim())
  const resultLabel = isSearching
    ? "Searching"
    : !hasActiveQuery
      ? "No search"
      : state.total
        ? `${state.activeResultIndex + 1} / ${state.total}`
        : "No results"
  const scrollToResult = React.useCallback(
    (index: number) => {
      const result = state.results[index]
      if (!result || !scroll) return
      scroll.scrollToPage({
        ...searchHitTarget(result, pageTurnsRef.current),
        behavior: "auto",
      })
    },
    [scroll, state.results]
  )
  React.useEffect(() => {
    providesRef.current = provides
    scrollRef.current = scroll
  }, [provides, scroll])
  const runSearch = React.useCallback((rawQuery: string) => {
    const query = rawQuery.trim()
    const requestId = searchRequestIdRef.current + 1
    searchRequestIdRef.current = requestId
    setSearchQuery(query)
    const searchProvider = providesRef.current
    const scrollProvider = scrollRef.current
    if (!searchProvider) {
      setIsSearching(false)
      return
    }
    if (!query) {
      searchProvider.stopSearch()
      setIsSearching(false)
      return
    }
    setIsSearching(true)
    searchProvider.startSearch()
    searchProvider.searchAllPages(query).wait(
      (result) => {
        if (searchRequestIdRef.current !== requestId) return
        const firstResult = result.results[0]
        if (firstResult && scrollProvider) {
          searchProvider.goToResult(0)
          scrollProvider.scrollToPage({
            ...searchHitTarget(firstResult, pageTurnsRef.current),
            behavior: "auto",
          })
        }
        setIsSearching(false)
      },
      () => {
        if (searchRequestIdRef.current !== requestId) return
        setIsSearching(false)
      }
    )
  }, [])
  React.useEffect(() => {
    if (!searchDraft.trim()) return
    const timeoutId = window.setTimeout(() => {
      runSearch(searchDraft)
    }, PDF_SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timeoutId)
  }, [runSearch, searchDraft])
  const handleSearchDraftChange = React.useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const nextDraft = event.target.value
      setSearchDraft(nextDraft)
      if (nextDraft.trim()) {
        setIsSearching(true)
        return
      }
      searchRequestIdRef.current += 1
      setSearchQuery("")
      setIsSearching(false)
      provides?.stopSearch()
    },
    [provides]
  )
  const clearSearch = React.useCallback(() => {
    searchRequestIdRef.current += 1
    setSearchDraft("")
    setSearchQuery("")
    setIsSearching(false)
    provides?.stopSearch()
  }, [provides])
  const navigate = React.useCallback(
    (direction: 1 | -1) => {
      if (!provides || state.total === 0) return
      const index =
        direction === 1 ? provides.nextResult() : provides.previousResult()
      scrollToResult(index)
    },
    [provides, scrollToResult, state.total]
  )
  return (
    <Popover>
      <ToolbarTooltip label="Search text">
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="Search text"
            disabled={controlsDisabled}
          >
            <SearchIcon
              className="size-4"
            />
          </Button>
        </PopoverTrigger>
      </ToolbarTooltip>
      <PopoverContent align="end" className="w-72">
        <div className="space-y-3">
          <Input
            placeholder="Search text"
            value={searchDraft}
            onChange={handleSearchDraftChange}
            onKeyDown={(event) => {
              if (event.key !== "Enter") return
              event.preventDefault()
              if (event.shiftKey && state.total) {
                navigate(-1)
              } else if (state.total) {
                navigate(1)
              } else if (searchDraft.trim()) {
                runSearch(searchDraft)
              }
            }}
          />
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0 text-xs text-muted-foreground">
              <div className="truncate">
                {state.total ? (
                  <>
                    <span className="text-primary">
                      {state.activeResultIndex + 1}
                    </span>
                    {` / ${state.total}`}
                  </>
                ) : (
                  resultLabel
                )}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1">
              <Button
                type="button"
                variant="outline"
                size="icon-sm"
                aria-label="Previous result"
                title="Previous result"
                disabled={isSearching || state.total === 0}
                onClick={() => navigate(-1)}
              >
                <ChevronLeftIcon
                  className="size-4"
                />
              </Button>
              <Button
                type="button"
                variant="outline"
                size="icon-sm"
                aria-label="Next result"
                title="Next result"
                disabled={isSearching || state.total === 0}
                onClick={() => navigate(1)}
              >
                <ArrowRightIcon
                  className="size-4"
                />
              </Button>
            </div>
          </div>
          <div className="flex justify-end">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={clearSearch}
            >
              Clear
            </Button>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  )
}
// Changed for Kaagaz (23 Oct 2026): one dark bar, as the browser's own PDF viewer. The pages
// sidebar at the start (the screen shows the file's name beside the viewer); page, zoom, fit
// and rotate next; search, the app's own actions, and download at the end. Plain icons, no
// circles or pills; on a narrow screen the icons wrap to a second row.
const TOOLBAR_BUTTON =
  "rounded-full text-viewer-bar-ink hover:bg-viewer-bar-field hover:text-viewer-bar-ink disabled:opacity-40"
function ToolbarDivider() {
  return <span aria-hidden="true" className="mx-1 h-5 w-px flex-none bg-white/20" />
}
// Changed for Kaagaz (#589): the rotate button opens a small menu of page turns. It stays open
// after a choice, so "Turn right" twice puts an upside-down page right in two clicks. "This
// page" is the pages picked in the sidebar when there are several, as the button was.
function PDFViewerTurnMenu({
  controlsDisabled,
  pickedPages,
  onRotate,
  turnWithAi,
}: {
  controlsDisabled: boolean
  pickedPages: number
  onRotate: (change: PageTurnChange) => void
  turnWithAi?: PDFViewerProps["turnWithAi"]
}) {
  const id = React.useId()
  const rows = [
    {
      pages: "picked",
      label: pickedPages > 1 ? `${pickedPages} picked pages` : "This page",
    },
    { pages: "all", label: "All pages" },
  ] as const
  return (
    <Popover>
      <ToolbarTooltip label="Turn pages">
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label="Turn pages"
            className={TOOLBAR_BUTTON}
            disabled={controlsDisabled}
          >
            <RotateCcwIcon className="size-4" />
          </Button>
        </PopoverTrigger>
      </ToolbarTooltip>
      <PopoverContent
        align="start"
        aria-label="Turn pages"
        className="flex w-auto flex-col gap-1 p-2"
      >
        {rows.map(({ pages, label }) => (
          <div
            key={pages}
            role="group"
            aria-labelledby={`${id}-${pages}`}
            className="flex items-center gap-1"
          >
            <span
              id={`${id}-${pages}`}
              className="flex-1 px-2 text-sm whitespace-nowrap"
            >
              {label}
            </span>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={controlsDisabled}
              onClick={() => onRotate({ pages, direction: -1 })}
            >
              <RotateCcwIcon />
              Turn left
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={controlsDisabled}
              onClick={() => onRotate({ pages, direction: 1 })}
            >
              <RotateCwIcon />
              Turn right
            </Button>
          </div>
        ))}
        <span aria-hidden="true" className="my-1 h-px bg-border" />
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="justify-start"
          disabled={controlsDisabled}
          onClick={() => onRotate(turnWithAi ? { ai: "wide" } : "wide")}
        >
          Make all pages wide
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="justify-start"
          disabled={controlsDisabled}
          onClick={() => onRotate(turnWithAi ? { ai: "tall" } : "tall")}
        >
          Make all pages tall
        </Button>
        {turnWithAi ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="justify-start"
            disabled={controlsDisabled}
            onClick={() => onRotate({ ai: "straighten" })}
          >
            Straighten all pages with AI
          </Button>
        ) : null}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="justify-start"
          disabled={controlsDisabled}
          onClick={() => onRotate("reset")}
        >
          Reset to original
        </Button>
        {turnWithAi?.status}
      </PopoverContent>
    </Popover>
  )
}
function PDFViewerToolbar({
  activePage,
  controlsDisabled,
  currentZoomLevel,
  downloadDisabled = controlsDisabled,
  isPreparingDownload = false,
  numPages,
  pickedPages = 1,
  searchControl,
  sidebarOpen,
  showDownload,
  showRotateControls,
  toolbarActions,
  onDownload,
  onPageChange,
  onRotate,
  turnMenuDisabled = false,
  turnWithAi,
  onToggleSidebar,
  onZoomChange,
  zoomLevel,
}: {
  activePage: number
  controlsDisabled: boolean
  currentZoomLevel: number
  downloadDisabled?: boolean
  isPreparingDownload?: boolean
  numPages: number
  pickedPages?: number
  searchControl: React.ReactNode
  sidebarOpen: boolean
  showDownload: boolean
  showRotateControls: boolean
  showUpload?: boolean
  toolbarActions?: React.ReactNode
  onDownload?: () => void
  onPageChange: (pageNumber: number) => void
  onRotate: (change: PageTurnChange) => void
  turnMenuDisabled?: boolean
  turnWithAi?: PDFViewerProps["turnWithAi"]
  onToggleSidebar: () => void
  onUploadFile?: (file: File) => void
  onZoomChange: (zoomLevel: ZoomMode | number) => void
  zoomLevel: ZoomMode | number
}) {
  const fitsPage = zoomLevel === ZoomMode.FitPage
  return (
    <div
      className={cn(
        "flex min-h-12 flex-wrap items-center gap-x-1 gap-y-1 border-b border-border bg-viewer-bar px-2 py-1.5 text-viewer-bar-ink",
        "[&_:is(button,a)]:rounded-full [&_:is(button,a)]:text-viewer-bar-ink [&_:is(button,a):hover]:bg-viewer-bar-field [&_:is(button,a):hover]:text-viewer-bar-ink [&_button[aria-pressed=true]]:bg-viewer-bar-field [&_button[aria-expanded=true]]:bg-viewer-bar-field"
      )}
    >
      <TooltipProvider>
        <div className="contents">
          <ToolbarTooltip label={sidebarOpen ? "Hide pages" : "Show pages"}>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label="Pages"
              aria-pressed={sidebarOpen}
              className={cn(TOOLBAR_BUTTON, sidebarOpen && "bg-viewer-bar-field")}
              disabled={controlsDisabled}
              onClick={onToggleSidebar}
            >
              <PanelLeftIcon className="size-4" />
            </Button>
          </ToolbarTooltip>
        </div>
        <div className="contents">
          <PDFViewerPageNumberControl
            activePage={activePage}
            controlsDisabled={controlsDisabled}
            numPages={numPages}
            onPageChange={onPageChange}
          />
          <ToolbarDivider />
          <ToolbarTooltip label="Zoom out">
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label="Zoom out"
              className={TOOLBAR_BUTTON}
              disabled={controlsDisabled || currentZoomLevel <= MIN_ZOOM}
              onClick={() => {
                const nextZoom = [...ZOOM_OPTIONS].reverse().find((option) => option < currentZoomLevel)
                onZoomChange(nextZoom ?? MIN_ZOOM)
              }}
            >
              <MinusIcon className="size-4" />
            </Button>
          </ToolbarTooltip>
          <span
            className="min-w-14 rounded-full bg-viewer-bar-field px-2 py-1 text-center text-sm tabular-nums"
            aria-live="polite"
          >
            {Math.round(currentZoomLevel * 100)}%
          </span>
          <ToolbarTooltip label="Zoom in">
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label="Zoom in"
              className={TOOLBAR_BUTTON}
              disabled={controlsDisabled || currentZoomLevel >= MAX_ZOOM}
              onClick={() => {
                const nextZoom = ZOOM_OPTIONS.find((option) => option > currentZoomLevel)
                onZoomChange(nextZoom ?? MAX_ZOOM)
              }}
            >
              <PlusIcon className="size-4" />
            </Button>
          </ToolbarTooltip>
          <ToolbarDivider />
          <ToolbarTooltip label={fitsPage ? "Fit to width" : "Fit to page"}>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={fitsPage ? "Fit to width" : "Fit to page"}
              className={TOOLBAR_BUTTON}
              disabled={controlsDisabled}
              onClick={() => onZoomChange(fitsPage ? ZoomMode.FitWidth : ZoomMode.FitPage)}
            >
              <ScanIcon className="size-4" />
            </Button>
          </ToolbarTooltip>
          {showRotateControls ? (
            <PDFViewerTurnMenu
              controlsDisabled={controlsDisabled || turnMenuDisabled}
              pickedPages={pickedPages}
              onRotate={onRotate}
              turnWithAi={turnWithAi}
            />
          ) : null}
        </div>
        <span aria-hidden="true" className="flex-1" />
        <div className="contents">
          {searchControl}
          {toolbarActions}
          {showDownload ? (
            <ToolbarTooltip label="Download">
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label="Download"
                className={TOOLBAR_BUTTON}
                disabled={downloadDisabled}
                onClick={onDownload}
              >
                {isPreparingDownload ? <InlineSpinner className="size-4" /> : <DownloadIcon className="size-4" />}
              </Button>
            </ToolbarTooltip>
          ) : null}
        </div>
      </TooltipProvider>
    </div>
  )
}
function setPdfViewerRef<T>(ref: React.Ref<T> | undefined, value: T | null) {
  if (!ref) return
  if (typeof ref === "function") {
    ref(value)
  } else {
    ref.current = value
  }
}
function PDFViewerScrollArea({
  children,
  className,
  viewportClassName,
  viewportProps,
  viewportRef,
}: {
  children: React.ReactNode
  className?: string
  viewportClassName?: string
  viewportProps?: React.HTMLAttributes<HTMLDivElement>
  viewportRef?: React.Ref<HTMLDivElement>
}) {
  const resolveScrollAreaViewport = React.useContext(
    PDFViewerScrollAreaResolverContext
  )
  const containerRef = React.useRef<HTMLDivElement | null>(null)
  const { className: viewportPropsClassName, ...resolvedViewportProps } =
    viewportProps ?? {}
  const setViewportRef = React.useCallback(
    (viewport: HTMLDivElement | null) => {
      setPdfViewerRef(viewportRef, viewport)
    },
    [viewportRef]
  )
  React.useLayoutEffect(() => {
    const container = containerRef.current
    if (!container) return
    const viewport = resolveScrollAreaViewport(container)
    if (!viewport) {
      console.error(
        `PDFViewer could not resolve the scroll viewport. Add ${DEFAULT_SCROLL_AREA_VIEWPORT_SELECTOR} to your ScrollArea viewport or pass resolveScrollAreaViewport.`
      )
      return
    }
    setViewportRef(viewport)
    return () => setViewportRef(null)
  }, [resolveScrollAreaViewport, setViewportRef])
  return (
    <div
      ref={containerRef}
      data-slot="pdf-viewer-scroll-area"
      className={cn("size-full min-h-0", className)}
    >
      <ScrollArea className="size-full min-h-0">
        <div
          {...resolvedViewportProps}
          data-slot="pdf-viewer-scroll-content"
          className={cn(
            "min-h-full",
            viewportPropsClassName,
            viewportClassName
          )}
        >
          {children}
        </div>
      </ScrollArea>
    </div>
  )
}
function PDFViewerThumbnails({
  basePageRotations,
  documentId,
  activePage,
  pageCount,
  pageRotationDeltas,
  pdfDocument,
  selectedPageIndexes,
  onSelectPage,
}: {
  basePageRotations: Rotation[]
  documentId: string
  activePage: number
  pageCount: number
  pageRotationDeltas: PageRotationDeltas
  pdfDocument: PdfDocumentObject | null
  selectedPageIndexes: Set<number>
  onSelectPage: (pageNumber: number, mode: ThumbnailSelectionMode) => void
}) {
  const thumbnailListboxId = React.useId()
  const activeDescendantId =
    activePage > 0 ? `${thumbnailListboxId}-page-${activePage}` : undefined
  const handleKeyDown = React.useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      if (pageCount < 1) return
      const currentPage = activePage > 0 ? activePage : 1
      let nextPage: number | null = null
      if (event.key === "ArrowDown") {
        nextPage = Math.min(pageCount, currentPage + 1)
      } else if (event.key === "ArrowUp") {
        nextPage = Math.max(1, currentPage - 1)
      } else if (event.key === "Home") {
        nextPage = 1
      } else if (event.key === "End") {
        nextPage = pageCount
      } else if (event.key === " ") {
        event.preventDefault()
        onSelectPage(currentPage, "toggle")
        return
      }
      if (nextPage === null) return
      event.preventDefault()
      onSelectPage(nextPage, event.shiftKey ? "range" : "replace")
    },
    [activePage, onSelectPage, pageCount]
  )
  return (
    <PDFViewerThumbnailScrollArea
      activeDescendantId={activeDescendantId}
      basePageRotations={basePageRotations}
      documentId={documentId}
      onKeyDown={handleKeyDown}
      pageRotationDeltas={pageRotationDeltas}
      pdfDocument={pdfDocument}
    >
      {(meta: ThumbMeta) => {
        const pageNumber = meta.pageIndex + 1
        const isActive = pageNumber === activePage
        const isSelected = selectedPageIndexes.has(meta.pageIndex)
        const imagePadding = meta.padding ?? 0
        const pageRotationDelta = pageRotationDeltas.get(meta.pageIndex) ?? 0
        const thumbnailImageStyle: React.CSSProperties =
          pageRotationDelta % 2 === 1
            ? {
                height: meta.width,
                transform: `rotate(${rotationToDegrees(pageRotationDelta)}deg)`,
                width: meta.height,
              }
            : {
                height: meta.height,
                transform:
                  pageRotationDelta === 0
                    ? undefined
                    : `rotate(${rotationToDegrees(pageRotationDelta)}deg)`,
                width: meta.width,
              }
        return (
          <div
            key={meta.pageIndex}
            data-pdf-viewer-thumbnail={pageNumber}
            className={cn(
              "absolute right-0 left-0 flex justify-center",
              isActive && "z-10"
            )}
            style={{ top: meta.top, height: meta.wrapperHeight }}
          >
            <div
              id={`${thumbnailListboxId}-page-${pageNumber}`}
              role="option"
              data-pdf-viewer-thumbnail-option={pageNumber}
              aria-current={isActive ? "page" : undefined}
              aria-label={`Page ${pageNumber}`}
              aria-posinset={pageNumber}
              aria-selected={isSelected}
              aria-setsize={pageCount}
              data-selected={isSelected ? "" : undefined}
              className={cn(
                "flex h-full w-full cursor-default flex-col items-center justify-between rounded-md px-2 py-0 text-xs transition-shadow outline-none select-none hover:bg-sidebar-accent",
                isActive || isSelected
                  ? "bg-sidebar-accent text-foreground"
                  : "text-muted-foreground",
                isActive && THUMBNAIL_FOCUS_RING_CLASS
              )}
              onClick={(event) => {
                const mode = event.shiftKey
                  ? "range"
                  : event.metaKey || event.ctrlKey
                    ? "toggle"
                    : "replace"
                onSelectPage(pageNumber, mode)
              }}
            >
              <span
                className="mt-0 flex items-center justify-center overflow-hidden rounded-md bg-transparent"
                style={{
                  width: meta.width + imagePadding * 2,
                  height: meta.height + imagePadding * 2,
                  padding: imagePadding,
                }}
              >
                <ThumbImg
                  documentId={documentId}
                  meta={meta}
                  className="block rounded-sm object-contain"
                  style={thumbnailImageStyle}
                />
              </span>
              <span
                className="flex items-center justify-center tabular-nums"
                style={{ height: meta.labelHeight }}
              >
                <span className="flex min-w-5 items-center justify-center px-1.5 text-center leading-5">
                  {pageNumber}
                </span>
              </span>
            </div>
          </div>
        )
      }}
    </PDFViewerThumbnailScrollArea>
  )
}
function PDFViewerThumbnailScrollArea({
  activeDescendantId,
  basePageRotations,
  children,
  documentId,
  onKeyDown,
  pageRotationDeltas,
  pdfDocument,
}: {
  activeDescendantId?: string
  basePageRotations: Rotation[]
  children: (meta: ThumbMeta) => React.ReactNode
  documentId: string
  onKeyDown: React.KeyboardEventHandler<HTMLDivElement>
  pageRotationDeltas: PageRotationDeltas
  pdfDocument: PdfDocumentObject | null
}) {
  const { plugin: thumbnailPlugin } = useThumbnailPlugin()
  const viewportRef = React.useRef<HTMLDivElement | null>(null)
  const [viewportMetrics, setViewportMetrics] = React.useState({
    clientHeight: 0,
    scrollTop: 0,
  })
  const thumbnailScope = React.useMemo(
    () => thumbnailPlugin?.provides().forDocument(documentId) ?? null,
    [documentId, thumbnailPlugin]
  )
  const windowState = React.useSyncExternalStore(
    React.useCallback(
      (onStoreChange) => {
        if (!thumbnailScope) return () => undefined
        return thumbnailScope.onWindow(() => onStoreChange())
      },
      [thumbnailScope]
    ),
    React.useCallback(
      () => thumbnailScope?.getWindow() ?? null,
      [thumbnailScope]
    ),
    () => null
  )
  const hasWindowState = Boolean(windowState)
  const paddingY = thumbnailPlugin?.cfg.paddingY ?? 0
  const thumbnailLayout = React.useMemo(
    () =>
      buildThumbnailLayout({
        basePageRotations,
        pageRotationDeltas,
        pdfDocument,
        width: thumbnailPlugin?.cfg.width ?? THUMBNAIL_WIDTH,
        gap: thumbnailPlugin?.cfg.gap ?? THUMBNAIL_GAP,
        imagePadding: thumbnailPlugin?.cfg.imagePadding ?? 0,
        labelHeight: thumbnailPlugin?.cfg.labelHeight ?? THUMBNAIL_LABEL_HEIGHT,
        paddingY,
      }),
    [
      basePageRotations,
      pageRotationDeltas,
      pdfDocument,
      paddingY,
      thumbnailPlugin,
    ]
  )
  const effectiveWindowState = React.useMemo(() => {
    if (!thumbnailLayout) return windowState
    const items = getVisibleThumbnailItems({
      buffer: thumbnailPlugin?.cfg.buffer ?? 3,
      clientHeight: viewportMetrics.clientHeight,
      items: thumbnailLayout.items,
      scrollTop: viewportMetrics.scrollTop,
    })
    return {
      start: items[0]?.pageIndex ?? -1,
      end: items.at(-1)?.pageIndex ?? -1,
      items,
      totalHeight: thumbnailLayout.totalHeight,
    }
  }, [thumbnailLayout, thumbnailPlugin, viewportMetrics, windowState])
  React.useEffect(() => {
    const viewport = viewportRef.current
    if (!viewport || !thumbnailScope) return
    const updateWindow = () => {
      setViewportMetrics({
        clientHeight: viewport.clientHeight,
        scrollTop: viewport.scrollTop,
      })
      thumbnailScope.updateWindow(viewport.scrollTop, viewport.clientHeight)
    }
    // Changed for Kaagaz: passive, so the thumbnails' scroll is never held for it.
    viewport.addEventListener("scroll", updateWindow, { passive: true })
    const frame = window.requestAnimationFrame(updateWindow)
    return () => {
      window.cancelAnimationFrame(frame)
      viewport.removeEventListener("scroll", updateWindow)
    }
  }, [thumbnailScope])
  React.useEffect(() => {
    const viewport = viewportRef.current
    if (!viewport || !thumbnailScope) return
    const resizeObserver = new ResizeObserver(() => {
      setViewportMetrics({
        clientHeight: viewport.clientHeight,
        scrollTop: viewport.scrollTop,
      })
      thumbnailScope.updateWindow(viewport.scrollTop, viewport.clientHeight)
    })
    resizeObserver.observe(viewport)
    return () => resizeObserver.disconnect()
  }, [thumbnailScope])
  React.useEffect(() => {
    const viewport = viewportRef.current
    if (!viewport || !thumbnailScope) return
    thumbnailScope.updateWindow(viewport.scrollTop, viewport.clientHeight)
  }, [thumbnailLayout, thumbnailScope, windowState])
  React.useEffect(() => {
    const viewport = viewportRef.current
    if (!viewport || !thumbnailScope || !hasWindowState) return
    return thumbnailScope.onScrollTo(({ top, behavior }) => {
      viewport.scrollTo({ top, behavior })
    })
  }, [hasWindowState, thumbnailScope])
  return (
    <PDFViewerScrollArea
      className="h-full w-full"
      viewportClassName="group/pdf-thumbnail-sidebar px-4 focus-visible:ring-0 focus-visible:ring-offset-0"
      viewportProps={{
        "aria-activedescendant": activeDescendantId,
        "aria-label": "PDF pages",
        "aria-multiselectable": true,
        onKeyDown,
        onMouseDown: (event) => {
          event.currentTarget.focus({ preventScroll: true })
        },
        role: "listbox",
        style: {
          paddingBottom: paddingY,
          paddingTop: paddingY,
        },
        tabIndex: 0,
      }}
      viewportRef={viewportRef}
    >
      <div
        className="relative"
        style={{ height: effectiveWindowState?.totalHeight ?? 0 }}
      >
        {effectiveWindowState?.items.map((meta) => children(meta))}
      </div>
    </PDFViewerScrollArea>
  )
}
function PDFViewerScrollAreaViewport({
  children,
  className,
  documentId,
}: {
  children: React.ReactNode
  className?: string
  documentId: string
}) {
  const viewportRef = useViewportRef(documentId)
  const { provides: viewport } = useViewportCapability()
  const isGated = useIsViewportGated(documentId)
  const viewportGap = viewport?.getViewportGap() ?? 0
  return (
    <ViewportElementContext.Provider value={viewportRef}>
      <PDFViewerScrollArea
        className={className}
        viewportClassName="relative select-none selection:bg-transparent selection:text-inherit"
        viewportProps={{
          style: {
            padding: viewportGap,
          },
        }}
        viewportRef={viewportRef}
      >
        {isGated ? null : children}
      </PDFViewerScrollArea>
    </ViewportElementContext.Provider>
  )
}
// Captures the scrollable viewport element so the imperative handle can expose
// it.
function PDFViewerViewportBridge({
  viewportElementRef,
}: {
  viewportElementRef: React.MutableRefObject<HTMLDivElement | null>
}) {
  const elementRef = useViewportElement()
  React.useEffect(() => {
    viewportElementRef.current = elementRef?.current ?? null
  })
  return null
}
function PDFViewerTextSelectionLayer({
  documentId,
  pageIndex,
  scale,
}: {
  documentId: string
  pageIndex: number
  scale: number
}) {
  const { plugin: selectionPlugin } = useSelectionPlugin()
  const [rects, setRects] = React.useState<Rect[]>([])
  React.useEffect(() => {
    if (!selectionPlugin) return
    return selectionPlugin.registerSelectionOnPage({
      documentId,
      pageIndex,
      onRectsChange: ({ rects: nextRects }) => {
        setRects(nextRects)
      },
    })
  }, [documentId, pageIndex, selectionPlugin])
  if (!rects.length) return null
  return (
    <>
      {rects.map((rect, index) => (
        <div
          key={`${index}-${rect.origin.x}-${rect.origin.y}`}
          className="pointer-events-none absolute"
          style={{
            background: TEXT_SELECTION_BACKGROUND,
            height: rect.size.height * scale,
            left: rect.origin.x * scale,
            top: rect.origin.y * scale,
            width: rect.size.width * scale,
          }}
        />
      ))}
    </>
  )
}
function PDFViewerSelectionReleaseGuard({
  documentId,
}: {
  documentId: string
}) {
  const { plugin: selectionPlugin } = useSelectionPlugin()
  const { provides: selection } = useSelectionCapability()
  const lastSelectionModeIdRef = React.useRef<string | null>(null)
  React.useEffect(() => {
    if (!selection) return
    return selection.forDocument(documentId).onBeginSelection(({ modeId }) => {
      lastSelectionModeIdRef.current = modeId
    })
  }, [documentId, selection])
  React.useEffect(() => {
    if (!selection) return
    let cleanupFrame = 0
    const finalizeIfStillSelecting = () => {
      window.cancelAnimationFrame(cleanupFrame)
      cleanupFrame = window.requestAnimationFrame(() => {
        const selectionState = selection.getState(documentId)
        if (!selectionState.selecting) return
        if (selectionState.selection && selectionPlugin) {
          const pluginWithEndSelection = selectionPlugin as unknown as {
            endSelection?: (documentId: string, modeId: string) => void
          }
          pluginWithEndSelection.endSelection?.(
            documentId,
            lastSelectionModeIdRef.current ?? "pointerMode"
          )
          return
        }
        if (!selectionState.selection) {
          selection.clear(documentId)
        }
      })
    }
    window.addEventListener("pointerup", finalizeIfStillSelecting)
    window.addEventListener("pointercancel", finalizeIfStillSelecting)
    window.addEventListener("blur", finalizeIfStillSelecting)
    return () => {
      window.cancelAnimationFrame(cleanupFrame)
      window.removeEventListener("pointerup", finalizeIfStillSelecting)
      window.removeEventListener("pointercancel", finalizeIfStillSelecting)
      window.removeEventListener("blur", finalizeIfStillSelecting)
    }
  }, [documentId, selection, selectionPlugin])
  return null
}
function isEditableCopyTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  return Boolean(target.closest("input, textarea, [contenteditable='true']"))
}
function PDFViewerSelectionCopyShortcut({
  documentId,
}: {
  documentId: string
}) {
  const { provides: selection } = useSelectionCapability()
  React.useEffect(() => {
    if (!selection) return
    const copySelectedPdfText = (event: Event) => {
      if (isEditableCopyTarget(event.target)) return
      if (!selection.getState(documentId).selection) return
      event.preventDefault()
      selection.copyToClipboard(documentId)
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() !== "c") return
      if (!event.metaKey && !event.ctrlKey) return
      copySelectedPdfText(event)
    }
    document.addEventListener("copy", copySelectedPdfText)
    document.addEventListener("keydown", handleKeyDown)
    return () => {
      document.removeEventListener("copy", copySelectedPdfText)
      document.removeEventListener("keydown", handleKeyDown)
    }
  }, [documentId, selection])
  return null
}
// Changed for Kaagaz (#589): the scroll plugin's layout has the person's turns in it already
// (PageTurnsPlugin), so it is drawn as it comes.
function PDFViewerScroller({
  documentId,
  renderPage,
}: {
  documentId: string
  renderPage: (props: PageLayout) => React.ReactNode
}) {
  const { plugin: scrollPlugin } = useScrollPlugin()
  const [layoutData, setLayoutData] = React.useState<{
    docId: string | null
    layout: ScrollerLayout | null
  }>({ docId: null, layout: null })
  React.useEffect(() => {
    if (!scrollPlugin || !documentId) return
    let frame = 0
    const setCurrentLayout = () => {
      try {
        setLayoutData({
          docId: documentId,
          layout: scrollPlugin.getScrollerLayout(documentId),
        })
      } catch {
        setLayoutData({ docId: documentId, layout: null })
      }
    }
    const unsubscribe = scrollPlugin.onScrollerData(documentId, (layout) => {
      setLayoutData({ docId: documentId, layout })
    })
    frame = window.requestAnimationFrame(setCurrentLayout)
    return () => {
      window.cancelAnimationFrame(frame)
      unsubscribe()
      setLayoutData({ docId: null, layout: null })
      scrollPlugin.clearLayoutReady(documentId)
    }
  }, [documentId, scrollPlugin])
  const scrollerLayout =
    layoutData.docId === documentId ? layoutData.layout : null
  React.useLayoutEffect(() => {
    if (!scrollPlugin || !documentId || !scrollerLayout) return
    scrollPlugin.setLayoutReady(documentId)
  }, [documentId, scrollPlugin, scrollerLayout])
  if (!scrollerLayout) return null
  return (
    <div
      style={{
        width: `${scrollerLayout.totalWidth}px`,
        height: `${scrollerLayout.totalHeight}px`,
        position: "relative",
        boxSizing: "border-box",
        margin: "0 auto",
        ...(scrollerLayout.strategy === ScrollStrategy.Horizontal && {
          display: "flex",
          flexDirection: "row",
        }),
      }}
    >
      <div
        style={
          scrollerLayout.strategy === ScrollStrategy.Horizontal
            ? {
                width: scrollerLayout.startSpacing,
                height: "100%",
                flexShrink: 0,
              }
            : {
                height: scrollerLayout.startSpacing,
                width: "100%",
              }
        }
      />
      <div
        style={{
          gap: scrollerLayout.pageGap,
          display: "flex",
          alignItems: "center",
          position: "relative",
          boxSizing: "border-box",
          ...(scrollerLayout.strategy === ScrollStrategy.Horizontal
            ? {
                flexDirection: "row",
                minHeight: "100%",
              }
            : {
                flexDirection: "column",
                minWidth: "fit-content",
              }),
        }}
      >
        {scrollerLayout.items.map((item) => (
          <div
            key={item.pageNumbers[0]}
            style={{
              display: "flex",
              justifyContent: "center",
              gap: scrollerLayout.pageGap,
            }}
          >
            {item.pageLayouts.map((layout) => (
              <div
                key={layout.pageNumber}
                style={{
                  width: `${layout.rotatedWidth}px`,
                  height: `${layout.rotatedHeight}px`,
                  position: "relative",
                  zIndex: layout.elevated ? 1 : undefined,
                }}
              >
                {renderPage(layout)}
              </div>
            ))}
          </div>
        ))}
      </div>
      <div
        style={
          scrollerLayout.strategy === ScrollStrategy.Horizontal
            ? {
                width: scrollerLayout.endSpacing,
                height: "100%",
                flexShrink: 0,
              }
            : {
                height: scrollerLayout.endSpacing,
                width: "100%",
              }
        }
      />
    </div>
  )
}
type PDFViewerInnerProps = {
  viewerRef: React.ForwardedRef<PDFViewerHandle>
  pdfFile: string
  documentId: string
  document: PdfDocumentObject | null
  defaultZoom: PDFViewerZoomLevel
  className?: string
  fileName?: string
  showDownload: boolean
  showToolbar: boolean
  showRotateControls: boolean
  showUpload: boolean
  toolbarActions?: React.ReactNode
  pageRotations?: PDFViewerProps["pageRotations"]
  onPageRotationsChange?: PDFViewerProps["onPageRotationsChange"]
  turnMenuDisabled?: PDFViewerProps["turnMenuDisabled"]
  turnWithAi?: PDFViewerProps["turnWithAi"]
  pageClassName?: (pageNumber: number) => string | undefined
  renderPageOverlay?: (props: PDFViewerPageOverlayProps) => React.ReactNode
  onActivePageChange?: (pageNumber: number) => void
  onPdfUpload?: (file: File) => void
  onPagePointerDown?: PDFViewerProps["onPagePointerDown"]
  onPagePointerMove?: PDFViewerProps["onPagePointerMove"]
  onPagePointerUp?: PDFViewerProps["onPagePointerUp"]
  onPagePointerCancel?: PDFViewerProps["onPagePointerCancel"]
  onUploadFile: (file: File) => void
}
function PDFViewerInner({
  viewerRef,
  pdfFile,
  documentId,
  document: pdfDocument,
  defaultZoom,
  className,
  fileName,
  showDownload,
  showToolbar,
  showRotateControls,
  showUpload,
  toolbarActions,
  pageRotations,
  onPageRotationsChange,
  turnMenuDisabled,
  turnWithAi,
  pageClassName,
  renderPageOverlay,
  onActivePageChange,
  onPdfUpload,
  onPagePointerDown,
  onPagePointerMove,
  onPagePointerUp,
  onPagePointerCancel,
  onUploadFile,
}: PDFViewerInnerProps) {
  const { registry } = useRegistry()
  const { state: scrollState, provides: scroll } = useScroll(documentId)
  const { state: zoomState, provides: zoom } = useZoom(documentId)
  const { provides: thumbnails } = useThumbnailCapability()
  const { plugin: thumbnailPlugin } = useThumbnailPlugin()
  const { plugin: scrollPlugin } = useScrollPlugin()
  const { provides: viewportCapability } = useViewportCapability()
  const pageTurns =
    registry?.getPlugin<PageTurnsPlugin>(PageTurnsPlugin.id)?.provides() ?? null
  const [sidebarOpen, setSidebarOpen] = React.useState(false)
  const [isPreparingDownload, setIsPreparingDownload] = React.useState(false)
  const [pageRotationDeltas, setPageRotationDeltas] =
    React.useState<PageRotationDeltas>(
      () =>
        new Map(
          Array.from(pageRotations ?? [], ([pageIndex, turn]) => [
            pageIndex,
            normalizeRotation(turn),
          ])
        )
    )
  const [selectedPageIndexes, setSelectedPageIndexes] = React.useState<
    Set<number>
  >(() => new Set())
  const basePageRotations = React.useMemo(
    () =>
      pdfDocument?.pages.map((page) => normalizeRotation(page.rotation)) ?? [],
    [pdfDocument]
  )
  const [viewerShellRef, viewerShellWidth] = useElementWidth<HTMLDivElement>()
  const sidebarInline = useInlineThumbnailSidebar(viewerShellWidth)
  const viewportElementRef = React.useRef<HTMLDivElement | null>(null)
  const pageRotationDeltasRef = React.useRef(pageRotationDeltas)
  const selectedPageIndexesRef = React.useRef(selectedPageIndexes)
  const selectionAnchorPageIndexRef = React.useRef<number | null>(null)
  const suppressActivePageSelectionSyncRef = React.useRef<number | null>(null)
  const initializedSelectionDocumentRef = React.useRef<string | null>(null)
  const activePage = scrollState.currentPage
  const numPages = pdfDocument?.pageCount ?? 0
  const isLoading = !pdfDocument
  const controlsDisabled = !numPages
  const downloadDisabled = controlsDisabled || isPreparingDownload
  const thumbnailSidebarVisible = sidebarOpen && !isLoading
  const currentZoomLevel = zoomState.currentZoomLevel
  const alignedThumbnailSidebarDocumentRef = React.useRef<string | null>(null)
  React.useEffect(() => {
    pageRotationDeltasRef.current = pageRotationDeltas
  }, [pageRotationDeltas])
  // Changed for Kaagaz (#589): the turns this viewer opened with, laid out from the start.
  React.useLayoutEffect(() => {
    if (pageRotationDeltasRef.current.size === 0) return
    pageTurns?.setPageTurns(documentId, pageRotationDeltasRef.current)
  }, [documentId, pageTurns])
  React.useEffect(() => {
    selectedPageIndexesRef.current = selectedPageIndexes
  }, [selectedPageIndexes])
  React.useEffect(() => {
    if (activePage > 0) onActivePageChange?.(activePage)
  }, [activePage, onActivePageChange])
  React.useEffect(() => {
    if (activePage < 1 || numPages < 1) return
    const activePageIndex = activePage - 1
    const suppressedPageIndex = suppressActivePageSelectionSyncRef.current
    suppressActivePageSelectionSyncRef.current = null
    if (suppressedPageIndex === activePageIndex) return
    const nextSelection = new Set([activePageIndex])
    selectionAnchorPageIndexRef.current = activePageIndex
    selectedPageIndexesRef.current = nextSelection
    setSelectedPageIndexes((previousSelection) =>
      arePageIndexSetsEqual(previousSelection, nextSelection)
        ? previousSelection
        : nextSelection
    )
  }, [activePage, numPages])
  React.useEffect(() => {
    if (
      numPages < 1 ||
      initializedSelectionDocumentRef.current === documentId
    ) {
      return
    }
    const initialPageIndex = Math.max(0, (activePage > 0 ? activePage : 1) - 1)
    const initialSelection = new Set([initialPageIndex])
    initializedSelectionDocumentRef.current = documentId
    selectionAnchorPageIndexRef.current = initialPageIndex
    selectedPageIndexesRef.current = initialSelection
    setSelectedPageIndexes(initialSelection)
  }, [activePage, documentId, numPages])
  React.useEffect(() => {
    if (!thumbnailSidebarVisible) {
      alignedThumbnailSidebarDocumentRef.current = null
      return
    }
    if (
      activePage < 1 ||
      !thumbnails ||
      alignedThumbnailSidebarDocumentRef.current === documentId
    ) {
      return
    }
    alignedThumbnailSidebarDocumentRef.current = documentId
    const frame = window.requestAnimationFrame(() => {
      thumbnails.forDocument(documentId).scrollToThumb(activePage - 1)
    })
    return () => window.cancelAnimationFrame(frame)
  }, [activePage, documentId, thumbnailSidebarVisible, thumbnails])
  // The zoom plugin only releases its viewport gate for mode-based zoom
  // levels (automatic/fit); with a numeric default the gate would never
  // lift, so apply the initial zoom explicitly once the document loads.
  const initialZoomDocumentRef = React.useRef<string | null>(null)
  React.useEffect(() => {
    if (!pdfDocument || !zoom) return
    if (initialZoomDocumentRef.current === documentId) return
    initialZoomDocumentRef.current = documentId
    zoom.requestZoom(toZoomLevel(defaultZoom))
  }, [defaultZoom, documentId, pdfDocument, zoom])
  const scrollToPage = React.useCallback(
    (pageNumber: number, options?: ScrollIntoViewOptions) => {
      scroll?.scrollToPage({
        pageNumber,
        behavior: options?.behavior === "smooth" ? "smooth" : "auto",
      })
    },
    [scroll]
  )
  const selectThumbnailPage = React.useCallback(
    (pageNumber: number, mode: ThumbnailSelectionMode) => {
      const pageIndex = pageNumber - 1
      if (pageIndex < 0 || pageIndex >= numPages) return
      suppressActivePageSelectionSyncRef.current = pageIndex
      setSelectedPageIndexes((previousSelection) => {
        let nextSelection: Set<number>
        if (mode === "range") {
          const anchorPageIndex =
            selectionAnchorPageIndexRef.current ??
            (activePage > 0 ? activePage - 1 : pageIndex)
          nextSelection = getPageIndexRange(anchorPageIndex, pageIndex)
        } else if (mode === "toggle") {
          nextSelection = new Set(previousSelection)
          if (nextSelection.has(pageIndex)) {
            nextSelection.delete(pageIndex)
          } else {
            nextSelection.add(pageIndex)
          }
          selectionAnchorPageIndexRef.current = pageIndex
        } else {
          nextSelection = new Set([pageIndex])
          selectionAnchorPageIndexRef.current = pageIndex
        }
        selectedPageIndexesRef.current = nextSelection
        return nextSelection
      })
      scrollToPage(pageNumber)
    },
    [activePage, numPages, scrollToPage]
  )
  React.useImperativeHandle(
    viewerRef,
    () => ({
      scrollToPage,
      scrollToPageArea: (pageNumber, area, options) => {
        const pageSize = pdfDocument?.pages[pageNumber - 1]?.size
        scroll?.scrollToPage({
          pageNumber,
          ...(pageSize
            ? {
                pageCoordinates: {
                  x: ((area.left ?? 0) / 100) * pageSize.width,
                  y: (area.top / 100) * pageSize.height,
                },
                alignY: 25,
              }
            : {}),
          behavior: options?.behavior === "smooth" ? "smooth" : "auto",
        })
      },
      getViewportElement: () => viewportElementRef.current,
    }),
    [pdfDocument, scroll, scrollToPage]
  )
  const handleDownload = React.useCallback(async () => {
    if (!pdfFile || isPreparingDownload) return
    setIsPreparingDownload(true)
    try {
      await downloadPdfWithPageRotations({
        fileName: getPdfDownloadFileName(fileName, pdfFile),
        pageRotationDeltas,
        src: pdfFile,
      })
    } catch (error) {
      console.error(error)
    } finally {
      setIsPreparingDownload(false)
    }
  }, [fileName, isPreparingDownload, pageRotationDeltas, pdfFile])
  // Changed for Kaagaz (#589): every choice of the page turn menu, not only a quarter turn left
  // of the picked pages; the person's turns are handed on to be kept.
  const rotatePages = React.useCallback(
    (change: PageTurnChange) => {
      if (!pdfDocument || !registry || activePage < 1) return false
      const documentState = registry.getStore().getState().core.documents[
        documentId
      ]
      const currentDocument = documentState?.document ?? pdfDocument
      // Changed for Kaagaz (#709): the screen asks the orientation check, then hands the turns in.
      if (typeof change === "object" && "ai" in change) {
        turnWithAi?.onPick(change.ai, currentDocument.pages)
        return true
      }
      const previousDeltas = pageRotationDeltasRef.current
      let nextDeltas: PageRotationDeltas
      if (change === "reset") {
        nextDeltas = new Map()
      } else if (change === "wide" || change === "tall") {
        nextDeltas = turnPagesTo(previousDeltas, currentDocument.pages, change)
      } else if ("set" in change) {
        nextDeltas = new Map(
          Array.from(change.set, ([pageIndex, turn]) => [
            pageIndex,
            normalizeRotation(turn),
          ])
        )
      } else if (change.pages === "all") {
        nextDeltas = turnPages(
          previousDeltas,
          currentDocument.pages.keys(),
          change.direction
        )
      } else {
        const selectedTargetPageIndexes = Array.from(
          selectedPageIndexesRef.current
        ).filter((pageIndex) => currentDocument.pages[pageIndex])
        const fallbackPageIndex = activePage - 1
        nextDeltas = turnPages(
          previousDeltas,
          (selectedTargetPageIndexes.length
            ? selectedTargetPageIndexes
            : [fallbackPageIndex]
          ).filter((pageIndex) => currentDocument.pages[pageIndex]),
          change.direction
        )
      }
      const targetPageIndexes = currentDocument.pages
        .map((_, pageIndex) => pageIndex)
        .filter(
          (pageIndex) =>
            (previousDeltas.get(pageIndex) ?? 0) !==
            (nextDeltas.get(pageIndex) ?? 0)
        )
      if (targetPageIndexes.length === 0) return true
      // Only the drawn pages are refreshed: the others are drawn afresh when they come near.
      let drawnPageIndexes: number[] = []
      try {
        drawnPageIndexes = (scrollPlugin?.getScrollerLayout(documentId).items ?? [])
          .flatMap((item) => item.pageLayouts.map((page) => page.pageIndex))
          .filter((pageIndex) => targetPageIndexes.includes(pageIndex))
      } catch {
        // no layout yet: nothing drawn to refresh
      }
      const store = registry.getStore()
      const viewport = viewportElementRef.current
      const scaleOf = () =>
        store.getState().core.documents[documentId]?.scale ?? currentZoomLevel
      const scrollTop = viewport?.scrollTop ?? 0
      const before = { layout: scroll?.getLayout(), scale: scaleOf() }
      pageRotationDeltasRef.current = nextDeltas
      flushSync(() => {
        setPageRotationDeltas(nextDeltas)
        store.dispatchToCore(refreshPages(documentId, drawnPageIndexes))
        // Lays the pages out again as turned (and fits them again when the zoom is a fit).
        pageTurns?.setPageTurns(documentId, nextDeltas)
      })
      const after = { layout: scroll?.getLayout(), scale: scaleOf() }
      const keptScrollTop =
        before.layout && after.layout
          ? scrollTopKeepingPage(
              activePage,
              scrollTop,
              viewportCapability?.getViewportGap() ?? 0,
              { items: before.layout.virtualItems, scale: before.scale },
              { items: after.layout.virtualItems, scale: after.scale }
            )
          : null
      if (viewport && keptScrollTop !== null) {
        holdScrollTop(viewport, keptScrollTop)
      }
      ;(
        thumbnailPlugin as {
          calculateWindowState?: (documentId: string) => void
        } | null
      )?.calculateWindowState?.(documentId)
      onPageRotationsChange?.(nextDeltas)
      return true
    },
    [
      activePage,
      currentZoomLevel,
      documentId,
      onPageRotationsChange,
      pageTurns,
      pdfDocument,
      registry,
      scroll,
      scrollPlugin,
      thumbnailPlugin,
      turnWithAi,
      viewportCapability,
    ]
  )
  // Changed for Kaagaz (#600): turns handed in after the file opened are shown once each, as a
  // menu choice is, so the reader keeps their place. Out of the effect (rotatePages uses
  // flushSync), and marked shown only once rotatePages went through.
  const handedTurns = React.useRef(pageRotations)
  React.useEffect(() => {
    if (!pageRotations || pageRotations === handedTurns.current) return
    queueMicrotask(() => {
      if (pageRotations !== handedTurns.current && rotatePages({ set: pageRotations })) {
        handedTurns.current = pageRotations
      }
    })
  }, [pageRotations, rotatePages])
  const handleUpload = React.useCallback(
    (file: File) => {
      onUploadFile(file)
      onPdfUpload?.(file)
    },
    [onPdfUpload, onUploadFile]
  )
  const renderPage = React.useCallback(
    (page: PageLayout) => {
      const pageNumber = page.pageNumber
      const basePageRotation =
        basePageRotations[page.pageIndex] ??
        pdfDocument?.pages[page.pageIndex]?.rotation ??
        normalizeRotation(0)
      const pageRotation = normalizeRotation(
        basePageRotation + (pageRotationDeltas.get(page.pageIndex) ?? 0)
      )
      return (
        <Rotate
          documentId={documentId}
          pageIndex={page.pageIndex}
          rotation={pageRotation}
        >
          <PagePointerProvider
            documentId={documentId}
            pageIndex={page.pageIndex}
            rotation={pageRotation}
            key={`${page.pageIndex}-${pageRotation}`}
            data-pdf-viewer-page={pageNumber}
            // Changed for Kaagaz (#454): overflow-hidden, so nothing drawn on the page (its
            // tiles, a mark's ring or badge) shows outside it while the pages move.
            className={cn(
              "relative overflow-hidden border border-transparent bg-transparent shadow-xs select-none selection:bg-transparent selection:text-inherit",
              pageClassName?.(pageNumber)
            )}
            style={{ backgroundColor: "transparent" }}
            onPointerDown={(event: React.PointerEvent<HTMLDivElement>) =>
              onPagePointerDown?.(event, pageNumber)
            }
            onPointerMove={(event: React.PointerEvent<HTMLDivElement>) =>
              onPagePointerMove?.(event, pageNumber)
            }
            onPointerUp={(event: React.PointerEvent<HTMLDivElement>) =>
              onPagePointerUp?.(event, pageNumber)
            }
            onPointerCancel={(event: React.PointerEvent<HTMLDivElement>) =>
              onPagePointerCancel?.(event, pageNumber)
            }
          >
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-0 bg-white"
            />
            <RenderLayer
              documentId={documentId}
              pageIndex={page.pageIndex}
              scale={Math.min(currentZoomLevel, PAGE_BASE_RENDER_MAX_SCALE)}
              dpr={PAGE_BASE_RENDER_DPR}
              className="pointer-events-none absolute inset-0 h-full w-full object-fill opacity-100 blur-[0.35px] transition-none"
            />
            <TilingLayer
              documentId={documentId}
              pageIndex={page.pageIndex}
              key={`tiles-${page.pageIndex}-${pageRotation}`}
              className="pointer-events-none opacity-100 transition-none [&_img]:opacity-100 [&_img]:transition-none"
            />
            <SearchLayer
              documentId={documentId}
              pageIndex={page.pageIndex}
              className="pointer-events-none"
              highlightColor="rgba(253, 224, 71, 0.45)"
              activeHighlightColor="rgba(249, 115, 22, 0.55)"
            />
            <PDFViewerTextSelectionLayer
              documentId={documentId}
              pageIndex={page.pageIndex}
              scale={currentZoomLevel}
            />
            {renderPageOverlay?.({
              pageNumber,
              pageWidth: page.width,
              pageHeight: page.height,
              scale: currentZoomLevel,
              rotation: rotationToDegrees(pageRotation),
              fileRotation: rotationToDegrees(basePageRotation),
            })}
          </PagePointerProvider>
        </Rotate>
      )
    },
    [
      basePageRotations,
      currentZoomLevel,
      onPagePointerCancel,
      onPagePointerDown,
      onPagePointerMove,
      onPagePointerUp,
      pageClassName,
      pageRotationDeltas,
      renderPageOverlay,
      documentId,
      pdfDocument,
    ]
  )
  return (
    <div
      data-slot="pdf-viewer"
      className={cn(
        "flex h-full max-h-full min-h-0 w-full flex-col overflow-hidden bg-background",
        className
      )}
    >
      {showToolbar ? (
        <PDFViewerToolbar
          activePage={activePage}
          controlsDisabled={controlsDisabled}
          currentZoomLevel={currentZoomLevel}
          downloadDisabled={downloadDisabled}
          isPreparingDownload={isPreparingDownload}
          numPages={numPages}
          pickedPages={selectedPageIndexes.size}
          searchControl={
            <PDFViewerSearchControl
              key={documentId}
              documentId={documentId}
              controlsDisabled={controlsDisabled}
              pageTurns={pageRotationDeltas}
            />
          }
          sidebarOpen={sidebarOpen}
          showDownload={showDownload}
          showRotateControls={showRotateControls}
          showUpload={showUpload}
          toolbarActions={toolbarActions}
          onDownload={handleDownload}
          onPageChange={scrollToPage}
          onRotate={rotatePages}
          turnMenuDisabled={turnMenuDisabled}
          turnWithAi={turnWithAi}
          onToggleSidebar={() => setSidebarOpen((open) => !open)}
          onUploadFile={handleUpload}
          onZoomChange={(zoomLevel) => zoom?.requestZoom(zoomLevel)}
          zoomLevel={zoomState.zoomLevel}
        />
      ) : null}
      <div
        ref={viewerShellRef}
        className="relative flex min-h-0 flex-1 overflow-hidden bg-muted/30"
      >
        {isLoading ? (
          <PDFViewerLoadingSkeleton
            sidebarInline={sidebarInline}
            sidebarOpen={sidebarOpen}
          />
        ) : null}
        <div className="flex h-full max-h-full min-h-0 w-full flex-1 overflow-hidden">
          <DocumentViewerThumbnailSidebar
            closedInlineClassName={THUMBNAIL_SIDEBAR_CLOSED_CLASS}
            inline={sidebarInline}
            open={thumbnailSidebarVisible}
            widthClassName={THUMBNAIL_SIDEBAR_WIDTH_CLASS}
          >
            {thumbnailSidebarVisible ? (
              <PDFViewerThumbnails
                basePageRotations={basePageRotations}
                documentId={documentId}
                activePage={activePage}
                pageCount={numPages}
                pageRotationDeltas={pageRotationDeltas}
                pdfDocument={pdfDocument}
                selectedPageIndexes={selectedPageIndexes}
                onSelectPage={selectThumbnailPage}
              />
            ) : null}
          </DocumentViewerThumbnailSidebar>
          <PDFViewerScrollAreaViewport
            documentId={documentId}
            className="relative h-full max-h-full min-h-0 min-w-0 flex-1"
          >
            <PDFViewerViewportBridge viewportElementRef={viewportElementRef} />
            <PDFViewerSelectionCopyShortcut documentId={documentId} />
            <PDFViewerSelectionReleaseGuard documentId={documentId} />
            <GlobalPointerProvider documentId={documentId}>
              <PDFViewerScroller
                documentId={documentId}
                renderPage={renderPage}
              />
            </GlobalPointerProvider>
            <CopyToClipboard />
          </PDFViewerScrollAreaViewport>
        </div>
      </div>
    </div>
  )
}
function PDFViewerDocumentLoader({
  pdfFile,
  onDocumentLoadSuccess,
  ...innerProps
}: {
  pdfFile: string
  onDocumentLoadSuccess?: (numPages: number) => void
} & Omit<PDFViewerInnerProps, "pdfFile" | "documentId" | "document">) {
  const { provides: documentManager } = useDocumentManagerCapability()
  const { activeDocumentId, activeDocument } = useActiveDocument()
  const [loadError, setLoadError] = React.useState(false)
  const openedFileRef = React.useRef<string | null>(null)
  const onDocumentLoadSuccessRef = React.useRef(onDocumentLoadSuccess)
  React.useEffect(() => {
    onDocumentLoadSuccessRef.current = onDocumentLoadSuccess
  })
  React.useEffect(() => {
    if (!documentManager || !pdfFile) return
    if (openedFileRef.current === pdfFile) return
    openedFileRef.current = pdfFile
    setLoadError(false)
    const previousDocumentIds = documentManager
      .getOpenDocuments()
      .map((openDocument) => openDocument.id)
    const handleOpenError = () => {
      if (openedFileRef.current === pdfFile) setLoadError(true)
    }
    documentManager
      .openDocumentUrl({
        url: pdfFile,
        mode: pdfFile.startsWith("blob:") ? "full-fetch" : "auto",
      })
      .wait((response) => {
        response.task.wait((openedDocument) => {
          onDocumentLoadSuccessRef.current?.(openedDocument.pageCount)
          previousDocumentIds.forEach((documentIdToClose) => {
            documentManager.closeDocument(documentIdToClose).wait(
              () => undefined,
              () => undefined
            )
          })
        }, handleOpenError)
      }, handleOpenError)
  }, [documentManager, pdfFile])
  const document =
    activeDocument?.status === "loaded" ? activeDocument.document : null
  const documentFailed = loadError || activeDocument?.status === "error"
  if (!activeDocumentId || documentFailed || !pdfFile) {
    return (
      <PDFViewerFallbackShell
        className={innerProps.className}
        defaultZoom={innerProps.defaultZoom}
        showDownload={innerProps.showDownload}
        showRotateControls={innerProps.showRotateControls}
        showToolbar={innerProps.showToolbar}
        showUpload={innerProps.showUpload}
        sidebarOpen={false}
        state={!pdfFile ? "empty" : documentFailed ? "error" : "loading"}
        toolbarActions={innerProps.toolbarActions}
        onUploadFile={(file) => {
          innerProps.onUploadFile(file)
          innerProps.onPdfUpload?.(file)
        }}
      />
    )
  }
  return (
    <PDFViewerInner
      key={activeDocumentId}
      {...innerProps}
      pdfFile={pdfFile}
      documentId={activeDocumentId}
      document={document}
    />
  )
}
export const PDFViewer = React.forwardRef<PDFViewerHandle, PDFViewerProps>(
  function PDFViewer(
    {
      className,
      defaultZoom = DEFAULT_ZOOM,
      fileName,
      resolveScrollAreaViewport,
      showDownload = true,
      showRotateControls = true,
      showToolbar = true,
      showUpload = true,
      src,
      toolbarActions,
      pageRotations,
      onPageRotationsChange,
      turnMenuDisabled,
      turnWithAi,
      pageClassName,
      renderPageOverlay,
      onActivePageChange,
      onDocumentLoadSuccess,
      onPdfUpload,
      onPagePointerDown,
      onPagePointerMove,
      onPagePointerUp,
      onPagePointerCancel,
    },
    ref
  ) {
    const { engine, error: engineError } = useSharedPdfEngine()
    const [uploadedPdfFile, setUploadedPdfFile] = React.useState<{
      src: string | undefined
      url: string | null
    }>(() => ({ src, url: null }))
    const uploadedPdfUrl =
      uploadedPdfFile.src === src ? uploadedPdfFile.url : null
    const pdfFile = uploadedPdfUrl ?? src ?? ""
    React.useEffect(
      () => () => {
        if (uploadedPdfUrl) URL.revokeObjectURL(uploadedPdfUrl)
      },
      [uploadedPdfUrl]
    )
    const handleUploadFile = React.useCallback(
      (nextFile: File) => {
        const nextUrl = URL.createObjectURL(nextFile)
        setUploadedPdfFile({ src, url: nextUrl })
      },
      [src]
    )
    // Plugin registrations are created once per viewer instance.
    const [plugins] = React.useState(() => [
      createPluginRegistration(DocumentManagerPluginPackage),
      createPluginRegistration(ViewportPluginPackage, {
        viewportGap: PAGE_GAP,
      }),
      createPluginRegistration(ScrollPluginPackage, {
        defaultPageGap: PAGE_GAP,
        defaultBufferSize: 2,
      }),
      createPluginRegistration(RenderPluginPackage),
      createPluginRegistration(TilingPluginPackage, {
        tileSize: 768,
        overlapPx: 2.5,
        extraRings: 0,
      }),
      createPluginRegistration(InteractionManagerPluginPackage),
      createPluginRegistration(SelectionPluginPackage, {
        marquee: { enabled: false },
      }),
      createPluginRegistration(SearchPluginPackage, {
        showAllResults: true,
      }),
      createPluginRegistration(ThumbnailPluginPackage, {
        width: THUMBNAIL_WIDTH,
        gap: THUMBNAIL_GAP,
        imagePadding: THUMBNAIL_IMAGE_PADDING,
        labelHeight: THUMBNAIL_LABEL_HEIGHT,
        paddingY: THUMBNAIL_PANE_PADDING_Y,
        buffer: 3,
        autoScroll: true,
        scrollBehavior: "auto",
      }),
      createPluginRegistration(ZoomPluginPackage, {
        defaultZoomLevel: toZoomLevel(defaultZoom),
        minZoom: MIN_ZOOM,
        maxZoom: MAX_ZOOM,
      }),
      createPluginRegistration(RotatePluginPackage),
      createPluginRegistration(PageTurnsPluginPackage),
    ])
    if (engineError) {
      return (
        <PDFViewerFallbackShell
          className={className}
          defaultZoom={defaultZoom}
          errorMessage="Unable to load the PDF engine."
          showDownload={showDownload}
          showRotateControls={showRotateControls}
          showToolbar={showToolbar}
          showUpload={showUpload}
          sidebarOpen={false}
          state="error"
          toolbarActions={toolbarActions}
          onUploadFile={(file) => {
            handleUploadFile(file)
            onPdfUpload?.(file)
          }}
        />
      )
    }
    if (!engine) {
      return (
        <PDFViewerFallbackShell
          className={className}
          defaultZoom={defaultZoom}
          showDownload={showDownload}
          showRotateControls={showRotateControls}
          showToolbar={showToolbar}
          showUpload={showUpload}
          sidebarOpen={false}
          state="loading"
          toolbarActions={toolbarActions}
          onUploadFile={(file) => {
            handleUploadFile(file)
            onPdfUpload?.(file)
          }}
        />
      )
    }
    return (
      <PDFViewerScrollAreaResolverContext.Provider
        value={resolveScrollAreaViewport ?? resolveDefaultScrollAreaViewport}
      >
        <EmbedPDF engine={engine} plugins={plugins}>
          <PDFViewerDocumentLoader
            viewerRef={ref}
            pdfFile={pdfFile}
            defaultZoom={defaultZoom}
            className={className}
            fileName={fileName}
            showDownload={showDownload}
            showToolbar={showToolbar}
            showRotateControls={showRotateControls}
            showUpload={showUpload}
            toolbarActions={toolbarActions}
            pageRotations={pageRotations}
            onPageRotationsChange={onPageRotationsChange}
            turnMenuDisabled={turnMenuDisabled}
            turnWithAi={turnWithAi}
            pageClassName={pageClassName}
            renderPageOverlay={renderPageOverlay}
            onActivePageChange={onActivePageChange}
            onDocumentLoadSuccess={onDocumentLoadSuccess}
            onPdfUpload={onPdfUpload}
            onPagePointerDown={onPagePointerDown}
            onPagePointerMove={onPagePointerMove}
            onPagePointerUp={onPagePointerUp}
            onPagePointerCancel={onPagePointerCancel}
            onUploadFile={handleUploadFile}
          />
        </EmbedPDF>
      </PDFViewerScrollAreaResolverContext.Provider>
    )
  }
)
function InlineSpinner({ className, ...props }: InlineRegistryIconProps) {
  return (
    <LoaderCircleIcon
      role="status"
      aria-label="Loading"
      className={cn("size-4 animate-spin", className)}
      {...props}
    />
  )
}
type InlineRegistryIconProps = Omit<
  React.ComponentProps<"svg">,
  "children" | "strokeWidth"
> & { strokeWidth?: number }
