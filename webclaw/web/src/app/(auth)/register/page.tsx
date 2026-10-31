"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
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
import { Badge } from "@/components/ui/badge";
import { API_BASE } from "@/lib/api";
import { useAuth, type User } from "@/lib/auth";
import { postLoginPath } from "@/lib/auth-routing";
import { CountrySelect } from "@/components/country-select";
import { CurrencySelect } from "@/components/currency-select";
import { TimezoneSelect } from "@/components/timezone-select";
import {
  ArrowLeft,
  ArrowRight,
  Building2,
  Check,
  Loader2,
  Lock,
  Sparkles,
} from "lucide-react";

const STEPS = [
  { id: 1, label: "Hotel info" },
  { id: 2, label: "Plan" },
  { id: 3, label: "Admin setup" },
];

const FALLBACK_PLANS = [
  {
    code: "starter",
    name: "Starter",
    monthly_price: "49",
    max_rooms: 25,
    billing_cycle: "monthly",
    blurb: "Front desk, housekeeping, and AI chat for a single property.",
    highlights: ["Front desk & reservations", "Housekeeping", "AI chat"],
  },
  {
    code: "business",
    name: "Business",
    monthly_price: "149",
    max_rooms: 120,
    billing_cycle: "monthly",
    blurb: "Reports, accounting, POS, and channel distribution.",
    highlights: ["Advanced reports", "Accounting", "Channel manager", "POS / F&B"],
  },
  {
    code: "enterprise",
    name: "Enterprise",
    monthly_price: "399",
    max_rooms: 0,
    billing_cycle: "annual",
    blurb: "Unlimited rooms, multi-property, API, and custom branding.",
    highlights: ["Unlimited rooms", "Multi-property", "API access", "Custom branding"],
  },
];

interface PublicPlan {
  code: string;
  name: string;
  monthly_price: string;
  max_rooms: number;
  billing_cycle: string;
  unlimited_rooms?: boolean;
  blurb?: string;
  highlights?: string[];
  features?: Record<string, boolean>;
}

function money(n: string): string {
  const num = Number(n);
  if (Number.isNaN(num)) return n;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(num);
}

function passwordHint(password: string): string | null {
  if (!password) return null;
  if (password.length < 8) return "At least 8 characters";
  if (!/[A-Z]/.test(password)) return "Include an uppercase letter";
  if (!/[a-z]/.test(password)) return "Include a lowercase letter";
  if (!/\d/.test(password)) return "Include a number";
  return null;
}

export default function RegisterHotelPage() {
  const router = useRouter();
  const { completeAuth, user, loading } = useAuth();
  const [step, setStep] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [plans, setPlans] = useState<PublicPlan[]>(FALLBACK_PLANS);
  const [form, setForm] = useState({
    hotel_name: "",
    country: "United States",
    currency: "USD",
    timezone: "UTC",
    plan_code: "starter",
    admin_email: "",
    password: "",
    confirm_password: "",
  });

  useEffect(() => {
    if (!loading && user) router.replace(postLoginPath(user));
  }, [user, loading, router]);

  useEffect(() => {
    fetch(`${API_BASE}/auth/plans`)
      .then((r) => r.json())
      .then((data) => {
        if (data.status === "ok" && Array.isArray(data.plans) && data.plans.length) {
          setPlans(
            data.plans.map((p: PublicPlan) => {
              const fallback = FALLBACK_PLANS.find((f) => f.code === p.code);
              return {
                ...fallback,
                ...p,
                highlights:
                  fallback?.highlights ||
                  Object.entries(p.features || {})
                    .filter(([, on]) => on)
                    .map(([key]) => key.replace(/_/g, " ")),
              };
            }),
          );
        }
      })
      .catch(() => {});
  }, []);

  function set<K extends keyof typeof form>(key: K, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
    setError("");
  }

  const selectedPlan = useMemo(
    () => plans.find((p) => p.code === form.plan_code) || plans[0],
    [plans, form.plan_code],
  );

  function canContinue(): boolean {
    if (step === 1) return form.hotel_name.trim().length >= 2;
    if (step === 2) return Boolean(form.plan_code);
    return (
      form.admin_email.includes("@") &&
      !passwordHint(form.password) &&
      form.password === form.confirm_password
    );
  }

  async function handleSubmit() {
    if (form.password !== form.confirm_password) {
      setError("Passwords do not match");
      return;
    }
    const pw = passwordHint(form.password);
    if (pw) {
      setError(pw);
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      const res = await fetch(`${API_BASE}/auth/register-hotel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          hotel_name: form.hotel_name.trim(),
          admin_email: form.admin_email.trim(),
          password: form.password,
          country: form.country,
          currency: form.currency,
          timezone: form.timezone,
          plan_code: form.plan_code,
          subscription_tier: form.plan_code,
        }),
      });
      const data = await res.json();
      if (!res.ok || data.status !== "ok") {
        setError(data.message || "Registration failed");
        setSubmitting(false);
        return;
      }
      if (data.access_token && data.user) {
        completeAuth(data.access_token as string, data.user as User);
        if (typeof data.checkout_url === "string" && data.checkout_url) {
          window.location.href = data.checkout_url;
          return;
        }
        router.push(postLoginPath(data.user as User));
      }
    } catch {
      setError("Could not reach the server");
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

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <Card className="w-full max-w-2xl">
        <CardHeader className="text-center">
          <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-xl bg-primary/10">
            <Building2 className="h-6 w-6 text-primary" />
          </div>
          <CardTitle className="text-2xl">Register with HotelPilot</CardTitle>
          <CardDescription>
            A private workspace for this property. Data never mixes with another hotel.
          </CardDescription>
          <ol className="mt-4 flex items-center justify-center gap-2">
            {STEPS.map((item, index) => (
              <li key={item.id} className="flex items-center gap-2">
                <span
                  className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold ${
                    step > item.id
                      ? "bg-primary text-primary-foreground"
                      : step === item.id
                        ? "bg-primary text-primary-foreground"
                        : "bg-muted text-muted-foreground"
                  }`}
                >
                  {step > item.id ? <Check className="h-3.5 w-3.5" /> : item.id}
                </span>
                <span className={`text-xs ${step === item.id ? "font-medium" : "text-muted-foreground"}`}>
                  {item.label}
                </span>
                {index < STEPS.length - 1 && (
                  <span className="mx-1 h-px w-6 bg-border" />
                )}
              </li>
            ))}
          </ol>
        </CardHeader>
        <CardContent className="space-y-5">
          {step === 1 && (
            <div className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="hotel_name">Hotel name</Label>
                <Input
                  id="hotel_name"
                  value={form.hotel_name}
                  onChange={(e) => set("hotel_name", e.target.value)}
                  placeholder="Harbor Inn"
                  required
                  minLength={2}
                  autoFocus
                />
              </div>
              <div className="grid gap-4 sm:grid-cols-3">
                <div className="space-y-2">
                  <Label htmlFor="country">Country</Label>
                  <CountrySelect
                    id="country"
                    value={form.country}
                    onValueChange={(v) => set("country", v)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="currency">Currency</Label>
                  <CurrencySelect
                    id="currency"
                    value={form.currency}
                    onValueChange={(v) => set("currency", v)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="timezone">Timezone</Label>
                  <TimezoneSelect
                    id="timezone"
                    value={form.timezone}
                    onValueChange={(v) => set("timezone", v)}
                  />
                </div>
              </div>
            </div>
          )}

          {step === 2 && (
            <div className="grid gap-3 sm:grid-cols-3">
              {plans.map((plan) => {
                const selected = form.plan_code === plan.code;
                const unlimited = plan.unlimited_rooms || plan.max_rooms <= 0;
                return (
                  <button
                    key={plan.code}
                    type="button"
                    onClick={() => set("plan_code", plan.code)}
                    className={`rounded-xl border p-4 text-left transition-colors ${
                      selected
                        ? "border-primary bg-primary/5 ring-1 ring-primary"
                        : "hover:bg-accent"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <p className="font-semibold">{plan.name}</p>
                      {plan.code === "business" && (
                        <Badge variant="secondary" className="gap-1">
                          <Sparkles className="h-3 w-3" />
                          Popular
                        </Badge>
                      )}
                    </div>
                    <p className="mt-2 text-2xl font-bold">
                      {money(plan.monthly_price)}
                      <span className="text-sm font-normal text-muted-foreground">/mo</span>
                    </p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {unlimited ? "Unlimited rooms" : `Up to ${plan.max_rooms} rooms`}
                    </p>
                    <p className="mt-2 text-xs text-muted-foreground">{plan.blurb}</p>
                    <ul className="mt-3 space-y-1 text-xs">
                      {(plan.highlights || []).slice(0, 4).map((h) => (
                        <li key={h} className="flex items-center gap-1.5">
                          <Check className="h-3 w-3 text-primary" />
                          {h}
                        </li>
                      ))}
                    </ul>
                  </button>
                );
              })}
            </div>
          )}

          {step === 3 && (
            <div className="space-y-4">
              <div className="rounded-lg border bg-muted/40 p-3 text-sm">
                <p className="font-medium">{form.hotel_name}</p>
                <p className="text-muted-foreground">
                  {form.country} · {form.currency} · {selectedPlan?.name} plan
                </p>
              </div>
              <div className="space-y-2">
                <Label htmlFor="admin_email">Admin email</Label>
                <Input
                  id="admin_email"
                  type="email"
                  autoComplete="email"
                  value={form.admin_email}
                  onChange={(e) => set("admin_email", e.target.value)}
                  placeholder="you@hotel.com"
                  required
                  autoFocus
                />
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label htmlFor="password">Password</Label>
                  <Input
                    id="password"
                    type="password"
                    autoComplete="new-password"
                    value={form.password}
                    onChange={(e) => set("password", e.target.value)}
                    required
                    minLength={8}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="confirm_password">Confirm password</Label>
                  <Input
                    id="confirm_password"
                    type="password"
                    autoComplete="new-password"
                    value={form.confirm_password}
                    onChange={(e) => set("confirm_password", e.target.value)}
                    required
                  />
                </div>
              </div>
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Lock className="h-3.5 w-3.5" />
                8+ characters with upper, lower, and a number. This account is the Hotel Owner.
              </p>
            </div>
          )}

          {error && <p className="text-sm text-destructive">{error}</p>}
          {passwordHint(form.password) && step === 3 && form.password && (
            <p className="text-xs text-muted-foreground">{passwordHint(form.password)}</p>
          )}

          <div className="flex items-center justify-between gap-3 pt-1">
            {step > 1 ? (
              <Button type="button" variant="outline" onClick={() => setStep((s) => s - 1)}>
                <ArrowLeft className="h-4 w-4" />
                Back
              </Button>
            ) : (
              <span />
            )}
            {step < 3 ? (
              <Button type="button" disabled={!canContinue()} onClick={() => setStep((s) => s + 1)}>
                Continue
                <ArrowRight className="h-4 w-4" />
              </Button>
            ) : (
              <Button type="button" disabled={!canContinue() || submitting} onClick={handleSubmit}>
                {submitting ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Creating hotel…
                  </>
                ) : (
                  "Create hotel & sign in"
                )}
              </Button>
            )}
          </div>
          <p className="text-center text-sm text-muted-foreground">
            Already registered?{" "}
            <Link href="/login" className="text-primary underline">
              Sign in
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
