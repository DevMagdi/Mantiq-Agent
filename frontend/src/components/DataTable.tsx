import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronsUpDown, Search } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "./ui";

export type DataTableProps<T> = {
  data: T[];
  columns: ColumnDef<T, unknown>[];
  searchPlaceholder?: string;
  toolbar?: ReactNode;
  empty?: ReactNode;
  dense?: boolean;
  initialSorting?: SortingState;
  onRowClick?: (row: T) => void;
  getRowId?: (row: T) => string;
  searchable?: boolean;
};

/**
 * Dense, keyboard-usable table built on TanStack Table.
 * Columns are virtualisation-friendly (fixed row heights) and every numeric cell uses tabular figures.
 */
export function DataTable<T>({
  data,
  columns,
  searchPlaceholder,
  toolbar,
  empty,
  dense = true,
  initialSorting = [],
  onRowClick,
  getRowId,
  searchable = true,
}: DataTableProps<T>) {
  const { t } = useTranslation();
  const [sorting, setSorting] = useState<SortingState>(initialSorting);
  const [filter, setFilter] = useState("");

  const table = useReactTable({
    data,
    columns,
    state: { sorting, globalFilter: filter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getRowId: getRowId ? (row) => getRowId(row) : undefined,
    globalFilterFn: "includesString",
  });

  // `table` is re-created by TanStack whenever data/sorting/filter change.
  const rows = useMemo(() => table.getRowModel().rows, [table]);

  return (
    <div className="flex min-h-0 flex-col">
      {(searchable || toolbar) && (
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
          {searchable ? (
            <label className="flex flex-1 items-center gap-2 rounded-md border border-line bg-surface px-2 py-1.5 focus-within:border-gold">
              <Search className="size-4 text-ink-subtle" aria-hidden />
              <input
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                placeholder={searchPlaceholder ?? t("common.search")}
                className="w-full bg-transparent text-sm text-ink placeholder:text-ink-subtle focus:outline-none"
              />
            </label>
          ) : null}
          {toolbar}
        </div>
      )}
      <div className={cn("min-h-0 flex-1 overflow-auto", dense ? "max-h-[calc(100vh-19rem)]" : "")}>
        <table className="table-dense">
          <thead>
            {table.getHeaderGroups().map((group) => (
              <tr key={group.id}>
                {group.headers.map((header) => {
                  const canSort = header.column.getCanSort();
                  const sorted = header.column.getIsSorted();
                  return (
                    <th
                      key={header.id}
                      scope="col"
                      className={cn("whitespace-nowrap", header.column.columnDef.meta as string | undefined)}
                      aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none"}
                    >
                      {header.isPlaceholder ? null : (
                        <button
                          type="button"
                          className={cn("inline-flex items-center gap-1", canSort ? "hover:text-ink" : "cursor-default")}
                          onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                          disabled={!canSort}
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}
                          {canSort ? (
                            sorted === "asc" ? (
                              <ArrowUp className="size-3" aria-hidden />
                            ) : sorted === "desc" ? (
                              <ArrowDown className="size-3" aria-hidden />
                            ) : (
                              <ChevronsUpDown className="size-3 opacity-50" aria-hidden />
                            )
                          ) : null}
                        </button>
                      )}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="p-0">
                  {empty ?? <p className="px-4 py-10 text-center text-sm text-ink-muted">{t("common.empty")}</p>}
                </td>
              </tr>
            ) : (
              rows.map((row) => (
                <tr
                  key={row.id}
                  className={onRowClick ? "cursor-pointer" : undefined}
                  onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                  tabIndex={onRowClick ? 0 : undefined}
                  onKeyDown={
                    onRowClick
                      ? (event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            onRowClick(row.original);
                          }
                        }
                      : undefined
                  }
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="whitespace-nowrap">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <div className="flex items-center justify-between border-t border-line px-3 py-2 text-xs text-ink-muted">
        <span>
          {rows.length} / {data.length}
        </span>
      </div>
    </div>
  );
}
