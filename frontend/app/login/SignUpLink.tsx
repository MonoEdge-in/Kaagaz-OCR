"use client";

// "New to Kaagaz? Create an account", under the sign-in forms (16 Oct 2026).
// Shown only once the server says sign-up is on (SELF_SIGNUP_ENABLED), so a
// switched-off site never shows a link that leads nowhere.

import Link from "next/link";
import { useEffect, useState } from "react";
import { signUpOpen } from "@/lib/api";
import styles from "./login.module.css";

export function SignUpLink() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let live = true;
    signUpOpen()
      .then((reply) => {
        if (live) setOpen(reply.enabled);
      })
      .catch(() => {}); // no answer: the link stays hidden
    return () => {
      live = false;
    };
  }, []);

  if (!open) return null;
  return (
    <p className={styles.switch}>
      New to Kaagaz? <Link href="/signup">Create an account</Link>
    </p>
  );
}
