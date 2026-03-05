/**
 * Application configuration constants.
 * Extracted to its own module to avoid circular imports between api.ts and auth.ts.
 */

const PRODUCTION_API = 'https://hpd-leads-app-production.up.railway.app';

const rawEnvUrl = (import.meta.env.VITE_API_URL || '')
  .replace(/\\r/g, '')
  .replace(/\\n/g, '')
  .replace(/[\r\n]+/g, '')
  .trim()
  .replace(/\/+$/, '');

export const API_BASE_URL = rawEnvUrl || (import.meta.env.PROD ? PRODUCTION_API : '');
