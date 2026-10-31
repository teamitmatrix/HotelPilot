"use client";

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ReportExportButtons, ReportPageShell } from "@/components/hotel/report-page-shell";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { fetchApi } from "@/lib/api";
import { formatMoney, hotelCurrency } from "@/lib/hospitality";
import type { ReportExportDoc } from "@/lib/report-export";
import { Loader2 } from "lucide-react";

type InvoiceRow = Record<string, unknown>;

function num(value: unknown): number {
  const n = parseFloat(String(value ?? "0"));
  return Number.isFinite(n) ? n : 0;
}

export default function InvoicesArReportPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const canView = perms.canGenerateReports || perms.canSeeInvoices;

  const invoicesQ = useQuery({
    queryKey: ["report-sales-invoices"],
    queryFn: () => fetchApi("/erpclaw/list-sales-invoices?limit=200"),
    enabled: canView,
  });

  const invoices = useMemo(() => {
    const raw =
      invoicesQ.data?.sales_invoices ||
      invoicesQ.data?.invoices ||
      invoicesQ.data?.items ||
      [];
    return Array.isArray(raw) ? (raw as InvoiceRow[]) : [];
  }, [invoicesQ.data]);

  const currency = hotelCurrency(invoicesQ.data?.currency);

  const totals = useMemo(() => {
    let billed = 0;
    let outstanding = 0;
    let openCount = 0;
    for (const inv of invoices) {
      billed += num(inv.grand_total ?? inv.total);
      const out = num(inv.outstanding_amount ?? inv.outstanding);
      outstanding += out;
      const status = String(inv.status || "").toLowerCase();
      if (out > 0 || status === "submitted" || status === "unpaid" || status === "overdue") {
        openCount += 1;
      }
    }
    return { billed, outstanding, openCount, count: invoices.length };
  }, [invoices]);

  const ready = canView && !invoicesQ.isLoading && !invoicesQ.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready) return null;
    return {
      title: "Invoices / AR",
      subtitle: "City-ledger and corporate sales invoices",
      kpis: [
        { label: "Invoices", value: totals.count },
        { label: "Open / unpaid", value: totals.openCount },
        { label: "Billed total", value: formatMoney(totals.billed, currency) },
        { label: "Outstanding", value: formatMoney(totals.outstanding, currency) },
      ],
      sheets: [
        {
          name: "Invoices",
          headers: ["Invoice", "Customer", "Date", "Status", "Total", "Outstanding"],
          rows: invoices.map((inv) => [
            String(inv.name || inv.naming_series || inv.id || ""),
            String(inv.customer_name || inv.customer || ""),
            String(inv.posting_date || inv.due_date || ""),
            String(inv.status || ""),
            formatMoney(inv.grand_total ?? inv.total, currency),
            formatMoney(inv.outstanding_amount ?? inv.outstanding ?? 0, currency),
          ]),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Invoices / AR"
      subtitle="City-ledger and corporate sales invoices (read-only)"
      actions={
        <ReportExportButtons
          disabled={!ready}
          filename="invoices-ar"
          buildDoc={buildExportDoc}
        />
      }
    >
      {!canView ? (
        <p className="text-sm text-muted-foreground">
          This report requires Hotel Admin, General Manager, Manager, or Accountant access.
        </p>
      ) : invoicesQ.isLoading ? (
        <div className="flex h-48 items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : invoicesQ.isError ? (
        <Card>
          <CardContent className="p-6 text-sm text-destructive">
            Could not load invoices
            {invoicesQ.error instanceof Error ? `: ${invoicesQ.error.message}` : "."}
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-4">
            <Kpi label="Invoices" value={String(totals.count)} />
            <Kpi label="Open / unpaid" value={String(totals.openCount)} />
            <Kpi label="Billed total" value={formatMoney(totals.billed, currency)} />
            <Kpi
              label="Outstanding"
              value={formatMoney(totals.outstanding, currency)}
            />
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Sales invoices</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Invoice</TableHead>
                    <TableHead>Customer</TableHead>
                    <TableHead>Date</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Total</TableHead>
                    <TableHead className="text-right">Outstanding</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {invoices.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="py-8 text-center text-muted-foreground">
                        No corporate invoices yet.
                      </TableCell>
                    </TableRow>
                  ) : (
                    invoices.map((inv) => (
                      <TableRow key={String(inv.id)}>
                        <TableCell className="font-mono text-xs">
                          {String(inv.name || inv.naming_series || inv.id || "—").slice(0, 16)}
                        </TableCell>
                        <TableCell>
                          {String(inv.customer_name || inv.customer || "—")}
                        </TableCell>
                        <TableCell>
                          {String(inv.posting_date || inv.due_date || "—")}
                        </TableCell>
                        <TableCell>
                          <Badge variant="secondary">{String(inv.status || "—")}</Badge>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {formatMoney(inv.grand_total ?? inv.total, currency)}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {formatMoney(
                            inv.outstanding_amount ?? inv.outstanding ?? 0,
                            currency,
                          )}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      )}
    </ReportPageShell>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium text-muted-foreground">{label}</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold tabular-nums">{value}</div>
      </CardContent>
    </Card>
  );
}
