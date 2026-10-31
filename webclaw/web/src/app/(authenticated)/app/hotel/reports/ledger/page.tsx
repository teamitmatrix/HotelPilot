"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
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

type TrialAccount = {
  account_id?: string;
  account_name?: string;
  account_number?: string;
  root_type?: string;
  opening_debit?: string;
  opening_credit?: string;
  debit?: string;
  credit?: string;
  closing_debit?: string;
  closing_credit?: string;
};

type GlEntry = Record<string, unknown>;

export default function LedgerReportPage() {
  const { user } = useAuth();
  const perms = useHotelPermissions(user);
  const canView =
    perms.canSeeLedger || perms.canSeeTaxReports || perms.canManageHotel;
  const [fromDate, setFromDate] = useState(monthStartIso);
  const [toDate, setToDate] = useState(todayIso);

  const trialQ = useQuery({
    queryKey: ["report-trial-balance", fromDate, toDate],
    queryFn: () =>
      fetchApi(
        `/erpclaw/trial-balance?from_date=${fromDate}&to_date=${toDate}`,
      ),
    enabled: canView,
  });

  const glQ = useQuery({
    queryKey: ["report-general-ledger", fromDate, toDate],
    queryFn: () =>
      fetchApi(
        `/erpclaw/general-ledger?from_date=${fromDate}&to_date=${toDate}&limit=200`,
      ),
    enabled: canView,
  });

  const currency = hotelCurrency(trialQ.data?.currency || glQ.data?.currency);
  const accounts = useMemo(
    () =>
      Array.isArray(trialQ.data?.accounts)
        ? (trialQ.data.accounts as TrialAccount[])
        : [],
    [trialQ.data],
  );
  const entries = useMemo(() => {
    const raw =
      glQ.data?.entries || glQ.data?.gl_entries || glQ.data?.items || [];
    return Array.isArray(raw) ? (raw as GlEntry[]) : [];
  }, [glQ.data]);

  const ready =
    canView &&
    !trialQ.isLoading &&
    !glQ.isLoading &&
    !trialQ.isError &&
    !glQ.isError;

  function buildExportDoc(): ReportExportDoc | null {
    if (!canView || (trialQ.isLoading && glQ.isLoading)) return null;
    return {
      title: "Trial balance / GL",
      subtitle: `${fromDate} to ${toDate}`,
      kpis: [
        { label: "Total debit", value: formatMoney(trialQ.data?.total_debit, currency) },
        { label: "Total credit", value: formatMoney(trialQ.data?.total_credit, currency) },
      ],
      sheets: [
        {
          name: "Trial balance",
          headers: [
            "Account",
            "Type",
            "Period debit",
            "Period credit",
            "Closing debit",
            "Closing credit",
          ],
          rows: accounts.map((a) => [
            a.account_number
              ? `${a.account_number} · ${a.account_name || ""}`
              : a.account_name || "",
            a.root_type || "",
            formatMoney(a.debit, currency),
            formatMoney(a.credit, currency),
            formatMoney(a.closing_debit, currency),
            formatMoney(a.closing_credit, currency),
          ]),
        },
        {
          name: "General ledger",
          headers: ["Date", "Account", "Voucher type", "Voucher id", "Debit", "Credit"],
          rows: entries.map((e) => [
            String(e.posting_date || ""),
            String(e.account_name || e.account_id || ""),
            String(e.voucher_type || ""),
            String(e.voucher_id || ""),
            formatMoney(e.debit, currency),
            formatMoney(e.credit, currency),
          ]),
        },
      ],
    };
  }

  return (
    <ReportPageShell
      title="Trial balance / GL"
      subtitle="Accounting statements for Admin and Accountant roles"
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
            filename={`ledger-${fromDate}-${toDate}`}
            buildDoc={buildExportDoc}
          />
        </div>
      }
    >
      {!canView ? (
        <p className="text-sm text-muted-foreground">
          Trial balance and general ledger are limited to Hotel Admin and Accountant roles.
        </p>
      ) : (
        <Tabs defaultValue="trial" className="space-y-4">
          <TabsList>
            <TabsTrigger value="trial">Trial balance</TabsTrigger>
            <TabsTrigger value="gl">General ledger</TabsTrigger>
          </TabsList>

          <TabsContent value="trial" className="space-y-4">
            {trialQ.isLoading ? (
              <div className="flex h-40 items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : trialQ.isError ? (
              <Card>
                <CardContent className="p-6 text-sm text-destructive">
                  Could not load trial balance
                  {trialQ.error instanceof Error ? `: ${trialQ.error.message}` : "."}
                </CardContent>
              </Card>
            ) : (
              <>
                <div className="grid gap-4 sm:grid-cols-2">
                  <Kpi
                    label="Total debit"
                    value={formatMoney(trialQ.data?.total_debit, currency)}
                  />
                  <Kpi
                    label="Total credit"
                    value={formatMoney(trialQ.data?.total_credit, currency)}
                  />
                </div>
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">
                      Accounts
                      {trialQ.data?.as_of_date
                        ? ` (as of ${String(trialQ.data.as_of_date)})`
                        : ""}
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="p-0 overflow-x-auto">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Account</TableHead>
                          <TableHead>Type</TableHead>
                          <TableHead className="text-right">Period debit</TableHead>
                          <TableHead className="text-right">Period credit</TableHead>
                          <TableHead className="text-right">Closing debit</TableHead>
                          <TableHead className="text-right">Closing credit</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {accounts.length === 0 ? (
                          <TableRow>
                            <TableCell
                              colSpan={6}
                              className="py-8 text-center text-muted-foreground"
                            >
                              No account activity in this period.
                            </TableCell>
                          </TableRow>
                        ) : (
                          accounts.map((a) => (
                            <TableRow key={a.account_id || a.account_name}>
                              <TableCell>
                                <div className="font-medium">
                                  {a.account_number
                                    ? `${a.account_number} · ${a.account_name}`
                                    : a.account_name || "—"}
                                </div>
                              </TableCell>
                              <TableCell className="capitalize text-muted-foreground">
                                {a.root_type || "—"}
                              </TableCell>
                              <TableCell className="text-right tabular-nums">
                                {formatMoney(a.debit, currency)}
                              </TableCell>
                              <TableCell className="text-right tabular-nums">
                                {formatMoney(a.credit, currency)}
                              </TableCell>
                              <TableCell className="text-right tabular-nums">
                                {formatMoney(a.closing_debit, currency)}
                              </TableCell>
                              <TableCell className="text-right tabular-nums">
                                {formatMoney(a.closing_credit, currency)}
                              </TableCell>
                            </TableRow>
                          ))
                        )}
                      </TableBody>
                    </Table>
                  </CardContent>
                </Card>
              </>
            )}
          </TabsContent>

          <TabsContent value="gl" className="space-y-4">
            {glQ.isLoading ? (
              <div className="flex h-40 items-center justify-center">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : glQ.isError ? (
              <Card>
                <CardContent className="p-6 text-sm text-destructive">
                  Could not load general ledger
                  {glQ.error instanceof Error ? `: ${glQ.error.message}` : "."}
                </CardContent>
              </Card>
            ) : (
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">
                    GL entries
                    {glQ.data?.opening_balance != null
                      ? ` · opening balance ${formatMoney(glQ.data.opening_balance, currency)}`
                      : ""}
                  </CardTitle>
                </CardHeader>
                <CardContent className="p-0 overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Date</TableHead>
                        <TableHead>Account</TableHead>
                        <TableHead>Voucher</TableHead>
                        <TableHead className="text-right">Debit</TableHead>
                        <TableHead className="text-right">Credit</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {entries.length === 0 ? (
                        <TableRow>
                          <TableCell
                            colSpan={5}
                            className="py-8 text-center text-muted-foreground"
                          >
                            No GL entries in this period.
                          </TableCell>
                        </TableRow>
                      ) : (
                        entries.map((e, idx) => (
                          <TableRow key={String(e.id || idx)}>
                            <TableCell>
                              {String(e.posting_date || "—")}
                            </TableCell>
                            <TableCell>
                              {String(e.account_name || e.account_id || "—")}
                            </TableCell>
                            <TableCell className="text-muted-foreground">
                              {String(e.voucher_type || "—")}
                              {e.voucher_id
                                ? ` · ${String(e.voucher_id).slice(0, 8)}`
                                : ""}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {formatMoney(e.debit, currency)}
                            </TableCell>
                            <TableCell className="text-right tabular-nums">
                              {formatMoney(e.credit, currency)}
                            </TableCell>
                          </TableRow>
                        ))
                      )}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            )}
          </TabsContent>
        </Tabs>
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
