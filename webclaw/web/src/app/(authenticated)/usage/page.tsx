"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Gauge, Plus, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import {
  assignTenantAddon,
  getTenantUsage,
  getUsageOverview,
  removeTenantAddon,
} from "@/lib/admin-api";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";

export default function UsagePage() {
  const queryClient = useQueryClient();
  const { data, isLoading, error } = useQuery({
    queryKey: ["usage-overview"],
    queryFn: getUsageOverview,
  });

  const [selectedTenant, setSelectedTenant] = useState("");
  const [addonId, setAddonId] = useState("");
  const [qty, setQty] = useState("1");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const { data: detail } = useQuery({
    queryKey: ["tenant-usage", selectedTenant],
    queryFn: () => getTenantUsage(selectedTenant),
    enabled: !!selectedTenant,
  });

  useEffect(() => {
    document.title = "Usage & Add-ons — HotelPilot Central Admin";
  }, []);

  async function assign() {
    if (!selectedTenant || !addonId) {
      setMessage("Select a hotel and add-on");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      await assignTenantAddon(selectedTenant, addonId, Math.max(1, Number(qty) || 1));
      await queryClient.invalidateQueries({ queryKey: ["usage-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["tenant-usage", selectedTenant] });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Assign failed");
    } finally {
      setBusy(false);
    }
  }

  async function remove(assignmentId: string) {
    if (!selectedTenant) return;
    setBusy(true);
    setMessage("");
    try {
      await removeTenantAddon(selectedTenant, assignmentId);
      await queryClient.invalidateQueries({ queryKey: ["usage-overview"] });
      await queryClient.invalidateQueries({ queryKey: ["tenant-usage", selectedTenant] });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Remove failed");
    } finally {
      setBusy(false);
    }
  }

  const usage = data?.usage || [];
  const addons = data?.addons || [];

  return (
    <div className="flex flex-1 flex-col">
      <header className="flex h-14 items-center gap-3 border-b px-4">
        <SidebarTrigger />
        <div className="flex-1">
          <h2 className="text-2xl font-bold tracking-tight">Usage & add-ons</h2>
          <p className="text-sm text-muted-foreground">
            Rooms, staff, API, AI, and storage meters vs plan limits and add-ons.
          </p>
        </div>
        <ConnectionStatus />
        <ThemeToggle />
        <ChatToggleButton />
      </header>

      <div className="flex-1 space-y-4 p-4">
        {message && (
          <p className="text-sm text-destructive" role="alert">
            {message}
          </p>
        )}
        {error && (
          <p className="text-sm text-destructive">
            {(error as Error).message || "Failed to load usage"}
          </p>
        )}

        <div className="grid gap-4 md:grid-cols-3">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Hotels tracked
              </CardTitle>
            </CardHeader>
            <CardContent className="text-2xl font-bold">{usage.length}</CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Over limit
              </CardTitle>
            </CardHeader>
            <CardContent className="text-2xl font-bold text-destructive">
              {data?.over_limit_count ?? 0}
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium text-muted-foreground">
                Catalog add-ons
              </CardTitle>
            </CardHeader>
            <CardContent className="text-2xl font-bold">{addons.length}</CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Gauge className="h-4 w-4" />
              Usage vs limits
            </CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Hotel</TableHead>
                    <TableHead>Plan</TableHead>
                    <TableHead>Rooms</TableHead>
                    <TableHead>Staff</TableHead>
                    <TableHead>API</TableHead>
                    <TableHead>AI tokens</TableHead>
                    <TableHead>Storage</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {usage.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={8} className="text-muted-foreground">
                        No tenants
                      </TableCell>
                    </TableRow>
                  ) : (
                    usage.map((u) => {
                      const over =
                        u.rooms_over_limit ||
                        u.staff_over_limit ||
                        u.api_calls_over_limit ||
                        u.ai_tokens_over_limit ||
                        u.storage_gb_over_limit;
                      const fmt = (used?: number, cap?: number | null) =>
                        `${used ?? 0}${cap != null ? ` / ${cap}` : " / ∞"}`;
                      return (
                        <TableRow
                          key={u.tenant_id}
                          className="cursor-pointer"
                          onClick={() => setSelectedTenant(u.tenant_id)}
                        >
                          <TableCell className="font-medium">
                            {u.hotel_name || u.tenant_id}
                          </TableCell>
                          <TableCell>{u.plan_code || "—"}</TableCell>
                          <TableCell>
                            {fmt(u.rooms, u.rooms_cap)}
                            {u.addon_rooms > 0 && (
                              <span className="text-muted-foreground text-xs ml-1">
                                (+{u.addon_rooms})
                              </span>
                            )}
                          </TableCell>
                          <TableCell>
                            {fmt(u.staff, u.staff_cap)}
                            {u.addon_staff > 0 && (
                              <span className="text-muted-foreground text-xs ml-1">
                                (+{u.addon_staff})
                              </span>
                            )}
                          </TableCell>
                          <TableCell className="text-xs">
                            {fmt(u.api_calls, u.api_calls_cap ?? null)}
                          </TableCell>
                          <TableCell className="text-xs">
                            {fmt(u.ai_tokens, u.ai_tokens_cap ?? null)}
                          </TableCell>
                          <TableCell className="text-xs">
                            {fmt(u.storage_gb, u.storage_gb_cap ?? null)} GB
                          </TableCell>
                          <TableCell>
                            {over ? (
                              <Badge variant="destructive">Over limit</Badge>
                            ) : (
                              <Badge variant="secondary">OK</Badge>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Assign add-on</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-3 items-end">
              <div className="min-w-[200px] space-y-1">
                <Label>Hotel</Label>
                <Select value={selectedTenant} onValueChange={setSelectedTenant}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select hotel" />
                  </SelectTrigger>
                  <SelectContent>
                    {usage.map((u) => (
                      <SelectItem key={u.tenant_id} value={u.tenant_id}>
                        {u.hotel_name || u.tenant_id}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="min-w-[200px] space-y-1">
                <Label>Add-on</Label>
                <Select value={addonId} onValueChange={setAddonId}>
                  <SelectTrigger>
                    <SelectValue placeholder="Select add-on" />
                  </SelectTrigger>
                  <SelectContent>
                    {addons
                      .filter((a) => a.is_active)
                      .map((a) => (
                        <SelectItem key={a.id} value={a.id}>
                          {a.name} (+{a.included_units} {a.metric})
                        </SelectItem>
                      ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="w-24 space-y-1">
                <Label>Qty</Label>
                <Input value={qty} onChange={(e) => setQty(e.target.value)} />
              </div>
              <Button disabled={busy} onClick={assign}>
                <Plus className="h-4 w-4 mr-1" />
                Assign
              </Button>
            </div>

            {detail && (
              <div>
                <p className="text-sm font-medium mb-2">
                  Assigned for {detail.usage.hotel_name || selectedTenant}
                </p>
                {detail.assigned_addons.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No add-ons</p>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Add-on</TableHead>
                        <TableHead>Metric</TableHead>
                        <TableHead>Qty</TableHead>
                        <TableHead>Units</TableHead>
                        <TableHead className="w-[60px]" />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {detail.assigned_addons.map((a) => (
                        <TableRow key={a.id}>
                          <TableCell>{a.name}</TableCell>
                          <TableCell>{a.metric}</TableCell>
                          <TableCell>{a.quantity}</TableCell>
                          <TableCell>{a.included_units * a.quantity}</TableCell>
                          <TableCell>
                            <Button
                              size="icon"
                              variant="ghost"
                              disabled={busy}
                              onClick={() => remove(a.id)}
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
