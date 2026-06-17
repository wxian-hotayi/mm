import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Merge conditional class names and de-conflict Tailwind utilities.
 * The single class-composition helper used across the UI kit (DESIGN §5).
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
