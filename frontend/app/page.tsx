import { redirect } from "next/navigation";

// There is no page of its own here: the dashboard is where people start (File management D1).
export default function Home() {
  redirect("/dashboard");
}
