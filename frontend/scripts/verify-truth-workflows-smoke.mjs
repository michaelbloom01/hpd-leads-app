import { chromium } from 'playwright';

const frontendUrl = (process.env.FRONTEND_URL || 'http://localhost:3000').replace(/\/+$/, '');
const apiUrl = (process.env.VITE_API_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '');
const email = process.env.SMOKE_EMAIL;
const password = process.env.SMOKE_PASSWORD;

function fail(message, details = []) {
  console.error(`Truth workflow smoke failed: ${message}`);
  for (const detail of details.filter(Boolean)) {
    console.error(`- ${detail}`);
  }
  process.exitCode = 1;
}

async function apiRequest(path, options = {}) {
  const response = await fetch(`${apiUrl}${path}`, {
    ...options,
    headers: {
      ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}),
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
  });
  if (!response.ok) {
    const body = await response.text().catch(() => '');
    throw new Error(`API ${path} failed with HTTP ${response.status}${body ? `: ${body}` : ''}`);
  }
  return response.json();
}

async function login() {
  if (!email || !password) {
    throw new Error('Set SMOKE_EMAIL and SMOKE_PASSWORD before running this smoke check.');
  }

  const payload = await apiRequest('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
  if (!payload?.token || !payload?.user) {
    throw new Error('Login response did not include both token and user.');
  }
  return payload;
}

async function loadSample(token) {
  const leads = await apiRequest('/api/leads?limit=1&count_mode=estimate', { token });
  const lead = leads?.leads?.[0];
  if (!lead?.lead_id) {
    throw new Error('Lead search did not return a sample lead.');
  }

  const leadId = String(lead.lead_id);
  const truth = await apiRequest(`/api/v1/truth/leads/${encodeURIComponent(leadId)}/summary`, { token });
  if (!truth?.belief_summary || !Array.isArray(truth?.claims)) {
    throw new Error('Sample lead truth summary did not include belief_summary and claims.');
  }

  let bbl = null;
  const contacts = await apiRequest(`/api/leads/${encodeURIComponent(leadId)}/contacts`, { token }).catch(() => null);
  for (const building of contacts?.buildings || []) {
    if (building?.bbl) {
      bbl = String(building.bbl);
      break;
    }
  }
  if (!bbl) {
    const buildings = await apiRequest(`/api/v1/buildings?lead_id=${encodeURIComponent(leadId)}&limit=1`, { token });
    bbl = buildings?.buildings?.[0]?.bbl ? String(buildings.buildings[0].bbl) : null;
  }
  if (!bbl) {
    throw new Error(`No representative building BBL found for lead ${leadId}.`);
  }

  const buildingTruth = await apiRequest(`/api/v1/truth/subjects/building/${encodeURIComponent(bbl)}/summary`, { token });
  if (!buildingTruth?.belief_summary || !Array.isArray(buildingTruth?.claims)) {
    throw new Error('Sample building truth summary did not include belief_summary and claims.');
  }

  return {
    leadId,
    bbl,
    leadClaimCount: truth.claims.length,
    leadBeliefCount: truth.belief_summary?.what_we_believe?.length || 0,
    buildingClaimCount: buildingTruth.claims.length,
  };
}

async function expectText(page, text, options = {}) {
  const locator = page.getByText(text, { exact: options.exact ?? false }).first();
  await locator.waitFor({ state: 'visible', timeout: options.timeout ?? 15000 });
}

async function expectScopedText(scope, text, options = {}) {
  const locator = scope.getByText(text, { exact: options.exact ?? false }).first();
  await locator.waitFor({ state: 'visible', timeout: options.timeout ?? 15000 });
}

async function expectScopedAnyText(scope, texts, options = {}) {
  const timeout = options.timeout ?? 15000;
  const failures = [];
  for (const text of texts) {
    const locator = scope.getByText(text, { exact: options.exact ?? false }).first();
    try {
      await locator.waitFor({ state: 'visible', timeout });
      return;
    } catch (error) {
      failures.push(`${text}: ${error?.message || error}`);
    }
  }
  throw new Error(`None of the expected text variants became visible: ${texts.join(' | ')}. ${failures.join(' || ')}`);
}

async function assertNoRouteError(page) {
  const routeError = await page.getByText('Something went wrong', { exact: false }).first().isVisible({ timeout: 1000 }).catch(() => false);
  if (routeError) {
    throw new Error('Route error boundary rendered.');
  }
}

async function verifyLeadTruth(page, leadId, expectedClaimCount, expectedBeliefCount) {
  await page.goto(`${frontendUrl}/leads?lead=${encodeURIComponent(leadId)}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  const dialog = page.getByRole('dialog');
  await dialog.waitFor({ state: 'visible', timeout: 20000 });
  await expectScopedText(dialog, 'Truth Confidence');
  await assertNoRouteError(page);

  await dialog.getByRole('button', { name: 'Truth', exact: true }).click();
  await page.waitForTimeout(500);
  await expectScopedAnyText(dialog, ['Truth & Confidence', 'TRUTH & CONFIDENCE'], { timeout: 30000 });
  await expectScopedAnyText(dialog, ['Claim Ledger', 'CLAIM LEDGER'], { timeout: 30000 });
  if (expectedBeliefCount > 0) {
    await expectScopedAnyText(dialog, ['Current Beliefs', 'CURRENT BELIEFS'], { timeout: 20000 });
  }
  if (expectedClaimCount > 0) {
    await expectScopedText(dialog, 'supporting');
  }
  await assertNoRouteError(page);
}

async function verifyBuildingTruth(page, bbl, expectedClaimCount) {
  await page.goto(`${frontendUrl}/buildings/${encodeURIComponent(bbl)}`, { waitUntil: 'domcontentloaded', timeout: 30000 });
  await expectText(page, 'Truth & Confidence');
  await expectText(page, 'Evidence Ledger');
  if (expectedClaimCount > 0) {
    await expectText(page, 'supporting');
  }
  await assertNoRouteError(page);
}

async function main() {
  const auth = await login();
  const sample = await loadSample(auth.token);
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const consoleErrors = [];
  const pageErrors = [];

  page.on('console', (message) => {
    if (message.type() === 'error') {
      consoleErrors.push(message.text());
    }
  });
  page.on('pageerror', (error) => {
    pageErrors.push(error.message);
  });

  await page.addInitScript(({ token, user }) => {
    window.localStorage.setItem('hpd_auth_token', token);
    window.localStorage.setItem('hpd_auth_user', JSON.stringify(user));
  }, auth);

  try {
    await verifyLeadTruth(page, sample.leadId, sample.leadClaimCount, sample.leadBeliefCount);
    await verifyBuildingTruth(page, sample.bbl, sample.buildingClaimCount);

    if (pageErrors.length > 0) {
      throw new Error(`Browser page errors: ${pageErrors.join(' | ')}`);
    }

    console.log(`Truth workflow smoke passed for lead ${sample.leadId} and BBL ${sample.bbl}.`);
    if (consoleErrors.length > 0) {
      console.log(`Console errors observed (${consoleErrors.length}):`);
      consoleErrors.slice(0, 5).forEach((entry) => console.log(`- ${entry}`));
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  fail(error?.message || String(error));
});
