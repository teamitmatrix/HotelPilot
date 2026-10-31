"use client";

import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { Handshake } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
  getPartnerAnalytics,
  getPartnerMe,
  getPartnerTenants,
} from "@/lib/admin-api";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";
import { Badge } from "@/components/ui/badge";

export default function PartnerConsolePage() {
  const { data: me, isLoading: meLoading, error: meError } = useQuery({
    queryKey: ["partner-me"],
    queryFn: getPartnerMe,
  });
  const { data: tenants = [], isLoading: tenantsLoading } = useQuery({
    queryKey: ["partner-tenants"],
    queryFn: getPartnerTenants,
  });
  const { data: analytics } = useQuery({
    queryKey: ["partner-analytics"],
    queryFn: getPartnerAnalytics,
  });

  useEffect(() => {
    document.title = "Partner console — HotelPilot";
  }, []);

  return (
    <div className="flex flex-1 flex-col">
      <header className="flex h-14 items-center gap-3 border-b px-4">
        <SidebarTrigger />
        <div className="flex-1">
          <h2 className="text-2xl font-bold tracking-tight">Partner console</h2>
          <p className="text-sm text-muted-foreground">
            Hotels linked to your reseller portfolio.
          </p>
        </div>
        <ConnectionStatus />
        <ThemeToggle />
        <ChatToggleButton />
      </header>

      <div className="flex-1 space-y-4 p-4">
        {meError ? (
          <p className="text-sm text-destructive">
            {(meError as Error).message || "Partner console unavailable"}
          </p>
        ) : null}

        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="flex items-center gap-2 text-base">
                <Handshake className="h-4 w-4" />
                {meLoading ? "…" : me?.partner.name || "Partner"}
              </CardTitle>
            </CardHeader>
            <CardContent className="text-sm text-muted-foreground">
              {me?.partner.contact_email}
              <div className="mt-2 text-2xl font-bold text-foreground">
                {me?.stats.commission_percent ?? "—"}%
              </div>
              <p className="text-xs">Commission share</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Linked hotels</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{me?.stats.tenant_count ?? 0}</div>
              <p className="text-xs text-muted-foreground">
                {me?.stats.active_hotels ?? 0} active
              </p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">By plan</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-2 text-sm">
              {Object.entries(analytics?.by_plan || {}).length === 0 ? (
                <span className="text-muted-foreground">No data</span>
              ) : (
                Object.entries(analytics?.by_plan || {}).map(([k, v]) => (
                  <Badge key={k} variant="secondary">
                    {k}: {v}
                  </Badge>
                ))
              )}
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Portfolio hotels</CardTitle>
          </CardHeader>
          <CardContent>
            {tenantsLoading ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Hotel</TableHead>
                    <TableHead>Plan</TableHead>
                    <TableHead>Country</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {tenants.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={4} className="text-muted-foreground">
                        No hotels linked yet
                      </TableCell>
                    </TableRow>
                  ) : (
                    tenants.map((t) => (
                      <TableRow key={String(t.id)}>
                        <TableCell>{t.hotel_name || t.id}</TableCell>
                        <TableCell>{t.plan_code || "—"}</TableCell>
                        <TableCell>{t.country || "—"}</TableCell>
                        <TableCell>
                          <Badge variant="outline">{t.status || "—"}</Badge>
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
