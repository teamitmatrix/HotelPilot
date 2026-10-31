"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Building2,
  CheckCircle2,
  Circle,
  CreditCard,
  Download,
  Eye,
  ListChecks,
  Rocket,
  Ticket,
  Users,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  createSupportTicket,
  downloadTenantExport,
  getTenantDetail,
  goLiveTenant,
  impersonateTenant,
  pauseTenant,
  resumeTenant,
  updateOnboardingStep,
} from "@/lib/admin-api";
import { useAuth } from "@/lib/auth";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";

function formatTs(iso?: string | null) {
  if (!iso) return "—";
  const t = Date.parse(iso.includes("T") ? iso : iso.replace(" ", "T") + "Z");
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleString();
}

function statusBadge(status: string, lifecycle?: string | null) {
  const phase = lifecycle || status;
  if (phase === "active") return <Badge>Active</Badge>;
  if (phase === "trial") return <Badge variant="secondary">Trial</Badge>;
  if (phase === "onboarding") return <Badge variant="secondary">Onboarding</Badge>;
  if (phase === "past_due") return <Badge variant="destructive">Past due</Badge>;
  if (phase === "suspended") return <Badge variant="destructive">Suspended</Badge>;
  if (phase === "cancelled") return <Badge variant="outline">Cancelled</Badge>;
  return <Badge variant="outline">{phase}</Badge>;
}

function onboardingBadge(status?: string | null) {
  if (!status || status === "missing") return <Badge variant="outline">No pipeline</Badge>;
  if (status === "live") return <Badge>Live</Badge>;
  if (status === "ready") return <Badge className="bg-emerald-600 hover:bg-emerald-600">Ready</Badge>;
  if (status === "blocked") return <Badge variant="destructive">Blocked</Badge>;
  return <Badge variant="secondary">In progress</Badge>;
}

export default function TenantDetailPage() {
  const params = useParams();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { startImpersonation } = useAuth();
  const tenantId = String(params.id || "");

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["tenant-detail", tenantId],
    queryFn: () => getTenantDetail(tenantId),
    enabled: Boolean(tenantId),
  });

  const [impersonateOpen, setImpersonateOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [ticketOpen, setTicketOpen] = useState(false);
  const [ticketSubject, setTicketSubject] = useState("");
  const [ticketBody, setTicketBody] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    document.title = data?.tenant.hotel_name
      ? `${data.tenant.hotel_name} — Platform Admin`
      : "Hotel detail — Platform Admin";
  }, [data?.tenant.hotel_name]);

  async function handleImpersonate() {
    if (reason.trim().length < 10) {
      setMessage("Enter a support reason of at least 10 characters.");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const result = await impersonateTenant(tenantId, reason.trim());
      startImpersonation(result.access_token, result.user);
    } catch (e) {
      setMessage((e as Error).message);
      setBusy(false);
    }
  }

  async function handleToggleSuspend() {
    if (!data?.tenant) return;
    setBusy(true);
    setMessage("");
    try {
      if (data.tenant.status === "suspended") await resumeTenant(tenantId);
      else await pauseTenant(tenantId);
      await refetch();
      await queryClient.invalidateQueries({ queryKey: ["admin-tenants"] });
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateTicket() {
    if (ticketSubject.trim().length < 3) return;
    setBusy(true);
    setMessage("");
    try {
      await createSupportTicket({
        tenant_id: tenantId,
        subject: ticketSubject.trim(),
        body: ticketBody,
        hotel_name: data?.tenant.hotel_name,
        requester_email: data?.tenant.admin_email,
      });
      setTicketOpen(false);
      setTicketSubject("");
      setTicketBody("");
      await refetch();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleStepToggle(stepKey: string, currentlyDone: boolean) {
    setBusy(true);
    setMessage("");
    try {
      await updateOnboardingStep(tenantId, stepKey, currentlyDone ? "pending" : "done");
      await refetch();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function handleGoLive() {
    setBusy(true);
    setMessage("");
    try {
      await goLiveTenant(tenantId);
      await refetch();
      await queryClient.invalidateQueries({ queryKey: ["admin-tenants"] });
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const tenant = data?.tenant;
  const usage = data?.usage;
  const onboarding = data?.onboarding;

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <Button variant="ghost" size="sm" onClick={() => router.push("/properties")}>
          <ArrowLeft className="h-4 w-4" />
          Hotels
        </Button>
        <h1 className="truncate text-lg font-semibold">
          {tenant?.hotel_name || "Hotel detail"}
        </h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6">
        {message && (
          <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">{message}</div>
        )}
        {error && (
          <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
            {(error as Error).message}
          </div>
        )}
        {isLoading || !tenant ? (
          <p className="text-sm text-muted-foreground">Loading hotel workspace…</p>
        ) : (
          <>
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <div>
                <div className="flex flex-wrap items-center gap-2">
                  <Building2 className="h-5 w-5 text-muted-foreground" />
                  <h2 className="text-2xl font-bold tracking-tight">{tenant.hotel_name}</h2>
                  {statusBadge(tenant.status, (tenant as { lifecycle?: string }).lifecycle)}
                </div>
                <p className="mt-1 text-muted-foreground">
                  {tenant.admin_email} · {tenant.plan_name || tenant.plan_code} ·{" "}
                  <code className="text-xs">{tenant.id}</code>
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={() => setTicketOpen(true)}>
                  <Ticket className="h-4 w-4" />
                  New ticket
                </Button>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    setMessage("");
                    try {
                      await downloadTenantExport(tenant.id);
                    } catch (e) {
                      setMessage((e as Error).message);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  <Download className="h-4 w-4" />
                  Export data
                </Button>
                <Button
                  variant="outline"
                  disabled={busy || tenant.status === "cancelled"}
                  onClick={handleToggleSuspend}
                >
                  {tenant.status === "suspended" ? "Resume hotel" : "Suspend hotel"}
                </Button>
                <Button
                  disabled={busy || tenant.status === "cancelled"}
                  onClick={() => setImpersonateOpen(true)}
                >
                  <Eye className="h-4 w-4" />
                  Impersonate
                </Button>
              </div>
            </div>

            {onboarding && onboarding.status !== "missing" && (
              <Card>
                <CardHeader className="flex flex-row items-start justify-between gap-4">
                  <div>
                    <CardTitle className="flex items-center gap-2 text-base">
                      <ListChecks className="h-4 w-4" />
                      Onboarding pipeline
                    </CardTitle>
                    <p className="mt-1 text-sm text-muted-foreground">
                      {onboarding.progress.required_done}/{onboarding.progress.required_total}{" "}
                      required · {onboarding.progress.done}/{onboarding.progress.total} total
                      {onboarding.source ? ` · via ${onboarding.source}` : ""}
                      {onboarding.owner_invite_status
                        ? ` · invite ${onboarding.owner_invite_status}`
                        : ""}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {onboardingBadge(onboarding.status)}
                    {onboarding.status !== "live" && (
                      <Button
                        size="sm"
                        disabled={busy || !onboarding.can_go_live}
                        onClick={handleGoLive}
                      >
                        <Rocket className="h-4 w-4" />
                        Go live
                      </Button>
                    )}
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="space-y-2">
                    {onboarding.steps.map((step) => {
                      const done = step.status === "done" || step.status === "skipped";
                      return (
                        <div
                          key={step.key}
                          className="flex items-start justify-between gap-3 rounded-md border px-3 py-2"
                        >
                          <div className="flex items-start gap-2">
                            {done ? (
                              <CheckCircle2 className="mt-0.5 h-4 w-4 text-emerald-600" />
                            ) : (
                              <Circle className="mt-0.5 h-4 w-4 text-muted-foreground" />
                            )}
                            <div>
                              <p className="text-sm font-medium">
                                {step.title}
                                {step.required ? (
                                  <span className="ml-1 text-xs text-muted-foreground">
                                    required
                                  </span>
                                ) : null}
                              </p>
                              <p className="text-xs text-muted-foreground">{step.description}</p>
                            </div>
                          </div>
                          {onboarding.status !== "live" && (
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={busy}
                              onClick={() => handleStepToggle(step.key, done)}
                            >
                              {done ? "Undo" : "Mark done"}
                            </Button>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  {onboarding.go_live_at && (
                    <p className="mt-3 text-xs text-muted-foreground">
                      Went live {formatTs(onboarding.go_live_at)}
                      {onboarding.go_live_by ? ` by ${onboarding.go_live_by}` : ""}
                    </p>
                  )}
                </CardContent>
              </Card>
            )}

            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    Rooms vs limit
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-2xl font-bold">
                    {usage?.rooms ?? 0}
                    <span className="text-base font-normal text-muted-foreground">
                      {" "}
                      / {usage?.rooms_unlimited ? "∞" : usage?.max_rooms ?? "—"}
                    </span>
                  </p>
                  {usage?.rooms_over_limit ? (
                    <p className="text-xs text-destructive">Over plan room limit</p>
                  ) : null}
                </CardContent>
              </Card>
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    Staff vs limit
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-2xl font-bold">
                    {usage?.staff ?? 0}
                    <span className="text-base font-normal text-muted-foreground">
                      {" "}
                      / {usage?.staff_unlimited ? "∞" : usage?.max_staff ?? "—"}
                    </span>
                  </p>
                  {usage?.staff_over_limit ? (
                    <p className="text-xs text-destructive">Over plan staff limit</p>
                  ) : null}
                </CardContent>
              </Card>
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    Stripe status
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-2xl font-bold capitalize">
                    {data.stripe?.status || "not linked"}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Period end: {formatTs(data.stripe?.current_period_end)}
                  </p>
                </CardContent>
              </Card>
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    Billing override
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <p className="text-sm">
                    Trial: {formatTs(data.billing_override?.trial_ends_at)}
                  </p>
                  <p className="text-sm">
                    Discount: {data.billing_override?.discount_percent || 0}%
                  </p>
                  <Button variant="link" className="h-auto p-0" asChild>
                    <Link href="/subscriptions">Manage on Subscriptions</Link>
                  </Button>
                </CardContent>
              </Card>
            </div>

            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <CreditCard className="h-4 w-4" />
                    Recent billing events
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Event</TableHead>
                        <TableHead>When</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {(data.subscription_events || []).length === 0 ? (
                        <TableRow>
                          <TableCell colSpan={2} className="text-muted-foreground">
                            No billing events yet.
                          </TableCell>
                        </TableRow>
                      ) : (
                        (data.subscription_events || []).slice(0, 8).map((ev) => (
                          <TableRow key={ev.id}>
                            <TableCell>
                              <p className="font-medium">{ev.event_type.replace(/_/g, " ")}</p>
                              {ev.detail ? (
                                <p className="truncate text-xs text-muted-foreground">{ev.detail}</p>
                              ) : null}
                            </TableCell>
                            <TableCell className="whitespace-nowrap text-muted-foreground">
                              {formatTs(ev.created_at)}
                            </TableCell>
                          </TableRow>
                        ))
                      )}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <Users className="h-4 w-4" />
                    Support tickets
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Subject</TableHead>
                        <TableHead>Status</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {(data.tickets || []).length === 0 ? (
                        <TableRow>
                          <TableCell colSpan={2} className="text-muted-foreground">
                            No tickets for this hotel.
                          </TableCell>
                        </TableRow>
                      ) : (
                        (data.tickets || []).map((t) => (
                          <TableRow key={t.id}>
                            <TableCell>
                              <Link
                                href="/superadmin/support"
                                className="font-medium text-primary hover:underline"
                              >
                                {t.subject}
                              </Link>
                            </TableCell>
                            <TableCell>
                              <Badge variant="outline">{t.status}</Badge>
                            </TableCell>
                          </TableRow>
                        ))
                      )}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            </div>
          </>
        )}
      </div>

      <Dialog open={impersonateOpen} onOpenChange={setImpersonateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Impersonate hotel</DialogTitle>
            <DialogDescription>
              Starts a 15-minute support session. Provide a reason (min 10 characters). Destructive
              account and SaaS billing actions are blocked while impersonating.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label>Support reason</Label>
            <Textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              placeholder="Investigating front-desk check-in error reported by owner"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setImpersonateOpen(false)}>
              Cancel
            </Button>
            <Button disabled={busy || reason.trim().length < 10} onClick={handleImpersonate}>
              Start session
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={ticketOpen} onOpenChange={setTicketOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New ticket for {tenant?.hotel_name}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>Subject</Label>
              <Input
                value={ticketSubject}
                onChange={(e) => setTicketSubject(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Details</Label>
              <Textarea
                value={ticketBody}
                onChange={(e) => setTicketBody(e.target.value)}
                rows={4}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setTicketOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={busy || ticketSubject.trim().length < 3}
              onClick={handleCreateTicket}
            >
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
