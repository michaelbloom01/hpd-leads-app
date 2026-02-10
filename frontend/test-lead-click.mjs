import { chromium } from 'playwright';
import { mkdir } from 'fs/promises';

(async () => {
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 }
  });
  const page = await context.newPage();

  // Create screenshots directory
  await mkdir('screenshots', { recursive: true });

  // Listen for console errors and logs
  page.on('console', msg => {
    const type = msg.type();
    if (type === 'error') {
      console.log('❌ BROWSER CONSOLE ERROR:', msg.text());
    } else if (type === 'warning') {
      console.log('⚠️  BROWSER CONSOLE WARNING:', msg.text());
    }
  });

  // Listen for page errors
  page.on('pageerror', error => {
    console.log('❌ BROWSER PAGE ERROR:', error.message);
    console.log('Stack:', error.stack);
  });

  console.log('Step 1: Navigating to http://localhost:3002...');
  await page.goto('http://localhost:3002');
  await page.screenshot({ path: 'screenshots/01-initial-load.png', fullPage: true });
  console.log('Screenshot saved: 01-initial-load.png');

  console.log('\nStep 2: Waiting for backend to connect (up to 3 minutes)...');
  let attempts = 0;
  const maxAttempts = 36; // 3 minutes (36 * 5 seconds)
  
  while (attempts < maxAttempts) {
    await page.waitForTimeout(5000);
    attempts++;
    
    // Check if backend is still starting
    const backendStarting = await page.locator('text=Backend is starting up').isVisible().catch(() => false);
    
    if (!backendStarting) {
      console.log('Backend connected!');
      break;
    }
    
    console.log(`Waiting for backend... (${attempts * 5}s / ${maxAttempts * 5}s)`);
    
    if (attempts % 6 === 0) {
      await page.screenshot({ path: `screenshots/02-waiting-${attempts * 5}s.png`, fullPage: true });
    }
  }

  await page.screenshot({ path: 'screenshots/03-backend-ready.png', fullPage: true });
  console.log('Screenshot saved: 03-backend-ready.png');

  console.log('\nStep 3: Looking for "LEADS" tab in sidebar...');
  try {
    const leadsTab = await page.locator('text=LEADS').first();
    if (await leadsTab.isVisible({ timeout: 5000 })) {
      console.log('Found "LEADS" tab, clicking...');
      await leadsTab.click();
      await page.waitForTimeout(2000);
      await page.screenshot({ path: 'screenshots/04-leads-tab-clicked.png', fullPage: true });
      console.log('Screenshot saved: 04-leads-tab-clicked.png');
    } else {
      console.log('LEADS tab not found or not visible');
    }
  } catch (error) {
    console.log('Could not find "LEADS" tab:', error.message);
  }

  console.log('\nStep 4: Waiting for leads to load...');
  await page.waitForTimeout(3000);
  await page.screenshot({ path: 'screenshots/05-leads-loaded.png', fullPage: true });
  console.log('Screenshot saved: 05-leads-loaded.png');

  console.log('\nStep 5: Looking for lead rows in table...');
  try {
    // Try multiple selectors for finding table rows
    const selectors = [
      'table tbody tr',
      '[role="row"]',
      '.lead-row',
      'tr[data-lead-id]',
      'div[role="button"]'
    ];

    let tableRow = null;
    for (const selector of selectors) {
      const element = await page.locator(selector).first();
      if (await element.isVisible({ timeout: 2000 }).catch(() => false)) {
        tableRow = element;
        console.log(`Found element with selector: ${selector}`);
        break;
      }
    }

    if (tableRow) {
      console.log('Found clickable lead element, clicking...');
      
      await tableRow.click();
      console.log('✅ Clicked on lead row');
      
      await page.waitForTimeout(2000);
      await page.screenshot({ path: 'screenshots/06-after-lead-click.png', fullPage: true });
      console.log('Screenshot saved: 06-after-lead-click.png');

      // Check if modal/panel opened
      const possibleModals = [
        '[role="dialog"]',
        '.modal',
        '.lead-detail-panel',
        '.side-panel',
        '[data-testid="lead-detail"]'
      ];

      let modalFound = false;
      for (const modalSelector of possibleModals) {
        const modal = await page.locator(modalSelector).first();
        if (await modal.isVisible({ timeout: 1000 }).catch(() => false)) {
          console.log(`\n✅ Modal/Panel found with selector: ${modalSelector}`);
          modalFound = true;
          await page.screenshot({ path: 'screenshots/07-modal-opened.png', fullPage: true });
          console.log('Screenshot saved: 07-modal-opened.png');

          // Try to scroll within the modal
          console.log('\nScrolling down in modal...');
          try {
            await modal.evaluate(el => el.scrollTop = el.scrollHeight / 2);
            await page.waitForTimeout(1000);
            await page.screenshot({ path: 'screenshots/08-modal-scrolled-half.png', fullPage: true });
            console.log('Screenshot saved: 08-modal-scrolled-half.png');

            await modal.evaluate(el => el.scrollTop = el.scrollHeight);
            await page.waitForTimeout(1000);
            await page.screenshot({ path: 'screenshots/09-modal-scrolled-bottom.png', fullPage: true });
            console.log('Screenshot saved: 09-modal-scrolled-bottom.png');
          } catch (scrollError) {
            console.log('Could not scroll modal:', scrollError.message);
          }
          break;
        }
      }

      if (!modalFound) {
        console.log('\n⚠️  No modal/panel found after clicking');
      }
    } else {
      console.log('❌ No clickable lead elements found');
      
      // Let's see what IS on the page
      const pageContent = await page.content();
      console.log('\nPage HTML length:', pageContent.length);
      
      // Try to find any text that might indicate what's showing
      const bodyText = await page.locator('body').innerText();
      console.log('\nVisible text on page (first 500 chars):');
      console.log(bodyText.substring(0, 500));
    }
  } catch (error) {
    console.log('❌ Error while clicking lead:', error.message);
    console.log('Stack:', error.stack);
    await page.screenshot({ path: 'screenshots/06-error-state.png', fullPage: true });
    console.log('Screenshot saved: 06-error-state.png');
  }

  console.log('\n\n=== SUMMARY ===');
  console.log('Test complete. Check the screenshots folder for all captured images.');
  console.log('Keeping browser open for 10 seconds so you can inspect...');
  
  await page.waitForTimeout(10000);

  await browser.close();
  console.log('\n✅ Browser closed.');
})();
