"use client";

// PRD 7 (#601): Management's Access activity (/access-activity): who changed who can see what,
// newest first, a page at a time (GET /access/activity), with no export. Admins and Management may
// read it; anyone else gets the server's 403 words. A file the person can't see is the server's
// "a file you can't see", with no folder and no link: the screen shows the mask as it comes.

import { listAccessActivity } from "@/lib/api";
import { Timeline } from "./Timeline";
import { ACCESS_ACTIVITY_TEXT } from "./words";

export function AccessActivity() {
  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>{ACCESS_ACTIVITY_TEXT.heading}</h1>
          <p>{ACCESS_ACTIVITY_TEXT.hint}</p>
        </div>
      </header>
      <Timeline load={listAccessActivity} label={ACCESS_ACTIVITY_TEXT.label} empty={ACCESS_ACTIVITY_TEXT.empty} />
    </div>
  );
}
