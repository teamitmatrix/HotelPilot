"use client";

import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  getIntegrationsHealth,
  getPlatformSettings,
  testIntegration,
  updatePlatformSettings,
  type PlatformSettings,
} from "@/lib/admin-api";
import { Badge } from "@/components/ui/badge";

const CURRENCY_OPTIONS = ["USD", "EUR", "GBP", "AED", "INR", "AUD", "CAD", "JPY", "SGD"];
const LANGUAGE_OPTIONS = [
  { code: "en", label: "English" },
  { code: "es", label: "Spanish" },
  { code: "fr", label: "French" },
  { code: "de", label: "German" },
  { code: "ar", label: "Arabic" },
  { code: "hi", label: "Hindi" },
];

export function PlatformGeneralSettings() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["platform-settings"],
    queryFn: getPlatformSettings,
  });
  const [form, setForm] = useState<PlatformSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (data) setForm(structuredClone(data));
  }, [data]);

  async function save() {
    if (!form) return;
    setSaving(true);
    setMessage("");
    try {
      const saved = await updatePlatformSettings({
        default_currency: form.default_currency,
        supported_currencies: form.supported_currencies,
        tax_policy: form.tax_policy,
        supported_languages: form.supported_languages,
        default_language: form.default_language,
      });
      setForm(saved);
      await queryClient.invalidateQueries({ queryKey: ["platform-settings"] });
      setMessage("Settings saved.");
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  if (isLoading || !form) {
    return <p className="text-sm text-muted-foreground">Loading platform settings…</p>;
  }

  function toggleCurrency(code: string) {
    setForm((f) => {
      if (!f) return f;
      const set = new Set(f.supported_currencies);
      if (set.has(code)) {
        if (code === f.default_currency) return f;
        set.delete(code);
      } else set.add(code);
      return { ...f, supported_currencies: Array.from(set) };
    });
  }

  function toggleLanguage(code: string) {
    setForm((f) => {
      if (!f) return f;
      const set = new Set(f.supported_languages);
      if (set.has(code)) {
        if (code === f.default_language) return f;
        set.delete(code);
      } else set.add(code);
      return { ...f, supported_languages: Array.from(set) };
    });
  }

  return (
    <div className="space-y-6">
      {message && (
        <div className="rounded-md border border-primary/20 bg-primary/5 p-3 text-sm">{message}</div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Global currency</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5 max-w-xs">
            <Label>Default currency</Label>
            <select
              className="flex h-9 w-full rounded-md border bg-background px-3 text-sm"
              value={form.default_currency}
              onChange={(e) =>
                setForm((f) => (f ? { ...f, default_currency: e.target.value } : f))
              }
            >
              {CURRENCY_OPTIONS.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </div>
          <div>
            <Label className="mb-2 block">Supported currencies</Label>
            <div className="flex flex-wrap gap-2">
              {CURRENCY_OPTIONS.map((c) => {
                const on = form.supported_currencies.includes(c);
                return (
                  <Button
                    key={c}
                    type="button"
                    size="sm"
                    variant={on ? "default" : "outline"}
                    onClick={() => toggleCurrency(c)}
                  >
                    {c}
                  </Button>
                );
              })}
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Platform tax policy</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label>Tax label</Label>
            <Input
              value={form.tax_policy.tax_label}
              onChange={(e) =>
                setForm((f) =>
                  f
                    ? {
                        ...f,
                        tax_policy: { ...f.tax_policy, tax_label: e.target.value },
                      }
                    : f,
                )
              }
            />
          </div>
          <div className="space-y-1.5">
            <Label>Default rate (%)</Label>
            <Input
              type="number"
              min={0}
              step={0.01}
              value={form.tax_policy.default_rate_percent}
              onChange={(e) =>
                setForm((f) =>
                  f
                    ? {
                        ...f,
                        tax_policy: {
                          ...f.tax_policy,
                          default_rate_percent: Number(e.target.value) || 0,
                        },
                      }
                    : f,
                )
              }
            />
          </div>
          <div className="flex items-center justify-between rounded-md border px-3 py-2">
            <div>
              <p className="text-sm font-medium">Inclusive pricing</p>
              <p className="text-xs text-muted-foreground">Prices include tax by default</p>
            </div>
            <Switch
              checked={form.tax_policy.inclusive_pricing}
              onCheckedChange={(v) =>
                setForm((f) =>
                  f
                    ? { ...f, tax_policy: { ...f.tax_policy, inclusive_pricing: v } }
                    : f,
                )
              }
            />
          </div>
          <div className="flex items-center justify-between rounded-md border px-3 py-2">
            <div>
              <p className="text-sm font-medium">Require tax ID</p>
              <p className="text-xs text-muted-foreground">Hotels must provide a tax ID</p>
            </div>
            <Switch
              checked={form.tax_policy.require_tax_id}
              onCheckedChange={(v) =>
                setForm((f) =>
                  f
                    ? { ...f, tax_policy: { ...f.tax_policy, require_tax_id: v } }
                    : f,
                )
              }
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Supported languages</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5 max-w-xs">
            <Label>Default language</Label>
            <select
              className="flex h-9 w-full rounded-md border bg-background px-3 text-sm"
              value={form.default_language}
              onChange={(e) =>
                setForm((f) => (f ? { ...f, default_language: e.target.value } : f))
              }
            >
              {LANGUAGE_OPTIONS.map((l) => (
                <option key={l.code} value={l.code}>
                  {l.label}
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-wrap gap-2">
            {LANGUAGE_OPTIONS.map((l) => {
              const on = form.supported_languages.includes(l.code);
              return (
                <Button
                  key={l.code}
                  type="button"
                  size="sm"
                  variant={on ? "default" : "outline"}
                  onClick={() => toggleLanguage(l.code)}
                >
                  {l.label}
                </Button>
              );
            })}
          </div>
        </CardContent>
      </Card>

      <Button onClick={save} disabled={saving}>
        {saving ? "Saving…" : "Save platform settings"}
      </Button>
    </div>
  );
}

export function PlatformIntegrationsSettings() {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["platform-settings"],
    queryFn: getPlatformSettings,
  });
  const { data: health, refetch: refetchHealth } = useQuery({
    queryKey: ["integrations-health"],
    queryFn: getIntegrationsHealth,
  });
  const [form, setForm] = useState<PlatformSettings | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState<string | null>(null);
  const [testTo, setTestTo] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (data) setForm(structuredClone(data));
  }, [data]);

  async function runTest(provider: string) {
    setTesting(provider);
    setMessage("");
    try {
      const result = await testIntegration(provider, testTo || undefined);
      setMessage(
        result.ok
          ? `${provider} test ok${result.detail ? `: ${result.detail}` : ""}`
          : `${provider} test failed: ${result.error || result.detail || result.status || "unknown"}`,
      );
      await refetchHealth();
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Test failed");
    } finally {
      setTesting(null);
    }
  }

  function patchSmtp(patch: Partial<NonNullable<PlatformSettings["integrations"]["smtp"]>>) {
    setForm((f) => {
      if (!f) return f;
      const base = {
        host: "",
        port: "587",
        username: "",
        password: "",
        from_email: "",
        from_name: "HotelPilot",
        use_tls: true,
        ...(f.integrations.smtp || {}),
      };
      return {
        ...f,
        integrations: {
          ...f.integrations,
          smtp: { ...base, ...patch },
        },
      };
    });
  }

  async function save() {
    if (!form) return;
    setSaving(true);
    setMessage("");
    try {
      const saved = await updatePlatformSettings({ integrations: form.integrations });
      setForm(saved);
      await queryClient.invalidateQueries({ queryKey: ["platform-settings"] });
      setMessage("Integration credentials saved.");
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  if (isLoading || !form) {
    return <p className="text-sm text-muted-foreground">Loading integrations…</p>;
  }

  const integ = form.integrations;

  const statusBadge = (status?: string) => {
    const s = (status || "unknown").toLowerCase();
    const variant =
      s === "ok" || s === "healthy" ? "default" : s === "unconfigured" ? "secondary" : "destructive";
    return <Badge variant={variant}>{s}</Badge>;
  };

  return (
    <div className="space-y-6">
      {message && (
        <div className="rounded-md border border-primary/20 bg-primary/5 p-3 text-sm">{message}</div>
      )}
      <p className="text-sm text-muted-foreground">
        Secrets are masked after save. Leave a masked value unchanged to keep the existing key.
      </p>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Delivery health</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap gap-3">
            {(
              [
                ["sendgrid", "sendgrid"],
                ["smtp", "smtp"],
                ["twilio", "twilio_sms"],
                ["stripe", "stripe"],
              ] as const
            ).map(([label, key]) => {
              const p = health?.providers?.[key];
              return (
                <div
                  key={key}
                  className="flex min-w-[140px] items-center justify-between gap-2 rounded-md border px-3 py-2 text-sm"
                >
                  <span className="capitalize">{label}</span>
                  {statusBadge(p?.status)}
                </div>
              );
            })}
          </div>
          <div className="flex flex-wrap items-end gap-2">
            <div className="space-y-1">
              <Label>Test recipient</Label>
              <Input
                placeholder="email or +E.164 phone"
                value={testTo}
                onChange={(e) => setTestTo(e.target.value)}
                className="w-[220px]"
              />
            </div>
            {(["sendgrid", "smtp", "twilio", "stripe"] as const).map((p) => (
              <Button
                key={p}
                variant="outline"
                size="sm"
                disabled={!!testing}
                onClick={() => runTest(p)}
              >
                {testing === p ? "Testing…" : `Test ${p}`}
              </Button>
            ))}
          </div>
          {(health?.recent_deliveries?.length || 0) > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-medium text-muted-foreground">Recent deliveries</p>
              <ul className="max-h-40 space-y-1 overflow-auto text-xs text-muted-foreground">
                {health!.recent_deliveries.slice(0, 10).map((d) => (
                  <li key={d.id}>
                    [{d.created_at || "—"}] {d.channel}/{d.trigger_key} → {d.recipient} —{" "}
                    {d.status}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">SendGrid (Email)</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5 sm:col-span-2">
            <Label>API key</Label>
            <Input
              type="password"
              autoComplete="off"
              value={integ.sendgrid.api_key}
              onChange={(e) =>
                setForm((f) =>
                  f
                    ? {
                        ...f,
                        integrations: {
                          ...f.integrations,
                          sendgrid: { ...f.integrations.sendgrid, api_key: e.target.value },
                        },
                      }
                    : f,
                )
              }
            />
          </div>
          <div className="space-y-1.5">
            <Label>From email</Label>
            <Input
              value={integ.sendgrid.from_email}
              onChange={(e) =>
                setForm((f) =>
                  f
                    ? {
                        ...f,
                        integrations: {
                          ...f.integrations,
                          sendgrid: { ...f.integrations.sendgrid, from_email: e.target.value },
                        },
                      }
                    : f,
                )
              }
            />
          </div>
          <div className="space-y-1.5">
            <Label>From name</Label>
            <Input
              value={integ.sendgrid.from_name}
              onChange={(e) =>
                setForm((f) =>
                  f
                    ? {
                        ...f,
                        integrations: {
                          ...f.integrations,
                          sendgrid: { ...f.integrations.sendgrid, from_name: e.target.value },
                        },
                      }
                    : f,
                )
              }
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Twilio (SMS / WhatsApp)</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label>Account SID</Label>
            <Input
              value={integ.twilio.account_sid}
              onChange={(e) =>
                setForm((f) =>
                  f
                    ? {
                        ...f,
                        integrations: {
                          ...f.integrations,
                          twilio: { ...f.integrations.twilio, account_sid: e.target.value },
                        },
                      }
                    : f,
                )
              }
            />
          </div>
          <div className="space-y-1.5">
            <Label>Auth token</Label>
            <Input
              type="password"
              autoComplete="off"
              value={integ.twilio.auth_token}
              onChange={(e) =>
                setForm((f) =>
                  f
                    ? {
                        ...f,
                        integrations: {
                          ...f.integrations,
                          twilio: { ...f.integrations.twilio, auth_token: e.target.value },
                        },
                      }
                    : f,
                )
              }
            />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label>From number (SMS)</Label>
            <Input
              value={integ.twilio.from_number}
              onChange={(e) =>
                setForm((f) =>
                  f
                    ? {
                        ...f,
                        integrations: {
                          ...f.integrations,
                          twilio: { ...f.integrations.twilio, from_number: e.target.value },
                        },
                      }
                    : f,
                )
              }
            />
          </div>
          <div className="space-y-1.5 sm:col-span-2">
            <Label>WhatsApp from (optional)</Label>
            <Input
              value={integ.twilio.whatsapp_from || ""}
              placeholder="whatsapp:+14155238886"
              onChange={(e) =>
                setForm((f) =>
                  f
                    ? {
                        ...f,
                        integrations: {
                          ...f.integrations,
                          twilio: { ...f.integrations.twilio, whatsapp_from: e.target.value },
                        },
                      }
                    : f,
                )
              }
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">SMTP (Email fallback)</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label>Host</Label>
            <Input
              value={integ.smtp?.host || ""}
              onChange={(e) => patchSmtp({ host: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Port</Label>
            <Input
              value={integ.smtp?.port || "587"}
              onChange={(e) => patchSmtp({ port: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Username</Label>
            <Input
              value={integ.smtp?.username || ""}
              onChange={(e) => patchSmtp({ username: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label>Password</Label>
            <Input
              type="password"
              autoComplete="off"
              value={integ.smtp?.password || ""}
              onChange={(e) => patchSmtp({ password: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label>From email</Label>
            <Input
              value={integ.smtp?.from_email || ""}
              onChange={(e) => patchSmtp({ from_email: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label>From name</Label>
            <Input
              value={integ.smtp?.from_name || "HotelPilot"}
              onChange={(e) => patchSmtp({ from_name: e.target.value })}
            />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Stripe (Payments)</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-3">
          <div className="space-y-1.5">
            <Label>Publishable key</Label>
            <Input
              value={integ.stripe.publishable_key}
              onChange={(e) =>
                setForm((f) =>
                  f
                    ? {
                        ...f,
                        integrations: {
                          ...f.integrations,
                          stripe: {
                            ...f.integrations.stripe,
                            publishable_key: e.target.value,
                          },
                        },
                      }
                    : f,
                )
              }
            />
          </div>
          <div className="space-y-1.5">
            <Label>Secret key</Label>
            <Input
              type="password"
              autoComplete="off"
              value={integ.stripe.secret_key}
              onChange={(e) =>
                setForm((f) =>
                  f
                    ? {
                        ...f,
                        integrations: {
                          ...f.integrations,
                          stripe: { ...f.integrations.stripe, secret_key: e.target.value },
                        },
                      }
                    : f,
                )
              }
            />
          </div>
          <div className="space-y-1.5">
            <Label>Webhook secret</Label>
            <Input
              type="password"
              autoComplete="off"
              value={integ.stripe.webhook_secret}
              onChange={(e) =>
                setForm((f) =>
                  f
                    ? {
                        ...f,
                        integrations: {
                          ...f.integrations,
                          stripe: {
                            ...f.integrations.stripe,
                            webhook_secret: e.target.value,
                          },
                        },
                      }
                    : f,
                )
              }
            />
          </div>
        </CardContent>
      </Card>

      <Button onClick={save} disabled={saving}>
        {saving ? "Saving…" : "Save integration credentials"}
      </Button>
    </div>
  );
}
