"use client";

// /chat: ask questions about your documents; every answer shows its sources.
// ?c=<id> reopens a conversation; ?document=<id> ("Ask about this document")
// starts one scoped to that document. The URL is read inside Suspense, as
// Next.js requires; ChatScreen reads it once, when it mounts.

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { ChatScreen } from "@/components/chat/ChatScreen";

function ChatRoute() {
  const search = useSearchParams();
  return <ChatScreen initialSearch={search.toString()} />;
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
