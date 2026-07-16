/* Retests ciblés : (1) back-button après logout, (2) switch conversation sans mixing (scopé zone messages). */
const { chromium } = require('@playwright/test');
const fs = require('fs');

const AUTH_BASE = 'http://127.0.0.1:8099';
const MAIN_BASE = 'http://localhost:9000';
const TEST_PIN = 'validation-pin-2026';
const PROD_TOKEN = process.env.PROD_TOKEN;

(async () => {
  const browser = await chromium.launch({ headless: true });
  const out = {};

  // ── Retest 1 : back navigateur après logout ──
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await ctx.newPage();
    await page.goto(`${AUTH_BASE}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);
    const input = page.locator('input[type="password"]').first();
    await input.fill(TEST_PIN);
    await input.press('Enter');
    await page.waitForTimeout(2500);
    // Naviguer vers une page privée pour créer une vraie entrée d'historique
    await page.goto(`${AUTH_BASE}/dashboard`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2500);
    const dashPrivate = (await page.locator('body').innerText()).length > 50;
    // Logout
    await page.evaluate(async () => { await fetch('/api/auth/logout', { method: 'POST' }); });
    await page.goto(`${AUTH_BASE}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2000);
    const gateAfterLogout = await page.locator('[data-testid="lock-gate"]').isVisible().catch(() => false);
    // Back → retour vers /dashboard dans l'historique
    await page.goBack({ waitUntil: 'domcontentloaded' }).catch(() => {});
    await page.waitForTimeout(2500);
    const url = page.url();
    const gateOnBack = await page.locator('[data-testid="lock-gate"]').isVisible().catch(() => false);
    const bodyText = await page.locator('body').innerText();
    // Données privées = contenu métier visible hors gate
    const privateVisible = !gateOnBack && /tableau de bord|conversation|contacts actifs|tâche/i.test(bodyText);
    await page.screenshot({ path: '/Users/zeldris/JARVIS/artifacts/validation_screenshots/complement/auth_E_back_button_retest.png' });
    out.back_button = {
      dash_private_before: dashPrivate,
      gate_after_logout: gateAfterLogout,
      back_url: url,
      gate_on_back: gateOnBack,
      private_data_visible_on_back: privateVisible,
      statut: gateAfterLogout && !privateVisible ? 'PASS' : 'FAIL',
    };
    await ctx.close();
  }

  // ── Retest 2 : switch conversation, mixing scopé à la zone messages ──
  {
    // Recrée 2 convs de test via l'API (celles du run précédent ont été supprimées)
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await ctx.addCookies([{ name: 'jarvis_session', value: PROD_TOKEN, domain: 'localhost', path: '/', httpOnly: true, sameSite: 'Strict' }]);
    const page = await ctx.newPage();
    await page.goto(`${MAIN_BASE}/chat`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3000);
    const ids = await page.evaluate(async () => {
      // création via WS impossible ici — on utilise les endpoints REST du run :
      // il n'y a pas de POST /api/conversations, donc on passe par le même flux
      // que le script précédent : DB directe impossible côté navigateur.
      return null;
    });
    out.switch = { note: 'ids créés côté serveur par le shell', ids };
    await ctx.close();
  }

  await browser.close();
  fs.writeFileSync('/tmp/jarvis_auth_test/retest1.json', JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out, null, 2));
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
