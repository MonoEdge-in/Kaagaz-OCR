"use client";

// The 6-digit code from an authenticator app (Components.html, 07): one box per
// digit, in a group named by the label. Typing moves to the next box,
// Backspace to the one before, and a pasted code fills every box. The first
// box offers the phone's one-time-code autofill.

import { useRef } from "react";
import type { ClipboardEvent, KeyboardEvent } from "react";
import { eraseAt, typeAt, typedInto } from "./codeDigits";
import styles from "./CodeInput.module.css";

export interface CodeInputProps {
  /** The id of the element that names the group ("Code from your authenticator app"). */
  labelledBy: string;
  value: string;
  onChange(value: string): void;
  length?: number;
  disabled?: boolean;
  invalid?: boolean;
  describedBy?: string;
}

export function CodeInput({ labelledBy, value, onChange, length = 6, disabled, invalid, describedBy }: CodeInputProps) {
  const boxes = useRef<(HTMLInputElement | null)[]>([]);
  const focusBox = (index: number) => boxes.current[index]?.focus();

  const write = (index: number, typed: string) => {
    const next = typeAt(value, index, typed, length);
    onChange(next.value);
    focusBox(next.focus);
  };

  const onKeyDown = (index: number, event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Backspace") {
      event.preventDefault();
      const next = eraseAt(value, index);
      onChange(next.value);
      focusBox(next.focus);
    } else if (event.key === "ArrowLeft" && index > 0) {
      event.preventDefault();
      focusBox(index - 1);
    } else if (event.key === "ArrowRight" && index < Math.min(value.length, length - 1)) {
      event.preventDefault();
      focusBox(index + 1);
    }
  };

  const onPaste = (index: number, event: ClipboardEvent<HTMLInputElement>) => {
    event.preventDefault();
    write(index, event.clipboardData.getData("text"));
  };

  return (
    <div
      className={styles.code}
      role="group"
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
    >
      {Array.from({ length }, (_, index) => (
        <input
          key={index}
          ref={(box) => {
            boxes.current[index] = box;
          }}
          aria-label={`Digit ${index + 1}`}
          aria-invalid={invalid || undefined}
          inputMode="numeric"
          autoComplete={index === 0 ? "one-time-code" : "off"}
          maxLength={length}
          value={value[index] ?? ""}
          disabled={disabled}
          onChange={(event) => write(index, typedInto(value[index] ?? "", event.target.value))}
          onKeyDown={(event) => onKeyDown(index, event)}
          onPaste={(event) => onPaste(index, event)}
          onFocus={(event) => event.target.select()}
        />
      ))}
    </div>
  );
}
