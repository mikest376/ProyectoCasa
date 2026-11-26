import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Une clases de Tailwind; la última gana si chocan. */
export function cx(...values: ClassValue[]): string {
  return twMerge(clsx(values));
}
