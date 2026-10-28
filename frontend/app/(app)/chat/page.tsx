"use client";

// /chat: ask questions about your documents; every answer shows its sources.
// ?c=<id> reopens a conversation; ?document=<id> ("Ask about this document")
// starts one scoped to that document. The URL is read inside Suspense, as
// Next.js requires; ChatScreen reads it once, when it mounts. The documents its
// answers open lead back to the conversation in the URL (lib/backLink.ts, #436).

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { ChatScreen } from "@/components/chat/ChatScreen";
import { chatHref, conversationFromSearch } from "@/components/chat/logic";
import { BackFrom } from "@/lib/backLink";

function ChatRoute() {
  const search = useSearchParams().toString();
  return (
    <BackFrom value={chatHref(conversationFromSearch(search))}>
      <ChatScreen initialSearch={search} />
    </BackFrom>
  );
}

export default function ChatPage() {
  return (
    <Suspense
      fallback={
        <div className="page">
          <p className="muted" role="status">
            Loading the chat…
          </p>
        </div>
      }
    >
      <ChatRoute />
    </Suspense>
  );
}
