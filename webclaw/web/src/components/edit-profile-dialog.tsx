"use client";

import { useEffect, useState } from "react";
import { Loader2, UserRound } from "lucide-react";
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
import { useAuth } from "@/lib/auth";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function EditProfileDialog({ open, onOpenChange }: Props) {
  const { user, updateProfile, logout } = useAuth();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    if (!open || !user) return;
    setFullName(user.full_name || "");
    setEmail(user.email || "");
    setCurrentPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setError("");
    setSuccess("");
    // Clear values again after mount — some browsers autofill after the first paint.
    const t = window.setTimeout(() => {
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
    }, 50);
    return () => window.clearTimeout(t);
  }, [open, user]);

  async function handleSave() {
    setError("");
    setSuccess("");
    if (!fullName.trim()) {
      setError("Full name is required.");
      return;
    }
    if (!email.trim() || !email.includes("@")) {
      setError("Enter a valid email address.");
      return;
    }
    // Only treat as a password change when the user typed a new password.
    // Ignore a lone autofilled "current password" so name/email saves still work.
    const changingPassword = Boolean(newPassword.trim() || confirmPassword.trim());
    if (changingPassword) {
      if (!currentPassword) {
        setError("Enter your current password to change it.");
        return;
      }
      if (newPassword.length < 8) {
        setError("New password must be at least 8 characters.");
        return;
      }
      if (newPassword !== confirmPassword) {
        setError("New password and confirmation do not match.");
        return;
      }
    }

    setSaving(true);
    try {
      const payload: {
        full_name: string;
        email: string;
        current_password?: string;
        new_password?: string;
      } = {
        full_name: fullName.trim(),
        email: email.trim().toLowerCase(),
      };
      if (changingPassword) {
        payload.current_password = currentPassword;
        payload.new_password = newPassword;
      }
      const result = await updateProfile(payload);
      if (result.requires_reauth) {
        setSuccess(result.message);
        setTimeout(() => {
          void logout();
        }, 1200);
        return;
      }
      setSuccess(result.message);
      onOpenChange(false);
    } catch (e) {
      setError((e as Error).message || "Failed to update profile");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <UserRound className="h-4 w-4" />
            Edit profile
          </DialogTitle>
          <DialogDescription>
            Update your name and email. Optionally change your password below.
          </DialogDescription>
        </DialogHeader>

        <form
          className="space-y-4 py-1"
          autoComplete="off"
          onSubmit={(e) => {
            e.preventDefault();
            void handleSave();
          }}
        >
          {/* Honeypot fields: browsers prefer these for autofill over the real inputs below */}
          <input
            type="text"
            name="username"
            autoComplete="username"
            tabIndex={-1}
            aria-hidden
            className="sr-only"
            defaultValue=""
          />
          <input
            type="password"
            name="password"
            autoComplete="current-password"
            tabIndex={-1}
            aria-hidden
            className="sr-only"
            defaultValue=""
          />

          <div className="space-y-1.5">
            <Label htmlFor="profile-name">Full name</Label>
            <Input
              id="profile-name"
              name="profile_display_name"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder="Your name"
              autoComplete="off"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="profile-email">Email</Label>
            <Input
              id="profile-email"
              name="profile_contact_email"
              type="text"
              inputMode="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
              autoComplete="off"
            />
          </div>

          <div className="space-y-3 rounded-lg border p-3">
            <p className="text-sm font-medium">Change password</p>
            <p className="text-xs text-muted-foreground">
              Leave blank to keep your current password. Changing it signs you out of all sessions.
            </p>
            <div className="space-y-1.5">
              <Label htmlFor="profile-current-pw">Current password</Label>
              <Input
                id="profile-current-pw"
                name="profile_pw_current"
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                autoComplete="off"
                data-lpignore="true"
                data-1p-ignore
                data-form-type="other"
                readOnly
                onFocus={(e) => e.currentTarget.removeAttribute("readOnly")}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="profile-new-pw">New password</Label>
              <Input
                id="profile-new-pw"
                name="profile_pw_new"
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                autoComplete="off"
                data-lpignore="true"
                data-1p-ignore
                data-form-type="other"
                readOnly
                onFocus={(e) => e.currentTarget.removeAttribute("readOnly")}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="profile-confirm-pw">Confirm new password</Label>
              <Input
                id="profile-confirm-pw"
                name="profile_pw_confirm"
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                autoComplete="off"
                data-lpignore="true"
                data-1p-ignore
                data-form-type="other"
                readOnly
                onFocus={(e) => e.currentTarget.removeAttribute("readOnly")}
              />
            </div>
          </div>

          {error ? <p className="text-sm text-destructive">{error}</p> : null}
          {success ? <p className="text-sm text-muted-foreground">{success}</p> : null}

          <DialogFooter className="px-0 sm:justify-end">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={saving}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={saving}>
              {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              Save changes
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
