import { chromium } from 'playwright';
import { mkdir } from 'fs/promises';

(async () => {
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 }
  });
  const page = await context.newPage();

  await mkdir('screenshots', { recursive: true });

  console.log('Navigating to http://localhost:3002...');
  await page.goto('http://localhost:3002');
  await page.waitForTimeout(5000);

  console.log('Clicking on LEADS tab...');
  await page.locator('text=LEADS').first().click();
  await page.waitForTimeout(2000);

  console.log('Clicking on first lead...');
  await page.locator('table tbody tr').first().click();
  await page.waitForTimeout(2000);

  console.log('Taking screenshot of lead detail...');
  await page.screenshot({ path: 'screenshots/lead-detail-top.png', fullPage: false });

  console.log('Scrolling down in the lead detail panel...');
  const panel = await page.locator('[class*="overflow-y-auto"]').last();
  await panel.evaluate(el => el.scrollTop = 500);
  await page.waitForTimeout(1000);
  await page.screenshot({ path: 'screenshots/lead-detail-middle.png', fullPage: false });

  await panel.evaluate(el => el.scrollTop = 1000);
  await page.waitForTimeout(1000);
  await page.screenshot({ path: 'screenshots/lead-detail-bottom.png', fullPage: false });

  console.log('Test complete! Screenshots saved.');
  await page.waitForTimeout(5000);
  await browser.close();
})();
