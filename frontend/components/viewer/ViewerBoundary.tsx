"use client";

// An error boundary around the PDF viewer (#246 A8). The viewer is a large third-party
// component (components/extend) drawing on canvases; a throw inside it used to take the whole
// document screen down to app/(app)/error.tsx. Now only the viewer's pane shows this, and the
// rest of the screen (the document's words, values and questions) keeps working. Try again
// draws the viewer again; Download still gives the file.

import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";
import { EmptyState } from "@/components/ui/EmptyState";

export function ViewerFailed({ onRetry, download }: { onRetry: () => void; download?: string | null }) {
  return (
    <div role="alert">
      <EmptyState
        icon="alert"
        title="The page viewer hit a problem"
        headingLevel={3}
        action={
          <>
            <button type="button" className="btn btn-primary" onClick={onRetry}>
              Try again
            </button>
            {download ? (
              <a className="btn" href={download}>
                Download
              </a>
            ) : null}
          </>
        }
      >
        <p>The document is safe, and the rest of this screen still works.</p>
      </EmptyState>
    </div>
  );
}

interface ViewerBoundaryProps {
  children?: ReactNode;
  /** The file's download address, offered while the viewer cannot draw. */
  download?: string | null;
}

export class ViewerBoundary extends Component<ViewerBoundaryProps, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(error, info.componentStack);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return <ViewerFailed onRetry={() => this.setState({ failed: false })} download={this.props.download} />;
  }
}
