"use client";

import { useCallback, useEffect, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast-provider";
import {
  createHotelStaff,
  listHotelStaff,
  revokeHotelStaff,
  updateHotelStaff,
  type HotelStaffRoleOption,
  type HotelStaffUser,
} from "@/lib/hotel-staff-api";
import { Loader2, MoreHorizontal, UserPlus } from "lucide-react";

const EMPTY_ADD = {
  full_name: "",
  email: "",
  password: "",
  role: "FRONT_DESK",
};

const PASSWORD_RULES = [
  { id: "length", label: "At least 8 characters", test: (p: string) => p.length >= 8 },
  { id: "upper", label: "One uppercase letter", test: (p: string) => /[A-Z]/.test(p) },
  { id: "lower", label: "One lowercase letter", test: (p: string) => /[a-z]/.test(p) },
  { id: "digit", label: "One number", test: (p: string) => /\d/.test(p) },
] as const;

function passwordChecks(password: string) {
  return PASSWORD_RULES.map((rule) => ({
    ...rule,
    ok: rule.test(password),
  }));
}

function isPasswordValid(password: string): boolean {
  return PASSWORD_RULES.every((rule) => rule.test(password));
}

function PasswordRequirements({ password }: { password: string }) {
  const checks = passwordChecks(password);
  const show = password.length > 0;
  return (
    <ul className="mt-1.5 space-y-0.5 text-xs">
      {checks.map((c) => (
        <li
          key={c.id}
          className={
            !show
              ? "text-muted-foreground"
              : c.ok
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-destructive"
          }
        >
          {show ? (c.ok ? "✓" : "✗") : "•"} {c.label}
        </li>
      ))}
    </ul>
  );
}

function formatLastLogin(value: string | null | undefined): string {
  if (!value) return "Never";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

/** Staff table + dialogs for Hotel Settings (and legacy /users redirect). */
export function StaffManagementPanel() {
  const { user } = useAuth();
  const { showToast } = useToast();

  const [users, setUsers] = useState<HotelStaffUser[]>([]);
  const [roles, setRoles] = useState<HotelStaffRoleOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const [addOpen, setAddOpen] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [revokeOpen, setRevokeOpen] = useState(false);
  const [selected, setSelected] = useState<HotelStaffUser | null>(null);

  const [addForm, setAddForm] = useState(EMPTY_ADD);
  const [editForm, setEditForm] = useState({
    full_name: "",
    email: "",
    role: "FRONT_DESK",
    password: "",
    status: "active" as "active" | "disabled",
  });

  const refresh = useCallback(async () => {
    try {
      const data = await listHotelStaff();
      setUsers(data.users);
      setRoles(data.assignable_roles);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load staff");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  function openEdit(member: HotelStaffUser) {
    setSelected(member);
    setError("");
    setEditForm({
      full_name: member.full_name || "",
      email: member.email || "",
      role: member.role_code || "FRONT_DESK",
      password: "",
      status: member.status === "disabled" ? "disabled" : "active",
    });
    setEditOpen(true);
  }

  function openRevoke(member: HotelStaffUser) {
    setSelected(member);
    setRevokeOpen(true);
  }

  const addPasswordOk = isPasswordValid(addForm.password);
  const editPasswordOk =
    !editForm.password.trim() || isPasswordValid(editForm.password);
  const editEmailOk = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(editForm.email.trim());
  const addFormReady =
    addForm.full_name.trim().length > 0 &&
    addForm.email.trim().length > 0 &&
    addPasswordOk;
  const editFormReady =
    editForm.full_name.trim().length > 0 && editEmailOk && editPasswordOk;

  async function handleAdd() {
    if (!addFormReady) return;
    setSaving(true);
    setError("");
    try {
      await createHotelStaff(addForm);
      showToast({ type: "success", message: "Staff member added" });
      setAddOpen(false);
      setAddForm(EMPTY_ADD);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create staff");
    } finally {
      setSaving(false);
    }
  }

  async function handleEdit() {
    if (!selected || !editFormReady) return;
    setSaving(true);
    setError("");
    try {
      await updateHotelStaff(selected.id, {
        full_name: editForm.full_name.trim(),
        email: editForm.email.trim().toLowerCase(),
        role: selected.is_owner ? undefined : editForm.role,
        status: editForm.status,
        password: editForm.password.trim() || undefined,
      });
      showToast({ type: "success", message: "Staff member updated" });
      setEditOpen(false);
      setSelected(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update staff");
    } finally {
      setSaving(false);
    }
  }

  async function handleRevoke() {
    if (!selected) return;
    setSaving(true);
    setError("");
    try {
      await revokeHotelStaff(selected.id);
      showToast({ type: "success", message: "Access revoked" });
      setRevokeOpen(false);
      setSelected(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not revoke access");
    } finally {
      setSaving(false);
    }
  }

  async function quickToggle(member: HotelStaffUser) {
    if (member.id === user?.user_id) {
      showToast({ type: "error", message: "You cannot suspend your own account" });
      return;
    }
    try {
      await updateHotelStaff(member.id, {
        status: member.status === "active" ? "disabled" : "active",
      });
      showToast({
        type: "success",
        message: member.status === "active" ? "Staff suspended" : "Staff reactivated",
      });
      await refresh();
    } catch (err) {
      showToast({
        type: "error",
        message: err instanceof Error ? err.message : "Update failed",
      });
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold">Staff & roles</h2>
          <p className="text-sm text-muted-foreground">
            Invite property staff and assign roles available for your account.
          </p>
        </div>
        <Button
          onClick={() => {
            setError("");
            setAddForm(EMPTY_ADD);
            setAddOpen(true);
          }}
        >
          <UserPlus className="mr-1.5 h-4 w-4" />
          Add Staff Member
        </Button>
      </div>

      {error && !addOpen && !editOpen ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      ) : null}

      {loading ? (
        <div className="flex justify-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Last login</TableHead>
                <TableHead className="w-[70px]" />
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={6} className="py-10 text-center text-muted-foreground">
                    No staff accounts yet. Add your first team member.
                  </TableCell>
                </TableRow>
              ) : (
                users.map((member) => (
                  <TableRow key={member.id}>
                    <TableCell className="font-medium">{member.full_name}</TableCell>
                    <TableCell>{member.email}</TableCell>
                    <TableCell>
                      <Badge variant="secondary">
                        {member.role_name || member.role_code || "—"}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <Badge variant={member.status === "active" ? "default" : "warning"}>
                        {member.status === "active" ? "Active" : "Suspended"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatLastLogin(member.last_login)}
                    </TableCell>
                    <TableCell>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon" className="h-8 w-8">
                            <MoreHorizontal className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => openEdit(member)}>
                            Edit / reset password
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => quickToggle(member)}>
                            {member.status === "active" ? "Suspend access" : "Reactivate"}
                          </DropdownMenuItem>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            className="text-destructive focus:text-destructive"
                            onClick={() => openRevoke(member)}
                            disabled={member.id === user?.user_id}
                          >
                            Revoke access
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </div>
      )}

      <Dialog open={addOpen} onOpenChange={setAddOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Add Staff Member</DialogTitle>
            <DialogDescription>
              Creates a login for this hotel and assigns a preset role.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label htmlFor="staff-name">Full name</Label>
              <Input
                id="staff-name"
                value={addForm.full_name}
                onChange={(e) => setAddForm((f) => ({ ...f, full_name: e.target.value }))}
                placeholder="Jane Doe"
              />
            </div>
            <div>
              <Label htmlFor="staff-email">Email</Label>
              <Input
                id="staff-email"
                type="email"
                value={addForm.email}
                onChange={(e) => setAddForm((f) => ({ ...f, email: e.target.value }))}
                placeholder="jane@hotel.com"
              />
            </div>
            <div>
              <Label htmlFor="staff-password">Temporary password</Label>
              <Input
                id="staff-password"
                type="password"
                value={addForm.password}
                onChange={(e) => setAddForm((f) => ({ ...f, password: e.target.value }))}
                placeholder="Create a temporary password"
                aria-invalid={addForm.password.length > 0 && !addPasswordOk}
              />
              <PasswordRequirements password={addForm.password} />
            </div>
            <div>
              <Label>Role</Label>
              <Select
                value={addForm.role}
                onValueChange={(role) => setAddForm((f) => ({ ...f, role }))}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {roles.map((r) => (
                    <SelectItem key={r.code} value={r.code}>
                      {r.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {error && addOpen ? (
              <p className="text-xs text-destructive">{error}</p>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={handleAdd} disabled={saving || !addFormReady}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Create account
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit staff member</DialogTitle>
            <DialogDescription>
              Update details, role, status, or set a new temporary password.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label htmlFor="edit-staff-name">Full name</Label>
              <Input
                id="edit-staff-name"
                value={editForm.full_name}
                onChange={(e) => setEditForm((f) => ({ ...f, full_name: e.target.value }))}
              />
            </div>
            <div>
              <Label htmlFor="edit-staff-email">Email</Label>
              <Input
                id="edit-staff-email"
                type="email"
                value={editForm.email}
                onChange={(e) => setEditForm((f) => ({ ...f, email: e.target.value }))}
                placeholder="jane@hotel.com"
                aria-invalid={editForm.email.length > 0 && !editEmailOk}
              />
              {editForm.email.length > 0 && !editEmailOk ? (
                <p className="mt-1 text-xs text-destructive">Enter a valid email address.</p>
              ) : null}
            </div>
            {!selected?.is_owner ? (
              <div>
                <Label>Role</Label>
                <Select
                  value={editForm.role}
                  onValueChange={(role) => setEditForm((f) => ({ ...f, role }))}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {roles.map((r) => (
                      <SelectItem key={r.code} value={r.code}>
                        {r.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                Hotel Owner role cannot be changed here.
              </p>
            )}
            <div>
              <Label>Status</Label>
              <Select
                value={editForm.status}
                onValueChange={(status: "active" | "disabled") =>
                  setEditForm((f) => ({ ...f, status }))
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="active">Active</SelectItem>
                  <SelectItem value="disabled">Suspended</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>New password (optional)</Label>
              <Input
                type="password"
                value={editForm.password}
                onChange={(e) => setEditForm((f) => ({ ...f, password: e.target.value }))}
                placeholder="Leave blank to keep current"
                aria-invalid={editForm.password.length > 0 && !editPasswordOk}
              />
              {editForm.password.trim() ? (
                <PasswordRequirements password={editForm.password} />
              ) : (
                <p className="mt-1.5 text-xs text-muted-foreground">
                  Leave blank to keep the current password.
                </p>
              )}
            </div>
            {error && editOpen ? (
              <p className="text-xs text-destructive">{error}</p>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={handleEdit} disabled={saving || !editFormReady}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Save changes
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={revokeOpen} onOpenChange={setRevokeOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Revoke access</DialogTitle>
            <DialogDescription>
              Suspend {selected?.full_name || "this user"} immediately. They will not be able to
              sign in until reactivated.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRevokeOpen(false)} disabled={saving}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleRevoke} disabled={saving}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Revoke access
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
