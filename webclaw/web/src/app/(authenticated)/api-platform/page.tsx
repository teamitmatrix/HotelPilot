"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Radio, Send, Trash2, Webhook } from "lucide-react";
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
  createPlatformApiKey,
  createPlatformWebhook,
  deletePlatformWebhook,
  getApiPlatformOverview,
  listPlatformApiKeys,
  listPlatformWebhooks,
  listTenants,
  listWebhookDeliveries,
  revokePlatformApiKey,
  testPlatformWebhook,
  updateApiRateLimits,
  updatePlatformApiKey,
  type ApiRateLimitPolicy,
} from "@/lib/admin-api";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";

function formatTs(iso?: string | null) {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function ApiPlatformPage() {
  const queryClient = useQueryClient();
  const { data: overviewData } = useQuery({
    queryKey: ["api-platform-overview"],
    queryFn: getApiPlatformOverview,
  });
  const { data: keys = [], isLoading: keysLoading } = useQuery({
    queryKey: ["api-platform-keys"],
    queryFn: () => listPlatformApiKeys(),
  });
  const { data: webhooks = [], isLoading: hooksLoading } = useQuery({
    queryKey: ["api-platform-webhooks"],
    queryFn: () => listPlatformWebhooks(),
  });
  const { data: deliveries = [] } = useQuery({
    queryKey: ["api-platform-deliveries"],
    queryFn: () => listWebhookDeliveries(),
  });
  const { data: tenants = [] } = useQuery({
    queryKey: ["platform-tenants"],
    queryFn: listTenants,
  });

  const [policy, setPolicy] = useState<ApiRateLimitPolicy | null>(null);
  const [keyName, setKeyName] = useState("");
  const [keyTenant, setKeyTenant] = useState<string>("platform");
  const [keyRate, setKeyRate] = useState("60");
  const [createdSecret, setCreatedSecret] = useState("");
  const [hookName, setHookName] = useState("");
  const [hookUrl, setHookUrl] = useState("");
  const [hookTenant, setHookTenant] = useState<string>("platform");
  const [hookSecret, setHookSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    document.title = "API & Webhooks — HotelPilot Central Admin";
  }, []);

  useEffect(() => {
    if (overviewData?.rate_limits) setPolicy({ ...overviewData.rate_limits });
  }, [overviewData]);

  async function refreshAll() {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["api-platform-overview"] }),
      queryClient.invalidateQueries({ queryKey: ["api-platform-keys"] }),
      queryClient.invalidateQueries({ queryKey: ["api-platform-webhooks"] }),
      queryClient.invalidateQueries({ queryKey: ["api-platform-deliveries"] }),
    ]);
  }

  async function savePolicy() {
    if (!policy) return;
    setBusy(true);
    setMessage("");
    try {
      const saved = await updateApiRateLimits(policy);
      setPolicy(saved);
      setMessage("Rate limit policy saved");
      await queryClient.invalidateQueries({ queryKey: ["api-platform-overview"] });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function createKey() {
    if (!keyName.trim()) {
      setMessage("Key name required");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const res = await createPlatformApiKey({
        name: keyName.trim(),
        tenant_id: keyTenant === "platform" ? null : keyTenant,
        rate_limit_per_min: Math.max(1, Number(keyRate) || 60),
      });
      setCreatedSecret(res.secret);
      setKeyName("");
      await refreshAll();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Create key failed");
    } finally {
      setBusy(false);
    }
  }

  async function revokeKey(id: string) {
    setBusy(true);
    try {
      await revokePlatformApiKey(id);
      await refreshAll();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Revoke failed");
    } finally {
      setBusy(false);
    }
  }

  async function createHook() {
    if (!hookName.trim() || !hookUrl.trim()) {
      setMessage("Webhook name and URL required");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const res = await createPlatformWebhook({
        name: hookName.trim(),
        url: hookUrl.trim(),
        tenant_id: hookTenant === "platform" ? null : hookTenant,
      });
      setHookSecret(res.secret);
      setHookName("");
      setHookUrl("");
      await refreshAll();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Create webhook failed");
    } finally {
      setBusy(false);
    }
  }

  async function testHook(id: string) {
    setBusy(true);
    setMessage("");
    try {
      const d = await testPlatformWebhook(id);
      setMessage(
        d.ok
          ? `Test delivery ok (HTTP ${d.http_status ?? "—"})`
          : `Test delivery failed: ${d.error || d.status}`,
      );
      await refreshAll();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Test failed");
    } finally {
      setBusy(false);
    }
  }

  const overview = overviewData?.overview;

  return (
    <div className="flex flex-1 flex-col">
      <header className="flex h-14 items-center gap-3 border-b px-4">
        <SidebarTrigger />
        <div className="flex-1">
          <h2 className="text-2xl font-bold tracking-tight">API & webhooks</h2>
          <p className="text-sm text-muted-foreground">
            Keys, rate limits, outbound endpoints, and delivery logs.
          </p>
        </div>
        <ConnectionStatus />
        <ThemeToggle />
        <ChatToggleButton />
      </header>

      <div className="flex-1 space-y-4 p-4">
        {message && (
          <p className="text-sm text-muted-foreground" role="status">
            {message}
          </p>
        )}

        <div className="grid gap-4 md:grid-cols-5">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-muted-foreground">Active keys</CardTitle>
            </CardHeader>
            <CardContent className="text-2xl font-bold">
              {overview?.keys_active ?? 0}
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-muted-foreground">Total keys</CardTitle>
            </CardHeader>
            <CardContent className="text-2xl font-bold">
              {overview?.keys_total ?? 0}
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-muted-foreground">Active webhooks</CardTitle>
            </CardHeader>
            <CardContent className="text-2xl font-bold">
              {overview?.webhooks_active ?? 0}
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-muted-foreground">Failing webhooks</CardTitle>
            </CardHeader>
            <CardContent className="text-2xl font-bold text-destructive">
              {overview?.webhooks_failing ?? 0}
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-muted-foreground">Failed today</CardTitle>
            </CardHeader>
            <CardContent className="text-2xl font-bold">
              {overview?.deliveries_failed_today ?? 0}
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Radio className="h-4 w-4" />
              Default rate limits
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {policy && (
              <div className="grid gap-3 md:grid-cols-4 items-end">
                <div className="space-y-1">
                  <Label>Default / min</Label>
                  <Input
                    type="number"
                    value={policy.default_per_min}
                    onChange={(e) =>
                      setPolicy({ ...policy, default_per_min: Number(e.target.value) || 60 })
                    }
                  />
                </div>
                <div className="space-y-1">
                  <Label>Burst / min</Label>
                  <Input
                    type="number"
                    value={policy.burst_per_min}
                    onChange={(e) =>
                      setPolicy({ ...policy, burst_per_min: Number(e.target.value) || 120 })
                    }
                  />
                </div>
                <div className="space-y-1">
                  <Label>Platform / min</Label>
                  <Input
                    type="number"
                    value={policy.platform_per_min}
                    onChange={(e) =>
                      setPolicy({ ...policy, platform_per_min: Number(e.target.value) || 300 })
                    }
                  />
                </div>
                <Button disabled={busy} onClick={savePolicy}>
                  Save policy
                </Button>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <KeyRound className="h-4 w-4" />
              API keys
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-3 items-end">
              <div className="min-w-[180px] space-y-1">
                <Label>Name</Label>
                <Input value={keyName} onChange={(e) => setKeyName(e.target.value)} />
              </div>
              <div className="min-w-[200px] space-y-1">
                <Label>Scope</Label>
                <Select value={keyTenant} onValueChange={setKeyTenant}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="platform">Platform-wide</SelectItem>
                    {tenants.map((t) => (
                      <SelectItem key={String(t.id)} value={String(t.id)}>
                        {t.hotel_name || t.id}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="w-28 space-y-1">
                <Label>Rate / min</Label>
                <Input value={keyRate} onChange={(e) => setKeyRate(e.target.value)} />
              </div>
              <Button disabled={busy} onClick={createKey}>
                Create key
              </Button>
            </div>
            {createdSecret && (
              <div className="rounded-md border bg-muted/40 p-3 text-sm">
                <p className="font-medium mb-1">Copy this key now — it won&apos;t be shown again</p>
                <code className="break-all text-xs">{createdSecret}</code>
              </div>
            )}
            {keysLoading ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Prefix</TableHead>
                    <TableHead>Hotel</TableHead>
                    <TableHead>Rate</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {keys.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="text-muted-foreground">
                        No API keys yet
                      </TableCell>
                    </TableRow>
                  ) : (
                    keys.map((k) => (
                      <TableRow key={k.id}>
                        <TableCell className="font-medium">{k.name}</TableCell>
                        <TableCell>
                          <code className="text-xs">{k.key_prefix}…</code>
                        </TableCell>
                        <TableCell>{k.hotel_name || (k.tenant_id ? k.tenant_id : "Platform")}</TableCell>
                        <TableCell>{k.rate_limit_per_min}/min</TableCell>
                        <TableCell>
                          {k.revoked_at ? (
                            <Badge variant="secondary">Revoked</Badge>
                          ) : k.is_active ? (
                            <Badge>Active</Badge>
                          ) : (
                            <Badge variant="outline">Inactive</Badge>
                          )}
                        </TableCell>
                        <TableCell className="space-x-1">
                          {!k.revoked_at && (
                            <>
                              <Button
                                size="sm"
                                variant="outline"
                                disabled={busy}
                                onClick={() =>
                                  updatePlatformApiKey(k.id, {
                                    rate_limit_per_min: k.rate_limit_per_min,
                                    is_active: !k.is_active,
                                  }).then(refreshAll)
                                }
                              >
                                {k.is_active ? "Disable" : "Enable"}
                              </Button>
                              <Button
                                size="sm"
                                variant="destructive"
                                disabled={busy}
                                onClick={() => revokeKey(k.id)}
                              >
                                Revoke
                              </Button>
                            </>
                          )}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Webhook className="h-4 w-4" />
              Webhook endpoints
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-3 items-end">
              <div className="min-w-[160px] space-y-1">
                <Label>Name</Label>
                <Input value={hookName} onChange={(e) => setHookName(e.target.value)} />
              </div>
              <div className="min-w-[260px] flex-1 space-y-1">
                <Label>URL</Label>
                <Input
                  placeholder="https://example.com/hooks/hotelpilot"
                  value={hookUrl}
                  onChange={(e) => setHookUrl(e.target.value)}
                />
              </div>
              <div className="min-w-[180px] space-y-1">
                <Label>Hotel</Label>
                <Select value={hookTenant} onValueChange={setHookTenant}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="platform">Platform-wide</SelectItem>
                    {tenants.map((t) => (
                      <SelectItem key={String(t.id)} value={String(t.id)}>
                        {t.hotel_name || t.id}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button disabled={busy} onClick={createHook}>
                Add webhook
              </Button>
            </div>
            {hookSecret && (
              <div className="rounded-md border bg-muted/40 p-3 text-sm">
                <p className="font-medium mb-1">Signing secret (copy once)</p>
                <code className="break-all text-xs">{hookSecret}</code>
              </div>
            )}
            {hooksLoading ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>URL</TableHead>
                    <TableHead>Failures</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {webhooks.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-muted-foreground">
                        No webhooks configured
                      </TableCell>
                    </TableRow>
                  ) : (
                    webhooks.map((w) => (
                      <TableRow key={w.id}>
                        <TableCell className="font-medium">{w.name}</TableCell>
                        <TableCell className="max-w-[240px] truncate text-xs">
                          {w.url}
                        </TableCell>
                        <TableCell>
                          {w.failure_count > 0 ? (
                            <Badge variant="destructive">{w.failure_count}</Badge>
                          ) : (
                            "0"
                          )}
                        </TableCell>
                        <TableCell>
                          <Badge variant={w.is_active ? "default" : "secondary"}>
                            {w.is_active ? "Active" : "Off"}
                          </Badge>
                        </TableCell>
                        <TableCell className="space-x-1">
                          <Button
                            size="sm"
                            variant="outline"
                            disabled={busy}
                            onClick={() => testHook(w.id)}
                          >
                            <Send className="h-3 w-3 mr-1" />
                            Test
                          </Button>
                          <Button
                            size="icon"
                            variant="ghost"
                            disabled={busy}
                            onClick={() =>
                              deletePlatformWebhook(w.id).then(refreshAll).catch((e) =>
                                setMessage(e instanceof Error ? e.message : "Delete failed"),
                              )
                            }
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Delivery log</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>When</TableHead>
                  <TableHead>Event</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>HTTP</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Error</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {deliveries.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="text-muted-foreground">
                      No deliveries yet — run a webhook test
                    </TableCell>
                  </TableRow>
                ) : (
                  deliveries.slice(0, 50).map((d) => (
                    <TableRow key={d.id}>
                      <TableCell>{formatTs(d.created_at)}</TableCell>
                      <TableCell>{d.event_type}</TableCell>
                      <TableCell>
                        <Badge
                          variant={
                            d.status === "success"
                              ? "default"
                              : d.status === "failed"
                                ? "destructive"
                                : "secondary"
                          }
                        >
                          {d.status}
                        </Badge>
                      </TableCell>
                      <TableCell>{d.http_status ?? "—"}</TableCell>
                      <TableCell>
                        {d.duration_ms != null ? `${d.duration_ms}ms` : "—"}
                      </TableCell>
                      <TableCell className="max-w-[200px] truncate text-xs text-muted-foreground">
                        {d.error || "—"}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
