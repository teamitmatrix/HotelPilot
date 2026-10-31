"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Building2,
  Loader2,
  MapPin,
  Plus,
  RefreshCw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";
import { TimezoneSelect } from "@/components/timezone-select";
import { CurrencySelect } from "@/components/currency-select";
import { CountrySelect } from "@/components/country-select";
import { useAuth } from "@/lib/auth";
import { useHotelPermissions } from "@/lib/rbac";
import { useToast } from "@/components/toast-provider";
import { setActivePropertyId } from "@/lib/active-tenant";
import {
  createOwnerProperty,
  fetchOwnerProperties,
  updateOwnerPropertyStatus,
} from "@/lib/owner-properties";

type FormState = {
  name: string;
  address: string;
  timezone: string;
  currency: string;
  country: string;
  tax_template: string;
};

const EMPTY_FORM: FormState = {
  name: "",
  address: "",
  timezone: "UTC",
  currency: "USD",
  country: "United States",
  tax_template: "us_gaap",
};

export default function HotelBranchesPage() {
  const { user, switchTenant, refreshUser } = useAuth();
  const perms = useHotelPermissions(user);
  const { showToast } = useToast();
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);

  const propsQ = useQuery({
    queryKey: ["owner-properties", user?.user_id],
    queryFn: fetchOwnerProperties,
    enabled: Boolean(user && perms.canManageHotel),
  });

  useEffect(() => {
    if (searchParams.get("new") === "1" && perms.canManageHotel) {
      setOpen(true);
    }
  }, [searchParams, perms.canManageHotel]);

  const createMut = useMutation({
    mutationFn: createOwnerProperty,
    onSuccess: async (data) => {
      if (data.status !== "ok") {
        showToast({
          type: "error",
          message: (data.message as string) || "Could not register branch",
        });
        return;
      }
      const propertyId = String(data.property_id || "");
      showToast({
        type: "success",
        message: `Branch “${form.name}” registered`,
      });
      setOpen(false);
      setForm(EMPTY_FORM);
      await refreshUser();
      await queryClient.invalidateQueries({ queryKey: ["owner-properties"] });
      await queryClient.invalidateQueries();
      if (propertyId) {
        setActivePropertyId(propertyId);
        await switchTenant(propertyId);
        router.push("/app/hotel/dashboard");
      }
    },
    onError: (err: Error) => {
      showToast({ type: "error", message: err.message || "Network error" });
    },
  });

  const statusMut = useMutation({
    mutationFn: ({
      propertyId,
      status,
    }: {
      propertyId: string;
      status: "active" | "closed";
    }) => updateOwnerPropertyStatus(propertyId, status),
    onSuccess: async (data, vars) => {
      if (data.status !== "ok") {
        showToast({
          type: "error",
          message: (data.message as string) || "Could not update branch status",
        });
        return;
      }
      showToast({
        type: "success",
        message:
          vars.status === "closed"
            ? "Branch closed for website and OTA bookings"
            : "Branch reopened for bookings",
      });
      await queryClient.invalidateQueries({ queryKey: ["owner-properties"] });
    },
    onError: (err: Error) => {
      showToast({ type: "error", message: err.message || "Network error" });
    },
  });

  function statusBadge(status?: string | null) {
    const value = (status || "active").toLowerCase();
    if (value === "closed") {
      return <Badge variant="secondary">Closed</Badge>;
    }
    if (value === "suspended") {
      return <Badge variant="warning">Suspended</Badge>;
    }
    if (value === "cancelled") {
      return <Badge variant="outline">Cancelled</Badge>;
    }
    return <Badge variant="default">Active</Badge>;
  }

  if (!perms.canManageHotel) {
    return (
      <div className="flex min-h-[40vh] flex-col items-center justify-center gap-2 p-6 text-center">
        <p className="text-sm text-muted-foreground">
          Only hotel owners and admins can manage branches.
        </p>
        <Button variant="outline" onClick={() => router.push("/app/hotel/dashboard")}>
          Back to dashboard
        </Button>
      </div>
    );
  }

  const properties = propsQ.data?.properties || [];

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Branches</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h2 className="text-2xl font-bold tracking-tight">Hotel branches</h2>
            <p className="text-muted-foreground">
              Register locations under your group. Each branch gets its own rooms,
              folios, and chart of accounts.
            </p>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => propsQ.refetch()}
              disabled={propsQ.isFetching}
            >
              {propsQ.isFetching ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              <span className="ml-1.5">Refresh</span>
            </Button>
            <Button size="sm" onClick={() => setOpen(true)}>
              <Plus className="mr-1.5 h-4 w-4" />
              Register branch
            </Button>
          </div>
        </div>

        {propsQ.data?.group_id ? (
          <Badge variant="secondary" className="gap-1 font-normal">
            <Building2 className="h-3 w-3" />
            Group {propsQ.data.group_id.slice(0, 8)}…
          </Badge>
        ) : null}

        <Card>
          <CardHeader>
            <CardTitle>Locations</CardTitle>
            <CardDescription>
              {properties.length === 0
                ? "No branches listed yet — register your first additional location."
                : `${properties.length} branch${properties.length === 1 ? "" : "es"} in your group. Close a branch to stop website and OTA bookings.`}
            </CardDescription>
          </CardHeader>
          <CardContent className="overflow-x-auto">
            {propsQ.isLoading ? (
              <div className="flex items-center gap-2 py-8 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                Loading branches…
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Timezone</TableHead>
                    <TableHead>Currency</TableHead>
                    <TableHead>Address</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {properties.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={6} className="text-muted-foreground">
                        Your current hotel will appear here after the group is
                        provisioned. Click <strong>Register branch</strong> to add
                        another location, or open the property switcher and choose
                        All Properties once.
                      </TableCell>
                    </TableRow>
                  ) : (
                    properties.map((p) => (
                      <TableRow key={p.property_id}>
                        <TableCell className="font-medium">
                          {p.hotel_name || "Branch"}
                        </TableCell>
                        <TableCell>{p.timezone || "—"}</TableCell>
                        <TableCell>{p.currency || "—"}</TableCell>
                        <TableCell className="max-w-[220px] truncate text-muted-foreground">
                          {p.address || "—"}
                        </TableCell>
                        <TableCell>{statusBadge(p.status)}</TableCell>
                        <TableCell className="text-right">
                          <div className="flex flex-wrap items-center justify-end gap-1">
                            {(p.status || "active").toLowerCase() === "closed" ||
                            p.accepting_bookings === false ? (
                              <Button
                                variant="outline"
                                size="sm"
                                disabled={statusMut.isPending || p.tenant_status === "suspended"}
                                onClick={() =>
                                  statusMut.mutate({
                                    propertyId: p.property_id,
                                    status: "active",
                                  })
                                }
                              >
                                Reopen
                              </Button>
                            ) : (p.status || "active").toLowerCase() === "active" ? (
                              <Button
                                variant="outline"
                                size="sm"
                                disabled={statusMut.isPending}
                                onClick={() => {
                                  if (
                                    !confirm(
                                      `Close “${p.hotel_name || "this branch"}” for new bookings?\n\nWebsite and OTA channels will stop selling this property. Existing stays and front-desk operations continue.`,
                                    )
                                  ) {
                                    return;
                                  }
                                  statusMut.mutate({
                                    propertyId: p.property_id,
                                    status: "closed",
                                  });
                                }}
                              >
                                Close for booking
                              </Button>
                            ) : null}
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={async () => {
                                const err = await switchTenant(p.property_id);
                                if (err) {
                                  showToast({ type: "error", message: err });
                                  return;
                                }
                                setActivePropertyId(p.property_id);
                                await queryClient.invalidateQueries();
                                router.push("/app/hotel/dashboard");
                              }}
                            >
                              Open
                            </Button>
                          </div>
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

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Register a new branch</DialogTitle>
            <DialogDescription>
              Creates a property under your hotel group, seeds the chart of
              accounts, and initializes hospitality for that location.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-2">
            <div className="grid gap-2">
              <Label htmlFor="branch-name">Branch name</Label>
              <Input
                id="branch-name"
                placeholder="IMTA hotels — Islamabad"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
              <p className="text-xs text-muted-foreground">
                Use a unique name per location (brand + city/area). Reusing an
                existing company name is auto-disambiguated in the ledger.
              </p>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="branch-address">Address</Label>
              <div className="relative">
                <MapPin className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  id="branch-address"
                  className="pl-8"
                  placeholder="123 Main St"
                  value={form.address}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, address: e.target.value }))
                  }
                />
              </div>
            </div>
            <div className="grid gap-2 sm:grid-cols-2 sm:gap-3">
              <div className="grid gap-2">
                <Label>Timezone</Label>
                <TimezoneSelect
                  value={form.timezone}
                  onValueChange={(timezone) =>
                    setForm((f) => ({ ...f, timezone }))
                  }
                />
              </div>
              <div className="grid gap-2">
                <Label>Currency</Label>
                <CurrencySelect
                  value={form.currency}
                  onValueChange={(currency) =>
                    setForm((f) => ({ ...f, currency }))
                  }
                />
              </div>
            </div>
            <div className="grid gap-2">
              <Label>Country</Label>
              <CountrySelect
                value={form.country}
                onValueChange={(country) => setForm((f) => ({ ...f, country }))}
              />
            </div>
            <div className="grid gap-2">
              <Label htmlFor="tax-template">Tax / CoA template</Label>
              <Input
                id="tax-template"
                value={form.tax_template}
                onChange={(e) =>
                  setForm((f) => ({ ...f, tax_template: e.target.value }))
                }
              />
              <p className="text-xs text-muted-foreground">
                Default chart template for the new branch ledger (e.g. us_gaap).
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              disabled={!form.name.trim() || createMut.isPending}
              onClick={() =>
                createMut.mutate({
                  name: form.name.trim(),
                  address: form.address.trim() || undefined,
                  timezone: form.timezone,
                  currency: form.currency,
                  country: form.country,
                  tax_template: form.tax_template || "us_gaap",
                })
              }
            >
              {createMut.isPending ? (
                <>
                  <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                  Provisioning…
                </>
              ) : (
                "Create branch"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
