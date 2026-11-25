"use client";

import { useState } from "react";

import { Button } from "@/components/ui";
import type { Cell, Column } from "@/lib/api";
import { columnDecimals, columnLabel, formatCell, isMonthly } from "@/lib/format";
import { cx } from "@/lib/cx";

const PAGE = 25;

export function DataTable({ columns, rows }: { columns: Column[]; rows: Cell[][] }) {
  const [visible, setVisible] = useState(PAGE);
  const styles = columns.map((column, index) => {
    const values = rows.map((row) => row[index]);
    return {
      monthly: column.kind === "date" && isMonthly(values),
      decimals: column.kind === "number" ? columnDecimals(values) : undefined,
    };
  });
  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-hairline">
              {columns.map((column) => (
                <th
                  key={column.name}
                  scope="col"
                  className={cx(
                    "px-3 py-2 font-medium whitespace-nowrap text-muted first:pl-0",
                    column.kind === "number" ? "text-right" : "text-left",
                  )}
                >
                  {columnLabel(column.name)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, visible).map((row, position) => (
              <tr key={position} className="border-b border-hairline last:border-0">
                {columns.map((column, index) => (
                  <td
                    key={column.name}
                    className={cx(
                      "px-3 py-2 first:pl-0",
                      column.kind === "number" && "text-right tabular-nums",
                    )}
                  >
                    {formatCell(row[index], column.kind, styles[index])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {visible < rows.length && (
        <Button variant="ghost" className="mt-2" onClick={() => setVisible(visible + PAGE)}>
          Mostrar más ({rows.length - visible} filas restantes)
        </Button>
      )}
    </div>
  );
}
