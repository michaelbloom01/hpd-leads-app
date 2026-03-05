/**
 * Application configuration constants.
 * Extracted to its own module to avoid circular imports between api.ts and auth.ts.
 */

const PRODUCTION_API = 'https://hpd-leads-app-production.up.railway.app';

function cleanUrl(raw: string): string {
  // Strip every flavour of newline/whitespace/backslash-literal that Vercel
  // env vars have historically smuggled into VITE_API_URL.
  let url = raw
    .replace(/\\r/g, '')
    .replace(/\\n/g, '')
    .replace(/[\r\n\t ]+/g, '')
    .trim()
    .replace(/\/+$/, '');
  // Validate it actually looks like a URL
  try {
    if (url && new URL(url).protocol.startsWith('http')) return url;
  } catch { /* invalid URL — fall through */ }
  return '';
}

const rawEnvUrl = cleanUrl(import.meta.env.VITE_API_URL || '');

export const API_BASE_URL = rawEnvUrl || (import.meta.env.PROD ? PRODUCTION_API : '');
