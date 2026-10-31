/**
 * Hotel Owner / Admin staff management API (tenant-scoped).
 */
import { fetchApi } from "./api";

export type HotelStaffRoleCode =
  | "HOTEL_ADMIN"
  | "FRONT_DESK"
  | "HOUSEKEEPING"
  | "ACCOUNTANT";

export interface HotelStaffRoleOption {
  code: HotelStaffRoleCode | string;
  name: string;
}

export interface HotelStaffUser {
  id: string;
  email: string;
  full_name: string;
  username?: string;
  status: "active" | "disabled" | string;
  status_label?: string;
  last_login: string | null;
  created_at?: string | null;
  role_code?: string | null;
  role_name?: string | null;
  role?: { id?: string | null; code?: string | null; name?: string | null } | null;
  is_owner?: boolean;
}

export interface HotelStaffListResponse {
  users: HotelStaffUser[];
  assignable_roles: HotelStaffRoleOption[];
  tenant_id?: string;
}

export async function listHotelStaff(): Promise<HotelStaffListResponse> {
  const data = await fetchApi("/hotel/users");
  return {
    users: (data.users as HotelStaffUser[]) || [],
    assignable_roles: (data.assignable_roles as HotelStaffRoleOption[]) || [],
    tenant_id: data.tenant_id as string | undefined,
  };
}

export async function createHotelStaff(input: {
  full_name: string;
  email: string;
  password: string;
  role: string;
}): Promise<HotelStaffUser> {
  const data = await fetchApi("/hotel/users", {
    method: "POST",
    body: JSON.stringify({
      full_name: input.full_name,
      email: input.email,
      password: input.password,
      role: input.role,
    }),
  });
  return data.user as HotelStaffUser;
}

export async function updateHotelStaff(
  userId: string,
  patch: {
    full_name?: string;
    email?: string;
    status?: "active" | "disabled" | "suspended";
    role?: string;
    password?: string;
  },
): Promise<HotelStaffUser> {
  const data = await fetchApi(`/hotel/users/${userId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
  return data.user as HotelStaffUser;
}

export async function revokeHotelStaff(userId: string): Promise<HotelStaffUser> {
  const data = await fetchApi(`/hotel/users/${userId}`, { method: "DELETE" });
  return data.user as HotelStaffUser;
}
