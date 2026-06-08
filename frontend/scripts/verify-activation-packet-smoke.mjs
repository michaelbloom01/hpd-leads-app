import { chromium } from 'playwright';

const frontendUrl = (process.env.FRONTEND_URL || 'http://localhost:3000').replace(/\/+$/, '');
const apiUrl = (process.env.VITE_API_URL || 'http://127.0.0.1:8000').replace(/\/+$/, '');
const email = process.env.SMOKE_EMAIL;
const password = process.env.SMOKE_PASSWORD;

function fail(message, details = []) {
  console.error(`Activation packet smoke failed: ${message}`);
  for (const detail of details.filter(Boolean)) {
    console.error(`- ${detail}`);
  }
  process.exitCode = 1;
}

async function expectText(page, text, options = {}) {
  const locator = page.getByText(text, { exact: options.exact ?? false }).first();
  await locator.waitFor({ state: 'attached', timeout: options.timeout ?? 120000 });
  await locator.scrollIntoViewIfNeeded({ timeout: options.timeout ?? 120000 });
  await locator.waitFor({ state: 'visible', timeout: options.timeout ?? 120000 });
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

async function main() {
  const auth = await login();
  const browser = await chromium.launch({
    headless: true,
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
  });
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
    await page.goto(`${frontendUrl}/settings`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.getByRole('button', { name: 'Data Health' }).click();

    await expectText(page, 'Data Truth & Confidence');
    await expectText(page, 'Activation packet');
    await expectText(page, 'materialization or review required');
    await expectText(page, 'Business use blocked');
    await expectText(page, 'approval required', { exact: true });
    await expectText(page, 'Ledger readiness');
    await expectText(page, 'Claims', { exact: true });
    await expectText(page, 'Verified', { exact: true });
    await expectText(page, 'Critical/high gaps', { exact: true });
    await expectText(page, 'Ledger backfill preview');
    await expectText(page, 'claims pending');
    await expectText(page, 'confidence');
    await expectText(page, 'Claim adjudication');
    await expectText(page, 'verification candidates');
    await expectText(page, 'fact groups sampled');
    await expectText(page, 'Multi-source facts');
    await expectText(page, 'Single-source facts');
    await expectText(page, 'No sampled fact group has independent supporting sources.');
    await expectText(page, 'Verification gap plan');
    await expectText(page, 'evidence acquisition proposal');
    await expectText(page, 'do not mark verified from this proposal alone');
    await expectText(page, 'Manual evidence preview');
    await page.getByRole('button', { name: 'Preview Manual Evidence' }).click();
    await expectText(page, 'preview only');
    await expectText(page, 'mutations planned: 3');
    await expectText(page, 'Rollback:');
    await expectText(page, 'Source-overlap approval packet');
    await expectText(page, /Current ledger: \d+ multi-source \/ \d+ source-ready/);
    await expectText(page, 'Strict HPM packet:');
    await expectText(page, 'Strict HPM manager-proof families');
    await expectText(page, 'external web profile');
    await expectText(page, 'ny dps order entry');
    await expectText(page, 'real estate listing');
    await expectText(page, 'Strict operator packet:');
    await expectText(page, 'operator confirmed');
    await expectText(page, 'Post-recording proof');
    await expectText(page, /Current multi-source: \d+ \/ current source-ready: \d+ \/ verified single-source: \d+/);
    await expectText(page, 'Rollback preview:');
    await expectText(page, 'Next source refresh jobs');

    const sourceChipVisible = await page
      .getByText(/acris|building coordinates|dob permits/i)
      .first()
      .isVisible({ timeout: 10000 })
      .catch(() => false);
    if (!sourceChipVisible) {
      throw new Error('No expected source refresh job chip was visible.');
    }

    if (pageErrors.length > 0) {
      throw new Error(`Browser page errors: ${pageErrors.join(' | ')}`);
    }

    console.log('Activation packet smoke passed.');
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
