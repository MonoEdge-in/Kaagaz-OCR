// Kaagaz on WhatsApp (#603 W3): Account, WhatsApp's number rules, the masking to the last 4 digits,
// the Connect screen's privacy text word for word, what each state says, the way back from Google;
// and the Admin's switches (the one dialog's words, the roles in order with the extra ticks, whether
// the form changed) and the Users tab's WhatsApp cell.

import { describe, expect, it } from "vitest";
import {
  BAD_NUMBER,
  CONSENT_TEXT,
  PRIVACY_TEXT,
  maskedNumber,
  normalizeNumber,
  numberProblem,
  pageNotice,
  stateText,
} from "@/components/account/whatsapp";
import {
  ROLE_TICKS,
  SENT_STAYS,
  TURN_ON_TEXT,
  formChanged,
  formOf,
  turnOnTitle,
  whatsappCell,
  withRole,
} from "@/components/admin/whatsappSettings";
import type { WhatsAppAdmin } from "@/lib/types";

describe("the number", () => {
  it("is kept as + and its digits, however it was typed", () => {
    expect(normalizeNumber("+91 98765-43210")).toBe("+919876543210");
    expect(normalizeNumber("(98765) 43210")).toBe("+9876543210");
    expect(normalizeNumber("+91 ")).toBe("+91"); // the prefix alone: numberProblem says so
  });

  it("needs 8 to 15 digits with a country code that does not start with 0", () => {
    expect(numberProblem("+91 98765 43210")).toBeNull();
    expect(numberProblem("+1 555 0100")).toBeNull();
    for (const bad of ["+91 ", "+91 123", "+0 98765 43210", "+91 98765 43210 12345", "phone"]) {
      expect(numberProblem(bad)).toBe(BAD_NUMBER);
    }
  });

  it("is shown only by its last 4 digits", () => {
    expect(maskedNumber("+91 98765 43210")).toBe("+91 ••••3210");
    expect(maskedNumber("+1 555 0100 1234")).toBe("••••1234");
    expect(maskedNumber("+91 98765 43210")).not.toContain("98765");
  });
});

describe("the Connect screen", () => {
  it("says the privacy text and asks for the tick in the spec's words", () => {
    expect(PRIVACY_TEXT).toBe(
      "Your questions, voice notes and answers pass through WhatsApp (Meta) and stay in your chat, on your phone, " +
        "its backups and WhatsApp Web. Files are never sent on WhatsApp; you open them in Kaagaz with your password. " +
        "Kaagaz cannot delete them later. Anyone who can open WhatsApp on this phone can ask about your files. " +
        "Keep your phone locked.",
    );
    expect(CONSENT_TEXT).toBe("I agree that my questions, voice notes and answers will pass through WhatsApp (Meta).");
  });

  it("says each state plainly, a connection by its last 4 digits only", () => {
    const base = { google: false, totp: false };
    expect(stateText({ ...base, state: "off" })).toContain("isn't turned on for your organisation");
    expect(stateText({ ...base, state: "not_allowed" })).toContain("for your role");
    expect(stateText({ ...base, state: "none" })).toContain("Files never leave Kaagaz");
    const connected = stateText({
      ...base,
      state: "connected",
      last4: "3210",
      linked_at: "2026-11-14T06:00:00+00:00",
      last_used_at: null,
    });
    expect(connected).toBe("Connected: ••••3210, since 14 Nov 2026.");
  });

  it("goes on to the number after Google, or says why not", () => {
    expect(pageNotice({ proved: "google" })).toEqual({ proved: true, error: null });
    expect(pageNotice({ error: "not_yours" }).error).toBe(
      "That Google account isn't the one connected to your Kaagaz account.",
    );
    expect(pageNotice({ error: "something new" }).error).toBe("Confirming with Google didn't work. Try again.");
    expect(pageNotice({})).toEqual({ proved: false, error: null });
  });
});

const SAVED: WhatsAppAdmin = {
  enabled: true,
  roles: ["member", "reviewer"],
  management: false,
  voice: true,
  uploads: false,
  available: true,
  people: [],
};

describe("the Admin's switches", () => {
  it("say the one dialog's words", () => {
    expect(turnOnTitle("Shree Ganesh CA Associates")).toBe("Turn on WhatsApp for Shree Ganesh CA Associates?");
    expect(TURN_ON_TEXT).toBe(
      "People can ask about their files and get links to them from WhatsApp. Files never leave Kaagaz: each link needs a Kaagaz sign-in.",
    );
    expect(SENT_STAYS).toBe("Answers already sent stay on the person's phone, even after their access is removed.");
  });

  it("keep the roles in order, Admins an extra tick", () => {
    expect(ROLE_TICKS.map((tick) => tick.role)).toEqual(["member", "reviewer", "admin"]);
    expect(ROLE_TICKS.find((tick) => tick.role === "admin")?.hint).toContain("extra tick");
    const form = formOf(SAVED);
    expect(withRole(form, "admin", true).roles).toEqual(["member", "reviewer", "admin"]);
    expect(withRole(withRole(form, "admin", true), "member", false).roles).toEqual(["reviewer", "admin"]);
  });

  it("know when the form changed", () => {
    const form = formOf(SAVED);
    expect(formChanged(SAVED, form)).toBe(false);
    expect(formChanged(SAVED, { ...form, voice: false })).toBe(true);
    expect(formChanged(SAVED, { ...form, management: true })).toBe(true);
    expect(formChanged(SAVED, withRole(form, "admin", true))).toBe(true);
    expect(formChanged(SAVED, withRole(withRole(form, "admin", true), "admin", false))).toBe(false);
  });

  it("show a connected person by the last 4 digits and since when", () => {
    const person = { user_id: "u1", last4: "3210", linked_at: "2026-11-14T06:00:00+00:00", last_used_at: null };
    expect(whatsappCell(person)).toBe("••••3210 since 14 Nov 2026");
    expect(whatsappCell(undefined)).toBe("");
  });
});
