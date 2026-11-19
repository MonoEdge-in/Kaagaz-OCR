// /my-access: what the signed-in person can see in Kaagaz, and why (#707), any role. The nav link
// shows for everyone but an Admin in an admin session (components/shell/nav.ts).

import { MyAccess } from "@/components/access/MyAccess";

export const metadata = { title: "My access" };

export default function MyAccessPage() {
  return <MyAccess />;
}
