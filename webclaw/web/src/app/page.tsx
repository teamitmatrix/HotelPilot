"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { useAuth } from "@/lib/auth";
import { postLoginPath } from "@/lib/auth-routing";
import {
  ArrowRight,
  BedDouble,
  Building2,
  CalendarDays,
  ChevronRight,
  DoorOpen,
  MessageSquare,
  Radio,
  Receipt,
  Shield,
  Sparkles,
} from "lucide-react";

const features = [
  {
    icon: CalendarDays,
    title: "Front Desk & Reservations",
    description:
      "See arrivals, departures, and occupancy on one board. Check guests in and out without leaving the desk.",
  },
  {
    icon: DoorOpen,
    title: "Rooms & Housekeeping",
    description:
      "Track room status, assign housekeeping, and know which rooms are ready before the next arrival.",
  },
  {
    icon: Receipt,
    title: "Folios & Billing",
    description:
      "Post charges, split folios, and close out guest accounts with a clear record of every stay.",
  },
  {
    icon: MessageSquare,
    title: "Staff AI Assistant",
    description:
      "Ask about a reservation, a guest, or a room in plain language — right beside your live hotel data.",
  },
  {
    icon: Radio,
    title: "Live Occupancy",
    description:
      "Room status and reservation changes update as they happen, so night audit and front desk stay in sync.",
  },
  {
    icon: Shield,
    title: "Role-Based Access",
    description:
      "Front desk, housekeeping, and managers see only what they need. Every property stays on its own workspace.",
  },
];

const steps = [
  {
    number: "01",
    title: "Register your hotel",
    description:
      "Create a private workspace for the property. Guest, room, and billing data never mix with another hotel.",
  },
  {
    number: "02",
    title: "Set up rooms and staff",
    description:
      "Add your room inventory, invite the team, and assign roles for front desk, housekeeping, and management.",
  },
  {
    number: "03",
    title: "Run the property",
    description:
      "Take reservations, manage stays, post folio charges, and watch occupancy from the HotelPilot dashboard.",
  },
];

const stats = [
  { value: "Front desk", label: "Reservations & stay calendar" },
  { value: "Rooms", label: "Status & housekeeping" },
  { value: "Guests", label: "Profiles & history" },
  { value: "Folios", label: "Charges & checkout" },
];

const modules = [
  {
    icon: CalendarDays,
    title: "Reservations",
    description: "Bookings, arrivals, and in-house guests on a 14-day board.",
  },
  {
    icon: BedDouble,
    title: "Room inventory",
    description: "Types, rates, and live availability for every room.",
  },
  {
    icon: Receipt,
    title: "Folios",
    description: "Stay charges, payments, and checkout in one ledger.",
  },
];

export default function LandingPage() {
  const { user, loading } = useAuth();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Don't render until client-side to avoid hydration mismatch
  if (!mounted) return null;

  return (
    <div className="min-h-screen bg-background">
      {/* Nav */}
      <nav className="border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="mx-auto max-w-6xl flex h-16 items-center justify-between px-6">
          <Link href="/" className="flex items-center gap-2">
            <Building2 className="h-6 w-6 text-primary" />
            <span className="text-xl font-bold tracking-tight">HotelPilot</span>
          </Link>
          <div className="flex items-center gap-3">
            {!loading && user ? (
              <Link href={postLoginPath(user)}>
                <Button>
                  Go to Dashboard
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Button>
              </Link>
            ) : (
              <>
                <Link href="/login">
                  <Button variant="ghost">Sign In</Button>
                </Link>
                <Link href="/register">
                  <Button>
                    Register your hotel
                    <ArrowRight className="ml-2 h-4 w-4" />
                  </Button>
                </Link>
              </>
            )}
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="mx-auto max-w-6xl px-6 py-20 text-center">
        <Badge variant="secondary" className="mb-6 gap-1">
          <Sparkles className="h-3 w-3" />
          Property Management System
        </Badge>
        <h1 className="text-4xl font-bold tracking-tight sm:text-5xl lg:text-6xl">
          The operations cockpit
          <br />
          <span className="text-primary">for your hotel</span>
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-lg text-muted-foreground">
          Reservations, rooms, guests, and billing in one place. HotelPilot
          gives every property a private workspace so front desk, housekeeping,
          and management stay aligned.
        </p>
        <div className="mt-10 flex items-center justify-center gap-4">
          {!loading && user ? (
            <Link href={postLoginPath(user)}>
              <Button size="lg">
                Go to Dashboard
                <ArrowRight className="ml-2 h-4 w-4" />
              </Button>
            </Link>
          ) : (
            <>
              <Link href="/register">
                <Button size="lg">
                  Register your hotel
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Button>
              </Link>
              <Link href="/login">
                <Button variant="outline" size="lg">
                  Sign In
                </Button>
              </Link>
            </>
          )}
        </div>
      </section>

      {/* Stats Bar */}
      <section className="border-y bg-muted/40">
        <div className="mx-auto max-w-6xl px-6 py-8">
          <div className="grid grid-cols-2 gap-6 md:grid-cols-4">
            {stats.map((stat) => (
              <div key={stat.label} className="text-center">
                <div className="text-3xl font-bold tracking-tight">{stat.value}</div>
                <div className="mt-1 text-sm text-muted-foreground">{stat.label}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="mx-auto max-w-6xl px-6 py-20">
        <div className="text-center mb-12">
          <h2 className="text-3xl font-bold tracking-tight">
            Everything the property needs
          </h2>
          <p className="mt-3 text-muted-foreground">
            Run the desk, the rooms, and the bill from a single hotel workspace.
          </p>
        </div>
        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          {features.map((feature) => {
            const Icon = feature.icon;
            return (
              <Card key={feature.title} className="border-0 shadow-none bg-muted/40">
                <CardContent className="pt-6">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 mb-4">
                    <Icon className="h-5 w-5 text-primary" />
                  </div>
                  <h3 className="font-semibold mb-2">{feature.title}</h3>
                  <p className="text-sm text-muted-foreground leading-relaxed">
                    {feature.description}
                  </p>
                </CardContent>
              </Card>
            );
          })}
        </div>
      </section>

      {/* How It Works */}
      <section className="border-y bg-muted/40">
        <div className="mx-auto max-w-6xl px-6 py-20">
          <div className="text-center mb-12">
            <h2 className="text-3xl font-bold tracking-tight">
              How it works
            </h2>
            <p className="mt-3 text-muted-foreground">
              Three steps from signup to a live front desk.
            </p>
          </div>
          <div className="grid gap-8 md:grid-cols-3">
            {steps.map((step, i) => (
              <div key={step.number} className="relative">
                <div className="text-5xl font-bold text-primary/15 mb-3">
                  {step.number}
                </div>
                <h3 className="font-semibold mb-2">{step.title}</h3>
                <p className="text-sm text-muted-foreground leading-relaxed">
                  {step.description}
                </p>
                {i < steps.length - 1 && (
                  <ChevronRight className="hidden md:block absolute top-8 -right-4 h-6 w-6 text-muted-foreground/30" />
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Operations */}
      <section className="mx-auto max-w-6xl px-6 py-20">
        <div className="text-center mb-12">
          <h2 className="text-3xl font-bold tracking-tight">
            Built for hotel operations
          </h2>
          <p className="mt-3 text-muted-foreground">
            The tools your team already uses at the desk — without switching systems.
          </p>
        </div>
        <div className="grid gap-6 md:grid-cols-3">
          {modules.map((mod) => {
            const Icon = mod.icon;
            return (
              <Card key={mod.title}>
                <CardContent className="pt-6">
                  <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 mb-4">
                    <Icon className="h-5 w-5 text-primary" />
                  </div>
                  <h3 className="font-semibold mb-2">{mod.title}</h3>
                  <p className="text-sm text-muted-foreground leading-relaxed">
                    {mod.description}
                  </p>
                </CardContent>
              </Card>
            );
          })}
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t">
        <div className="mx-auto max-w-6xl px-6 py-8">
          <div className="flex flex-col items-center justify-between gap-4 sm:flex-row">
            <div className="flex items-center gap-2">
              <Building2 className="h-5 w-5 text-primary" />
              <span className="font-semibold">HotelPilot</span>
              <span className="text-sm text-muted-foreground">
                Property management for independent hotels
              </span>
            </div>
            <div className="flex items-center gap-6 text-sm text-muted-foreground">
              <Link href="/login" className="hover:text-foreground transition-colors">
                Sign In
              </Link>
              <Link href="/register" className="hover:text-foreground transition-colors">
                Register
              </Link>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
