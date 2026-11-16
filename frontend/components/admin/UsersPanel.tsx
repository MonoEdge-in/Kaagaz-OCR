"use client";

// Users of this customer: the list (one User column, the name with the e-mail
// under it, so neither is squeezed) with each account's status, changing a
// role, and each row's other actions in one menu (UI audit AD2, 16 Oct 2026):
// disabling or enabling an account, unlocking it, resetting its password or its
// two-step sign-in, each that takes something away asked first in a dialog. Each menu item
// has one plain line under it saying what it does (USER_MENU_HELP). "Add user" opens
// the form in a side panel (AD3), with a password field across it and Generate.
// What happened shows in a toast in the corner. An admin cannot disable themselves, drop
// their own admin role or reset their own two-step sign-in; the controls for that are not
// shown, and the server refuses it anyway. An admin may allow a member or
// reviewer to delete forever (asked first; needs the admin's own two-step
// sign-in), and stop it again.
//
// Connected accounts (#457 phase 1): "Connections to approve" above the list while any wait
// (Approve, Decline; focus then moves to the next row's Approve, else the Users heading), a
// "Signs in with" column, and "Remove connected accounts" in a row's menu, asked first. The
// section hides only on the server's 404 (no provider on); any other failure says so, with Try
// again. Approving one another admin already decided (404) reloads the list.
// #507: "Needs to connect Google" beside how someone signs in, while their role uses Google only
// and they have no Google account that passes (googleTag: or that its domain is not checked yet); Reset password says it disconnects Google too.
// Add a user says, under the role, that the person connects Google after choosing a password,
// while Google sign-in is on for that role (the rules from Settings, read when the form opens).
//
// #603: while the server has WhatsApp on, a WhatsApp column (the last 4 digits and since when) and
// "Disconnect WhatsApp" in a connected person's menu, asked first; the bot tells them once.
//
// A reset password comes back once, in the reply. It is shown in a card above
// the list, with Copy, until Done or until the tab is left; it is never stored.
//
// PRD 7 (#604): an Access column (Management, then the person's departments), read only; the
// menu's Access opens their Access page, where it is changed; Make or Stop Management asks first
// (on yourself, Make Management asks for your password too), hidden for a turned-off account.

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";
import type { FormEvent } from "react";
import { asksFirst } from "@/components/access/passwordStep";
import { usePasswordStep } from "@/components/access/SelfPasswordDialog";
import {
  MANAGEMENT_CHIP,
  MANAGEMENT_MEANS,
  MANAGEMENT_TITLE,
  MANAGEMENT_WHAT,
  accessErrorText,
  departmentsText,
  makeManagementQuestion,
  managementToast,
  stopManagementQuestion,
} from "@/components/access/words";
import { ActionDialog } from "@/components/documents/ActionDialog";
import { useMinuteNow } from "@/components/chat/hooks";
import { roleName } from "@/components/shell/nav";
import { closeAllowed, isBackdropClick } from "@/components/ui/drawerLogic";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { PasswordField } from "@/components/ui/PasswordField";
import {
  ApiError,
  adminDisconnectWhatsApp,
  adminWhatsApp,
  approveConnection,
  createUser,
  declineConnection,
  getSettings,
  listUsers,
  pendingConnections,
  removeConnections,
  resetUser2fa,
  resetUserPassword,
  setDeleteForever,
  setManagement,
  unlockUser,
  updateUser,
} from "@/lib/api";
import { DRAFT_KEPT, clearDraft, readDraft, saveDraftSoon } from "@/lib/drafts";
import { errorWords } from "@/lib/errorWords";
import { formatDate } from "@/lib/format";
import type { AddedUser, AdminUser, GoogleRules, PendingConnection, Role, WhatsAppAdmin } from "@/lib/types";
import table from "@/components/documents/Table.module.css";
import { CopyButton } from "./CopyButton";
import { MoreRows, useRowCap } from "./RowCap";
import { RowMenu } from "./RowMenu";
import type { RowMenuItem } from "./RowMenu";
import { Toast } from "./Toast";
import type { Notice } from "./Toast";
import {
  ADD_USER_DRAFT,
  ALREADY_DECIDED,
  CONNECTIONS_FAILED,
  MIN_PASSWORD_LENGTH,
  NEW_USER_FIELD_IDS,
  addedNotice,
  addUserGoogleHint,
  ROLES,
  allowForeverQuestion,
  decideLabel,
  firstInvalidField,
  googleTag,
  foreverControl,
  generatePassword,
  newUserDraft,
  newUserFromDraft,
  pendingLine,
  removeConnectionsQuestion,
  resetPasswordQuestion,
  signsInWith,
  twoStepNote,
  userActions,
  userStatus,
  validateNewUser,
} from "./logic";
import type { NewUserErrors, NewUserInput } from "./logic";
import { whatsappCellState } from "./whatsappSettings";
import type { WhatsAppCellState, WhatsAppLoad } from "./whatsappSettings";
import styles from "./Admin.module.css";

const SKELETON_ROWS = [0, 1, 2];
const USERS_HEADING_ID = "users-heading";
const approveId = (connectionId: string) => `approve-${connectionId}`;

/** A temporary password just made for one user: shown once, then gone. */
interface Revealed {
  name: string;
  password: string;
}

/** providers: the sign-in providers switched on (Me's), named in Remove connected accounts.
 * whatsapp: the server has WhatsApp on (Me's), so the WhatsApp column shows. */
export function UsersPanel({
  currentUserId,
  adminTwoStep,
  providers,
  whatsapp = false,
}: {
  currentUserId: string;
  adminTwoStep: boolean;
  providers: readonly string[];
  whatsapp?: boolean;
}) {
  const [attempt, setAttempt] = useState(0);
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<{ attempt: number; message: string } | null>(null);
  const [revealed, setRevealed] = useState<Revealed | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [adding, setAdding] = useState(false);
  const closeNotice = useCallback(() => setNotice(null), []);
  // Rounded to the minute, so "Locked until" and Unlock clear on their own.
  const now = useMinuteNow();
  const cap = useRowCap();
  const [connected, setConnected] = useState<WhatsAppLoad>("loading");
  const [whatsAppAttempt, setWhatsAppAttempt] = useState(0);

  useEffect(() => {
    if (!whatsapp) return;
    let live = true;
    adminWhatsApp().then(
      (reply) => live && setConnected(reply.people),
      () => live && setConnected("failed"), // the column says so, with Try again; never "Not connected"
    );
    return () => {
      live = false;
    };
  }, [whatsapp, whatsAppAttempt]);

  useEffect(() => {
    let cancelled = false;
    listUsers().then(
      (result) => {
        if (!cancelled) setUsers(result.items);
      },
      (caught: unknown) => {
        if (!cancelled) {
          setError({
            attempt,
            message: errorWords(caught),
          });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [attempt]);

  const failed = error !== null && error.attempt === attempt ? error.message : null;

  function replace(user: AdminUser) {
    // A reply without signs_in_with (#457) changed no connection: the row keeps what it had.
    setUsers((current) =>
      current ? current.map((existing) => (existing.id === user.id ? { ...existing, ...user } : existing)) : current,
    );
  }

  return (
    <div className={styles.panelStack}>
      <ConnectionsToApprove onApproved={() => setAttempt((n) => n + 1)} onNotice={setNotice} />
      <section className={styles.section} aria-labelledby={USERS_HEADING_ID}>
        <div className={styles.sectionHead}>
          <h2 id={USERS_HEADING_ID} tabIndex={-1} className={styles.sectionHeading}>
            Users
          </h2>
          <button
            type="button"
            className="btn btn-primary"
            aria-haspopup="dialog"
            aria-expanded={adding}
            onClick={() => setAdding(true)}
          >
            <Icon name="plus" />
            Add user
          </button>
        </div>
        {revealed && <TemporaryPassword revealed={revealed} onDone={() => setRevealed(null)} />}
        {failed && !users && (
          <div className={styles.errorBanner} role="alert">
            <Icon name="alert" className={styles.errorIcon} />
            <div className={styles.errorBody}>
              <p className={styles.errorTitle}>Could not load the users.</p>
              <p>{failed}</p>
            </div>
            <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
              <Icon name="refresh" />
              Try again
            </button>
          </div>
        )}
        {!users && !failed && (
          <>
            <p className="visually-hidden" role="status">
              Loading the users…
            </p>
            <div className={`delayed ${styles.skeletons}`} aria-hidden="true">
              {SKELETON_ROWS.map((row) => (
                <span key={row} className={`skeleton ${styles.skeletonRow}`} />
              ))}
            </div>
          </>
        )}
        {users && (
          <div className={`${table.reflowWrap} ${styles.tableScroll}`}>
            <table className={`table ${table.reflow}`} role="table">
              <caption className="visually-hidden">Users of this customer</caption>
              <thead role="rowgroup">
                <tr role="row">
                  <th scope="col" role="columnheader">
                    User
                  </th>
                  <th scope="col" role="columnheader" className={styles.roleColumn}>
                    Role
                  </th>
                  <th scope="col" role="columnheader">
                    Access
                  </th>
                  <th scope="col" role="columnheader" className={styles.statusColumn}>
                    Status
                  </th>
                  <th scope="col" role="columnheader">
                    Signs in with
                  </th>
                  {whatsapp && (
                    <th scope="col" role="columnheader">
                      WhatsApp
                    </th>
                  )}
                  <th scope="col" role="columnheader" className={styles.addedColumn}>
                    Added
                  </th>
                  <th scope="col" role="columnheader" className={styles.actionsColumn}>
                    <span className="visually-hidden">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody role="rowgroup">
                {users.slice(0, cap.limit).map((user) => (
                  <UserRow
                    key={user.id}
                    user={user}
                    isSelf={user.id === currentUserId}
                    myId={currentUserId}
                    adminTwoStep={adminTwoStep}
                    providers={providers}
                    whatsapp={whatsapp ? whatsappCellState(connected, user.id) : null}
                    onWhatsApp={(reply) => setConnected(reply.people)}
                    onWhatsAppRetry={() => {
                      setConnected("loading");
                      setWhatsAppAttempt((n) => n + 1);
                    }}
                    now={now}
                    onChanged={replace}
                    onPasswordReset={setRevealed}
                    onNotice={setNotice}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {users && <MoreRows shown={Math.min(cap.limit, users.length)} total={users.length} what="users" onMore={cap.showMore} />}
        {/* AD7: two-step is required for admins (0035); one without it sets it up at the next sign-in,
            except an admin who turned their own off (30 Oct 2026). */}
        {users?.some((user) => user.role === "admin" && !user.totp_enabled && !user.two_step_turned_off) && (
          <p className="hint">{twoStepNote()}</p>
        )}
      </section>
      {adding && (
        <AddUserPanel
          userId={currentUserId}
          google={providers.includes("google")}
          onClose={() => setAdding(false)}
          onAdded={(user) => {
            setUsers((current) => (current ? [...current, user] : [user]));
            setAdding(false);
            // #457: with email on, whether the invite went; else the temporary password, as before.
            setNotice(addedNotice(user));
          }}
        />
      )}
      <Toast notice={notice} onClose={closeNotice} />
    </div>
  );
}

/** Disabling and the two resets ask first (each signs the user out on every device), and
 * so does allowing Delete forever (UI audit AD2: every action that takes something away). */
type Confirming = "disable" | "password" | "2fa" | "forever" | "connections" | "whatsapp" | "makeManagement" | "stopManagement";

const CONFIRM_TEXT: Record<Confirming, (name: string, providers: readonly string[], ways?: readonly string[]) => string> = {
  connections: (name, providers) => removeConnectionsQuestion(name, providers),
  disable: (name) => `Disable ${name}'s account? They are signed out and can't sign in until it is enabled again.`,
  password: (name, _providers, ways) => resetPasswordQuestion(name, ways),
  "2fa": (name) =>
    `Reset ${name}'s two-step code? They are signed out, and can sign in with their password, then set up a new code.`,
  forever: allowForeverQuestion,
  whatsapp: (name) => `Disconnect ${name}'s WhatsApp? Their chat can no longer reach Kaagaz. The Kaagaz bot tells them once.`,
  makeManagement: makeManagementQuestion,
  stopManagement: stopManagementQuestion,
};

// Plain words (25 Oct 2026): the confirm button says the menu item's own verb, and the
// toast says it was done ("Disable", then "disabled.").
const CONFIRM_BUTTON: Record<Confirming, string> = {
  disable: "Disable",
  password: "Reset password",
  "2fa": "Reset two-step code",
  forever: "Allow Delete forever",
  connections: "Remove connected accounts",
  whatsapp: "Disconnect WhatsApp",
  makeManagement: "Make Management",
  stopManagement: "Stop Management",
};

/** What each item in a user's menu does, in one plain line under it (plain words, 25 Oct 2026). */
export const USER_MENU_HELP = {
  unlock: "Lets them try to sign in again now, without waiting for the lock to end.",
  enable: "Lets them sign in again.",
  stopForever: "Takes back their right to delete documents in Trash permanently.",
  allowForever: "Lets them delete any document in Trash permanently. Deleted documents can't be recovered.",
  password: "Makes a one-time password for you to give them. They are signed out, then choose their own when they sign in.",
  twoStep: "For a lost phone: turns off their two-step code and signs them out, so they can set up a new one.",
  disable: "Signs them out and stops them signing in until you enable them again.",
  connections: "Stops them signing in with Google. Their password still works.",
  whatsapp: "Their WhatsApp chat can no longer reach their files. They can connect it again.",
  // PRD 7 (#604)
  access: "See and change which departments, private folders and files they can see.",
  makeManagement: "They see every file in the organisation.",
  stopManagement: "They see only their departments' files and files given to them.",
} as const;

/** PRD 7 (#604): Make or Stop Management, or neither for a turned-off account (409 disabled). */
export function managementItem(user: AdminUser): "make" | "stop" | null {
  if (user.disabled) return null;
  return user.management ? "stop" : "make";
}

/** The Access column: a Management chip, then their departments ("Finance (head), Sales"). */
export function AccessCell({ user }: { user: AdminUser }) {
  return (
    <span className={styles.statusList}>
      {user.management && (
        <span className="status-chip" data-tone="working" title={MANAGEMENT_TITLE}>
          {MANAGEMENT_CHIP}
          <span className="visually-hidden">{MANAGEMENT_MEANS}</span>
        </span>
      )}
      <span>{departmentsText(user.departments ?? [])}</span>
    </span>
  );
}

function UserRow({
  user,
  isSelf,
  myId,
  adminTwoStep,
  providers,
  whatsapp,
  onWhatsApp,
  onWhatsAppRetry,
  now,
  onChanged,
  onPasswordReset,
  onNotice,
}: {
  user: AdminUser;
  isSelf: boolean;
  myId: string;
  adminTwoStep: boolean;
  providers: readonly string[];
  /** Their WhatsApp cell (loading, failed, none, connected); null while the server has WhatsApp off. */
  whatsapp: WhatsAppCellState | null;
  onWhatsApp(reply: WhatsAppAdmin): void;
  onWhatsAppRetry(): void;
  now: number;
  onChanged: (user: AdminUser) => void;
  onPasswordReset: (revealed: Revealed) => void;
  onNotice: (notice: Notice) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<Confirming | null>(null);
  const router = useRouter();
  const passwordStep = usePasswordStep();

  /** Management on or off; making yourself Management asks for your password first. */
  async function management(on: boolean) {
    setConfirming(null);
    setBusy(true);
    try {
      const first = asksFirst({ myId, targetId: user.id, on });
      const row = await passwordStep.run(MANAGEMENT_WHAT, (password) => setManagement(user.id, on, password), first);
      if (row) {
        onChanged(row);
        onNotice({ text: managementToast(user.display_name, on) });
      }
    } catch (caught) {
      onNotice({ problem: true, text: accessErrorText(caught) });
    } finally {
      setBusy(false);
    }
  }

  async function act(send: () => Promise<AdminUser>, done: string) {
    setBusy(true);
    try {
      onChanged(await send());
      onNotice({ text: `${user.display_name}: ${done}` });
    } catch (caught) {
      onNotice({
        problem: true,
        text: errorWords(caught),
      });
    } finally {
      setBusy(false);
      setConfirming(null);
    }
  }

  const change = (changes: { role?: Role; disabled?: boolean }, done: string) =>
    act(() => updateUser(user.id, changes), done);

  const confirmed = (kind: Confirming) => {
    if (kind === "makeManagement" || kind === "stopManagement") return management(kind === "makeManagement");
    if (kind === "disable") return change({ disabled: true }, "disabled.");
    if (kind === "forever") return act(() => setDeleteForever(user.id, true), "Delete forever allowed.");
    if (kind === "2fa") return act(() => resetUser2fa(user.id), "two-step code reset.");
    if (kind === "connections") return act(() => removeConnections(user.id), "connected accounts removed.");
    if (kind === "whatsapp") return disconnectWhatsApp();
    return act(async () => {
      const reset = await resetUserPassword(user.id);
      onPasswordReset({ name: user.display_name, password: reset.temporary_password });
      return reset.user;
    }, "password reset.");
  };

  async function disconnectWhatsApp() {
    setBusy(true);
    try {
      onWhatsApp(await adminDisconnectWhatsApp(user.id));
      onNotice({ text: `${user.display_name}: WhatsApp disconnected.` });
    } catch (caught) {
      onNotice({ problem: true, text: errorWords(caught) });
    } finally {
      setBusy(false);
      setConfirming(null);
    }
  }

  const can = userActions(user, { isSelf, now });
  const forever = foreverControl(user, { isSelf, adminTwoStep });
  const reason = forever.kind === "allow" ? forever.disabledReason : null;
  const selectId = `role-${user.id}`;
  const managing = managementItem(user);
  // AD2: one menu per row. Safe actions first; the one that takes something away last, apart.
  const items: RowMenuItem[] = [
    { label: "Access", description: USER_MENU_HELP.access, onSelect: () => router.push(`/admin/people/${user.id}`) },
    ...(managing === "make"
      ? [{ label: "Make Management", description: USER_MENU_HELP.makeManagement, onSelect: () => setConfirming("makeManagement") }]
      : []),
    ...(managing === "stop"
      ? [{ label: "Stop Management", description: USER_MENU_HELP.stopManagement, onSelect: () => setConfirming("stopManagement") }]
      : []),
    ...(can.unlock
      ? [{ label: "Unlock", description: USER_MENU_HELP.unlock, onSelect: () => void act(() => unlockUser(user.id), "unlocked.") }]
      : []),
    ...(can.toggleDisabled && user.disabled
      ? [{ label: "Enable", description: USER_MENU_HELP.enable, onSelect: () => void change({ disabled: false }, "enabled.") }]
      : []),
    ...(forever.kind === "stop"
      ? [
          {
            label: "Stop Delete forever",
            description: USER_MENU_HELP.stopForever,
            onSelect: () => void act(() => setDeleteForever(user.id, false), "Delete forever stopped."),
          },
        ]
      : []),
    ...(forever.kind === "allow"
      ? [{ label: "Allow Delete forever", description: USER_MENU_HELP.allowForever, onSelect: () => setConfirming("forever"), disabledReason: reason }]
      : []),
    ...(can.resetPassword
      ? [{ label: "Reset password", description: USER_MENU_HELP.password, onSelect: () => setConfirming("password") }]
      : []),
    ...(can.reset2fa
      ? [{ label: "Reset two-step code", description: USER_MENU_HELP.twoStep, onSelect: () => setConfirming("2fa") }]
      : []),
    ...((user.signs_in_with ?? []).some((way) => way !== "password")
      ? [{ label: "Remove connected accounts", description: USER_MENU_HELP.connections, onSelect: () => setConfirming("connections") }]
      : []),
    ...(whatsapp?.kind === "connected"
      ? [{ label: "Disconnect WhatsApp", description: USER_MENU_HELP.whatsapp, onSelect: () => setConfirming("whatsapp") }]
      : []),
    ...(can.toggleDisabled && !user.disabled
      ? [{ label: "Disable", description: USER_MENU_HELP.disable, onSelect: () => setConfirming("disable"), destructive: true }]
      : []),
  ];
  const tag = googleTag(user);
  return (
    <tr role="row">
      <td role="cell" className={styles.userCell}>
        <span className={styles.userName}>
          {user.display_name}
          {isSelf && <span className="muted"> (you)</span>}
        </span>
        {/* AD1: one line, cut with "…"; the whole address on hover and for screen readers. */}
        <span className={styles.userEmail} title={user.email}>
          <span className="visually-hidden">E-mail: </span>
          {user.email}
        </span>
      </td>
      <td role="cell" data-label="Role">
        <label htmlFor={selectId} className="visually-hidden">
          Role for {user.display_name}
        </label>
        <select
          id={selectId}
          className={`input ${styles.roleSelect}`}
          value={user.role}
          disabled={busy || isSelf}
          onChange={(event) => {
            const role = ROLES.find((option) => option === event.target.value);
            if (role) void change({ role }, `role changed to ${roleName(role)}.`);
          }}
        >
          {ROLES.map((role) => (
            <option key={role} value={role}>
              {roleName(role)}
            </option>
          ))}
        </select>
      </td>
      <td role="cell" data-label="Access">
        <AccessCell user={user} />
      </td>
      <td role="cell" data-label="Status">
        <span className={styles.statusList}>
          {userStatus(user, now).map((part) => (
            <span key={part.text} className="status-chip" data-tone={part.tone}>
              {part.text}
            </span>
          ))}
        </span>
      </td>
      <td role="cell" data-label="Signs in with">
        <span className={styles.statusList}>
          {signsInWith(user.signs_in_with)}
          {tag && (
            <span className="status-chip" data-tone="working">
              {tag}
            </span>
          )}
        </span>
      </td>
      {whatsapp !== null && (
        <td role="cell" data-label="WhatsApp" className="tabular">
          {whatsapp.kind === "connected" && whatsapp.text}
          {whatsapp.kind === "none" && <span className="muted">Not connected</span>}
          {whatsapp.kind === "loading" && <span className="muted">Loading…</span>}
          {whatsapp.kind === "failed" && (
            <span className={styles.statusList}>
              <span className="muted">Couldn&apos;t load</span>
              <button type="button" className="btn btn-quiet" onClick={onWhatsAppRetry}>
                Try again
              </button>
            </span>
          )}
        </td>
      )}
      <td role="cell" data-label="Added" className="tabular">
        <span className={styles.date}>{formatDate(user.created_at)}</span>
      </td>
      <td role="cell" className={styles.actionsCell}>
        <span className={styles.rowActions}>
          <RowMenu name={user.display_name} items={items} />
          {busy && <span className="spinner" aria-hidden="true" />}
        </span>
        {confirming && (
          <ActionDialog title={user.display_name} onClose={() => setConfirming(null)} canClose={() => !busy}>
            <p>{CONFIRM_TEXT[confirming](user.display_name, providers, user.signs_in_with)}</p>
            {/* [Cancel] [Yes, ...]: the action last; Cancel has focus, so Enter never acts by accident. */}
            <div className="actions">
              <button type="button" className="btn" data-autofocus disabled={busy} onClick={() => setConfirming(null)}>
                Cancel
              </button>
              <button
                type="button"
                className={`btn ${confirming === "makeManagement" ? "btn-primary" : "btn-danger"} ${busy ? styles.busy : ""}`}
                disabled={busy}
                aria-busy={busy || undefined}
                onClick={() => void confirmed(confirming)}
              >
                {busy && <span className="spinner" aria-hidden="true" />}
                {CONFIRM_BUTTON[confirming]}
              </button>
            </div>
          </ActionDialog>
        )}
        {passwordStep.dialog}
      </td>
    </tr>
  );
}

/** Connections to approve (#457 phase 1): each waiting Google account, its person, email and
 * day, with Approve and Decline; shown while any wait, or when the list could not be loaded
 * (never on the 404 while no provider is on). */
function ConnectionsToApprove({ onApproved, onNotice }: { onApproved(): void; onNotice(notice: Notice): void }) {
  const [attempt, setAttempt] = useState(0);
  const [pending, setPending] = useState<PendingConnection[]>([]);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    pendingConnections().then(
      (reply) => {
        if (!live) return;
        setPending(reply.items);
        setFailed(false);
      },
      (caught: unknown) => {
        // 404: no provider is on, so there is nothing to approve. Anything else is said.
        if (live) setFailed(!(caught instanceof ApiError && caught.status === 404));
      },
    );
    return () => {
      live = false;
    };
  }, [attempt]);

  async function decide(row: PendingConnection, approve: boolean, at: number) {
    setBusy(row.id);
    try {
      const reply = await (approve ? approveConnection(row.id) : declineConnection(row.id));
      flushSync(() => {
        setPending(reply.items);
        setBusy(null);
      });
      // The row is gone: on to the next row's Approve, else the Users heading.
      const next = reply.items[at];
      document.getElementById(next ? approveId(next.id) : USERS_HEADING_ID)?.focus();
      onNotice({ text: `${row.display_name}: Google ${approve ? "approved" : "declined"}.` });
      if (approve) onApproved();
    } catch (caught) {
      setBusy(null);
      if (caught instanceof ApiError && caught.status === 404) {
        onNotice({ text: ALREADY_DECIDED });
        setAttempt((n) => n + 1); // the list as it is now
        return;
      }
      onNotice({
        problem: true,
        text: errorWords(caught),
      });
    }
  }

  if (failed) {
    return (
      <section className={styles.section} aria-labelledby="connections-heading">
        <h2 id="connections-heading" className={styles.sectionHeading}>
          Connections to approve
        </h2>
        <div className={styles.errorBanner} role="alert">
          <Icon name="alert" className={styles.errorIcon} />
          <div className={styles.errorBody}>
            <p className={styles.errorTitle}>{CONNECTIONS_FAILED}</p>
          </div>
          <button type="button" className="btn" onClick={() => setAttempt((n) => n + 1)}>
            <Icon name="refresh" />
            Try again
          </button>
        </div>
      </section>
    );
  }
  if (pending.length === 0) return null;
  return (
    <section className={styles.section} aria-labelledby="connections-heading">
      <h2 id="connections-heading" className={styles.sectionHeading}>
        Connections to approve
      </h2>
      <ul className={styles.connections}>
        {pending.map((row, at) => (
          <li key={row.id} className={styles.connection}>
            <span className={styles.connectionText}>{pendingLine(row)}</span>
            <span className={styles.rowActions}>
              <button
                type="button"
                id={approveId(row.id)}
                className="btn"
                aria-label={decideLabel(row, true)}
                disabled={busy !== null}
                onClick={() => void decide(row, true, at)}
              >
                Approve
              </button>
              <button
                type="button"
                className="btn"
                aria-label={decideLabel(row, false)}
                disabled={busy !== null}
                onClick={() => void decide(row, false, at)}
              >
                Decline
              </button>
              {busy === row.id && <span className="spinner" aria-hidden="true" />}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * The temporary password, shown once. Focus moves to its heading so a screen
 * reader hears it at once; Done (or leaving the tab) drops it from memory.
 */
function TemporaryPassword({ revealed, onDone }: { revealed: Revealed; onDone: () => void }) {
  const focusOnMount = useCallback((element: HTMLHeadingElement | null) => {
    element?.focus();
  }, []);
  return (
    <section className={`card stack ${styles.reveal}`} aria-labelledby="temporary-password-heading">
      <h3
        id="temporary-password-heading"
        ref={focusOnMount}
        tabIndex={-1}
        className={`${styles.sectionHeading} ${styles.revealHeading}`}
      >
        Temporary password for {revealed.name}
      </h3>
      <p>
        It is shown only this once. Give it to {revealed.name} yourself. They choose their own
        password when they sign in with it.
      </p>
      <div className={styles.secretRow}>
        <code className={styles.secret} translate="no">
          {revealed.password}
        </code>
        <CopyButton text={revealed.password} what="Password" buttonText="Copy password" />
      </div>
      <div>
        <button type="button" className="btn" onClick={onDone}>
          Done
        </button>
      </div>
    </section>
  );
}

const ADD_USER_TITLE_ID = "add-user-heading";
const UNSAVED_QUESTION = "Close without adding? What you typed here will be lost.";

/**
 * AD3: the new-user form in a side panel, a native <dialog> (top layer, focus trap,
 * Esc, inert page). Esc, the backdrop (press and release both on it) and Close ask
 * first when something is typed; focus goes back to "Add user" when it closes.
 */
function AddUserPanel({
  userId,
  google,
  onClose,
  onAdded,
}: {
  userId: string; // #468: the draft (email, name, role) is this admin's only
  google: boolean; // Google sign-in is switched on (Me's providers): its rules are read
  onClose: () => void;
  onAdded: (user: AddedUser) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const pressTarget = useRef<EventTarget | null>(null);
  // #468: what was typed before a session ended comes back; the password never does. A line says
  // so (DRAFT_KEPT) until the form is edited or sent.
  const [kept] = useState(() => readDraft(userId, ADD_USER_DRAFT));
  const [input, setInput] = useState<NewUserInput>(() => newUserFromDraft(kept));
  const [keptNote, setKeptNote] = useState(kept !== null);
  const edit = (next: NewUserInput) => {
    setKeptNote(false);
    setInput(next);
  };
  const [errors, setErrors] = useState<NewUserErrors>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [masked, setMasked] = useState(true);
  // #507: the organisation's Google rules, for the hint under the role; none until read (or if
  // reading fails: the hint is only a hint).
  const [googleRules, setGoogleRules] = useState<GoogleRules | undefined>(undefined);
  const googleHint = addUserGoogleHint(googleRules, input.role);
  const unsaved = input.email !== "" || input.display_name !== "" || input.password !== "";
  const askClose = () => {
    if (!busy && closeAllowed(unsaved, () => window.confirm(UNSAVED_QUESTION))) {
      clearDraft(userId, ADD_USER_DRAFT); // closed on purpose: what was typed is let go
      onClose();
    }
  };

  useEffect(() => {
    saveDraftSoon(userId, ADD_USER_DRAFT, newUserDraft(input));
  }, [userId, input]);

  useEffect(() => {
    if (!google) return;
    let live = true;
    getSettings().then(
      (settings) => {
        if (live) setGoogleRules(settings.google);
      },
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [google]);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.showModal();
    document.getElementById(NEW_USER_FIELD_IDS.email)?.focus();
    return () => {
      dialog.close();
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return; // Enter in a field while adding: never a second user
    setKeptNote(false);
    setMasked(true); // never leave a password showing once it is sent
    setServerError(null);
    const found = validateNewUser(input);
    setErrors(found);
    const first = firstInvalidField(found);
    if (first) {
      document.getElementById(NEW_USER_FIELD_IDS[first])?.focus();
      return;
    }
    setBusy(true);
    try {
      const user = await createUser({
        ...input,
        email: input.email.trim(),
        display_name: input.display_name.trim(),
      });
      clearDraft(userId, ADD_USER_DRAFT);
      onAdded(user);
    } catch (caught) {
      const message = errorWords(caught);
      if (caught instanceof ApiError && caught.code === "conflict") {
        setErrors({ email: message });
      } else {
        setServerError(message);
      }
      setBusy(false);
    }
  }

  return (
    <dialog
      ref={dialogRef}
      className={styles.sidePanel}
      aria-labelledby={ADD_USER_TITLE_ID}
      onCancel={(event) => {
        event.preventDefault(); // Esc: asks first when something is typed
        askClose();
      }}
      onPointerDown={(event) => {
        pressTarget.current = event.target;
      }}
      onClick={(event) => {
        if (isBackdropClick(pressTarget.current, event.target, event.currentTarget)) askClose();
        pressTarget.current = null;
      }}
    >
      <div className={styles.sidePanelBody}>
        <div className={styles.sectionHead}>
          <h2 id={ADD_USER_TITLE_ID} className={styles.sectionHeading}>
            Add a user
          </h2>
          <IconButton icon="close" label="Close" className="btn-quiet" onClick={askClose} />
        </div>
        {keptNote && <p className="muted">{DRAFT_KEPT}</p>}
        <form className={styles.form} onSubmit={submit} noValidate>
          <Field
            id={NEW_USER_FIELD_IDS.email}
            label="E-mail"
            error={errors.email}
            type="email"
            value={input.email}
            onChange={(email) => edit({ ...input, email })}
          />
          <Field
            id={NEW_USER_FIELD_IDS.display_name}
            label="Name"
            error={errors.display_name}
            type="text"
            value={input.display_name}
            onChange={(display_name) => edit({ ...input, display_name })}
          />
          <div className="field">
            <label htmlFor="new-user-role">Role</label>
            <select
              id="new-user-role"
              className="input"
              aria-describedby={googleHint ? "new-user-google-hint" : undefined}
              value={input.role}
              onChange={(event) => {
                const role = ROLES.find((option) => option === event.target.value);
                if (role) edit({ ...input, role });
              }}
            >
              {ROLES.map((role) => (
                <option key={role} value={role}>
                  {roleName(role)}
                </option>
              ))}
            </select>
            {googleHint && (
              <span id="new-user-google-hint" className="hint">
                {googleHint}
              </span>
            )}
          </div>
          {/* AD3: the password across the panel, Generate and Copy under it. */}
          <div className={styles.passwordBlock}>
            <PasswordField
              id={NEW_USER_FIELD_IDS.password}
              label="Temporary password"
              autoComplete="new-password"
              hint={`At least ${MIN_PASSWORD_LENGTH} characters. Give it to them yourself; they set their own when they sign in.`}
              value={input.password}
              onChange={(password) => edit({ ...input, password })}
              error={errors.password ?? null}
              masked={masked}
              onMaskedChange={setMasked}
            />
            <div className={styles.passwordTools}>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  edit({ ...input, password: generatePassword() });
                  setMasked(false); // shown, so it can be copied and given to them
                }}
              >
                Generate
              </button>
              {input.password !== "" && <CopyButton text={input.password} what="Password" buttonText="Copy" />}
            </div>
          </div>
          {serverError && (
            <p className={`error-text ${styles.formEnd}`} role="alert">
              {serverError}
            </p>
          )}
          <div className={`actions ${styles.formEnd}`}>
            <button type="button" className="btn" disabled={busy} onClick={askClose}>
              Cancel
            </button>
            <button
              type="submit"
              className={`btn btn-primary ${busy ? styles.busy : ""}`}
              disabled={busy}
              aria-busy={busy || undefined}
            >
              {busy && <span className="spinner" aria-hidden="true" />}
              Add user
            </button>
          </div>
        </form>
      </div>
    </dialog>
  );
}

function Field({
  id,
  label,
  error,
  type,
  value,
  onChange,
}: {
  id: string;
  label: string;
  error: string | undefined;
  type: "email" | "text";
  value: string;
  onChange: (value: string) => void;
}) {
  const errorId = `${id}-error`;
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        className="input"
        type={type}
        autoComplete="off"
        spellCheck={false}
        autoCapitalize={type === "email" ? "none" : "words"}
        inputMode={type === "email" ? "email" : undefined}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? errorId : undefined}
      />
      {error && (
        <span id={errorId} className="error-text">
          {error}
        </span>
      )}
    </div>
  );
}
