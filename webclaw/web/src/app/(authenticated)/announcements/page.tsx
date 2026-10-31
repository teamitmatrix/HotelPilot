"use client";

import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bold,
  Italic,
  Link2,
  List,
  Megaphone,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
  createAnnouncement,
  deleteAnnouncement,
  listAnnouncements,
  updateAnnouncement,
  type Announcement,
} from "@/lib/admin-api";
import { ChatToggleButton } from "@/components/chat-panel";
import { ThemeToggle } from "@/components/theme-toggle";
import { ConnectionStatus } from "@/components/connection-status";

const AUDIENCE_LABELS: Record<string, string> = {
  all: "All hotel owners",
  starter: "Starter tier",
  business: "Professional tier",
  enterprise: "Enterprise tier",
};

type FormState = {
  title: string;
  body: string;
  channel: "banner" | "email" | "both";
  audience: string;
  status: "draft" | "published" | "archived";
  scheduled_at: string;
  expires_at: string;
  regions: string;
};

const emptyForm: FormState = {
  title: "",
  body: "",
  channel: "banner",
  audience: "all",
  status: "draft",
  scheduled_at: "",
  expires_at: "",
  regions: "",
};

function toLocalInput(iso?: string | null) {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const d = new Date(t);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function fromLocalInput(local: string) {
  if (!local.trim()) return null;
  const t = Date.parse(local);
  if (Number.isNaN(t)) return null;
  return new Date(t).toISOString();
}

function formatTs(iso?: string | null) {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function wrapSelection(
  textarea: HTMLTextAreaElement,
  before: string,
  after: string,
  fallback: string,
) {
  const start = textarea.selectionStart;
  const end = textarea.selectionEnd;
  const value = textarea.value;
  const selected = value.slice(start, end) || fallback;
  const next = value.slice(0, start) + before + selected + after + value.slice(end);
  const cursor = start + before.length + selected.length + after.length;
  return { next, cursor };
}

export default function AnnouncementsPage() {
  const queryClient = useQueryClient();
  const bodyRef = useRef<HTMLTextAreaElement>(null);
  const { data: announcements = [], isLoading, error } = useQuery({
    queryKey: ["platform-announcements"],
    queryFn: listAnnouncements,
  });

  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Announcement | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Announcement | null>(null);

  useEffect(() => {
    document.title = "Announcements — HotelPilot Central Admin";
  }, []);

  function openCreate() {
    setEditing(null);
    setForm(emptyForm);
    setMessage("");
    setOpen(true);
  }

  function openEdit(row: Announcement) {
    setEditing(row);
    setForm({
      title: row.title,
      body: row.body,
      channel: row.channel,
      audience: row.audience === "business" ? "business" : row.audience,
      status: row.status,
      scheduled_at: toLocalInput(row.scheduled_at),
      expires_at: toLocalInput(row.expires_at),
      regions: (row.regions || []).join(", "),
    });
    setMessage("");
    setOpen(true);
  }

  function applyFormat(kind: "bold" | "italic" | "link" | "list") {
    const el = bodyRef.current;
    if (!el) return;
    let result: { next: string; cursor: number };
    if (kind === "bold") result = wrapSelection(el, "**", "**", "bold text");
    else if (kind === "italic") result = wrapSelection(el, "_", "_", "italic text");
    else if (kind === "link")
      result = wrapSelection(el, "[", "](https://)", "link text");
    else {
      const start = el.selectionStart;
      const end = el.selectionEnd;
      const selected = el.value.slice(start, end) || "List item";
      const lines = selected
        .split("\n")
        .map((l) => (l.startsWith("- ") ? l : `- ${l}`))
        .join("\n");
      result = {
        next: el.value.slice(0, start) + lines + el.value.slice(end),
        cursor: start + lines.length,
      };
    }
    setForm((f) => ({ ...f, body: result.next }));
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(result.cursor, result.cursor);
    });
  }

  async function save(publish: boolean) {
    if (!form.title.trim()) {
      setMessage("Title is required.");
      return;
    }
    setBusy(true);
    setMessage("");
    const regions = form.regions
      .split(/[,;\s]+/)
      .map((r) => r.trim().toUpperCase())
      .filter(Boolean);
    const payload = {
      title: form.title.trim(),
      body: form.body,
      channel: form.channel,
      audience: form.audience,
      status: (publish
        ? "published"
        : form.status === "archived"
          ? "archived"
          : "draft") as FormState["status"],
      scheduled_at: fromLocalInput(form.scheduled_at),
      expires_at: fromLocalInput(form.expires_at),
      regions,
    };
    try {
      if (editing) {
        await updateAnnouncement(editing.id, payload);
      } else {
        await createAnnouncement(payload);
      }
      await queryClient.invalidateQueries({ queryKey: ["platform-announcements"] });
      setOpen(false);
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    setBusy(true);
    try {
      await deleteAnnouncement(deleteTarget.id);
      await queryClient.invalidateQueries({ queryKey: ["platform-announcements"] });
      setDeleteTarget(null);
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col">
      <header className="flex h-14 items-center gap-4 border-b bg-background px-6">
        <SidebarTrigger />
        <Separator orientation="vertical" className="h-6" />
        <h1 className="text-lg font-semibold">Announcements</h1>
        <div className="ml-auto flex items-center gap-2">
          <ConnectionStatus />
          <ThemeToggle />
          <ChatToggleButton />
        </div>
      </header>

      <div className="flex-1 space-y-6 p-6">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 className="text-2xl font-bold tracking-tight">Platform announcements</h2>
            <p className="text-muted-foreground">
              Publish banners or emails by subscription tier and country/region.
            </p>
          </div>
          <Button onClick={openCreate}>
            <Plus className="h-4 w-4" />
            New announcement
          </Button>
        </div>

        {error ? (
          <p className="text-sm text-destructive">{(error as Error).message}</p>
        ) : null}

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Published & drafts</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Title</TableHead>
                  <TableHead>Audience</TableHead>
                  <TableHead>Channel</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Updated</TableHead>
                  <TableHead className="w-[100px]" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  <TableRow>
                    <TableCell colSpan={6} className="py-12 text-center text-muted-foreground">
                      Loading…
                    </TableCell>
                  </TableRow>
                ) : announcements.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={6} className="py-16 text-center">
                      <div className="flex flex-col items-center gap-2">
                        <Megaphone className="h-8 w-8 text-muted-foreground" />
                        <p className="font-medium">No announcements yet</p>
                        <p className="text-sm text-muted-foreground">
                          Create a maintenance banner or email broadcast for hotel owners.
                        </p>
                      </div>
                    </TableCell>
                  </TableRow>
                ) : (
                  announcements.map((row) => (
                    <TableRow key={row.id}>
                      <TableCell>
                        <p className="font-medium">{row.title}</p>
                        <p className="line-clamp-1 text-xs text-muted-foreground">
                          {row.body.replace(/[#*_`[\]]/g, "").slice(0, 80) || "—"}
                        </p>
                      </TableCell>
                      <TableCell className="text-sm">
                        {AUDIENCE_LABELS[row.audience] || row.audience}
                      </TableCell>
                      <TableCell className="capitalize text-sm">{row.channel}</TableCell>
                      <TableCell>
                        <Badge
                          variant={
                            row.status === "published"
                              ? "default"
                              : row.status === "draft"
                                ? "secondary"
                                : "outline"
                          }
                          className="capitalize"
                        >
                          {row.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {formatTs(row.updated_at)}
                      </TableCell>
                      <TableCell>
                        <div className="flex gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => openEdit(row)}
                            aria-label="Edit"
                          >
                            <Pencil className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => setDeleteTarget(row)}
                            aria-label="Delete"
                          >
                            <Trash2 className="h-4 w-4 text-destructive" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>
              {editing ? "Edit announcement" : "New announcement"}
            </DialogTitle>
            <DialogDescription>
              Use markdown for formatting. Publish as a banner, email, or both.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-1">
            <div className="space-y-1.5">
              <Label htmlFor="ann-title">Title</Label>
              <Input
                id="ann-title"
                value={form.title}
                onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))}
                placeholder="System maintenance — Sunday 02:00 UTC"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ann-regions">Regions (country codes)</Label>
              <Input
                id="ann-regions"
                value={form.regions}
                onChange={(e) => setForm((f) => ({ ...f, regions: e.target.value }))}
                placeholder="Leave empty for all, or AE, US, GB"
              />
              <p className="text-xs text-muted-foreground">
                Comma-separated ISO country codes. Empty = every region.
              </p>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="space-y-1.5">
                <Label>Audience</Label>
                <Select
                  value={form.audience}
                  onValueChange={(v) => setForm((f) => ({ ...f, audience: v }))}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All hotel owners</SelectItem>
                    <SelectItem value="starter">Starter</SelectItem>
                    <SelectItem value="business">Professional</SelectItem>
                    <SelectItem value="enterprise">Enterprise</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Channel</Label>
                <Select
                  value={form.channel}
                  onValueChange={(v) =>
                    setForm((f) => ({
                      ...f,
                      channel: v as FormState["channel"],
                    }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="banner">In-app banner</SelectItem>
                    <SelectItem value="email">Email broadcast</SelectItem>
                    <SelectItem value="both">Banner + email</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label>Status</Label>
                <Select
                  value={form.status}
                  onValueChange={(v) =>
                    setForm((f) => ({
                      ...f,
                      status: v as FormState["status"],
                    }))
                  }
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="draft">Draft</SelectItem>
                    <SelectItem value="published">Published</SelectItem>
                    <SelectItem value="archived">Archived</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="ann-sched">Publish at (optional)</Label>
                <Input
                  id="ann-sched"
                  type="datetime-local"
                  value={form.scheduled_at}
                  onChange={(e) => setForm((f) => ({ ...f, scheduled_at: e.target.value }))}
                />
                <p className="text-xs text-muted-foreground">
                  Future times stay draft until the schedule fires.
                </p>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="ann-exp">Expires at (optional)</Label>
                <Input
                  id="ann-exp"
                  type="datetime-local"
                  value={form.expires_at}
                  onChange={(e) => setForm((f) => ({ ...f, expires_at: e.target.value }))}
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="ann-body">Body</Label>
              <div className="flex flex-wrap gap-1 rounded-t-md border border-b-0 bg-muted/40 p-1">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => applyFormat("bold")}
                >
                  <Bold className="h-3.5 w-3.5" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => applyFormat("italic")}
                >
                  <Italic className="h-3.5 w-3.5" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => applyFormat("link")}
                >
                  <Link2 className="h-3.5 w-3.5" />
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => applyFormat("list")}
                >
                  <List className="h-3.5 w-3.5" />
                </Button>
              </div>
              <Textarea
                id="ann-body"
                ref={bodyRef}
                value={form.body}
                onChange={(e) => setForm((f) => ({ ...f, body: e.target.value }))}
                placeholder="Write the announcement body (markdown supported)…"
                rows={10}
                className="rounded-t-none"
              />
            </div>
            {message ? <p className="text-sm text-destructive">{message}</p> : null}
          </div>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button variant="outline" onClick={() => setOpen(false)} disabled={busy}>
              Cancel
            </Button>
            <Button variant="secondary" onClick={() => save(false)} disabled={busy}>
              Save draft
            </Button>
            <Button onClick={() => save(true)} disabled={busy}>
              <Megaphone className="h-4 w-4" />
              Publish
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!deleteTarget} onOpenChange={(o) => !o && setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete announcement?</DialogTitle>
            <DialogDescription>
              This removes “{deleteTarget?.title}” permanently.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={confirmDelete} disabled={busy}>
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
