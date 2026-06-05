import { create } from 'zustand';
import type { User } from '../types';
import * as authApi from '../api/auth';
interface AuthState { user: User | null; token: string | null; login: (email: string, password: string) => Promise<void>; register: (data: Parameters<typeof authApi.register>[0]) => Promise<void>; logout: () => void; loadMe: () => Promise<void>; }
export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: localStorage.getItem('token'),
  async login(email, password) { const res = await authApi.login(email, password); localStorage.setItem('token', res.access_token); set({ token: res.access_token, user: res.user }); },
  async register(data) { const res = await authApi.register(data); localStorage.setItem('token', res.access_token); set({ token: res.access_token, user: res.user }); },
  logout() { localStorage.removeItem('token'); set({ token: null, user: null }); },
  async loadMe() { if (!localStorage.getItem('token')) return; const user = await authApi.me(); set({ user, token: localStorage.getItem('token') }); }
}));
