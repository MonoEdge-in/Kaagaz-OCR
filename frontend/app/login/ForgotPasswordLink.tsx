"use client";

// Under the sign-in form (#457 phase 0): "Forgot password?" to /forgot-password while the
// server sends email (GET /auth/email answers); else, as before, ask the admin. Not on the
// operator's sign-in page: operators reset theirs on the server.

import Link from "next/link";
import { useEffect, useState } from "react";
import { emailOpen } from "@/lib/api";
import styles from "./login.module.css";

export function ForgotPasswordLink() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let live = true;
    emailOpen()
      .then((reply) => {
        if (live) setOpen(reply.enabled);
      })
      .catch(() => {}); // 404 while email is off, or no answer: ask the admin
    return () => {
      live = false;
    };
  }, []);

  return open ? (
    <p className={styles.help}>
      <Link href="/forgot-password">Forgot password?</Link>
    </p>
  ) : (
    <p className={styles.help}>Forgot your password? Ask your account admin to reset it.</p>
  );
}
