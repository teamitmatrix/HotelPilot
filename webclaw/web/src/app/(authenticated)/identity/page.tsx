"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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
  getIdentity,
  listIdentityConfigs,
  listTenants,
  regenerateScimToken,
  upsertIdentity,
  type TenantIdentity,
} from "@/lib/admin-api";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";

const empty: Omit<TenantIdentity, "tenant_id"> = {
  sso_enabled: false,
  sso_protocol: "oidc",
  issuer_url: "",
  client_id: "",
  client_secret: "",
  metadata_url: "",
  scim_enabled: false,
  scim_bearer_token: "",
  scim_base_url: "",
  sso_start_url: "",
};

export default function IdentityPage() {
  const queryClient = useQueryClient();
  const { data: configs = [], isLoading } = useQuery({
    queryKey: ["identity-configs"],
    queryFn: listIdentityConfigs,
  });
  const { data: tenants = [] } = useQuery({
    queryKey: ["platform-tenants"],
    queryFn: listTenants,
  });

  const [tenantId, setTenantId] = useState("");
  const [form, setForm] = useState(empty);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    document.title = "SSO / SCIM — HotelPilot Central Admin";
  }, []);

  useEffect(() => {
    if (!tenantId) {
      setForm(empty);
      return;
    }
    getIdentity(tenantId)
      .then((i) =>
        setForm({
          sso_enabled: i.sso_enabled,
          sso_protocol: i.sso_protocol || "oidc",
          issuer_url: i.issuer_url || "",
          client_id: i.client_id || "",
          client_secret: i.client_secret || "",
          metadata_url: i.metadata_url || "",
          scim_enabled: i.scim_enabled,
          scim_bearer_token: i.scim_bearer_token || "",
          scim_base_url: i.scim_base_url || "",
          sso_start_url: i.sso_start_url || "",
        }),
      )
      .catch(() => setForm(empty));
  }, [tenantId]);

  async function save() {
    if (!tenantId) {
      setMessage("Select a hotel");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const saved = await upsertIdentity(tenantId, form);
      setForm({
        ...form,
        client_secret: saved.client_secret,
        scim_bearer_token: saved.scim_bearer_token || "",
        scim_base_url: saved.scim_base_url || form.scim_base_url,
        sso_start_url: saved.sso_start_url || form.sso_start_url,
      });
      await queryClient.invalidateQueries({ queryKey: ["identity-configs"] });
      setMessage(
        saved.scim_token_revealed
          ? "Saved — copy the SCIM token now; it will be masked later."
          : "Saved",
      );
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  async function regenToken() {
    if (!tenantId) return;
    setBusy(true);
    setMessage("");
    try {
      const saved = await regenerateScimToken(tenantId);
      setForm((f) => ({
        ...f,
        scim_bearer_token: saved.scim_bearer_token || "",
        scim_base_url: saved.scim_base_url || f.scim_base_url,
      }));
      setMessage("New SCIM token generated — copy it now.");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Regenerate failed");
    } finally {
      setBusy(false);
    }
  }

  const hotelName = (id: string) =>
    tenants.find((t) => String(t.id) === id)?.hotel_name || id;

  return (
    <div className="flex flex-1 flex-col">
      <header className="flex h-14 items-center gap-3 border-b px-4">
        <SidebarTrigger />
        <div className="flex-1">
          <h2 className="text-2xl font-bold tracking-tight">Enterprise SSO / SCIM</h2>
          <p className="text-sm text-muted-foreground">
            OIDC/SAML config and SCIM provisioning tokens per hotel.
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

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <KeyRound className="h-4 w-4" />
              Identity config
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="max-w-md space-y-1">
              <Label>Hotel</Label>
              <Select value={tenantId} onValueChange={setTenantId}>
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

            <div className="flex flex-wrap gap-6">
              <div className="flex items-center gap-2">
                <Switch
                  checked={form.sso_enabled}
                  onCheckedChange={(v) => setForm((f) => ({ ...f, sso_enabled: v }))}
                />
                <Label>SSO enabled</Label>
              </div>
              <div className="flex items-center gap-2">
                <Switch
                  checked={form.scim_enabled}
                  onCheckedChange={(v) => setForm((f) => ({ ...f, scim_enabled: v }))}
                />
                <Label>SCIM enabled</Label>
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1">
                <Label>Protocol</Label>
                <Select
                  value={form.sso_protocol}
                  onValueChange={(v) => setForm((f) => ({ ...f, sso_protocol: v }))}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="oidc">OIDC</SelectItem>
                    <SelectItem value="saml">SAML</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1">
                <Label>Issuer URL</Label>
                <Input
                  value={form.issuer_url}
                  onChange={(e) => setForm((f) => ({ ...f, issuer_url: e.target.value }))}
                />
              </div>
              <div className="space-y-1">
                <Label>Client ID</Label>
                <Input
                  value={form.client_id}
                  onChange={(e) => setForm((f) => ({ ...f, client_id: e.target.value }))}
                />
              </div>
              <div className="space-y-1">
                <Label>Client secret</Label>
                <Input
                  type="password"
                  value={form.client_secret}
                  onChange={(e) => setForm((f) => ({ ...f, client_secret: e.target.value }))}
                  placeholder="Leave masked value to keep existing"
                />
              </div>
              <div className="space-y-1 md:col-span-2">
                <Label>Metadata URL</Label>
                <Input
                  value={form.metadata_url}
                  onChange={(e) => setForm((f) => ({ ...f, metadata_url: e.target.value }))}
                />
              </div>
              {form.scim_enabled && (
                <>
                  <div className="space-y-1 md:col-span-2">
                    <Label>SCIM base URL</Label>
                    <Input
                      value={
                        form.scim_base_url ||
                        (tenantId ? `/api/v1/scim/v2/${tenantId}` : "")
                      }
                      readOnly
                    />
                  </div>
                  {form.scim_bearer_token && (
                    <div className="space-y-1 md:col-span-2">
                      <Label>SCIM bearer token</Label>
                      <Input value={form.scim_bearer_token} readOnly />
                    </div>
                  )}
                </>
              )}
              {form.sso_enabled && tenantId && (
                <div className="space-y-1 md:col-span-2">
                  <Label>SSO start URL</Label>
                  <Input
                    value={form.sso_start_url || `/api/v1/auth/sso/${tenantId}/start`}
                    readOnly
                  />
                </div>
              )}
            </div>

            <div className="flex flex-wrap gap-2">
              <Button disabled={busy || !tenantId} onClick={save}>
                Save identity
              </Button>
              {form.scim_enabled && (
                <Button
                  variant="outline"
                  disabled={busy || !tenantId}
                  onClick={regenToken}
                >
                  Regenerate SCIM token
                </Button>
              )}
              {form.sso_enabled && tenantId && (
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() =>
                    window.open(
                      `/api/v1/auth/sso/${tenantId}/start?format=redirect`,
                      "_blank",
                    )
                  }
                >
                  Test SSO
                </Button>
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Configured tenants</CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Hotel</TableHead>
                    <TableHead>SSO</TableHead>
                    <TableHead>Protocol</TableHead>
                    <TableHead>SCIM</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {configs.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={4} className="text-muted-foreground">
                        No SSO configs
                      </TableCell>
                    </TableRow>
                  ) : (
                    configs.map((c) => (
                      <TableRow
                        key={c.tenant_id}
                        className="cursor-pointer"
                        onClick={() => setTenantId(c.tenant_id)}
                      >
                        <TableCell>{hotelName(c.tenant_id)}</TableCell>
                        <TableCell>
                          <Badge variant={c.sso_enabled ? "default" : "secondary"}>
                            {c.sso_enabled ? "On" : "Off"}
                          </Badge>
                        </TableCell>
                        <TableCell>{c.sso_protocol}</TableCell>
                        <TableCell>
                          <Badge variant={c.scim_enabled ? "default" : "secondary"}>
                            {c.scim_enabled ? "On" : "Off"}
                          </Badge>
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
