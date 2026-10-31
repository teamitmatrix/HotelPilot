"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Flag, Plus, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Switch } from "@/components/ui/switch";
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
  clearFeatureFlagTenantOverride,
  listFeatureFlags,
  listTenants,
  setFeatureFlagTenantOverride,
  updateFeatureFlag,
  type FeatureFlag,
} from "@/lib/admin-api";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";

export default function FeatureFlagsPage() {
  const queryClient = useQueryClient();
  const { data: flags = [], isLoading, error } = useQuery({
    queryKey: ["feature-flags"],
    queryFn: listFeatureFlags,
  });
  const { data: tenants = [] } = useQuery({
    queryKey: ["platform-tenants"],
    queryFn: listTenants,
  });

  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [overrideTenant, setOverrideTenant] = useState<Record<string, string>>({});
  const [overrideEnabled, setOverrideEnabled] = useState<Record<string, boolean>>({});
  const [message, setMessage] = useState("");

  useEffect(() => {
    document.title = "Feature Flags — HotelPilot Central Admin";
  }, []);

  async function toggleGlobal(flag: FeatureFlag, enabled: boolean) {
    setBusyKey(flag.key);
    setMessage("");
    try {
      await updateFeatureFlag(flag.key, { enabled_global: enabled });
      await queryClient.invalidateQueries({ queryKey: ["feature-flags"] });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Update failed");
    } finally {
      setBusyKey(null);
    }
  }

  async function addOverride(flag: FeatureFlag) {
    const tenantId = overrideTenant[flag.key];
    if (!tenantId) {
      setMessage("Select a hotel for the override");
      return;
    }
    setBusyKey(flag.key);
    setMessage("");
    try {
      await setFeatureFlagTenantOverride(
        flag.key,
        tenantId,
        overrideEnabled[flag.key] ?? true,
      );
      await queryClient.invalidateQueries({ queryKey: ["feature-flags"] });
      setOverrideTenant((s) => ({ ...s, [flag.key]: "" }));
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Override failed");
    } finally {
      setBusyKey(null);
    }
  }

  async function removeOverride(flagKey: string, tenantId: string) {
    setBusyKey(flagKey);
    setMessage("");
    try {
      await clearFeatureFlagTenantOverride(flagKey, tenantId);
      await queryClient.invalidateQueries({ queryKey: ["feature-flags"] });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Clear failed");
    } finally {
      setBusyKey(null);
    }
  }

  const tenantName = (id: string) =>
    tenants.find((t) => String(t.id) === id)?.hotel_name || id;

  return (
    <div className="flex flex-1 flex-col">
      <header className="flex h-14 items-center gap-3 border-b px-4">
        <SidebarTrigger />
        <div className="flex-1">
          <h2 className="text-2xl font-bold tracking-tight">Feature flags</h2>
          <p className="text-sm text-muted-foreground">
            Global defaults with per-hotel runtime overrides.
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
            {(error as Error).message || "Failed to load flags"}
          </p>
        )}

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Flag className="h-4 w-4" />
              Runtime toggles
            </CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : flags.length === 0 ? (
              <p className="text-sm text-muted-foreground">No flags seeded yet.</p>
            ) : (
              <div className="space-y-6">
                {flags.map((flag) => (
                  <div key={flag.key} className="rounded-lg border p-4 space-y-3">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="flex items-center gap-2">
                          <p className="font-medium">{flag.label}</p>
                          <Badge variant="outline">{flag.key}</Badge>
                        </div>
                        <p className="text-sm text-muted-foreground mt-1">
                          {flag.description}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        <Label htmlFor={`g-${flag.key}`} className="text-sm">
                          Global
                        </Label>
                        <Switch
                          id={`g-${flag.key}`}
                          checked={flag.enabled_global}
                          disabled={busyKey === flag.key}
                          onCheckedChange={(v) => toggleGlobal(flag, v)}
                        />
                      </div>
                    </div>

                    <div>
                      <p className="text-xs font-medium uppercase text-muted-foreground mb-2">
                        Tenant overrides
                      </p>
                      {flag.tenant_overrides.length === 0 ? (
                        <p className="text-sm text-muted-foreground mb-2">None</p>
                      ) : (
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Hotel</TableHead>
                              <TableHead>Enabled</TableHead>
                              <TableHead className="w-[80px]" />
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {flag.tenant_overrides.map((o) => (
                              <TableRow key={o.tenant_id}>
                                <TableCell>{tenantName(o.tenant_id)}</TableCell>
                                <TableCell>
                                  <Badge variant={o.enabled ? "default" : "secondary"}>
                                    {o.enabled ? "On" : "Off"}
                                  </Badge>
                                </TableCell>
                                <TableCell>
                                  <Button
                                    size="icon"
                                    variant="ghost"
                                    disabled={busyKey === flag.key}
                                    onClick={() => removeOverride(flag.key, o.tenant_id)}
                                  >
                                    <Trash2 className="h-4 w-4" />
                                  </Button>
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      )}

                      <div className="mt-3 flex flex-wrap items-end gap-2">
                        <div className="min-w-[200px] space-y-1">
                          <Label>Hotel</Label>
                          <Select
                            value={overrideTenant[flag.key] || ""}
                            onValueChange={(v) =>
                              setOverrideTenant((s) => ({ ...s, [flag.key]: v }))
                            }
                          >
                            <SelectTrigger>
                              <SelectValue placeholder="Select hotel" />
                            </SelectTrigger>
                            <SelectContent>
                              {tenants.map((t) => (
                                <SelectItem key={String(t.id)} value={String(t.id)}>
                                  {t.hotel_name || t.id}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                        <div className="flex items-center gap-2 pb-2">
                          <Switch
                            checked={overrideEnabled[flag.key] ?? true}
                            onCheckedChange={(v) =>
                              setOverrideEnabled((s) => ({ ...s, [flag.key]: v }))
                            }
                          />
                          <span className="text-sm">Enabled</span>
                        </div>
                        <Button
                          size="sm"
                          disabled={busyKey === flag.key}
                          onClick={() => addOverride(flag)}
                        >
                          <Plus className="h-4 w-4 mr-1" />
                          Override
                        </Button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
