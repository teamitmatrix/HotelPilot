"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Paintbrush } from "lucide-react";
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
  getWhiteLabel,
  listTenants,
  listWhiteLabel,
  upsertWhiteLabel,
  verifyWhiteLabelDns,
  type TenantBranding,
} from "@/lib/admin-api";
import { Badge } from "@/components/ui/badge";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";

const emptyForm: Omit<TenantBranding, "tenant_id"> = {
  brand_name: "",
  logo_url: "",
  primary_color: "",
  custom_domain: "",
  email_from_name: "",
  email_from_address: "",
  support_url: "",
};

export default function WhiteLabelPage() {
  const queryClient = useQueryClient();
  const { data: brands = [], isLoading } = useQuery({
    queryKey: ["white-label"],
    queryFn: listWhiteLabel,
  });
  const { data: tenants = [] } = useQuery({
    queryKey: ["platform-tenants"],
    queryFn: listTenants,
  });

  const [tenantId, setTenantId] = useState("");
  const [form, setForm] = useState(emptyForm);
  const [dnsInfo, setDnsInfo] = useState<{
    dns_status?: string | null;
    dns_verified?: boolean;
    dns_target?: string | null;
    dns_checked_at?: string | null;
  }>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    document.title = "White-label — HotelPilot Central Admin";
  }, []);

  useEffect(() => {
    if (!tenantId) {
      setForm(emptyForm);
      return;
    }
    getWhiteLabel(tenantId)
      .then((b) => {
        setForm({
          brand_name: b.brand_name || "",
          logo_url: b.logo_url || "",
          primary_color: b.primary_color || "",
          custom_domain: b.custom_domain || "",
          email_from_name: b.email_from_name || "",
          email_from_address: b.email_from_address || "",
          support_url: b.support_url || "",
        });
        setDnsInfo({
          dns_status: b.dns_status,
          dns_verified: b.dns_verified,
          dns_target: b.dns_target,
          dns_checked_at: b.dns_checked_at,
        });
      })
      .catch(() => {
        setForm(emptyForm);
        setDnsInfo({});
      });
  }, [tenantId]);

  async function verifyDns() {
    if (!tenantId) return;
    setBusy(true);
    setMessage("");
    try {
      const res = await verifyWhiteLabelDns(tenantId);
      setDnsInfo({
        dns_status: res.dns.dns_status,
        dns_verified: res.dns.dns_verified,
        dns_target: res.dns.target,
        dns_checked_at: res.dns.dns_checked_at,
      });
      setMessage(res.dns.detail || res.dns.dns_status);
      await queryClient.invalidateQueries({ queryKey: ["white-label"] });
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "DNS verify failed");
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!tenantId) {
      setMessage("Select a hotel");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      await upsertWhiteLabel(tenantId, form);
      await queryClient.invalidateQueries({ queryKey: ["white-label"] });
      setMessage("Saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Save failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-1 flex-col">
      <header className="flex h-14 items-center gap-3 border-b px-4">
        <SidebarTrigger />
        <div className="flex-1">
          <h2 className="text-2xl font-bold tracking-tight">White-label</h2>
          <p className="text-sm text-muted-foreground">
            Brand name, domain, and email sender per hotel.
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
              <Paintbrush className="h-4 w-4" />
              Configure branding
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

            <div className="grid gap-3 md:grid-cols-2">
              {(
                [
                  ["brand_name", "Brand name"],
                  ["logo_url", "Logo URL"],
                  ["primary_color", "Primary color"],
                  ["custom_domain", "Custom domain"],
                  ["email_from_name", "Email from name"],
                  ["email_from_address", "Email from address"],
                  ["support_url", "Support URL"],
                ] as const
              ).map(([key, label]) => (
                <div key={key} className="space-y-1">
                  <Label>{label}</Label>
                  <Input
                    value={(form[key] as string) || ""}
                    onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
                  />
                </div>
              ))}
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <Button disabled={busy || !tenantId} onClick={save}>
                Save branding
              </Button>
              <Button
                variant="outline"
                disabled={busy || !tenantId || !form.custom_domain}
                onClick={verifyDns}
              >
                Verify DNS
              </Button>
              {dnsInfo.dns_status && (
                <Badge variant={dnsInfo.dns_verified ? "default" : "secondary"}>
                  DNS: {dnsInfo.dns_status}
                  {dnsInfo.dns_target ? ` → ${dnsInfo.dns_target}` : ""}
                </Badge>
              )}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Configured hotels</CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Hotel</TableHead>
                    <TableHead>Brand</TableHead>
                    <TableHead>Domain</TableHead>
                    <TableHead>DNS</TableHead>
                    <TableHead>Email from</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {brands.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={5} className="text-muted-foreground">
                        No branding configured
                      </TableCell>
                    </TableRow>
                  ) : (
                    brands.map((b) => (
                      <TableRow
                        key={b.tenant_id}
                        className="cursor-pointer"
                        onClick={() => setTenantId(b.tenant_id)}
                      >
                        <TableCell>{b.hotel_name || b.tenant_id}</TableCell>
                        <TableCell>{b.brand_name || "—"}</TableCell>
                        <TableCell>{b.custom_domain || "—"}</TableCell>
                        <TableCell>
                          {b.custom_domain
                            ? b.dns_verified
                              ? "Verified"
                              : b.dns_status || "Pending"
                            : "—"}
                        </TableCell>
                        <TableCell>
                          {b.email_from_address
                            ? `${b.email_from_name || ""} <${b.email_from_address}>`
                            : "—"}
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
