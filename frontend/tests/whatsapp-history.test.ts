// #603 W4: a conversation asked on WhatsApp is marked "From WhatsApp" in the history list;
// the browser's, and one from before the channel was stored, are not.
import { describe, expect, it } from "vitest";
import { conversationOrigin } from "@/components/chat/logic";
import type { ConversationSummary } from "@/lib/types";

const item = (channel?: "whatsapp" | null): ConversationSummary => ({
  id: "c1",
  title: "Electricity bill",
  created_at: "2026-11-15T10:00:00+05:30",
  ...(channel === undefined ? {} : { channel }),
});

describe("conversationOrigin", () => {
  it("marks a WhatsApp conversation", () => {
    expect(conversationOrigin(item("whatsapp"))).toBe("From WhatsApp");
  });

  it("leaves the browser's unmarked", () => {
    expect(conversationOrigin(item(null))).toBeNull();
    expect(conversationOrigin(item())).toBeNull();
  });
});
