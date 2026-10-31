"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Ticket } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import {
  applySupportMacro,
  createSupportTicket,
  getSupportTicket,
  listSupportMacros,
  listSupportTickets,
  listTenants,
  updateSupportTicket,
  type SupportTicket,
} from "@/lib/admin-api";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";

function formatTs(iso?: string | null) {
  if (!iso) return "—";
  const t = DateParse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function DateParse(iso: string) {
  return Date.parse(iso.includes("T") ? iso : iso.replace(" ", "T") + "Z");
}

function statusBadge(status: string) {
  if (status === "open") return <Badge>Open</Badge>;
  if (status === "in_progress") return <Badge variant="secondary">In progress</Badge>;
  if (status === "waiting") return <Badge variant="outline">Waiting</Badge>;
  if (status === "resolved") return <Badge variant="outline">Resolved</Badge>;
  if (status === "closed") return <Badge variant="outline">Closed</Badge>;
  return <Badge variant="outline">{status}</Badge>;
}

function priorityBadge(priority: string) {
  if (priority === "urgent") return <Badge variant="destructive">Urgent</Badge>;
  if (priority === "high") return <Badge variant="warning">High</Badge>;
  if (priority === "low") return <Badge variant="outline">Low</Badge>;
  return <Badge variant="secondary">Normal</Badge>;
}

export default function SupportPage() {
  const queryClient = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("open");
  const [query, setQuery] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const [form, setForm] = useState({
    tenant_id: "",
    subject: "",
    body: "",
    priority: "normal",
  });
  const [note, setNote] = useState("");
  const [detailStatus, setDetailStatus] = useState("open");
  const [detailPriority, setDetailPriority] = useState("normal");

  const { data: tenants = [] } = useQuery({
    queryKey: ["admin-tenants"],
    queryFn: listTenants,
  });

  const { data, isLoading, error } = useQuery({
    queryKey: ["support-tickets", statusFilter, query],
    queryFn: () =>
      listSupportTickets({
        status: statusFilter === "all" ? undefined : statusFilter,
        q: query.trim() || undefined,
        limit: 100,
      }),
  });

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ["support-ticket", detailId],
    queryFn: () => getSupportTicket(detailId!),
    enabled: Boolean(detailId),
  });

  const { data: macros = [] } = useQuery({
    queryKey: ["support-macros"],
    queryFn: listSupportMacros,
  });

  useEffect(() => {
    document.title = "Support & Tickets — HotelPilot Central Admin";
  }, []);

  useEffect(() => {
    if (detail) {
      setDetailStatus(detail.status);
      setDetailPriority(detail.priority);
      setNote("");
    }
  }, [detail]);

  const tickets = data?.tickets ?? [];
  const openCount = useMemo(
    () => tickets.filter((t) => t.status === "open" || t.status === "in_progress").length,
    [tickets],
  );

  async function handleCreate() {
    if (!form.tenant_id || form.subject.trim().length < 3) return;
    setBusy(true);
    setMessage("");
    try {
      const hotel = tenants.find((t) => t.id === form.tenant_id);
      await createSupportTicket({
        tenant_id: form.tenant_id,
        subject: form.subject.trim(),
        body: form.body,
        priority: form.priority,
        hotel_name: hotel?.hotel_name,
        requester_email: hotel?.admin_email,
      });
      setCreateOpen(false);
      setForm({ tenant_id: "", subject: "", body: "", priority: "normal" });
      await queryClient.invalidateQueries({ queryKey: ["support-tickets"] });
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleUpdate(ticket: SupportTicket) {
    setBusy(true);
    setMessage("");
    try {
      await updateSupportTicket(ticket.id, {
        status: detailStatus,
        priority: detailPriority,
        note: note.trim() || undefined,
      });
      await queryClient.invalidateQueries({ queryKey: ["support-tickets"] });
      await queryClient.invalidateQueries({ queryKey: ["support-ticket", ticket.id] });
      setNote("");
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Support & Tickets</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-2xl font-bold tracking-tight">Hotel support queue</h2>
            <p className="text-muted-foreground">
              Track issues linked to hotel tenants. {openCount} active in this view.
            </p>
          </div>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" />
            New ticket
          </Button>
        </div>

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <Input
            className="max-w-md"
            placeholder="Search subject, hotel, email…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-[180px]">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="open">Open</SelectItem>
              <SelectItem value="in_progress">In progress</SelectItem>
              <SelectItem value="waiting">Waiting</SelectItem>
              <SelectItem value="resolved">Resolved</SelectItem>
              <SelectItem value="closed">Closed</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {message && (
          <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{message}</div>
        )}
        {error && (
          <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
            {(error as Error).message}
          </div>
        )}

        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Subject</TableHead>
                <TableHead>Hotel</TableHead>
                <TableHead>Priority</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>SLA</TableHead>
                <TableHead>Updated</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <TableRow>
                  <TableCell colSpan={6} className="text-muted-foreground">
                    Loading tickets…
                  </TableCell>
                </TableRow>
              ) : tickets.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6}>
                    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
                      <Ticket className="h-8 w-8 text-muted-foreground" />
                      <p className="font-medium">No tickets in this view</p>
                      <p className="max-w-sm text-sm text-muted-foreground">
                        Create a ticket for a hotel or change the status filter.
                      </p>
                    </div>
                  </TableCell>
                </TableRow>
              ) : (
                tickets.map((t) => (
                  <TableRow
                    key={t.id}
                    className="cursor-pointer"
                    onClick={() => setDetailId(t.id)}
                  >
                    <TableCell className="font-medium">{t.subject}</TableCell>
                    <TableCell>
                      <Link
                        href={`/properties/${t.tenant_id}`}
                        className="text-primary hover:underline"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {t.hotel_name || t.tenant_id}
                      </Link>
                    </TableCell>
                    <TableCell>{priorityBadge(t.priority)}</TableCell>
                    <TableCell>{statusBadge(t.status)}</TableCell>
                    <TableCell>
                      {t.sla_breached ? (
                        <Badge variant="destructive">Breached</Badge>
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          {formatTs(t.sla_due_at)}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground">{formatTs(t.updated_at)}</TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      </div>

      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New support ticket</DialogTitle>
            <DialogDescription>Link the ticket to a hotel tenant for follow-up.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>Hotel</Label>
              <Select
                value={form.tenant_id}
                onValueChange={(v) => setForm((f) => ({ ...f, tenant_id: v }))}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select hotel" />
                </SelectTrigger>
                <SelectContent>
                  {tenants.map((t) => (
                    <SelectItem key={t.id} value={t.id}>
                      {t.hotel_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Subject</Label>
              <Input
                value={form.subject}
                onChange={(e) => setForm((f) => ({ ...f, subject: e.target.value }))}
                placeholder="Cannot sync OTA rates"
              />
            </div>
            <div className="space-y-1.5">
              <Label>Details</Label>
              <Textarea
                value={form.body}
                onChange={(e) => setForm((f) => ({ ...f, body: e.target.value }))}
                rows={4}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Priority</Label>
              <Select
                value={form.priority}
                onValueChange={(v) => setForm((f) => ({ ...f, priority: v }))}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="low">Low</SelectItem>
                  <SelectItem value="normal">Normal</SelectItem>
                  <SelectItem value="high">High</SelectItem>
                  <SelectItem value="urgent">Urgent</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={busy || !form.tenant_id || form.subject.trim().length < 3}
              onClick={handleCreate}
            >
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={Boolean(detailId)} onOpenChange={(o) => !o && setDetailId(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{detail?.subject || "Ticket"}</DialogTitle>
            <DialogDescription>
              {detailLoading
                ? "Loading…"
                : detail
                  ? `${detail.hotel_name || detail.tenant_id} · ${formatTs(detail.created_at)}`
                  : ""}
            </DialogDescription>
          </DialogHeader>
          {detail && (
            <div className="space-y-4">
              {detail.body ? (
                <p className="whitespace-pre-wrap rounded-md border bg-muted/30 p-3 text-sm">
                  {detail.body}
                </p>
              ) : null}
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label>Status</Label>
                  <Select value={detailStatus} onValueChange={setDetailStatus}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="open">Open</SelectItem>
                      <SelectItem value="in_progress">In progress</SelectItem>
                      <SelectItem value="waiting">Waiting</SelectItem>
                      <SelectItem value="resolved">Resolved</SelectItem>
                      <SelectItem value="closed">Closed</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label>Priority</Label>
                  <Select value={detailPriority} onValueChange={setDetailPriority}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="low">Low</SelectItem>
                      <SelectItem value="normal">Normal</SelectItem>
                      <SelectItem value="high">High</SelectItem>
                      <SelectItem value="urgent">Urgent</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                <span>SLA due: {formatTs(detail.sla_due_at)}</span>
                {detail.first_response_at ? (
                  <span>· First response: {formatTs(detail.first_response_at)}</span>
                ) : null}
                {detail.sla_breached ? (
                  <Badge variant="destructive">SLA breached</Badge>
                ) : null}
              </div>
              {macros.length > 0 && (
                <div className="space-y-1.5">
                  <Label>Apply macro</Label>
                  <Select
                    onValueChange={async (macroId) => {
                      if (!detail) return;
                      setBusy(true);
                      setMessage("");
                      try {
                        await applySupportMacro(detail.id, macroId);
                        await queryClient.invalidateQueries({
                          queryKey: ["support-ticket", detail.id],
                        });
                        await queryClient.invalidateQueries({
                          queryKey: ["support-tickets"],
                        });
                      } catch (e) {
                        setMessage((e as Error).message);
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Insert canned reply…" />
                    </SelectTrigger>
                    <SelectContent>
                      {macros.map((m) => (
                        <SelectItem key={m.id} value={m.id}>
                          {m.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
              <div className="space-y-1.5">
                <Label>Add note</Label>
                <Textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  rows={3}
                  placeholder="Internal note for the support trail"
                />
              </div>
              {(detail.notes || []).length > 0 && (
                <div className="space-y-2">
                  <Label>Notes</Label>
                  <div className="max-h-40 space-y-2 overflow-y-auto">
                    {(detail.notes || []).map((n) => (
                      <div key={n.id} className="rounded-md border p-2 text-sm">
                        <p className="whitespace-pre-wrap">{n.body}</p>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {n.author_email || "operator"} · {formatTs(n.created_at)}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="flex justify-between gap-2">
                <Button variant="outline" asChild>
                  <Link href={`/properties/${detail.tenant_id}`}>Open hotel</Link>
                </Button>
                <Button disabled={busy} onClick={() => handleUpdate(detail)}>
                  Save
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
