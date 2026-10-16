// /admin/login is gone (one sign-in page, 16 Oct 2026): old links and bookmarks
// go to /login, which comes back to /admin once signed in.

import { redirect } from "next/navigation";

export default function AdminLoginPage() {
  redirect("/login?next=/admin");
}
