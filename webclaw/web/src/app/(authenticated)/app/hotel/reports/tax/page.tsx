"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ReportDateRange,
  ReportExportButtons,
  ReportPageShell,
  monthStartIso,
  todayIso,
} from "@/components/hotel/report-page-shell";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { fetchApi } from "@/lib/api";
import { formatMoney, hotelCurrency } from "@/lib/hospitality";
import type { ReportExportDoc } from "@/lib/report-export";
import { Loader2 } from "lucide-react";

type TaxLine = {
  account_id?: string;
  account_name?: string;
  amount?: string;
};

export default function TaxSummaryReportPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const [fromDate, setFromDate] = useState(monthStartIso);
  const [toDate, setToDate] = useState(todayIso);

  const taxQ = useQuery({
    queryKey: ["report-tax-summary", fromDate, toDate],
    queryFn: () =>
      fetchApi(`/erpclaw/tax-summary?from_date=${fromDate}&to_date=${toDate}`),
    enabled: perms.canGenerateReports,
  });

  const currency = hotelCurrency(taxQ.data?.currency);
  const byAccount = useMemo(
    () =>
      Array.isArray(taxQ.data?.by_account)
        ? (taxQ.data.by_account as TaxLine[])
        : [],
    [taxQ.data],
  );

  const ready = perms.canGenerateReports && !taxQ.isLoading && !taxQ.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!ready) return null;
    return {
      title: "Tax summary",
      subtitle: `${fromDate} to ${toDate}`,
      kpis: [
        { label: "Collected", value: formatMoney(taxQ.data?.collected, currency) },
        { label: "Paid", value: formatMoney(taxQ.data?.paid, currency) },
        { label: "Net liability", value: formatMoney(taxQ.data?.net_liability, currency) },
      ],
      sheets: [
        {
          name: "By account",
          headers: ["Account", "Net amount"],
          rows: byAccount.map((row) => [
            row.account_name || "",
            formatMoney(row.amount, currency),
          ]),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Tax summary"
      subtitle="Collected vs paid tax liability for the selected period (read-only)"
      actions={
        <div className="flex flex-wrap items-end gap-3">
          <ReportDateRange
            fromDate={fromDate}
            toDate={toDate}
            onFromChange={setFromDate}
            onToChange={setToDate}
          />
          <ReportExportButtons
            disabled={!ready}
            filename={`tax-summary-${fromDate}-${toDate}`}
            buildDoc={buildExportDoc}
          />
        </div>
      }
    >
      {!perms.canGenerateReports ? (
        <p className="text-sm text-muted-foreground">
          This report requires Hotel Admin, General Manager, or Manager access.
        </p>
      ) : taxQ.isLoading ? (
        <div className="flex h-48 items-center justify-center">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      ) : taxQ.isError ? (
        <Card>
          <CardContent className="p-6 text-sm text-destructive">
            Could not load tax summary
            {taxQ.error instanceof Error ? `: ${taxQ.error.message}` : "."}
            {" "}Tax accounts must exist on the property chart of accounts.
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            <Kpi label="Collected" value={formatMoney(taxQ.data?.collected, currency)} />
            <Kpi label="Paid" value={formatMoney(taxQ.data?.paid, currency)} />
            <Kpi
              label="Net liability"
              value={formatMoney(taxQ.data?.net_liability, currency)}
            />
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">By tax account</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Account</TableHead>
                    <TableHead className="text-right">Net amount</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {byAccount.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={2} className="py-8 text-center text-muted-foreground">
                        No tax activity in this period.
                      </TableCell>
                    </TableRow>
                  ) : (
                    byAccount.map((row, idx) => (
                      <TableRow key={row.account_id || `${row.account_name}-${idx}`}>
                        <TableCell>{row.account_name || "—"}</TableCell>
                        <TableCell className="text-right tabular-nums">
                          {formatMoney(row.amount, currency)}
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
