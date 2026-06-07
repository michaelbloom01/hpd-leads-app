import { chromium } from 'playwright';

const frontendUrl = (process.env.FRONTEND_URL || 'http://127.0.0.1:3000').replace(/\/+$/, '');
const apiUrl = (process.env.VITE_API_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '');
const email = process.env.SMOKE_EMAIL;
const password = process.env.SMOKE_PASSWORD;

const MUTATING_LABEL = /\b(delete|remove|save|create|add|load|refresh|run|start|execute|approve|reject|import|export|upload|enrich|generate|discover|sync|materialize|validate|send|submit|confirm|rescore|log outcome|mark sent|archive|clear all|contacted|meeting|won|lost)\b/i;
const SAFE_LABEL = /\b(data health|source status|system|dashboard|overview|truth|evidence|lineage|contacts|buildings|leads|settings|alerts|targets|smart lists|building lists|next|previous|clear|filters|sort|view|details|close|cancel|search|pipeline|due diligence|score|table|kanban|pm companies|address lookup|man|bklyn|qns|bronx|si|phone|email|go|apply|scoring weights|preview recalculation|preview manual evidence)\b/i;
const CLICKABLE_SELECTOR = [
  'button',
  '[role="button"]',
  'a[href]',
  '[role="link"]',
  'input[type="button"]',
  'input[type="submit"]',
  'input[type="reset"]',
  'summary',
  '[onclick]',
].join(', ');

function fail(message, details = []) {
  console.error(`Safe clickthrough smoke failed: ${message}`);
  for (const detail of details.filter(Boolean)) {
    console.error(`- ${detail}`);
  }
  process.exitCode = 1;
}

async function login() {
  if (!email || !password) {
    throw new Error('Set SMOKE_EMAIL and SMOKE_PASSWORD before running this smoke check.');
  }
  const response = await fetch(`${apiUrl}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new Error(`Login failed with HTTP ${response.status}${body ? `: ${body}` : ''}`);
  }
  const payload = await response.json();
  if (!payload?.token || !payload?.user) {
    throw new Error('Login response did not include both token and user.');
  }
  return payload;
}

async function getJson(path, token) {
  const response = await fetch(`${apiUrl}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with HTTP ${response.status}`);
  }
  return response.json();
}

async function launchBrowser() {
  if ((process.env.PLAYWRIGHT_CHANNEL || 'chrome').toLowerCase() === 'chromium') {
    return chromium.launch({ headless: true });
  }
  try {
    return await chromium.launch({ channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome', headless: true });
  } catch (error) {
    console.warn(`Chrome channel unavailable; falling back to bundled Chromium: ${error.message}`);
    return chromium.launch({ headless: true });
  }
}

function normalizedLabel(raw) {
  return String(raw || '').replace(/\s+/g, ' ').trim();
}

async function assertHealthyPage(page, route) {
  const body = await page.locator('body').innerText({ timeout: 15000 });
  const badText = [
    'Something went wrong',
    'Route Error',
    'Cannot read properties',
    'Unhandled Runtime Error',
    'Failed to load dashboard data',
  ].find((needle) => body.includes(needle));
  if (badText) {
    throw new Error(`${route} rendered error text: ${badText}`);
  }
}

async function collectInteractiveElements(page) {
  const dialogCount = await page.locator('[role="dialog"]').count().catch(() => 0);
  const dialogVisible = dialogCount > 0
    ? await page.locator('[role="dialog"]').first().isVisible().catch(() => false)
    : false;
  const selector = dialogVisible
    ? `[role="dialog"] ${CLICKABLE_SELECTOR.split(', ').join(', [role="dialog"] ')}`
    : CLICKABLE_SELECTOR;
  return page.locator(selector).evaluateAll((elements) =>
    elements.map((element, index) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      const href = element.href || element.getAttribute('href') || '';
      return {
        index,
        label: (
          element.getAttribute('aria-label') ||
          element.getAttribute('title') ||
          element.getAttribute('value') ||
          element.innerText ||
          element.textContent ||
          ''
        ).replace(/\s+/g, ' ').trim(),
        visible: rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden' && style.display !== 'none',
        disabled: Boolean(element.disabled) || element.getAttribute('aria-disabled') === 'true',
        tagName: element.tagName.toLowerCase(),
        role: element.getAttribute('role') || '',
        href,
        target: element.getAttribute('target') || '',
        download: Boolean(element.getAttribute('download')),
      };
    })
  ).then((items) => items.map((item) => ({ ...item, selector })));
}

async function clickSafeButtons(page, route) {
  const clicked = [];
  const skipped = [];
  const buttons = await collectInteractiveElements(page);
  const targets = [];
  const labelOccurrences = new Map();
  for (const button of buttons) {
    const label = normalizedLabel(button.label) || `button#${button.index}`;
    const occurrence = labelOccurrences.get(label) || 0;
    labelOccurrences.set(label, occurrence + 1);
    if (!button.visible || button.disabled) {
      skipped.push({ label, reason: 'hidden_or_disabled' });
      continue;
    }
    const href = String(button.href || '');
    if (
      button.tagName === 'a' &&
      (
        button.download ||
        button.target === '_blank' ||
        /^(mailto:|tel:|javascript:)/i.test(href) ||
        (href.startsWith('http') && !href.startsWith(frontendUrl))
      )
    ) {
      skipped.push({ label, reason: 'external_new_tab_download_or_protocol_link' });
      continue;
    }
    if (MUTATING_LABEL.test(label) && !/^preview\b/i.test(label)) {
      skipped.push({ label, reason: 'mutation_risk' });
      continue;
    }
    if (!SAFE_LABEL.test(label)) {
      skipped.push({ label, reason: 'not_in_safe_click_allowlist' });
      continue;
    }
    targets.push({ label, occurrence });
  }

  for (let targetIndex = 0; targetIndex < targets.length; targetIndex += 1) {
    const { label, occurrence } = targets[targetIndex];
    if (targetIndex > 0) {
      await page.goto(`${frontendUrl}${route}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForTimeout(750);
      await assertHealthyPage(page, route);
    }
    const currentButtons = await collectInteractiveElements(page);
    let seen = 0;
    const current = currentButtons.find((button) => {
      const currentLabel = normalizedLabel(button.label) || `button#${button.index}`;
      if (currentLabel !== label || !button.visible || button.disabled) {
        return false;
      }
      const isMatch = seen === occurrence;
      seen += 1;
      return isMatch;
    });
    if (!current) {
      skipped.push({ label, reason: 'not_visible_on_revisit' });
      continue;
    }
    const locator = page.locator(current.selector || CLICKABLE_SELECTOR).nth(current.index);
    if (!(await locator.isVisible().catch(() => false))) {
      skipped.push({ label, reason: 'became_hidden' });
      continue;
    }
    try {
      await locator.click({ timeout: 5000, noWaitAfter: true });
      await page.waitForLoadState('domcontentloaded', { timeout: 10000 }).catch(() => {});
      await page.waitForTimeout(250);
      await page.keyboard.press('Escape').catch(() => {});
      await assertHealthyPage(page, `${route} after ${label}`);
      clicked.push(label);
    } catch (error) {
      throw new Error(`${route} safe button "${label}" failed: ${error.message}`);
    }
  }
  return { clicked, skipped };
}

async function main() {
  const auth = await login();
  const [leadPayload, buildingPayload] = await Promise.all([
    getJson('/api/leads?limit=1&count_mode=estimate', auth.token),
    getJson('/api/v1/buildings?limit=1', auth.token),
  ]);
  const leadId = leadPayload?.leads?.[0]?.lead_id;
  const bbl = buildingPayload?.buildings?.[0]?.bbl;

  const routes = [
    '/',
    '/leads',
    leadId ? `/leads/${leadId}` : null,
    '/buildings',
    bbl ? `/buildings/${bbl}` : null,
    '/building-lists',
    '/smart-lists',
    '/targets',
    '/alerts',
    '/settings',
  ].filter(Boolean);

  const browser = await launchBrowser();
  const page = await browser.newPage();
  const consoleErrors = [];
  const pageErrors = [];
  const rateLimitedResponses = [];
  const notFoundResponses = [];

  page.on('console', (message) => {
    if (message.type() === 'error') {
      consoleErrors.push(message.text());
    }
  });
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('response', (response) => {
    if (response.status() === 429) {
      rateLimitedResponses.push(response.url());
    }
    if (response.status() === 404) {
      notFoundResponses.push(response.url());
    }
  });

  await page.addInitScript(({ token, user }) => {
    window.localStorage.setItem('hpd_auth_token', token);
    window.localStorage.setItem('hpd_auth_user', JSON.stringify(user));
  }, auth);

  const results = [];
  try {
    for (const route of routes) {
      await page.goto(`${frontendUrl}${route}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.waitForTimeout(750);
      await assertHealthyPage(page, route);
      const buttonResult = await clickSafeButtons(page, route);
      results.push({
        route,
        interactive_count: buttonResult.clicked.length + buttonResult.skipped.length,
        clicked: buttonResult.clicked,
        skipped: buttonResult.skipped,
      });
    }
    if (pageErrors.length > 0) {
      throw new Error(`Browser page errors: ${pageErrors.join(' | ')}`);
    }
    const nonHealthRateLimits = rateLimitedResponses.filter((url) => !/\/api\/health\b/.test(url));
    if (nonHealthRateLimits.length > 0) {
      throw new Error(`HTTP 429 responses observed: ${[...new Set(nonHealthRateLimits)].slice(0, 8).join(' | ')}`);
    }
    const unexpectedConsoleErrors = consoleErrors.filter((entry) => {
      if (/favicon|ResizeObserver/i.test(entry)) {
        return false;
      }
      if (/404|Not Found/i.test(entry) && notFoundResponses.every((url) => /\/favicon\.ico\b/.test(url))) {
        return false;
      }
      if (/429|Too Many Requests/i.test(entry) && nonHealthRateLimits.length === 0) {
        return false;
      }
      return true;
    });
    if (unexpectedConsoleErrors.length > 0) {
      throw new Error(`Console errors observed: ${unexpectedConsoleErrors.slice(0, 5).join(' | ')}; 404 URLs: ${[...new Set(notFoundResponses)].slice(0, 8).join(' | ')}`);
    }
    console.log(JSON.stringify({
      status: 'passed',
      browser_channel: process.env.PLAYWRIGHT_CHANNEL || 'chrome',
      route_count: routes.length,
      total_interactive: results.reduce((sum, result) => sum + result.interactive_count, 0),
      total_clicked: results.reduce((sum, result) => sum + result.clicked.length, 0),
      total_skipped: results.reduce((sum, result) => sum + result.skipped.length, 0),
      skipped_reason_counts: results
        .flatMap((result) => result.skipped)
        .reduce((counts, item) => {
          counts[item.reason] = (counts[item.reason] || 0) + 1;
          return counts;
        }, {}),
      ignored_health_429_count: rateLimitedResponses.length - nonHealthRateLimits.length,
      results,
    }, null, 2));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  fail(error?.message || String(error));
});
