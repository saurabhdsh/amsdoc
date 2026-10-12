import { create } from "zustand";

export type User = {
  id: string;
  email: string;
  name: string;
  role: string;
  account_id: string;
  participant_id: string | null;
  permissions: string[];
};

type AuthState = {
  token: string | null;
  user: User | null;
  setSession: (token: string, user: User) => void;
  clear: () => void;
};

export const useAuth = create<AuthState>((set) => ({
  token: sessionStorage.getItem("ams-token"),
  user: null,
  setSession: (token, user) => {
    sessionStorage.setItem("ams-token", token);
    set({ token, user });
  },
  clear: () => {
    sessionStorage.removeItem("ams-token");
    set({ token: null, user: null });
  },
}));

export function can(user: User | null, permission: string): boolean {
  if (!user) return false;
  return user.permissions.includes("*") || user.permissions.includes(permission);
}
