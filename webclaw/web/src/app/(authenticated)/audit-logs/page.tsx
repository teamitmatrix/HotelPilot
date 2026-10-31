"use client";

import { Fragment, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Download, Search, ScrollText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { exportAuditLogsCsv, listAuditLogs, type AuditLogEntry } from "@/lib/admin-api";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";

const PAGE_SIZE = 25;

function formatDiff(val: AuditLogEntry["before"]) {
  if (val == null) return "—";
  if (typeof val === "string") return val;
  try {
    return JSON.stringify(val, null, 2);
  } catch {
    return String(val);
  }
}

function formatTs(iso: string) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function shortId(id: string | null) {
  if (!id) return "—";
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}

export default function AuditLogsPage() {
  const [query, setQuery] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [category, setCategory] = useState<string>("all");
  const [status, setStatus] = useState<string>("all");
  const [page, setPage] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    document.title = "Audit Logs — HotelPilot Central Admin";
  }, []);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(query.trim()), 300);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => {
    setPage(0);
  }, [debouncedQ, category, status]);

  const { data, isLoading, error } = useQuery({
    queryKey: ["platform-audit", debouncedQ, category, status, page],
    queryFn: () =>
      listAuditLogs({
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
        q: debouncedQ || undefined,
        category: category === "all" ? undefined : category,
        status: status === "all" ? undefined : status,
      }),
  });

  const logs = data?.logs ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Audit Logs</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="text-2xl font-bold tracking-tight">Platform activity</h2>
            <p className="text-muted-foreground">
              Immutable record of administrative actions across tenants and security events.
            </p>
          </div>
          <Button
            variant="outline"
            disabled={exporting}
            onClick={async () => {
              setExporting(true);
              setExportError("");
              try {
                await exportAuditLogsCsv({
                  q: debouncedQ || undefined,
                  category: category === "all" ? undefined : category,
                  status: status === "all" ? undefined : status,
                });
              } catch (e) {
                setExportError((e as Error).message);
              } finally {
                setExporting(false);
              }
            }}
          >
            <Download className="h-4 w-4" />
            {exporting ? "Exporting…" : "Export CSV"}
          </Button>
        </div>
        {exportError ? (
          <p className="text-sm text-destructive">{exportError}</p>
        ) : null}

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              className="pl-9"
              placeholder="Search email, action, tenant, IP…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <Select value={category} onValueChange={setCategory}>
            <SelectTrigger className="w-[180px]">
              <SelectValue placeholder="Category" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All categories</SelectItem>
              <SelectItem value="settings">Settings</SelectItem>
              <SelectItem value="announcement">Announcement</SelectItem>
              <SelectItem value="security">Security</SelectItem>
              <SelectItem value="backup">Backup</SelectItem>
              <SelectItem value="tenant">Tenant</SelectItem>
              <SelectItem value="billing">Billing</SelectItem>
              <SelectItem value="users">Users</SelectItem>
              <SelectItem value="support">Support</SelectItem>
              <SelectItem value="ops">Ops</SelectItem>
            </SelectContent>
          </Select>
          <Select value={status} onValueChange={setStatus}>
            <SelectTrigger className="w-[140px]">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="success">Success</SelectItem>
              <SelectItem value="failure">Failure</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {error ? (
          <p className="text-sm text-destructive">{(error as Error).message}</p>
        ) : null}

        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[180px]">Timestamp</TableHead>
                <TableHead>Performed By</TableHead>
                <TableHead>Tenant ID</TableHead>
                <TableHead>Action Category</TableHead>
                <TableHead>IP Address</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="w-[70px]">Diff</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <TableRow>
                  <TableCell colSpan={7} className="py-12 text-center text-muted-foreground">
                    Loading audit logs…
                  </TableCell>
                </TableRow>
              ) : logs.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="py-16 text-center">
                    <div className="flex flex-col items-center gap-2">
                      <ScrollText className="h-8 w-8 text-muted-foreground" />
                      <p className="font-medium">No audit events</p>
                      <p className="text-sm text-muted-foreground">
                        Platform actions will appear here as they occur.
                      </p>
                    </div>
                  </TableCell>
                </TableRow>
              ) : (
                logs.map((row) => (
                  <Fragment key={row.id}>
                    <TableRow
                      className={row.has_diff ? "cursor-pointer" : undefined}
                      onClick={() =>
                        row.has_diff &&
                        setExpandedId((id) => (id === row.id ? null : row.id))
                      }
                    >
                      <TableCell className="whitespace-nowrap text-xs tabular-nums">
                        {formatTs(row.timestamp)}
                      </TableCell>
                      <TableCell>
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">
                            {row.user_email || "System"}
                          </p>
                          <p className="truncate font-mono text-[11px] text-muted-foreground">
                            {shortId(row.user_id)}
                          </p>
                        </div>
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {shortId(row.tenant_id)}
                      </TableCell>
                      <TableCell>
                        <div>
                          <Badge variant="outline" className="capitalize">
                            {row.action_category}
                          </Badge>
                          <p className="mt-1 text-xs text-muted-foreground">{row.action}</p>
                        </div>
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {row.ip_address || "—"}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={row.status === "success" ? "default" : "destructive"}
                          className="capitalize"
                        >
                          {row.status}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {row.has_diff ? (
                          <Badge variant="secondary">
                            {expandedId === row.id ? "Hide" : "View"}
                          </Badge>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </TableCell>
                    </TableRow>
                    {expandedId === row.id && row.has_diff ? (
                      <TableRow>
                        <TableCell colSpan={7} className="bg-muted/30">
                          <div className="grid gap-3 md:grid-cols-2">
                            <div>
                              <p className="mb-1 text-xs font-medium text-muted-foreground">
                                Before
                              </p>
                              <pre className="max-h-48 overflow-auto rounded border bg-background p-2 text-[11px]">
                                {formatDiff(row.before)}
                              </pre>
                            </div>
                            <div>
                              <p className="mb-1 text-xs font-medium text-muted-foreground">
                                After
                              </p>
                              <pre className="max-h-48 overflow-auto rounded border bg-background p-2 text-[11px]">
                                {formatDiff(row.after)}
                              </pre>
                            </div>
                          </div>
                          {row.detail ? (
                            <p className="mt-2 text-xs text-muted-foreground">
                              Detail: {row.detail}
                            </p>
                          ) : null}
                        </TableCell>
                      </TableRow>
                    ) : null}
                  </Fragment>
                ))
              )}
            </TableBody>
          </Table>
        </div>

        {total > 0 ? (
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>
              {total} event{total === 1 ? "" : "s"} · page {page + 1} of {totalPages}
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
              >
                <ChevronLeft className="h-4 w-4" />
                Prev
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={page + 1 >= totalPages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
