// Componentes propios al estilo de Tremor Raw: el código vive aquí, sin dependencias de terceros.
import type { ButtonHTMLAttributes, ComponentPropsWithoutRef, ReactNode } from "react";

import { cx } from "@/lib/cx";

export function Card({ className, ...props }: ComponentPropsWithoutRef<"section">) {
  return (
    <section
      className={cx("rounded-lg border border-hairline bg-surface", className)}
      {...props}
    />
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "ghost";
};

export function Button({ variant = "primary", className, type = "button", ...props }: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-md text-sm font-medium",
        "disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" && "bg-accent px-4 py-2 text-accent-ink hover:opacity-90",
        variant === "ghost" && "px-2 py-1 text-muted hover:bg-raised hover:text-ink",
        className,
      )}
      {...props}
    />
  );
}

export function Badge({
  tone = "neutral",
  className,
  ...props
}: ComponentPropsWithoutRef<"span"> & { tone?: "neutral" | "accent" }) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium whitespace-nowrap",
        tone === "neutral" && "bg-raised text-muted",
        tone === "accent" && "bg-accent-wash text-ink",
        className,
      )}
      {...props}
    />
  );
}

export function Callout({
  title,
  tone,
  children,
}: {
  title: string;
  tone: "critical" | "warning";
  children: ReactNode;
}) {
  return (
    <div
      role={tone === "critical" ? "alert" : "status"}
      className={cx(
        "flex gap-3 rounded-md p-4 text-sm",
        tone === "critical" ? "bg-critical-wash" : "bg-warning-wash",
      )}
    >
      <AlertIcon className={cx("mt-0.5", tone === "critical" ? "text-critical" : "text-ink")} />
      <div className="min-w-0">
        <p className="font-semibold">{title}</p>
        <div className="mt-1 text-muted">{children}</div>
      </div>
    </div>
  );
}

/** Interruptor de dos o más opciones. */
export function Segmented<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div role="group" aria-label={label} className="inline-flex rounded-md bg-raised p-0.5">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={option.value === value}
          onClick={() => onChange(option.value)}
          className={cx(
            "rounded px-3 py-1 text-sm font-medium",
            option.value === value
              ? "bg-surface text-ink shadow-sm"
              : "text-muted hover:text-ink",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

type IconProps = { className?: string };

function Icon({ className, children }: IconProps & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 20 20"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={cx("shrink-0", className)}
    >
      {children}
    </svg>
  );
}

export function ArrowIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 10h12M11 5l5 5-5 5" />
    </Icon>
  );
}

export function AlertIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M10 3 2.5 16.5h15L10 3ZM10 8v4M10 14.4v.1" />
    </Icon>
  );
}

export function LockIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="4.5" y="9" width="11" height="7.5" rx="1.5" />
      <path d="M7 9V6.5a3 3 0 0 1 6 0V9" />
    </Icon>
  );
}

export function CloudIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 15.5a3.5 3.5 0 0 1-.3-7A5 5 0 0 1 15 9.3a3.1 3.1 0 0 1-.6 6.2H6Z" />
    </Icon>
  );
}

export function ChevronIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m7 5 5 5-5 5" />
    </Icon>
  );
}

export function ContrastIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <circle cx="10" cy="10" r="6.5" />
      <path d="M10 3.5v13a6.5 6.5 0 0 0 0-13Z" fill="currentColor" />
    </Icon>
  );
}
