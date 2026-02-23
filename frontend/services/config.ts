/**
 * Application configuration constants.
 * Extracted to its own module to avoid circular imports between api.ts and auth.ts.
 */

export const API_BASE_URL = (import.meta.env.VITE_API_URL || '')
  .trim()
  .replace(/\\r\\n$/, '')
  .replace(/[\r\n]+$/, '');
