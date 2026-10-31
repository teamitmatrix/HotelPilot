"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Building2,
  ChevronLeft,
  ChevronRight,
  Eye,
  Pause,
  Pencil,
  Play,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
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
  createTenant,
  deleteTenant,
  impersonateTenant,
  listPlans,
  listTenants,
  pauseTenant,
  resumeTenant,
  updateTenant,
  type HotelTenant,
} from "@/lib/admin-api";
import { useAuth } from "@/lib/auth";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";
import { Textarea } from "@/components/ui/textarea";

const PAGE_SIZE = 10;

function statusBadge(status: string) {
  if (status === "active") return <Badge>Active</Badge>;
  if (status === "suspended") return <Badge variant="warning">Suspended</Badge>;
  return <Badge variant="outline">{status}</Badge>;
}

function formatDate(iso?: string) {
  if (!iso) return "—";
  const t = Date.parse(iso.includes("T") ? iso : iso.replace(" ", "T") + "Z");
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function shortId(id: string) {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}

export default function PropertiesPage() {
  const queryClient = useQueryClient();
  const searchParams = useSearchParams();
  const { startImpersonation } = useAuth();
  const { data: tenants = [], isLoading, error } = useQuery({
    queryKey: ["admin-tenants"],
    queryFn: listTenants,
  });
  const { data: planData } = useQuery({
    queryKey: ["admin-plans"],
    queryFn: listPlans,
  });
  const plans = planData?.plans ?? [];

  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "active" | "suspended">("all");
  const [page, setPage] = useState(0);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [message, setMessage] = useState("");

  const [addOpen, setAddOpen] = useState(false);
  const [editTenant, setEditTenant] = useState<HotelTenant | null>(null);
  const [suspendTenant, setSuspendTenant] = useState<HotelTenant | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<HotelTenant | null>(null);
  const [impersonateTarget, setImpersonateTarget] = useState<HotelTenant | null>(null);
  const [impersonateReason, setImpersonateReason] = useState("");

  const [form, setForm] = useState({
    hotel_name: "",
    admin_email: "",
    password: "",
    plan_code: "starter",
    timezone: "UTC",
    currency: "USD",
    country: "United States",
  });

  const [editForm, setEditForm] = useState({
    hotel_name: "",
    admin_email: "",
    timezone: "UTC",
    currency: "USD",
    country: "United States",
    plan_code: "starter",
  });

  useEffect(() => {
    document.title = "Properties / Hotels — HotelPilot Central Admin";
  }, []);

  useEffect(() => {
    if (searchParams.get("create") === "1") setAddOpen(true);
  }, [searchParams]);

  const planOptions = useMemo(
    () =>
      plans.length
        ? plans
        : [
            { code: "starter", name: "Starter" },
            { code: "business", name: "Professional" },
            { code: "enterprise", name: "Enterprise" },
          ],
    [plans],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return tenants.filter((t) => {
      if (statusFilter === "active" && t.status !== "active") return false;
      if (statusFilter === "suspended" && t.status !== "suspended") return false;
      if (!q) return true;
      return (
        t.hotel_name.toLowerCase().includes(q) ||
        t.admin_email.toLowerCase().includes(q) ||
        t.id.toLowerCase().includes(q) ||
        (t.plan_name || "").toLowerCase().includes(q)
      );
    });
  }, [tenants, query, statusFilter]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const pageSafe = Math.min(page, pageCount - 1);
  const pageRows = filtered.slice(pageSafe * PAGE_SIZE, pageSafe * PAGE_SIZE + PAGE_SIZE);

  useEffect(() => {
    setPage(0);
  }, [query, statusFilter]);

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ["admin-tenants"] });
    await queryClient.invalidateQueries({ queryKey: ["admin-analytics"] });
  }

  async function handleCreate() {
    setMessage("");
    setBusyId("create");
    try {
      await createTenant(form);
      setAddOpen(false);
      setForm({
        hotel_name: "",
        admin_email: "",
        password: "",
        plan_code: "starter",
        timezone: "UTC",
        currency: "USD",
        country: "United States",
      });
      await refresh();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  async function handleEditSave() {
    if (!editTenant) return;
    setBusyId(editTenant.id);
    setMessage("");
    try {
      await updateTenant(editTenant.id, {
        hotel_name: editForm.hotel_name,
        timezone: editForm.timezone,
        country: editForm.country,
        currency: editForm.currency,
        plan_code: editForm.plan_code,
      });
      setEditTenant(null);
      await refresh();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  async function handleSuspendConfirm() {
    if (!suspendTenant) return;
    setBusyId(suspendTenant.id);
    setMessage("");
    try {
      if (suspendTenant.status === "suspended") await resumeTenant(suspendTenant.id);
      else await pauseTenant(suspendTenant.id);
      setSuspendTenant(null);
      await refresh();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  async function handleDeleteConfirm() {
    if (!deleteTarget) return;
    setBusyId(deleteTarget.id);
    setMessage("");
    try {
      await deleteTenant(deleteTarget.id);
      setDeleteTarget(null);
      await refresh();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusyId(null);
    }
  }

  function openEdit(tenant: HotelTenant) {
    setEditTenant(tenant);
    setEditForm({
      hotel_name: tenant.hotel_name,
      admin_email: tenant.admin_email,
      timezone: tenant.timezone || "UTC",
      currency: tenant.currency || "USD",
      country: tenant.country || "United States",
      plan_code: tenant.plan_code || "starter",
    });
  }

  async function handleImpersonateConfirm() {
    if (!impersonateTarget || impersonateReason.trim().length < 10) {
      setMessage("Impersonation requires a support reason (at least 10 characters).");
      return;
    }
    setBusyId(impersonateTarget.id);
    setMessage("");
    try {
      const result = await impersonateTenant(impersonateTarget.id, impersonateReason.trim());
      startImpersonation(result.access_token, result.user);
    } catch (e) {
      setMessage((e as Error).message);
      setBusyId(null);
    }
  }

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Properties / Hotels</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-4 p-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-2xl font-bold tracking-tight">Properties / Hotels</h2>
            <p className="text-muted-foreground">
              Manage hotel tenants, subscriptions, and access across the platform.
            </p>
          </div>
          <Button onClick={() => setAddOpen(true)}>
            <Plus className="h-4 w-4" />
            Add Hotel
          </Button>
        </div>

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <div className="relative flex-1 max-w-md">
            <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <Input
              className="pl-9"
              placeholder="Search hotel, owner email, or tenant ID…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <Select
            value={statusFilter}
            onValueChange={(v) => setStatusFilter(v as typeof statusFilter)}
          >
            <SelectTrigger className="w-[180px]">
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All statuses</SelectItem>
              <SelectItem value="active">Active</SelectItem>
              <SelectItem value="suspended">Suspended</SelectItem>
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
                <TableHead>Hotel Name</TableHead>
                <TableHead>Tenant ID</TableHead>
                <TableHead>Owner Email</TableHead>
                <TableHead>Room Count</TableHead>
                <TableHead>Subscription Status</TableHead>
                <TableHead>Date Onboarded</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-muted-foreground">
                    Loading hotels…
                  </TableCell>
                </TableRow>
              ) : pageRows.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7} className="text-muted-foreground">
                    No hotels match your filters.
                  </TableCell>
                </TableRow>
              ) : (
                pageRows.map((tenant) => (
                  <TableRow key={tenant.id}>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <Building2 className="h-4 w-4 shrink-0 text-muted-foreground" />
                        <div>
                          <Link
                            href={`/properties/${tenant.id}`}
                            className="font-medium text-primary hover:underline"
                          >
                            {tenant.hotel_name}
                          </Link>
                          <p className="text-xs text-muted-foreground">
                            {tenant.plan_name || tenant.plan_code}
                          </p>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <code className="text-xs" title={tenant.id}>
                        {shortId(tenant.id)}
                      </code>
                    </TableCell>
                    <TableCell>{tenant.admin_email}</TableCell>
                    <TableCell>
                      {tenant.room_count ?? 0}
                      {tenant.max_rooms ? (
                        <span className="text-muted-foreground"> / {tenant.max_rooms}</span>
                      ) : null}
                    </TableCell>
                    <TableCell>{statusBadge(tenant.status)}</TableCell>
                    <TableCell>{formatDate(tenant.created_at)}</TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button size="sm" variant="outline" asChild>
                          <Link href={`/properties/${tenant.id}`}>Open</Link>
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busyId === tenant.id || tenant.status === "cancelled"}
                          onClick={() => {
                            setImpersonateTarget(tenant);
                            setImpersonateReason("");
                          }}
                        >
                          <Eye className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busyId === tenant.id}
                          onClick={() => openEdit(tenant)}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                          Edit
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={busyId === tenant.id || tenant.status === "cancelled"}
                          onClick={() => setSuspendTenant(tenant)}
                        >
                          {tenant.status === "suspended" ? (
                            <Play className="h-3.5 w-3.5" />
                          ) : (
                            <Pause className="h-3.5 w-3.5" />
                          )}
                          {tenant.status === "suspended" ? "Resume" : "Suspend"}
                        </Button>
                        <Button
                          size="sm"
                          variant="destructive"
                          disabled={busyId === tenant.id}
                          onClick={() => setDeleteTarget(tenant)}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>

        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>
            {filtered.length} hotel{filtered.length === 1 ? "" : "s"}
            {statusFilter !== "all" || query ? " (filtered)" : ""}
          </span>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={pageSafe <= 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <span>
              Page {pageSafe + 1} of {pageCount}
            </span>
            <Button
              size="sm"
              variant="outline"
              disabled={pageSafe >= pageCount - 1}
              onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>

      {/* Add Hotel */}
      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add Hotel</DialogTitle>
            <DialogDescription>
              Provisions an isolated hotel workspace and owner account.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="hotel_name">Hotel name</Label>
              <Input
                id="hotel_name"
                value={form.hotel_name}
                onChange={(e) => setForm((f) => ({ ...f, hotel_name: e.target.value }))}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="admin_email">Owner email</Label>
              <Input
                id="admin_email"
                type="email"
                value={form.admin_email}
                onChange={(e) => setForm((f) => ({ ...f, admin_email: e.target.value }))}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="password">Owner password</Label>
              <Input
                id="password"
                type="password"
                value={form.password}
                onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Subscription plan</Label>
              <Select
                value={form.plan_code}
                onValueChange={(v) => setForm((f) => ({ ...f, plan_code: v }))}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {planOptions.map((p) => (
                    <SelectItem key={p.code} value={p.code}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleCreate} disabled={busyId === "create"}>
              Add Hotel
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Edit Property */}
      <Dialog open={!!editTenant} onOpenChange={(o) => !o && setEditTenant(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit Property Details</DialogTitle>
            <DialogDescription>
              Update hotel metadata and subscription assignment for {editTenant?.hotel_name}.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-3">
            <div className="space-y-1.5">
              <Label>Hotel name</Label>
              <Input
                value={editForm.hotel_name}
                onChange={(e) => setEditForm((f) => ({ ...f, hotel_name: e.target.value }))}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Owner email</Label>
              <Input value={editForm.admin_email} disabled />
              <p className="text-[11px] text-muted-foreground">
                Owner email is set at registration and cannot be changed here.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>Country</Label>
                <Input
                  value={editForm.country}
                  onChange={(e) => setEditForm((f) => ({ ...f, country: e.target.value }))}
                />
              </div>
              <div className="space-y-1.5">
                <Label>Currency</Label>
                <Input
                  value={editForm.currency}
                  onChange={(e) => setEditForm((f) => ({ ...f, currency: e.target.value }))}
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>Timezone</Label>
              <Input
                value={editForm.timezone}
                onChange={(e) => setEditForm((f) => ({ ...f, timezone: e.target.value }))}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Subscription plan</Label>
              <Select
                value={editForm.plan_code}
                onValueChange={(v) => setEditForm((f) => ({ ...f, plan_code: v }))}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {planOptions.map((p) => (
                    <SelectItem key={p.code} value={p.code}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditTenant(null)}>
              Cancel
            </Button>
            <Button
              onClick={handleEditSave}
              disabled={!editTenant || busyId === editTenant.id}
            >
              Save changes
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Suspend Access */}
      <Dialog open={!!suspendTenant} onOpenChange={(o) => !o && setSuspendTenant(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {suspendTenant?.status === "suspended" ? "Resume Access" : "Suspend Access"}
            </DialogTitle>
            <DialogDescription>
              {suspendTenant?.status === "suspended"
                ? `Re-enable staff logins for ${suspendTenant?.hotel_name}.`
                : `Staff at ${suspendTenant?.hotel_name} will be locked out until you resume access.`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSuspendTenant(null)}>
              Cancel
            </Button>
            <Button
              variant={suspendTenant?.status === "suspended" ? "default" : "destructive"}
              onClick={handleSuspendConfirm}
              disabled={!suspendTenant || busyId === suspendTenant.id}
            >
              {suspendTenant?.status === "suspended" ? "Resume Access" : "Suspend Access"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Tenant */}
      <Dialog open={!!deleteTarget} onOpenChange={(o) => !o && setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete Tenant</DialogTitle>
            <DialogDescription>
              Permanently mark <strong>{deleteTarget?.hotel_name}</strong> as cancelled and
              disable all staff accounts. This cannot be undone from the console.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteConfirm}
              disabled={!deleteTarget || busyId === deleteTarget.id}
            >
              Delete Tenant
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={!!impersonateTarget}
        onOpenChange={(o) => !o && setImpersonateTarget(null)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Impersonate {impersonateTarget?.hotel_name}</DialogTitle>
            <DialogDescription>
              Starts a 15-minute support session. Provide a reason (min 10 characters). Account
              and SaaS billing changes are blocked while impersonating.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label>Support reason</Label>
            <Textarea
              value={impersonateReason}
              onChange={(e) => setImpersonateReason(e.target.value)}
              rows={3}
              placeholder="Investigating reported reservation sync failure"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setImpersonateTarget(null)}>
              Cancel
            </Button>
            <Button
              onClick={handleImpersonateConfirm}
              disabled={
                !impersonateTarget ||
                busyId === impersonateTarget.id ||
                impersonateReason.trim().length < 10
              }
            >
              Start session
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
