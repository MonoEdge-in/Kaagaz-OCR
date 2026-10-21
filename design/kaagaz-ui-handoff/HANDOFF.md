# Kaagaz UI handoff: for Claude Code

This folder is the approved UI design for Kaagaz (by MonoEdge). Your job is to rebuild
the existing frontend to match it **and keep it wired to the existing backend**.
Do not change backend contracts. Do not invent data.

## What is in this folder

| Path | What it is |
|---|---|
| `styles/kaagaz.css` | **Source of truth.** All design tokens (light + dark) and every component style. |
| `screens/system/Main.html` | Foundations: colours, type, spacing, radius, status meanings, icons (with SVG paths). |
| `screens/system/Components.html` | Every component in every state. |
| `screens/system/DevNotes.html` | Token table, component → CSS module table, formatting and a11y rules. |
| `screens/desktop/*.html` | Desktop screens (1440 wide). |
| `screens/phone/*.html` | Phone screens (375 wide). |
| `screens/flows/*.html` | Three end-to-end flows (upload → version 2, ask → cited spot, review five values). |

### How to read the screen files
They come from a design tool, so they carry a little template syntax. Read them as plain HTML + CSS classes:
- Ignore `<script src="./support.js">`, `<x-dc>`, `<helmet>` wrappers.
- `class="k-…"` = classes in `styles/kaagaz.css`. That is the styling to reproduce.
- `{{name}}` = a value from the `renderVals()` script at the bottom of the file. That script is **sample data only**; the real values come from the backend.
- `<sc-for list="{{rows}}" as="r">` = `rows.map(r => …)`. `<sc-if value="{{x}}">` = `{x && …}`.
- `data-theme="{{theme}}"` = the light/dark switch.
- All names, numbers and documents in the screens are made-up examples.

## Ground rules
1. **Explore first, change second.** Before writing UI, read the backend: API routes/endpoints, auth/session, data models, status enums, upload pipeline, search and ask endpoints (streaming or not). Write `docs/ui-mapping.md`: for every screen, the data it shows → the endpoint/field that provides it. List gaps as **"needs backend"**. Do not fake them, and leave such UI behind a clearly marked TODO.
2. **Stack:** keep the existing framework. If it is Next.js: App Router, **plain CSS custom properties + CSS Modules**. No Tailwind, no component library, no CSS-in-JS.
3. **Tokens:** copy the two token blocks from `kaagaz.css` (`:root,[data-theme="light"]` and `[data-theme="dark"]`) plus the type/space/radius block into `app/styles/tokens.css`. Components only ever use `var(--k-…)`. No raw hex in components.
4. **Fonts** via `next/font/google`: Archivo (variable, wdth 100–125, wght 400–700, used at `font-stretch: 112%`), IBM Plex Sans (400/500/600), IBM Plex Sans Devanagari (400/500/600), IBM Plex Mono (400/500). Expose them as `--k-font-display`, `--k-font-body`, `--k-font-mono`.
5. **Theme:** `data-theme` on `<html>`, with three choices (light, dark, system), saved per user. Set it before paint so there is no flash.
6. Work in **small steps, one PR per step**, and keep the app running after each.

## Build order
1. **Foundation:** `tokens.css`, `base.css`, fonts, theme switch, `Icon` component (paths in `Main.html` → Icons section; 24 grid, stroke 1.6, round caps).
2. **UI kit** in `components/ui/<Name>/<Name>.tsx + .module.css`, ported 1:1 from the matching section of `kaagaz.css`:
   Button (primary/secondary/quiet/danger/danger-outline; sm/lg/icon/block; loading) · Field/Input/Select/DateField/CodeInput/Checkbox/Switch · Chip/FilterChip · LabelChip · StatusBadge · VersionChip · Tabs · Table (+SortHeader, BulkBar) · Tree · Breadcrumbs · Segmented · Card/StatStrip · Banner (info/warning/danger/version/quiet) · Toast · Dialog · InlineConfirm · Drawer · Menu · DropZone · StageProgress · BatchMeter · Timeline · CitationChip · SourceCard · AnswerCard · LiveSteps · Composer + DevanagariKeys · PageThumb · PaperPage + Marks · Zoom · KeyValueList · ReviewReading · EmptyState · Skeleton · Avatar · Kbd · PhoneTabBar · BottomSheet.
   Check each against `Components.html` in light and dark.
3. **AppShell:** light sidebar (Home, Documents, Ask, Review, My words, Admin for admins only), top bar (search pill, Upload, bell, account menu with theme / sign out / sign out everywhere). Below 768px: floating pill tab bar (Home, Documents, Ask, Review, More).
4. **Screens**, wiring each to real data as you go:

| Screen | Reference file | Typical data |
|---|---|---|
| Sign in, admin code, 2-step setup, new password, locked | `desktop/SignIn.html` | auth endpoints |
| Home | `desktop/Home.html`, `phone/PhoneHome.html` | counts, docs by type, departments, recent docs, activity, review count |
| Documents (list/grid, tree, filters, bulk) | `desktop/Documents.html`, `DocumentsGrid.html`, `phone/PhoneDocuments.html` | folders tree, documents list (sort/filter/paginate), bulk actions |
| Upload + batch tray | `desktop/Upload.html`, `phone/PhoneUpload.html` | upload API, per-file status, filing result, duplicate/new-version detection |
| Document page (viewer + tabs) | `desktop/DocumentPage.html`, `DocumentTabs.html`, `phone/PhoneDocument.html` | pages/images, extracted values with boxes, text, versions, activity, questions |
| New version + Compare | `desktop/VersionCompare.html` | version suggestion, diff |
| Ask | `desktop/Ask.html`, `AskStates.html`, `phone/PhoneAsk.html` | conversations, ask (stream), citations with page + box |
| Search | `desktop/Search.html` | search (inside docs / names), synonyms from My words |
| Review | `desktop/Review.html`, `phone/PhoneReview.html` | review queue, two readings, submit correction |
| My words | `desktop/MyWords.html` | glossary CRUD |
| Admin | `desktop/Admin.html`, `AdminTabs.html` | users, departments, settings, activity export, usage, failed files |
| Operator console | `desktop/Operator.html`, `OperatorOrg.html` | orgs, health, models/costs (operator auth only) |
| Empty / loading / offline / error / no access / 404 | `desktop/States.html` | apply to every screen |

## Behaviour that must not drift
- **Status mapping:** map whatever the backend returns onto exactly these six states, and keep the mapping in one place (`lib/status.ts`):
  Verified (solid green, check) · Needs review (dashed amber) · From documents (soft blue) · Not found (neutral) · Reading (calm animation) · Failed (red, **always with reason + fix**).
- **Sensitivity labels:** Public · Internal (default) · Confidential · Restricted. Same chip everywhere.
- **Formats:** `Intl.NumberFormat('en-IN', {style:'currency', currency:'INR', maximumFractionDigits:0})` → ₹1,50,000; dates `14 Oct 2026` via `Intl.DateTimeFormat('en-IN', {day:'2-digit', month:'short', year:'numeric'})`; GSTIN and survey numbers in mono.
- **Upload progress:** three stages Sending → Saving → Reading (weights 20/20/60). Never show 100% before Reading is done.
- **Ask:** show live steps while working (`aria-live="polite"`); the answer survives tab switches; every claim has a numbered citation that opens the page at the boxed spot; "Not found in the readable text" plus the unreadable pages. Never a guess.
- **Copy:** plain and specific ("Upload a sharper scan"). No technical words (OCR, vector, embedding, model) in member-facing UI. Errors say what went wrong and how to fix it.
- **Accessibility:** WCAG AA (tokens already pass), visible focus (`--k-focus`), real buttons/links/labels, `aria-sort` on sortable headers, 44px touch targets on phone, `prefers-reduced-motion` respected, nothing scrolls sideways at 375px (wide tables scroll inside their own box).

## Done means
- Each screen matches its reference at 1440 and 375 wide, in light and dark (take Playwright screenshots and compare).
- Typecheck, lint and existing tests pass, and nothing in the backend changed.
- `docs/ui-mapping.md` lists every "needs backend" gap.
