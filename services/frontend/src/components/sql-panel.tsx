"use client";

import { useState } from "react";

import { Button, ChevronIcon } from "@/components/ui";
import { formatSql } from "@/lib/format";

export function SqlPanel({ title, sql }: { title: string; sql: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(sql);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Sin permiso de portapapeles: el texto se puede seleccionar.
    }
  }

  return (
    <details className="group mt-4 border-t border-hairline pt-4">
      <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium [&::-webkit-details-marker]:hidden">
        <ChevronIcon className="transition-transform group-open:rotate-90" />
        {title}
      </summary>
      <div className="mt-3 rounded-md bg-raised p-4">
        <pre className="overflow-x-auto font-mono text-[13px] leading-6">
          <code>{formatSql(sql)}</code>
        </pre>
        <div className="mt-3 flex justify-end">
          <Button variant="ghost" onClick={copy}>
            {copied ? "Copiado" : "Copiar SQL"}
          </Button>
        </div>
      </div>
    </details>
  );
}
