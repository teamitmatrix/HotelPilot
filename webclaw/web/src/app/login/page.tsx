"use client";

import { Suspense, useState, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth, type User } from "@/lib/auth";
import { postLoginPath } from "@/lib/auth-routing";
import { API_BASE } from "@/lib/api";
import { Building2 } from "lucide-react";

const APP_TITLE = process.env.NEXT_PUBLIC_OCUI_TITLE || "HotelPilot Central Admin";

type PublicBranding = {
  brand_name?: string | null;
  logo_url?: string | null;
  primary_color?: string | null;
  support_url?: string | null;
};

function LoginForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [branding, setBranding] = useState<PublicBranding | null>(null);
  const { login, user, loading, completeAuth } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();

  useEffect(() => {
    const tid = searchParams.get("tenant") || searchParams.get("tenant_id") || "";
    if (tid) setTenantId(tid);

    const ssoToken = searchParams.get("access_token");
    if (ssoToken && searchParams.get("sso") === "1") {
      (async () => {
        try {
          const res = await fetch(`${API_BASE}/auth/me`, {
            headers: { Authorization: `Bearer ${ssoToken}` },
            credentials: "include",
          });
          const data = await res.json();
          if (res.ok && data.status === "ok" && data.user) {
            completeAuth(ssoToken, data.user as User);
            return;
          }
          setError("SSO session could not be established");
        } catch {
          setError("SSO session could not be established");
        }
      })();
    }
  }, [searchParams, completeAuth]);

  useEffect(() => {
    const tid = searchParams.get("tenant") || searchParams.get("tenant_id") || "";
    const qs = tid
      ? `?tenant_id=${encodeURIComponent(tid)}`
      : `?host=${encodeURIComponent(typeof window !== "undefined" ? window.location.host : "")}`;
    fetch(`${API_BASE}/admin/public/branding${qs}`)
      .then((r) => r.json())
      .then((data) => {
        if (data?.branding) setBranding(data.branding as PublicBranding);
      })
      .catch(() => {});
  }, [searchParams]);

  useEffect(() => {
    if (!loading && user) router.replace(postLoginPath(user));
  }, [user, loading, router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    const err = await login(email, password);
    setSubmitting(false);
    if (err) setError(err);
  }

  async function handleSso() {
    const tid = tenantId.trim();
    if (!tid) {
      setError("Enter a hotel tenant ID to sign in with SSO");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      const res = await fetch(`${API_BASE}/auth/sso/${encodeURIComponent(tid)}/start`);
      const data = await res.json();
      if (!res.ok || !data.authorize_url) {
        setError(data.message || "SSO is not available for this hotel");
        setSubmitting(false);
        return;
      }
      window.location.href = data.authorize_url;
    } catch {
      setError("Could not start SSO");
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
      </div>
    );
  }

  const title = branding?.brand_name || APP_TITLE;
  const accent = branding?.primary_color || undefined;

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <div
            className="mx-auto mb-2 flex h-12 w-12 items-center justify-center overflow-hidden rounded-xl bg-primary/10"
            style={accent ? { backgroundColor: `${accent}22` } : undefined}
          >
            {branding?.logo_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={branding.logo_url} alt="" className="h-10 w-10 object-contain" />
            ) : (
              <Building2
                className="h-6 w-6 text-primary"
                style={accent ? { color: accent } : undefined}
              />
            )}
          </div>
          <CardTitle className="text-2xl" style={accent ? { color: accent } : undefined}>
            {title}
          </CardTitle>
          <CardDescription>Sign in to your hotel workspace</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="email">Email</Label>
              <Input
                id="email"
                type="email"
                placeholder="you@hotel.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                autoFocus
                autoComplete="email"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">Password</Label>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                autoComplete="current-password"
              />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" className="w-full" disabled={submitting}>
              {submitting ? "Signing in..." : "Sign in"}
            </Button>
            <div className="space-y-2 border-t pt-4">
              <Label htmlFor="tenant">Hotel ID (SSO)</Label>
              <Input
                id="tenant"
                value={tenantId}
                onChange={(e) => setTenantId(e.target.value)}
                placeholder="Tenant UUID"
              />
              <Button
                type="button"
                variant="outline"
                className="w-full"
                disabled={submitting}
                onClick={handleSso}
              >
                Sign in with SSO
              </Button>
            </div>
            <p className="text-center text-sm text-muted-foreground">
              New property?{" "}
              <a href="/register" className="text-primary underline">
                Register your hotel
              </a>
            </p>
            {branding?.support_url && (
              <p className="text-center text-xs text-muted-foreground">
                <a href={branding.support_url} className="underline">
                  Support
                </a>
              </p>
            )}
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="flex min-h-screen items-center justify-center">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-primary border-t-transparent" />
        </div>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
