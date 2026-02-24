/**
 * Authentication service for Double Edge frontend.
 * Manages JWT tokens in localStorage and provides login/logout/token helpers.
 */

import { API_BASE_URL } from './config';

const TOKEN_KEY = 'hpd_auth_token';
const USER_KEY = 'hpd_auth_user';

export interface AuthUserInfo {
  user_id: string;
  email: string;
  role: string;
}

// ---------------------------------------------------------------------------
// Token storage
// ---------------------------------------------------------------------------

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export function getStoredUser(): AuthUserInfo | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function storeUser(user: AuthUserInfo): void {
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

// ---------------------------------------------------------------------------
// API calls
// ---------------------------------------------------------------------------

export async function login(email: string, password: string): Promise<{ token: string; user: AuthUserInfo }> {
  const response = await fetch(`${API_BASE_URL}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({ detail: 'Login failed' }));
    throw new Error(data.detail || 'Login failed');
  }
  const data = await response.json();
  setToken(data.token);
  storeUser(data.user);
  return data;
}

export async function fetchMe(): Promise<AuthUserInfo> {
  const token = getToken();
  if (!token) throw new Error('No token');
  const response = await fetch(`${API_BASE_URL}/api/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    if (response.status === 401) {
      clearToken();
      throw new Error('Session expired');
    }
    throw new Error('Failed to fetch user info');
  }
  const user = await response.json();
  storeUser(user);
  return user;
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  const token = getToken();
  if (!token) throw new Error('No token');
  const response = await fetch(`${API_BASE_URL}/api/auth/change-password`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({ detail: 'Failed to change password' }));
    throw new Error(data.detail || 'Failed to change password');
  }
}

export function logout(): void {
  clearToken();
}

// ---------------------------------------------------------------------------
// Auth header helper — attach to every API request
// ---------------------------------------------------------------------------

export function getAuthHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}
