/**
 * Retest switch conversation — contenu du panneau messages UNIQUEMENT.
 * Sélecteur : le conteneur avec classes px-3 + min-h-0 + overflow-y-auto
 * (ChatView L585), distinct de la sidebar.
 */
const { chromium } = require('@playwright/test');
const fs = require('fs');

const MAIN = 'http://localhost:9000';
const TOKEN = process.env.PROD_TOKEN;
const A = Number(process.env.CONV_A);
const B = Number(process.env.CONV_B);

(async () => {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addCookies([{
    name: 'jarvis_session', value: TOKEN, domain: 'localhost', path: '/',
    httpOnly: true, sameSite: 'Strict',
  }]);
  const page = await ctx.newPage();
  await page.goto(`${MAIN}/chat`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3500);

  async function msgPaneText() {
    return page.evaluate(() => {
      const el = [...document.querySelectorAll('div')].find((d) => {
        const c = typeof d.className === 'string' ? d.className : '';
        return (
          c.includes('min-h-0') &&
          c.includes('overflow-y-auto') &&
          c.includes('px-3') &&
          c.includes('py-4')
        );
      });
      return el ? el.innerText : null;
    });
  }

  async function waitForMsg(needle, ms = 5000) {
    const start = Date.now();
    while (Date.now() - start < ms) {
      const t = await msgPaneText();
      if (t && t.includes(needle)) return t;
      await page.waitForTimeout(200);
    }
    return await msgPaneText();
  }

  const out = { a: A, b: B };

  // Sélection B via API-assisted click (titre exact)
  await page.getByText('TEST-SWITCH-B', { exact: true }).first().click();
  const textB = await waitForMsg('UNIQUE-MSG-BETA-8462');
  out.after_b = {
    has_beta: !!(textB && textB.includes('UNIQUE-MSG-BETA-8462')),
    has_alpha: !!(textB && textB.includes('UNIQUE-MSG-ALPHA-7391')),
    len: textB ? textB.length : 0,
  };

  await page.getByText('TEST-SWITCH-A', { exact: true }).first().click();
  const textA = await waitForMsg('UNIQUE-MSG-ALPHA-7391');
  out.after_a = {
    has_alpha: !!(textA && textA.includes('UNIQUE-MSG-ALPHA-7391')),
    has_beta: !!(textA && textA.includes('UNIQUE-MSG-BETA-8462')),
    len: textA ? textA.length : 0,
  };

  // Contre-preuve API : le détail récupéré par le client doit être cohérent
  out.api = await page.evaluate(async ({ A, B }) => {
    const da = await (await fetch(`/api/conversations/${A}`)).json();
    const db = await (await fetch(`/api/conversations/${B}`)).json();
    const msgA = (da.messages || []).map((m) => m.content);
    const msgB = (db.messages || []).map((m) => m.content);
    return { msgA, msgB };
  }, { A, B });

  out.statut =
    out.after_b.has_beta &&
    !out.after_b.has_alpha &&
    out.after_a.has_alpha &&
    !out.after_a.has_beta
      ? 'PASS'
      : 'FAIL';

  await page.screenshot({
    path: '/Users/zeldris/JARVIS/artifacts/validation_screenshots/complement/chat_switch_retest.png',
  });

  // cleanup
  await page.evaluate(async ({ A, B }) => {
    await fetch(`/api/conversations/${A}`, { method: 'DELETE' });
    await fetch(`/api/conversations/${B}`, { method: 'DELETE' });
  }, { A, B });

  await browser.close();
  fs.writeFileSync('/tmp/jarvis_auth_test/retest_switch_final.json', JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out, null, 2));
})().catch((e) => {
  console.error('FATAL', e);
  process.exit(1);
});
