"use client";

// Every kit component in the states Components.html draws, in its order. Hover
// and focus are checked by pointing and tabbing; the rest are drawn here.

import { useState } from "react";
import type { ReactNode } from "react";
import { AnswerCard, Question, Working } from "@/components/ui/AnswerCard/AnswerCard";
import { Avatar } from "@/components/ui/Avatar/Avatar";
import { Banner } from "@/components/ui/Banner/Banner";
import { BarChart } from "@/components/ui/BarChart/BarChart";
import { BatchMeter } from "@/components/ui/BatchMeter/BatchMeter";
import { Breadcrumbs } from "@/components/ui/Breadcrumbs/Breadcrumbs";
import { Button, ButtonGroup, ButtonLink } from "@/components/ui/Button/Button";
import type { ButtonVariant } from "@/components/ui/Button/Button";
import { Card, CardHead, Divider, LinkCard, StatCard } from "@/components/ui/Card/Card";
import { Checkbox } from "@/components/ui/Checkbox/Checkbox";
import { Chip, ChipRow, Tag } from "@/components/ui/Chip/Chip";
import { CitationChip } from "@/components/ui/Citation/Citation";
import { CodeInput } from "@/components/ui/CodeInput/CodeInput";
import { Composer, DevanagariKeys, SuggestionChip } from "@/components/ui/Composer/Composer";
import { DialogPanel } from "@/components/ui/Dialog/Dialog";
import { DrawerPanel } from "@/components/ui/Drawer/Drawer";
import { DropZone } from "@/components/ui/DropZone/DropZone";
import { EmptyState } from "@/components/ui/EmptyState/EmptyState";
import { Field, Select, TextInput } from "@/components/ui/Field/Field";
import { InlineConfirm } from "@/components/ui/InlineConfirm/InlineConfirm";
import { Kbd } from "@/components/ui/Kbd/Kbd";
import { KeyValueList } from "@/components/ui/KeyValueList/KeyValueList";
import { LabelChip } from "@/components/ui/LabelChip/LabelChip";
import { Menu, MenuHeader, MenuItem, MenuItems, MenuRow } from "@/components/ui/Menu/Menu";
import { NavItem, PhoneTabBar } from "@/components/ui/NavItem/NavItem";
import { PageThumb } from "@/components/ui/PageThumb/PageThumb";
import { Mark, MarkLegend, Paper, PaperRule, Viewer } from "@/components/ui/Paper/Paper";
import { Reading } from "@/components/ui/Reading/Reading";
import { RadioPill, Segmented } from "@/components/ui/Segmented/Segmented";
import { Skeleton } from "@/components/ui/Skeleton/Skeleton";
import { SourceCard } from "@/components/ui/SourceCard/SourceCard";
import { FiledPath, StageProgress } from "@/components/ui/StageProgress/StageProgress";
import { StatusBadge } from "@/components/ui/StatusBadge/StatusBadge";
import { Switch } from "@/components/ui/Switch/Switch";
import { BulkBar, Cell, NameCell, Row, SortHeader, SubLine, Table } from "@/components/ui/Table/Table";
import { Tabs } from "@/components/ui/Tabs/Tabs";
import { Timeline } from "@/components/ui/Timeline/Timeline";
import { Toast } from "@/components/ui/Toast/Toast";
import { Tree, TreeGroup, TreeItem } from "@/components/ui/Tree/Tree";
import { ProblemFlag, VersionChip } from "@/components/ui/VersionChip/VersionChip";
import { Zoom } from "@/components/ui/Zoom/Zoom";
import styles from "./kit.module.css";

const noop = () => {};

function Section({ n, eyebrow, title, children }: { n: string; eyebrow: string; title: string; children: ReactNode }) {
  return (
    <section className={styles.section}>
      <div className={styles.heading}>
        <span className="k-eyebrow">
          {n} · {eyebrow}
        </span>
        <h2 className="k-h1">{title}</h2>
      </div>
      {children}
    </section>
  );
}

const BUTTON_ROWS: [string, ButtonVariant, string, string][] = [
  ["Primary", "primary", "Make it version 2", "Linking…"],
  ["Secondary", "secondary", "Keep separate", "Saving…"],
  ["Quiet", "quiet", "Compare first", "Opening…"],
  ["Danger", "danger", "Move to Trash", "Moving…"],
  ["Danger outline", "danger-outline", "Sign out everywhere", "Signing out…"],
];

export function KitSheet({ theme }: { theme: "light" | "dark" }) {
  const [tab, setTab] = useState("details");
  const [view, setView] = useState("list");
  const [scope, setScope] = useState("inside");
  const [code, setCode] = useState("482");
  const [autofile, setAutofile] = useState(true);
  const [twoStep, setTwoStep] = useState(false);
  const [question, setQuestion] = useState("");
  const [themeChoice, setThemeChoice] = useState<string>(theme);

  return (
    <div className={`k-root ${styles.sheet}`} data-theme={theme}>
      <div className={styles.page}>
        <header className={styles.header}>
          <div className={styles.stack}>
            <span className="k-eyebrow">Kaagaz design system · Components</span>
            <h1 className="k-display">Components and their states</h1>
          </div>
          <p className="k-muted">
            The UI kit, against screens/system/Components.html. <a href={theme === "dark" ? "/kit" : "/kit?theme=dark"}>Show {theme === "dark" ? "light" : "dark"}</a>
          </p>
        </header>

        <Section n="06" eyebrow="Buttons" title="Buttons say exactly what happens">
          <Card variant="flush">
            <div className={styles.states}>
              <span className="k-eyebrow">Variant</span>
              <span className="k-eyebrow">Default</span>
              <span className="k-eyebrow">Disabled</span>
              <span className="k-eyebrow">Loading</span>
              {BUTTON_ROWS.map(([name, variant, words, busy]) => [
                <span key={`${variant}-n`} className={styles.col}>
                  <strong>{name}</strong>
                </span>,
                <span key={`${variant}-d`}>
                  <Button variant={variant}>{words}</Button>
                </span>,
                <span key={`${variant}-x`}>
                  <Button variant={variant} disabled>
                    {words}
                  </Button>
                </span>,
                <span key={`${variant}-l`}>
                  <Button variant={variant} loading>
                    {busy}
                  </Button>
                </span>,
              ])}
            </div>
          </Card>
          <div className={styles.grid}>
            <Card>
              <h3 className="k-h4">Sizes</h3>
              <ButtonGroup>
                <Button variant="primary" size="sm">
                  Review
                </Button>
                <Button variant="primary">Upload</Button>
                <Button variant="primary" size="lg">
                  Sign in
                </Button>
              </ButtonGroup>
            </Card>
            <Card>
              <h3 className="k-h4">With icons, icon-only</h3>
              <ButtonGroup>
                <Button variant="primary" icon="upload">
                  Upload
                </Button>
                <Button icon="download">Download</Button>
                <Button icon="sheet">Export to Excel</Button>
                <Button icon="more" label="More actions" />
                <Button variant="quiet" icon="bell" label="Notifications" />
              </ButtonGroup>
            </Card>
          </div>
        </Section>

        <Section n="07" eyebrow="Inputs and controls" title="Fields that tell you how to fix them">
          <div className={styles.grid}>
            <Card>
              <h3 className="k-h4">Text input · states</h3>
              <div className={styles.two}>
                <Field id="kit-in1" label="Party name">
                  {(control) => <TextInput {...control} placeholder="e.g. Mehta Textiles" />}
                </Field>
                <Field id="kit-in3" label="Party name">
                  {(control) => <TextInput {...control} defaultValue="Mehta Textiles" />}
                </Field>
                <Field id="kit-in5" label="Party name">
                  {(control) => <TextInput {...control} defaultValue="Mehta Textiles" disabled />}
                </Field>
                <div />
                <Field id="kit-in4" label="GSTIN" error="Enter a 15-character GSTIN, like 27AABCM1234F1Z5" className={styles.full}>
                  {(control) => <TextInput {...control} mono defaultValue="27AABCM1234F1" />}
                </Field>
              </div>
            </Card>
            <Card>
              <h3 className="k-h4">Select, date, search</h3>
              <div className={styles.two}>
                <Field id="kit-sel" label="Department">
                  {(control) => (
                    <Select {...control} defaultValue="Land & Legal">
                      <option>Land &amp; Legal</option>
                      <option>Accounts</option>
                      <option>HR</option>
                    </Select>
                  )}
                </Field>
                <Field id="kit-date" label="Document date" help="Day Month Year">
                  {(control) => <TextInput {...control} icon="calendar" defaultValue="14 Oct 2026" />}
                </Field>
                <Field id="kit-search" label="Search inside documents" className={styles.full}>
                  {(control) => <TextInput {...control} icon="search" placeholder="e.g. rent paid in September, सातबारा 44" />}
                </Field>
              </div>
            </Card>
            <Card>
              <h3 className="k-h4">6-digit code (admin sign-in)</h3>
              <div className={styles.stack}>
                <span className="k-label" id="kit-code-l">
                  <strong>Code from your authenticator app</strong>
                </span>
                <CodeInput labelledBy="kit-code-l" value={code} onChange={setCode} />
                <span className="k-small k-muted">Paste works. The code changes every 30 seconds.</span>
              </div>
            </Card>
            <Card>
              <h3 className="k-h4">Checkbox, switch</h3>
              <div className={styles.stack}>
                <Checkbox label="Remember this correction" defaultChecked />
                <Checkbox label="Include Trash" />
                <Checkbox label="Read handwriting (turned off by admin)" disabled />
                <Divider />
                <Switch label="File new uploads into folders on their own" checked={autofile} onChange={() => setAutofile(!autofile)} />
                <Switch label="Two-step sign-in for members" checked={twoStep} onChange={() => setTwoStep(!twoStep)} />
              </div>
            </Card>
          </div>
        </Section>

        <Section n="08" eyebrow="Chips, labels, status" title="Small parts, fixed meanings">
          <div className={styles.grid}>
            <Card>
              <h3 className="k-h4">Filter chips</h3>
              <ChipRow>
                <Chip end="menu">Type</Chip>
                <Chip pressed value="Accounts" end="remove">
                  Department:{" "}
                </Chip>
                <Chip pressed value="Confidential" end="remove">
                  Label:{" "}
                </Chip>
                <Chip add>Party</Chip>
                <Chip pressed={false}>Needs attention</Chip>
              </ChipRow>
              <Divider />
              <h3 className="k-h4">Tags</h3>
              <ChipRow>
                <Tag>GST</Tag>
                <Tag>FY 2026–27</Tag>
                <Tag>Wakad</Tag>
                <Tag deva>सातबारा</Tag>
              </ChipRow>
            </Card>
            <Card>
              <h3 className="k-h4">Sensitivity labels</h3>
              <ChipRow>
                <LabelChip sensitivity="public" />
                <LabelChip sensitivity="internal" />
                <LabelChip sensitivity="confidential" />
                <LabelChip sensitivity="restricted" />
              </ChipRow>
              <Divider />
              <h3 className="k-h4">Version chips, problems</h3>
              <ChipRow>
                <VersionChip>v1</VersionChip>
                <VersionChip>v2</VersionChip>
                <VersionChip kind="current">Current</VersionChip>
                <VersionChip kind="newer">v1 · newer?</VersionChip>
                <ProblemFlag>2 pages unreadable</ProblemFlag>
              </ChipRow>
            </Card>
            <Card className={styles.full}>
              <h3 className="k-h4">Status badges</h3>
              <ChipRow>
                <StatusBadge state="verified" />
                <StatusBadge state="review" />
                <StatusBadge state="from" />
                <StatusBadge state="notfound" />
                <StatusBadge state="reading">Reading 4 of 12</StatusBadge>
                <StatusBadge state="failed">Couldn’t read</StatusBadge>
              </ChipRow>
              <Banner tone="danger" title="Couldn’t read Invoice_12.pdf page 3.">
                The photo is blurred. Upload a sharper scan, or type the values in.
              </Banner>
            </Card>
          </div>
        </Section>

        <Section n="09" eyebrow="Navigation" title="Tabs, tree, breadcrumbs">
          <div className={styles.grid}>
            <Card>
              <h3 className="k-h4">Tabs</h3>
              <Tabs
                label="Document sections"
                idPrefix="kit"
                selected={tab}
                onSelect={setTab}
                tabs={[
                  { id: "details", label: "Details" },
                  { id: "text", label: "Text" },
                  { id: "questions", label: "Questions", count: 4 },
                  { id: "versions", label: "Versions", count: 2 },
                  { id: "activity", label: "Activity" },
                  { id: "values", label: "Values", count: "3 to check", countTone: "review" },
                ]}
              />
              <Divider />
              <h3 className="k-h4">Breadcrumbs</h3>
              <Breadcrumbs
                items={[
                  { label: "All documents", href: "/kit" },
                  { label: "Land & Legal", href: "/kit" },
                  { label: "Land records", href: "/kit" },
                  { label: "Survey 44, Wakad" },
                ]}
              />
              <Divider />
              <h3 className="k-h4">Segmented</h3>
              <ButtonGroup>
                <Segmented
                  label="View"
                  value={view}
                  onChange={setView}
                  options={[
                    { value: "list", label: "List", icon: "list" },
                    { value: "grid", label: "Grid", icon: "grid" },
                  ]}
                />
                <RadioPill
                  label="Search in"
                  name="kit-scope"
                  value={scope}
                  onChange={setScope}
                  options={[
                    { value: "inside", label: "Inside documents" },
                    { value: "names", label: "Names" },
                  ]}
                />
              </ButtonGroup>
            </Card>
            <Card>
              <h3 className="k-h4">Folder tree</h3>
              <Tree label="Folders">
                <TreeGroup>Departments</TreeGroup>
                <TreeItem href="/kit" label="Accounts" icon="department" count="1,310" expanded />
                <TreeItem href="/kit" label="Invoices" level={1} icon="folder" count="820" expanded />
                <TreeItem href="/kit" label="Mehta Textiles" level={2} icon="folder" count="64" />
                <TreeItem href="/kit" label="Land & Legal" icon="department" count="420" expanded onToggle={noop} />
                <TreeItem href="/kit" label="Land records" level={1} icon="folder" count="138" expanded />
                <TreeItem href="/kit" label="Survey 44, Wakad" level={2} icon="folder" count="12" active />
                <TreeItem href="/kit" label="HR" icon="department" count="260" expanded={false} />
                <TreeGroup>Other</TreeGroup>
                <TreeItem href="/kit" label="Not in a department" icon="file" count="312" />
                <TreeItem href="/kit" label="Trash" icon="trash" count="9" />
              </Tree>
            </Card>
          </div>
        </Section>

        <Section n="10" eyebrow="Table" title="Sortable list, dense and calm">
          <BulkBar count="1 selected" onClear={noop}>
            <Button size="sm" icon="move">
              Move
            </Button>
            <Button size="sm" icon="tag">
              Tag
            </Button>
            <Button size="sm" icon="read-again">
              Read again
            </Button>
            <Button size="sm" icon="trash">
              Move to Trash
            </Button>
          </BulkBar>
          <Table>
            <thead>
              <tr>
                <th>
                  <input type="checkbox" aria-label="Select all" />
                </th>
                <SortHeader label="Name" onSort={noop} />
                <SortHeader label="Type" onSort={noop} />
                <SortHeader label="Label" onSort={noop} />
                <SortHeader label="Version" onSort={noop} />
                <SortHeader label="Document date" sort="descending" onSort={noop} />
                <SortHeader label="Updated by" onSort={noop} />
              </tr>
            </thead>
            <tbody>
              <Row>
                <Cell>
                  <input type="checkbox" aria-label="Select GST Invoice 0412" />
                </Cell>
                <Cell>
                  <NameCell ext="PDF" name="GST Invoice 0412 Mehta Textiles.pdf" />
                </Cell>
                <Cell>GST invoice</Cell>
                <Cell>
                  <LabelChip sensitivity="internal" />
                </Cell>
                <Cell>
                  <VersionChip>v1</VersionChip>
                </Cell>
                <Cell>14 Oct 2026</Cell>
                <Cell>Priya</Cell>
              </Row>
              <Row selected>
                <Cell>
                  <input type="checkbox" aria-label="Select Rent Agreement Shinde" defaultChecked />
                </Cell>
                <Cell>
                  <NameCell ext="PDF" name="Rent Agreement Shinde.pdf" />
                </Cell>
                <Cell>Rent agreement</Cell>
                <Cell>
                  <LabelChip sensitivity="confidential" />
                </Cell>
                <Cell>
                  <VersionChip>v2</VersionChip>
                </Cell>
                <Cell>1 Oct 2026</Cell>
                <Cell>Rahul</Cell>
              </Row>
              <Row>
                <Cell>
                  <input type="checkbox" aria-label="Select 7/12 Survey 44 Wakad" />
                </Cell>
                <Cell>
                  <NameCell ext="PDF" name="7/12 Survey 44 Wakad.pdf" sub={<SubLine deva>सातबारा उतारा · मराठी</SubLine>} />
                </Cell>
                <Cell>7/12 extract</Cell>
                <Cell>
                  <LabelChip sensitivity="confidential" />
                </Cell>
                <Cell>
                  <VersionChip kind="newer">v1 · newer?</VersionChip>
                </Cell>
                <Cell>28 Sep 2026</Cell>
                <Cell>Tushar</Cell>
              </Row>
              <Row>
                <Cell>
                  <input type="checkbox" aria-label="Select Sale Deed Shinde" />
                </Cell>
                <Cell>
                  <NameCell ext="PDF" name="Sale Deed Shinde Survey 44.pdf" sub={<ProblemFlag>2 pages unreadable</ProblemFlag>} />
                </Cell>
                <Cell>Sale deed</Cell>
                <Cell>
                  <LabelChip sensitivity="restricted" />
                </Cell>
                <Cell>
                  <VersionChip>v1</VersionChip>
                </Cell>
                <Cell>12 Mar 2024</Cell>
                <Cell>Priya</Cell>
              </Row>
            </tbody>
          </Table>
        </Section>

        <Section n="11" eyebrow="Cards, banners, toasts" title="Say what happened and what to do">
          <div className={styles.gridNarrow}>
            <StatCard label="Documents" value="2,560" delta="+148 this week" />
            <LinkCard href="/kit" icon="department" title="Land & Legal" meta="420 files · 3 folders" />
            <LinkCard href="/kit" icon="file" title="Not in a department" meta="312 files · Sort them into folders" dashed />
          </div>
          <div className={styles.gridWide}>
            <Banner tone="info" title="Still reading 5 files.">
              You can keep working; answers will include them once they’re ready.
            </Banner>
            <Banner
              tone="warning"
              title="Glare covers the lower half of page 3. Upload a better photo."
              actions={
                <ButtonGroup>
                  <Button size="sm">Read this page again</Button>
                  <Button size="sm">Upload a better scan</Button>
                  <Button size="sm" variant="quiet">
                    Keep as is
                  </Button>
                </ButtonGroup>
              }
            >
              6 values on this page couldn’t be checked.
            </Banner>
            <Banner
              tone="version"
              title="This looks like a new version of 7/12 Survey 44 Wakad.pdf."
              actions={
                <ButtonGroup>
                  <Button size="sm" variant="primary">
                    Make it version 2
                  </Button>
                  <Button size="sm">Keep separate</Button>
                  <Button size="sm" variant="quiet">
                    Compare first
                  </Button>
                </ButtonGroup>
              }
            >
              86% of the text is the same and the survey number matches.
            </Banner>
            <div className={styles.stack}>
              <Banner
                tone="quiet"
                icon="link"
                inline
                actions={
                  <Button size="sm" variant="quiet" icon="undo">
                    Undo
                  </Button>
                }
              >
                Linked as version 3 of GST Invoice 0412 Mehta Textiles.pdf (same file, scanned again).
              </Banner>
              <Banner tone="danger" title="Salary Register Sept.xlsx wasn’t saved.">
                The file is 62 MB; the limit is 50 MB. Split it by month and upload again.
              </Banner>
            </div>
          </div>
          <div className={styles.grid}>
            <div className={styles.stack}>
              <span className="k-eyebrow">Toasts · bottom left, 6 s, pause on hover</span>
              <Toast action={{ label: "Undo", onClick: noop }}>Moved 3 files to Land &amp; Legal › Land records</Toast>
              <Toast icon="trash" action={{ label: "Undo", onClick: noop }}>
                Sale Deed Shinde Survey 44.pdf moved to Trash
              </Toast>
            </div>
            <div className={styles.stack}>
              <span className="k-eyebrow">In-page confirmation · never a browser popup</span>
              <InlineConfirm
                label="Confirm move to Trash"
                question="Move 3 files to Trash?"
                detail="You can restore them for 30 days."
                confirmLabel="Move to Trash"
                onConfirm={noop}
                onCancel={noop}
              />
            </div>
          </div>
        </Section>

        <Section n="12" eyebrow="Dialog, drawer, menu" title="Things that float">
          <div className={styles.grid}>
            <div className={styles.scrim}>
              <DialogPanel
                id="kit-dialog"
                title="Make version 2 the current one?"
                onClose={noop}
                actions={
                  <>
                    <Button>Cancel</Button>
                    <Button variant="primary">Make version 2 current</Button>
                  </>
                }
              >
                <p className="k-muted">
                  Rent Agreement Shinde.pdf will open on version 2 for everyone. Version 1 stays in Versions and can be made current again.
                </p>
                <Field id="kit-note" label="Note for this version (optional)">
                  {(control) => <TextInput {...control} defaultValue="Rent changed to ₹45,000" />}
                </Field>
              </DialogPanel>
            </div>
            <div className={styles.drawerFrame}>
              <div className={styles.drawerScrim} />
              <DrawerPanel
                id="kit-drawer"
                title="Move 3 files"
                width={300}
                onClose={noop}
                footer={
                  <>
                    <Button>Cancel</Button>
                    <Button variant="primary">Move here</Button>
                  </>
                }
              >
                <TextInput icon="search" aria-label="Find a folder" placeholder="Find a folder" />
                <Tree label="Folders to move to">
                  <TreeItem href="/kit" label="Land & Legal" />
                  <TreeItem href="/kit" label="Land records" level={1} active />
                  <TreeItem href="/kit" label="Agreements" level={1} />
                  <TreeItem href="/kit" label="Accounts" />
                </Tree>
              </DrawerPanel>
            </div>
            <div className={styles.stack}>
              <span className={styles.row}>
                <Avatar name="Priya Kulkarni" />
                <span className="k-small">Account menu</span>
              </span>
              <Menu>
                <MenuHeader title="Priya Kulkarni" sub="Admin · Wakad Realty" />
                <MenuRow label="Theme">
                  <Segmented
                    label="Theme"
                    value={themeChoice}
                    onChange={setThemeChoice}
                    options={[
                      { value: "light", label: "Light", icon: "sun" },
                      { value: "dark", label: "Dark", icon: "moon" },
                      { value: "system", label: "System" },
                    ]}
                  />
                </MenuRow>
                <MenuItems label="Account">
                  <MenuItem icon="user" onSelect={noop}>
                    Your profile
                  </MenuItem>
                  <MenuItem icon="sign-out" onSelect={noop}>
                    Sign out
                  </MenuItem>
                  <MenuItem icon="lock" danger onSelect={noop}>
                    Sign out everywhere
                  </MenuItem>
                </MenuItems>
              </Menu>
            </div>
          </div>
        </Section>

        <Section n="13" eyebrow="Upload progress" title="Three stages, never 100% early">
          <DropZone
            title="Drop up to 100 files here, or choose files"
            help="PDF, JPG, PNG, Excel, CSV · up to 50 MB each · scans and phone photos are fine"
            onFiles={noop}
            actions={
              <>
                <Button variant="primary" icon="file">
                  Choose files
                </Button>
                <Button icon="camera">Take a photo</Button>
              </>
            }
          />
          <div className={styles.grid}>
            <Card>
              <h3 className="k-h4">Per file · Sending → Saving → Reading</h3>
              <div className={styles.stack}>
                <StageProgress name="Bank Statement HDFC Sept.xlsx" progress={{ stage: "sending", percent: 60 }} />
                <StageProgress name="NA Permission Plot 7.pdf" progress={{ stage: "saving" }} />
                <StageProgress name="7/12 Survey 44 Wakad (2).pdf" progress={{ stage: "reading", done: 4, total: 12 }} />
                <div className={styles.stack}>
                  <span className={styles.row}>
                    <span>GST Invoice 0413 Mehta Textiles.pdf</span>
                    <StatusBadge state="verified">Ready</StatusBadge>
                  </span>
                  <FiledPath>Accounts / Invoices / Mehta Textiles</FiledPath>
                </div>
              </div>
            </Card>
            <Card>
              <h3 className="k-h4">Batch summary</h3>
              <strong>48 files · 41 ready · 5 reading · 2 waiting · 1 already uploaded</strong>
              <BatchMeter counts={{ ready: 41, reading: 5, waiting: 2, dup: 1 }} />
              <Banner tone="version" compact title="4 files look like new versions." actions={<Button size="sm" variant="primary">Review</Button>} />
            </Card>
          </div>
        </Section>

        <Section n="14" eyebrow="Timeline, citations, source card" title="Every answer points at its proof">
          <div className={styles.grid}>
            <Card>
              <h3 className="k-h4">Timeline</h3>
              <Timeline
                entries={[
                  { key: "1", icon: "eye", text: <><strong>Priya</strong> viewed</>, when: "today 11:04" },
                  { key: "2", icon: "versions", tone: "accent", text: <><strong>Rahul</strong> linked version 2 · “Rent changed to ₹45,000”</>, when: "today 10:42" },
                  { key: "3", icon: "tag", tone: "review", text: <><strong>Anita</strong> changed the label Internal → Confidential</>, when: "5 Oct 2026, 16:20" },
                  { key: "4", icon: "upload", text: <><strong>Rahul</strong> uploaded version 1 to Land &amp; Legal / Agreements</>, when: "3 Sep 2026, 09:15" },
                ]}
              />
            </Card>
            <div className={styles.stack}>
              <Question>What is the monthly rent in the Shinde agreement?</Question>
              <AnswerCard status="verified" meta="from 2 pages in 1 document">
                The monthly rent is <strong>₹45,000</strong>, due on or before the 5th of each month <CitationChip n={1} active onSelect={noop} />. The security
                deposit is ₹1,50,000 <CitationChip n={2} onSelect={noop} />, refundable at the end of the licence on 30 Sep 2027 <CitationChip n={3} onSelect={noop} />.
              </AnswerCard>
              <SourceCard
                n={1}
                name="Rent Agreement Shinde.pdf"
                version={<VersionChip>v2</VersionChip>}
                meta="Page 2 · clause 3 · Land & Legal / Agreements"
                crop={
                  <>
                    3. The Licensee shall pay a monthly licence fee of <Mark kind="cited">Rs. 45,000 (Rupees Forty Five Thousand only)</Mark> on or before the 5th…
                  </>
                }
                actions={
                  <ButtonGroup>
                    <ButtonLink href="/kit" size="sm" icon="open-spot">
                      Open page 2
                    </ButtonLink>
                    <Button size="sm" variant="quiet" icon="copy">
                      Copy answer with quotes
                    </Button>
                  </ButtonGroup>
                }
              />
              <Working seconds={7} />
              <Composer
                id="kit-question"
                label="Your question"
                value={question}
                onChange={setQuestion}
                onSend={noop}
                placeholder="Ask about your documents"
                tools={<SuggestionChip onAsk={setQuestion}>Rent due this month</SuggestionChip>}
              />
            </div>
          </div>
        </Section>

        <Section n="15" eyebrow="Viewer parts" title="Thumbnails, marks, zoom, readings">
          <div className={styles.grid}>
            <Card>
              <h3 className="k-h4">Page thumbnails</h3>
              <div className={styles.thumbRow}>
                <PageThumb page={1} current onSelect={noop} />
                <PageThumb page={2} onSelect={noop} />
                <PageThumb page={3} flag="unreadable" onSelect={noop} />
                <PageThumb page={4} flag="review" toReview={2} onSelect={noop} />
              </div>
              <Divider />
              <h3 className="k-h4">Zoom</h3>
              <Zoom percent={125} onZoomIn={noop} onZoomOut={noop} onFit={noop} />
            </Card>
            <Card>
              <h3 className="k-h4">Marks on a page</h3>
              <Viewer style={{ padding: 20, justifyContent: "center", borderRadius: 4 }}>
                <Paper style={{ padding: "28px 30px", maxWidth: 460 }}>
                  <p style={{ fontWeight: 600, textAlign: "center" }}>LEAVE AND LICENCE AGREEMENT</p>
                  <PaperRule style={{ margin: "8px 0 10px" }} />
                  <p>
                    Licensor: <Mark kind="unconfirmed">Suresh Baburao Shinde</Mark>, Survey 44/2, Wakad, Pune.
                  </p>
                  <p>
                    Licensee: <Mark kind="unsure">Prakash</Mark> Mehta
                  </p>
                  <p>
                    3. Monthly licence fee of <Mark kind="cited">Rs. 45,000</Mark> payable on or before the 5th.
                  </p>
                  <p>
                    4. Security deposit of <Mark kind="unreadable">Rs. 1,50,000</Mark> paid by cheque.
                  </p>
                </Paper>
              </Viewer>
              <MarkLegend />
            </Card>
            <Card>
              <h3 className="k-h4">Two readings (review)</h3>
              <div className={styles.two}>
                <Reading label="First reading" value="₹45,000" note="matches page 1 summary" picked />
                <Reading label="Second reading" value="₹46,000" note="The 5 is smudged" />
              </div>
              <KeyValueList
                items={[
                  { key: "type", name: "Type", value: "Rent agreement" },
                  { key: "date", name: "Document date", value: "1 Oct 2026" },
                ]}
              />
            </Card>
            <Card>
              <h3 className="k-h4">Marathi and Hindi keyboard helper</h3>
              <DevanagariKeys onKey={noop} onSpace={noop} onErase={noop} />
            </Card>
          </div>
        </Section>

        <Section n="16" eyebrow="Empty, loading, small parts" title="Paper and signal, not cartoons">
          <div className={styles.grid}>
            <Card>
              <EmptyState
                title="No documents in this folder yet"
                action={
                  <Button variant="primary" icon="upload">
                    Upload to Survey 44, Wakad
                  </Button>
                }
              >
                Drop bills, agreements or 7/12 extracts here. Kaagaz reads every page and files each one into the right folder.
              </EmptyState>
            </Card>
            <Card>
              <h3 className="k-h4">Skeletons</h3>
              <div className={styles.stack} aria-busy="true" aria-label="Loading documents">
                {["62%", "48%", "70%"].map((width) => (
                  <div key={width} className={styles.skelRow}>
                    <Skeleton width={22} height={28} />
                    <span className={styles.skelText}>
                      <Skeleton width={width} height={12} />
                      <Skeleton width="34%" height={10} />
                    </span>
                    <Skeleton width={84} height={20} />
                  </div>
                ))}
                <div className={styles.tiles}>
                  <Skeleton height={72} />
                  <Skeleton height={72} />
                  <Skeleton height={72} />
                  <Skeleton height={72} />
                </div>
              </div>
              <Divider />
              <h3 className="k-h4">Avatar, keyboard keys</h3>
              <span className={styles.row}>
                <Avatar name="Priya Kulkarni" />
                <Avatar name="Rahul" />
                <Avatar name="Anita" size="lg" />
                <Avatar name="Tushar" size="lg" />
                <span className={`k-small k-muted ${styles.keyNote}`}>
                  <Kbd>1</Kbd> first reading
                </span>
                <span className={`k-small k-muted ${styles.keyNote}`}>
                  <Kbd>2</Kbd> second
                </span>
                <span className={`k-small k-muted ${styles.keyNote}`}>
                  <Kbd>T</Kbd> type
                </span>
                <span className={`k-small k-muted ${styles.keyNote}`}>
                  <Kbd>J</Kbd>
                  <Kbd>K</Kbd> next / previous
                </span>
              </span>
            </Card>
            <Card>
              <CardHead>
                <h3 className="k-h4">Sidebar and phone tabs</h3>
              </CardHead>
              <NavItem href="/kit" label="Home" icon="home" active />
              <NavItem href="/kit" label="Documents" icon="documents" />
              <NavItem href="/kit" label="Review" icon="review" count={23} countWords="to review" />
              <PhoneTabBar
                places={[
                  { href: "/kit?tab=home", label: "Home", icon: "home", active: true },
                  { href: "/kit?tab=documents", label: "Documents", icon: "documents" },
                  { href: "/kit?tab=ask", label: "Ask", icon: "ask" },
                  { href: "/kit?tab=review", label: "Review", icon: "review", count: 23, countWords: "to review" },
                  { href: "/kit?tab=more", label: "More", icon: "menu" },
                ]}
              />
            </Card>
            <Card>
              <h3 className="k-h4">Documents by type</h3>
              <BarChart
                label="Documents by type"
                bars={[
                  { label: "GST invoice", value: 820 },
                  { label: "7/12 extract", value: 410 },
                  { label: "Rent agreement", value: 160 },
                ]}
              />
            </Card>
          </div>
        </Section>
      </div>
    </div>
  );
}
