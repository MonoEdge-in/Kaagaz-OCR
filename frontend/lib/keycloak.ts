import { ApiError, startKeycloakReauth } from "@/lib/api";
import { safeNextPath } from "@/components/shell/gate";
import type { Me } from "@/lib/types";

export const KEYCLOAK_PROOF_ACTIONS = ["locked_open", "delete_forever", "whatsapp", "access", "locks"] as const;
export type KeycloakProofAction = (typeof KEYCLOAK_PROOF_ACTIONS)[number];

const ACTION_SET = new Set<string>(KEYCLOAK_PROOF_ACTIONS);

export function isKeycloakManaged(me: Me | null | undefined): boolean {
  return me?.user.auth_authority === "keycloak";
}

/** A safe same-origin path for the server callback. Invalid input falls back to the dashboard. */
export function keycloakNextPath(next: unknown): string {
  return safeNextPath(next) ?? "/dashboard";
}

/** The anonymous sign-in's link is rendered only when the server advertises Keycloak. */
export function keycloakLoginHref(enabled: boolean, next: unknown): string | null {
  if (!enabled) return null;
  return `/api/auth/keycloak/start?next=${encodeURIComponent(keycloakNextPath(next))}`;
}

/** Keycloak's own page for the person's password or authenticator app (the server passes the action
 * on only when it is one of these two), then back here. */
export function keycloakAccountHref(action: "UPDATE_PASSWORD" | "CONFIGURE_TOTP", next: unknown): string {
  return `/api/auth/keycloak/start?next=${encodeURIComponent(keycloakNextPath(next))}&action=${action}`;
}

/** The callback returns to this page with a fixed action marker; selections are not persisted. */
export function keycloakReauthReturnPath(path: unknown, action: KeycloakProofAction): string {
  const pathname = keycloakNextPath(path);
  return `${pathname}${pathname.includes("?") ? "&" : "?"}reauth=${action}`;
}

export function isKeycloakReauthRequired(caught: unknown): boolean {
  return caught instanceof ApiError && caught.status === 403 && caught.code === "keycloak_reauth_required";
}

export function isKeycloakAction(value: string | null): value is KeycloakProofAction {
  return value !== null && ACTION_SET.has(value);
}

const REAUTH_ERRORS: Record<string, string> = {
  cancelled: "Company sign-in verification was cancelled. No change was made. Try the action again.",
  expired: "Company sign-in verification expired. No change was made. Try the action again.",
  refused: "Your company account could not verify this action. No change was made.",
  unavailable: "Company sign-in verification is unavailable right now. No change was made. Try again shortly.",
  not_connected: "This company account is not connected to Kaagaz. Ask your admin for help.",
  mfa_verify_again: "Your authenticator setup is complete. Repeat company sign-in or account linking to finish.",
};

export function keycloakCallbackError(value: string | null): string | null {
  if (!value) return null;
  return REAUTH_ERRORS[value] ?? "Company sign-in did not complete. No change was made. Try again.";
}

export function keycloakReauthNotice(action: KeycloakProofAction): string {
  switch (action) {
    case "locked_open":
      return "Company sign-in verified. No file was opened yet. Return to the locked file and choose Open again.";
    case "delete_forever":
      return "Company sign-in verified. Nothing was deleted. Review the item and confirm Delete forever again.";
    case "whatsapp":
      return "Company sign-in verified. No WhatsApp setting changed. Repeat the action and confirm again.";
    case "access":
      return "Company sign-in verified. No access was changed. Repeat the action and confirm again.";
    case "locks":
      return "Company sign-in verified. No files were locked or unlocked. Reopen the dialog, reselect files if needed, and confirm again.";
  }
}

/** Accept only an IdP URL returned by our backend; never accept a user-provided redirect. */
export function keycloakAuthorizationUrl(value: string): string {
  const target = new URL(value, window.location.origin);
  const localHttp = target.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(target.hostname);
  if (target.username || target.password || (target.protocol !== "https:" && !localHttp)) {
    throw new Error("Invalid sign-in destination");
  }
  return target.href;
}

/** Starts a backend-bound, action-specific flow and navigates only to its returned IdP URL. */
export async function beginKeycloakReauth(action: KeycloakProofAction, path: string): Promise<void> {
  const next = keycloakReauthReturnPath(path, action);
  const { url } = await startKeycloakReauth(action, next);
  window.location.assign(keycloakAuthorizationUrl(url));
}

export function keycloakStartFailure(caught: unknown): string {
  if (caught instanceof ApiError && caught.status === 429) {
    return "Too many company sign-in checks were started. Wait a minute, then try again.";
  }
  return "Could not start company sign-in verification. Try again in a minute.";
}
