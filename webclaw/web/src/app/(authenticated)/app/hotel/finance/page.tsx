"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookOpen,
  CircleDollarSign,
  ClipboardCheck,
  FileSpreadsheet,
  Loader2,
  Receipt,
  Scale,
  ShieldAlert,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";
import { HotelOpsHeader } from "@/components/hotel/hotel-ops-header";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { useToast } from "@/components/toast-provider";
import { fetchApi, postAction } from "@/lib/api";
import { formatMoney, hotelCurrency } from "@/lib/hospitality";
import { getNightAuditExceptions } from "@/lib/night-audit-api";

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function severityVariant(severity: string): "default" | "secondary" | "destructive" | "outline" {
  if (severity === "error") return "destructive";
  if (severity === "warning") return "default";
  return "secondary";
}

export default function HotelFinancePage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const router = useRouter();
  const { showToast } = useToast();
  const queryClient = useQueryClient();
  const trialBalanceDate = todayIso();
  const [journalMemo, setJournalMemo] = useState("");
  const [refundFolioId, setRefundFolioId] = useState("");
  const [refundAmount, setRefundAmount] = useState("");
  const [refundNote, setRefundNote] = useState("");

  const revenueQ = useQuery({
    queryKey: ["finance-revenue"],
    queryFn: () => fetchApi("/hospitalityclaw/list-revenue?limit=25"),
    enabled: perms.canSeeFinance,
  });

  const journalsQ = useQuery({
    queryKey: ["finance-journals"],
    queryFn: () => fetchApi("/erpclaw/list-journal-entries?limit=25"),
    enabled: perms.canSeeFinance && perms.canSeeLedger,
  });

  const invoicesQ = useQuery({
    queryKey: ["finance-invoices"],
    queryFn: () => fetchApi("/erpclaw/list-sales-invoices?limit=25"),
    enabled: perms.canSeeFinance && perms.canSeeInvoices,
  });

  const trialQ = useQuery({
    queryKey: ["finance-trial-balance", trialBalanceDate],
    queryFn: () =>
      fetchApi(`/erpclaw/trial-balance?to_date=${trialBalanceDate}`),
    enabled: perms.canSeeFinance && (perms.canSeeTaxReports || perms.canSeeLedger),
  });

  const exceptionsQ = useQuery({
    queryKey: ["night-audit-exceptions", trialBalanceDate],
    queryFn: () => getNightAuditExceptions(trialBalanceDate),
    enabled: perms.canSeeFinance && perms.canRunNightAudit,
  });

  const nightAuditMut = useMutation({
    mutationFn: async () => {
      return fetchApi("/hotel/night-audit/run", {
        method: "POST",
        body: JSON.stringify({ send_email: true, force: true }),
      });
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["finance-"] });
      queryClient.invalidateQueries({ queryKey: ["night-audit"] });
      queryClient.invalidateQueries({ queryKey: ["night-audit-exceptions"] });
      const first = Array.isArray(data.results) ? data.results[0] : data;
      const ok = data.status === "ok" && first?.status !== "error";
      const kpis = first?.kpis as
        | { room_revenue?: string; occupancy_rate_pct?: string; revpar?: string }
        | undefined;
      const exCount = Number(first?.exception_count || 0);
      showToast({
        type: ok ? "success" : "error",
        message: ok
          ? `Night audit complete${
              kpis?.room_revenue != null
                ? ` — room revenue ${kpis.room_revenue}, occupancy ${kpis.occupancy_rate_pct}%, RevPAR ${kpis.revpar}`
                : ""
            }${exCount ? ` · ${exCount} exception(s)` : ""}`
          : (first?.message as string) ||
            (data.message as string) ||
            "Night audit failed",
      });
    },
    onError: (err: Error) => {
      showToast({ type: "error", message: err.message || "Night audit failed" });
    },
  });

  const journalMut = useMutation({
    mutationFn: () =>
      postAction("erpclaw", "add-journal-entry", {
        posting_date: todayIso(),
        remarks: journalMemo || "Manual adjustment",
      }),
    onSuccess: (data) => {
      if (data.status === "error") {
        showToast({
          type: "error",
          message: (data.message as string) || "Could not create journal",
        });
        return;
      }
      showToast({
        type: "success",
        message: "Journal draft created — complete line items in Financial Core if needed.",
      });
      setJournalMemo("");
      queryClient.invalidateQueries({ queryKey: ["finance-journals"] });
    },
  });

  const refundMut = useMutation({
    mutationFn: () =>
      postAction("hospitalityclaw", "add-folio-charge", {
        folio_id: refundFolioId,
        amount: String(-Math.abs(parseFloat(refundAmount) || 0)),
        charge_type: "other",
        description: refundNote || "Refund / discount override",
        posting_date: todayIso(),
      }),
    onSuccess: (data) => {
      if (data.status === "error") {
        showToast({
          type: "error",
          message: (data.message as string) || "Override failed",
        });
        return;
      }
      showToast({ type: "success", message: "Refund / discount override posted" });
      setRefundFolioId("");
      setRefundAmount("");
      setRefundNote("");
      queryClient.invalidateQueries({ queryKey: ["finance-revenue"] });
    },
  });

  const currency = hotelCurrency(
    (revenueQ.data?.currency as string) ||
      (trialQ.data?.currency as string) ||
      "USD",
  );

  const invoices = useMemo(() => {
    const raw = invoicesQ.data?.sales_invoices || invoicesQ.data?.invoices || [];
    return Array.isArray(raw) ? raw : [];
  }, [invoicesQ.data]);

  const journals = useMemo(() => {
    const raw = journalsQ.data?.journal_entries || journalsQ.data?.entries || [];
    return Array.isArray(raw) ? raw : [];
  }, [journalsQ.data]);

  if (!perms.canSeeFinance) {
    return (
      <div className="flex min-h-[40vh] flex-col items-center justify-center gap-3 p-6 text-center">
        <p className="text-sm text-muted-foreground">
          Finance &amp; Accounting is limited to Hotel Admin and Accountant roles. General Managers use Analytics for reports.
        </p>
        <Button variant="outline" onClick={() => router.push("/app/hotel/dashboard")}>
          Back to dashboard
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Finance &amp; Accounting</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6">
        <HotelOpsHeader
          title="Finance & Accounting"
          subtitle="Night audit, general ledger, city ledger invoicing, tax, and override approvals"
          perms={perms}
        />

        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Collected revenue
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {formatMoney(revenueQ.data?.revenue_total, currency)}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Outstanding folios
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">
                {formatMoney(revenueQ.data?.outstanding_total, currency)}
              </div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Open journals
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{journals.length}</div>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                City ledger invoices
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{invoices.length}</div>
            </CardContent>
          </Card>
        </div>

        <Tabs defaultValue="night-audit" className="space-y-4">
          <TabsList className="flex h-auto flex-wrap justify-start gap-1">
            {perms.canRunNightAudit ? (
              <TabsTrigger value="night-audit" className="gap-1.5">
                <ClipboardCheck className="h-3.5 w-3.5" />
                Night audit
              </TabsTrigger>
            ) : null}
            {perms.canWriteLedger || perms.canSeeLedger ? (
              <TabsTrigger value="ledger" className="gap-1.5">
                <BookOpen className="h-3.5 w-3.5" />
                General ledger
              </TabsTrigger>
            ) : null}
            {perms.canSeeInvoices ? (
              <TabsTrigger value="ar" className="gap-1.5">
                <Receipt className="h-3.5 w-3.5" />
                City ledger / AR
              </TabsTrigger>
            ) : null}
            {perms.canSeeTaxReports ? (
              <TabsTrigger value="tax" className="gap-1.5">
                <FileSpreadsheet className="h-3.5 w-3.5" />
                Tax &amp; reports
              </TabsTrigger>
            ) : null}
            {perms.canApproveRefunds ? (
              <TabsTrigger value="refunds" className="gap-1.5">
                <ShieldAlert className="h-3.5 w-3.5" />
                Refunds
              </TabsTrigger>
            ) : null}
          </TabsList>

          {perms.canRunNightAudit ? (
            <TabsContent value="night-audit" className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>End-of-day closeout</CardTitle>
                  <CardDescription>
                    Posts room charges and taxes for in-house guests, marks
                    no-shows with policy penalties, closes the cash drawer, and
                    emails the night audit report to owners and accountants.
                  </CardDescription>
                </CardHeader>
                <CardContent className="flex flex-wrap gap-3">
                  <Button
                    onClick={() => nightAuditMut.mutate()}
                    disabled={nightAuditMut.isPending}
                  >
                    {nightAuditMut.isPending ? (
                      <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                    ) : (
                      <ClipboardCheck className="mr-1.5 h-4 w-4" />
                    )}
                    Run night audit
                  </Button>
                  <Button variant="outline" asChild>
                    <Link href="/app/hotel/revenue">Open revenue detail</Link>
                  </Button>
                  <Button variant="outline" asChild>
                    <Link href="/app/hotel/reports/night-audit">Audit history</Link>
                  </Button>
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Exception queue</CardTitle>
                  <CardDescription>
                    Open unpaid folios, overdue departures, pending arrivals, and
                    out-of-order rooms for {trialBalanceDate}.
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  {exceptionsQ.isLoading ? (
                    <div className="flex h-24 items-center justify-center">
                      <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                    </div>
                  ) : exceptionsQ.isError ? (
                    <p className="text-sm text-destructive">
                      Could not load exceptions
                      {exceptionsQ.error instanceof Error
                        ? `: ${exceptionsQ.error.message}`
                        : "."}
                    </p>
                  ) : (exceptionsQ.data?.exceptions || []).length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      No exceptions for this business date.
                    </p>
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Severity</TableHead>
                          <TableHead>Issue</TableHead>
                          <TableHead>Detail</TableHead>
                          <TableHead className="text-right">Action</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {(exceptionsQ.data?.exceptions || []).map((ex, idx) => (
                          <TableRow key={`${ex.code}-${ex.reservation_id || ex.room_number || idx}`}>
                            <TableCell>
                              <Badge variant={severityVariant(ex.severity)}>
                                {ex.severity}
                              </Badge>
                            </TableCell>
                            <TableCell className="font-medium">{ex.title}</TableCell>
                            <TableCell className="text-sm text-muted-foreground">
                              {ex.message}
                            </TableCell>
                            <TableCell className="text-right">
                              {ex.href ? (
                                <Button variant="ghost" size="sm" asChild>
                                  <Link href={ex.href}>Open</Link>
                                </Button>
                              ) : (
                                "—"
                              )}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  )}
                </CardContent>
              </Card>
            </TabsContent>
          ) : null}

          {(perms.canWriteLedger || perms.canSeeLedger) && (
            <TabsContent value="ledger" className="space-y-4">
              {perms.canWriteLedger ? (
                <Card>
                  <CardHeader>
                    <CardTitle>Manual journal entry</CardTitle>
                    <CardDescription>
                      Draft a journal in erpclaw-engine for adjustments that are
                      not routine guest settlements.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-end">
                    <div className="grid flex-1 gap-2">
                      <Label htmlFor="journal-memo">Memo</Label>
                      <Input
                        id="journal-memo"
                        value={journalMemo}
                        onChange={(e) => setJournalMemo(e.target.value)}
                        placeholder="Accrual / reclass / correction"
                      />
                    </div>
                    <Button
                      onClick={() => journalMut.mutate()}
                      disabled={journalMut.isPending}
                    >
                      {journalMut.isPending ? (
                        <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                      ) : (
                        <Scale className="mr-1.5 h-4 w-4" />
                      )}
                      Create draft
                    </Button>
                  </CardContent>
                </Card>
              ) : null}
              <Card>
                <CardHeader>
                  <CardTitle>Recent journals</CardTitle>
                </CardHeader>
                <CardContent className="overflow-x-auto">
                  {journalsQ.isLoading ? (
                    <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                  ) : (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Id</TableHead>
                          <TableHead>Date</TableHead>
                          <TableHead>Status</TableHead>
                          <TableHead>Remarks</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {journals.length === 0 ? (
                          <TableRow>
                            <TableCell colSpan={4} className="text-muted-foreground">
                              No journal entries yet.
                            </TableCell>
                          </TableRow>
                        ) : (
                          journals.slice(0, 20).map((j: Record<string, unknown>) => (
                            <TableRow key={String(j.id || j.journal_entry_id)}>
                              <TableCell className="font-mono text-xs">
                                {String(j.id || j.journal_entry_id || "—").slice(0, 8)}
                              </TableCell>
                              <TableCell>
                                {String(j.posting_date || j.date || "—")}
                              </TableCell>
                              <TableCell>
                                <Badge variant="outline">
                                  {String(j.status || "draft")}
                                </Badge>
                              </TableCell>
                              <TableCell className="max-w-[240px] truncate">
                                {String(j.remarks || j.title || "—")}
                              </TableCell>
                            </TableRow>
                          ))
                        )}
                      </TableBody>
                    </Table>
                  )}
                </CardContent>
              </Card>
            </TabsContent>
          )}

          {perms.canSeeInvoices ? (
            <TabsContent value="ar" className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>Accounts receivable / city ledger</CardTitle>
                  <CardDescription>
                    Corporate invoices billed outside walk-up guest checkout.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  {perms.canWriteInvoices ? (
                    <Button asChild variant="outline">
                      <Link href="/skills/erpclaw/sales-invoices/new">
                        <CircleDollarSign className="mr-1.5 h-4 w-4" />
                        New sales invoice
                      </Link>
                    </Button>
                  ) : null}
                  <div className="overflow-x-auto">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Invoice</TableHead>
                          <TableHead>Customer</TableHead>
                          <TableHead>Status</TableHead>
                          <TableHead className="text-right">Total</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {invoicesQ.isLoading ? (
                          <TableRow>
                            <TableCell colSpan={4}>
                              <Loader2 className="h-4 w-4 animate-spin" />
                            </TableCell>
                          </TableRow>
                        ) : invoices.length === 0 ? (
                          <TableRow>
                            <TableCell colSpan={4} className="text-muted-foreground">
                              No corporate invoices yet.
                            </TableCell>
                          </TableRow>
                        ) : (
                          invoices.slice(0, 20).map((inv: Record<string, unknown>) => (
                            <TableRow key={String(inv.id)}>
                              <TableCell className="font-mono text-xs">
                                {String(inv.name || inv.id || "—").slice(0, 12)}
                              </TableCell>
                              <TableCell>
                                {String(inv.customer_name || inv.customer || "—")}
                              </TableCell>
                              <TableCell>
                                <Badge variant="outline">
                                  {String(inv.status || "—")}
                                </Badge>
                              </TableCell>
                              <TableCell className="text-right tabular-nums">
                                {formatMoney(inv.grand_total ?? inv.total, currency)}
                              </TableCell>
                            </TableRow>
                          ))
                        )}
                      </TableBody>
                    </Table>
                  </div>
                </CardContent>
              </Card>
            </TabsContent>
          ) : null}

          {perms.canSeeTaxReports ? (
            <TabsContent value="tax" className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>Tax liability &amp; audit reports</CardTitle>
                  <CardDescription>
                    Trial balance and tax summaries for period audits.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        queryClient.invalidateQueries({
                          queryKey: ["finance-trial-balance"],
                        })
                      }
                    >
                      Refresh trial balance
                    </Button>
                    <Button variant="outline" size="sm" asChild>
                      <Link href="/skills/erpclaw">Open Financial Core</Link>
                    </Button>
                  </div>
                  {trialQ.isLoading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : trialQ.data?.status === "ok" ? (
                    <pre className="max-h-80 overflow-auto rounded-md bg-muted p-3 text-xs">
                      {JSON.stringify(trialQ.data, null, 2).slice(0, 4000)}
                    </pre>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      {(trialQ.data?.message as string) ||
                        "Trial balance unavailable — ensure the chart of accounts is seeded."}
                    </p>
                  )}
                </CardContent>
              </Card>
            </TabsContent>
          ) : null}

          {perms.canApproveRefunds ? (
            <TabsContent value="refunds" className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>Refund &amp; discount overrides</CardTitle>
                  <CardDescription>
                    Post an adjustment to a folio. Routine checkout payments stay
                    on Front Desk Billing.
                  </CardDescription>
                </CardHeader>
                <CardContent className="grid gap-3 sm:grid-cols-2">
                  <div className="grid gap-2">
                    <Label htmlFor="refund-folio">Folio ID</Label>
                    <Input
                      id="refund-folio"
                      value={refundFolioId}
                      onChange={(e) => setRefundFolioId(e.target.value)}
                      placeholder="folio uuid"
                    />
                  </div>
                  <div className="grid gap-2">
                    <Label htmlFor="refund-amount">Amount</Label>
                    <Input
                      id="refund-amount"
                      type="number"
                      step="0.01"
                      value={refundAmount}
                      onChange={(e) => setRefundAmount(e.target.value)}
                      placeholder="25.00"
                    />
                  </div>
                  <div className="grid gap-2 sm:col-span-2">
                    <Label htmlFor="refund-note">Reason</Label>
                    <Input
                      id="refund-note"
                      value={refundNote}
                      onChange={(e) => setRefundNote(e.target.value)}
                      placeholder="Manager-approved discount / refund"
                    />
                  </div>
                  <div className="sm:col-span-2">
                    <Button
                      disabled={
                        !refundFolioId.trim() ||
                        !refundAmount ||
                        refundMut.isPending
                      }
                      onClick={() => refundMut.mutate()}
                    >
                      {refundMut.isPending ? (
                        <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                      ) : (
                        <ShieldAlert className="mr-1.5 h-4 w-4" />
                      )}
                      Post override
                    </Button>
                  </div>
                </CardContent>
              </Card>
            </TabsContent>
          ) : null}
        </Tabs>
      </div>
    </div>
  );
}
