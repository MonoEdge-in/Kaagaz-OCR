import { redirect } from "next/navigation";

// There is no home page of its own: the documents list is where people start.
export default function Home() {
  redirect("/documents");
}
