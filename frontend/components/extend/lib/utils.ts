// Copied from extend-hq/ui (https://github.com/extend-hq/ui), MIT licence: see LICENSE in
// this folder. Changed only to fit this app: import paths, lucide-react icons in place of the
// registry's icon placeholder, the radix-ui package, and the PDF engine served by this app.
import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
