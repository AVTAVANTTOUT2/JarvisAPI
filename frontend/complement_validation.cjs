/* Validation complémentaire ciblée — 4 zones.
 * Zone 1 : Auth/LockGate sur backend isolé 8099 (DB temp, PIN de test).
 * Zone 2 : actions secondaires chat sur :9000 (conversations de test uniquement).
 * Zone 3 : responsive 768/1024.
 * Zone 4 : console+réseau des 12 routes survolées.
 * Sortie : artifacts/validation_screenshots/complement/*.png + complement_report.json
 */
const { chromium } = require('@playwright/test');
const fs = require('fs');
const path = require('path');

const ROOT = '/Users/zeldris/JARVIS';
const OUT = path.join(ROOT, 'artifacts', 'validation_screenshots', 'complement');
fs.mkdirSync(OUT, { recursive: true });

const AUTH_BASE = 'http://127.0.0.1:8099';
const MAIN_BASE = 'http://localhost:9000';
const TEST_PIN = 'validation-pin-2026';
const WRONG_PIN = 'mauvais-pin-000';
const PROD_TOKEN = process.env.PROD_TOKEN;
const TEST_CONV_A = Number(process.env.TEST_CONV_A || 0);
const TEST_CONV_B = Number(process.env.TEST_CONV_B || 0);

const report = { auth: {}, chat: {}, responsive: {}, routes: {}, issues: [] };

function issue(id, severity, route, detail) {
  report.issues.push({ id, severity, route, detail });
  console.log(`ISSUE ${id} [${severity}] ${route}: ${detail}`);
}

function collectors(page, bucket) {
  bucket.console_errors = [];
  bucket.failed_requests = [];
  bucket.bad_status = [];
  page.on('console', (m) => {
    if (m.type() === 'error') bucket.console_errors.push(m.text().slice(0, 250));
  });
  page.on('pageerror', (e) => bucket.console_errors.push('pageerror: ' + String(e).slice(0, 250)));
  page.on('requestfailed', (r) => {
    // net::ERR_ABORTED sur navigation interne = bénin
    if ((r.failure()?.errorText || '').includes('ERR_ABORTED')) return;
    bucket.failed_requests.push({ url: r.url().slice(0, 160), err: r.failure()?.errorText });
  });
  page.on('response', (resp) => {
    const s = resp.status();
    const url = resp.url();
    if (s >= 400 && !url.includes('favicon')) {
      bucket.bad_status.push({ url: url.slice(0, 160), status: s });
    }
  });
}

async function shot(page, name) {
  await page.screenshot({ path: path.join(OUT, name) });
}

// ─────────────────────────────────────────────────────────────────
async function zoneAuth(browser) {
  const R = (report.auth = {});

  // A. Non configuré
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await ctx.newPage();
    const b = {};
    collectors(page, b);
    // Capture réseau /api pour détecter flash privé ou boucle
    const apiCalls = [];
    page.on('request', (r) => { if (r.url().includes('/api/')) apiCalls.push(r.url()); });
    await page.goto(`${AUTH_BASE}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2500);
    const gate = page.locator('[data-testid="lock-gate"]');
    const gateVisible = await gate.isVisible().catch(() => false);
    const bodyText = await page.locator('body').innerText();
    const privateLeak = /conversation|contact|tâche/i.test(bodyText) && !gateVisible;
    await shot(page, 'auth_A_setup_screen.png');
    // pas de boucle : compter les appels status sur 3s de plus
    const before = apiCalls.length;
    await page.waitForTimeout(3000);
    const growth = apiCalls.length - before;
    R.setup_screen = {
      gate_visible: gateVisible,
      private_flash: privateLeak,
      api_calls_initial: before,
      api_calls_growth_3s: growth,
      console_errors: b.console_errors,
      statut: gateVisible && !privateLeak && growth < 10 ? 'PASS' : 'FAIL',
    };
    if (R.setup_screen.statut === 'FAIL') issue('CVAL-A1', 'P1', '/', 'écran setup incorrect ou boucle');

    // Validation champ court
    const input = page.locator('input[type="password"]').first();
    await input.fill('ab');
    const confirm = page.locator('input[type="password"]').nth(1);
    if (await confirm.isVisible().catch(() => false)) await confirm.fill('ab');
    await page.locator('button[type="submit"]').click();
    await page.waitForTimeout(1200);
    const stillGate = await gate.isVisible().catch(() => false);
    R.setup_short_pin_rejected = { statut: stillGate ? 'PASS' : 'FAIL' };

    // Création PIN de test
    await input.fill(TEST_PIN);
    if (await confirm.isVisible().catch(() => false)) await confirm.fill(TEST_PIN);
    await page.locator('button[type="submit"]').click();
    await page.waitForTimeout(3000);
    const gateGone = !(await gate.isVisible().catch(() => false));
    const st = await page.evaluate(async () => {
      const r = await fetch('/api/auth/status');
      return r.json();
    });
    await shot(page, 'auth_A_after_setup.png');
    R.setup_creates_session = {
      gate_gone: gateGone,
      configured: st.configured,
      authenticated: st.authenticated,
      statut: gateGone && st.configured && st.authenticated ? 'PASS' : 'FAIL',
    };
    if (R.setup_creates_session.statut === 'FAIL') issue('CVAL-A2', 'P0', '/', 'setup ne crée pas de session');
    await ctx.close();
  }

  // B+C. Verrouillé + mauvais secret (nouveau contexte = pas de cookie)
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await ctx.newPage();
    const b = {};
    collectors(page, b);
    await page.goto(`${AUTH_BASE}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2000);
    const gate = page.locator('[data-testid="lock-gate"]');
    const locked = await gate.isVisible().catch(() => false);
    await shot(page, 'auth_B_locked_screen.png');

    // Mauvais secret — soumission avec Entrée
    const input = page.locator('input[type="password"]').first();
    await input.fill(WRONG_PIN);
    let unlockStatus = 0;
    page.on('response', (r) => {
      if (r.url().includes('/api/auth/unlock')) unlockStatus = r.status();
    });
    await input.press('Enter');
    await page.waitForTimeout(2000);
    const stillLocked = await gate.isVisible().catch(() => false);
    const errText = await page.locator('body').innerText();
    const secretInConsole = b.console_errors.some((t) => t.includes(WRONG_PIN));
    await shot(page, 'auth_C_wrong_secret.png');
    report.auth.wrong_secret = {
      locked_screen_first: locked,
      unlock_http: unlockStatus,
      still_locked: stillLocked,
      user_message: /incorrect|erreur|invalide/i.test(errText),
      secret_in_console: secretInConsole,
      statut:
        locked && unlockStatus === 401 && stillLocked && !secretInConsole ? 'PASS' : 'FAIL',
    };
    if (report.auth.wrong_secret.statut === 'FAIL')
      issue('CVAL-C1', 'P1', '/', `mauvais secret: http=${unlockStatus} locked=${stillLocked}`);

    // D. Bon secret
    await input.fill(TEST_PIN);
    await input.press('Enter');
    await page.waitForTimeout(3000);
    const gateGone = !(await gate.isVisible().catch(() => false));
    // refresh conserve la session
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2500);
    const afterRefreshGate = await gate.isVisible().catch(() => false);
    const st = await page.evaluate(async () => (await fetch('/api/auth/status')).json());
    const apiOk = await page.evaluate(async () => (await fetch('/api/tasks')).status);
    await shot(page, 'auth_D_unlocked.png');
    report.auth.unlock = {
      gate_gone: gateGone,
      refresh_keeps_session: !afterRefreshGate && st.authenticated,
      api_tasks_status: apiOk,
      statut: gateGone && !afterRefreshGate && st.authenticated && apiOk === 200 ? 'PASS' : 'FAIL',
    };
    if (report.auth.unlock.statut === 'FAIL') issue('CVAL-D1', 'P0', '/', 'unlock/session KO');

    // E. Logout
    const logoutResp = await page.evaluate(async () => {
      const r = await fetch('/api/auth/logout', { method: 'POST' });
      return r.status;
    });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2500);
    const lockedAgain = await gate.isVisible().catch(() => false);
    const apiAfter = await page.evaluate(async () => (await fetch('/api/tasks')).status);
    // retour navigateur ne réaffiche pas de privé
    await page.goBack().catch(() => {});
    await page.waitForTimeout(1500);
    const backGate = await gate.isVisible().catch(() => false);
    await shot(page, 'auth_E_after_logout.png');
    report.auth.logout = {
      logout_http: logoutResp,
      locked_after_refresh: lockedAgain,
      api_after_logout: apiAfter,
      back_button_locked: backGate,
      statut:
        logoutResp === 200 && lockedAgain && apiAfter === 401 && backGate ? 'PASS' : 'FAIL',
    };
    if (report.auth.logout.statut === 'FAIL')
      issue('CVAL-E1', 'P0', '/', `logout: api_after=${apiAfter} locked=${lockedAgain}`);
    await ctx.close();
  }

  // F. Session révoquée côté serveur
  {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await ctx.newPage();
    await page.goto(`${AUTH_BASE}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(1500);
    const input = page.locator('input[type="password"]').first();
    await input.fill(TEST_PIN);
    await input.press('Enter');
    await page.waitForTimeout(2500);
    // révoquer la session courante via l'API sessions
    const revoked = await page.evaluate(async () => {
      const sessions = await (await fetch('/api/auth/sessions')).json();
      const cur = sessions.sessions.find((s) => s.current);
      if (!cur) return { ok: false };
      const r = await fetch(`/api/auth/sessions/${cur.id}/revoke`, { method: 'POST' });
      return { ok: r.status === 200, id: cur.id };
    });
    // appel API → 401 attendu, puis reload → LockGate
    const apiAfter = await page.evaluate(async () => (await fetch('/api/tasks')).status);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2500);
    const gate = page.locator('[data-testid="lock-gate"]');
    const lockedAgain = await gate.isVisible().catch(() => false);
    const blank = (await page.locator('body').innerText()).trim().length < 5;
    await shot(page, 'auth_F_session_revoked.png');
    report.auth.session_revoked = {
      revoke_ok: revoked.ok,
      api_after_revoke: apiAfter,
      lockgate_after_reload: lockedAgain,
      blank_screen: blank,
      statut: revoked.ok && apiAfter === 401 && lockedAgain && !blank ? 'PASS' : 'FAIL',
    };
    if (report.auth.session_revoked.statut === 'FAIL')
      issue('CVAL-F1', 'P1', '/', `révocation: api=${apiAfter} locked=${lockedAgain}`);
    await ctx.close();
  }
}

// ─────────────────────────────────────────────────────────────────
async function zoneChat(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addCookies([{ name: 'jarvis_session', value: PROD_TOKEN, domain: 'localhost', path: '/', httpOnly: true, sameSite: 'Strict' }]);
  const page = await ctx.newPage();
  const b = {};
  collectors(page, b);
  page.on('dialog', (d) => d.accept());

  const api = async (url, opts) =>
    page.evaluate(async ({ url, opts }) => {
      const r = await fetch(url, { ...opts, headers: { 'Content-Type': 'application/json', ...(opts?.headers || {}) } });
      let body = null;
      try { body = await r.json(); } catch {}
      return { status: r.status, body };
    }, { url, opts });

  await page.goto(`${MAIN_BASE}/chat`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3500);

  const itemA = page.locator('text=TEST-VALIDATION-A').first();
  const visibleA = await itemA.isVisible().catch(() => false);
  report.chat.sidebar_shows_test_conv = visibleA;
  if (!visibleA) issue('CVAL-CH0', 'P1', '/chat', 'conversation de test invisible dans la sidebar');

  // A. Renommer via API PATCH + vérif UI après reload (l'UI inline nécessite le menu ···)
  // — d'abord tentative UI : ouvrir le menu contextuel de la conversation
  let uiRenameOk = false;
  try {
    const row = page.locator('div', { hasText: 'TEST-VALIDATION-A' }).locator('..');
    await itemA.hover();
    // le bouton ··· est dans la même rangée
    const menuBtn = page.locator('button:has-text("···")').first();
    if (await menuBtn.isVisible({ timeout: 2000 }).catch(() => false)) {
      await menuBtn.click();
      const renameOpt = page.locator('button:has-text("Renommer")').first();
      if (await renameOpt.isVisible({ timeout: 1500 }).catch(() => false)) {
        await renameOpt.click();
        const inp = page.locator('input.border-b').first();
        await inp.fill('TEST-VALIDATION-A-RENOMMÉ');
        await inp.press('Enter');
        await page.waitForTimeout(1500);
        uiRenameOk = await page.locator('text=TEST-VALIDATION-A-RENOMMÉ').first().isVisible().catch(() => false);
      }
    }
  } catch (e) { /* fallback API */ }

  if (!uiRenameOk) {
    const r = await api(`/api/conversations/${TEST_CONV_A}`, { method: 'PATCH', body: JSON.stringify({ title: 'TEST-VALIDATION-A-RENOMMÉ' }) });
    report.chat.rename_transport = 'api';
    report.chat.rename_http = r.status;
  } else {
    report.chat.rename_transport = 'ui';
  }
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);
  const renamedVisible = await page.locator('text=TEST-VALIDATION-A-RENOMMÉ').first().isVisible().catch(() => false);
  // titre vide rejeté ?
  const emptyTitle = await api(`/api/conversations/${TEST_CONV_A}`, { method: 'PATCH', body: JSON.stringify({ title: '' }) });
  const detailAfterEmpty = await api(`/api/conversations/${TEST_CONV_A}`);
  const titleAfterEmpty = detailAfterEmpty.body?.conversation?.title ?? detailAfterEmpty.body?.title;
  // titre long
  const longTitle = 'L'.repeat(300);
  await api(`/api/conversations/${TEST_CONV_A}`, { method: 'PATCH', body: JSON.stringify({ title: longTitle }) });
  const detailAfterLong = await api(`/api/conversations/${TEST_CONV_A}`);
  const longStored = (detailAfterLong.body?.conversation?.title ?? detailAfterLong.body?.title ?? '').length;
  await api(`/api/conversations/${TEST_CONV_A}`, { method: 'PATCH', body: JSON.stringify({ title: 'TEST-VALIDATION-A-RENOMMÉ' }) });
  await shot(page, 'chat_rename.png');
  report.chat.rename = {
    persisted_after_refresh: renamedVisible,
    empty_title_http: emptyTitle.status,
    title_after_empty: titleAfterEmpty,
    long_title_stored_len: longStored,
    statut: renamedVisible ? 'PASS' : 'FAIL',
  };
  if (!renamedVisible) issue('CVAL-CH1', 'P1', '/chat', 'rename non persisté');

  // B. Pin / Unpin
  const pin1 = await api(`/api/conversations/${TEST_CONV_A}/pin`, { method: 'POST' });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);
  const bodyTxt = await page.locator('body').innerText();
  const pinnedGroupShown = /épinglé|epinglé|epingle/i.test(bodyTxt);
  const listAfterPin = await api('/api/conversations?archived=false&limit=100');
  const convApin = (listAfterPin.body?.conversations || []).find((c) => c.id === TEST_CONV_A);
  const occurrences = (listAfterPin.body?.conversations || []).filter((c) => c.id === TEST_CONV_A).length;
  await shot(page, 'chat_pin.png');
  const pin2 = await api(`/api/conversations/${TEST_CONV_A}/pin`, { method: 'POST' });
  const listAfterUnpin = await api('/api/conversations?archived=false&limit=100');
  const convAunpin = (listAfterUnpin.body?.conversations || []).find((c) => c.id === TEST_CONV_A);
  report.chat.pin = {
    pin_http: pin1.status,
    pinned_flag: !!convApin?.pinned,
    pinned_group_ui: pinnedGroupShown,
    no_duplicate: occurrences === 1,
    unpin_http: pin2.status,
    unpinned_flag: convAunpin ? !convAunpin.pinned : null,
    statut: pin1.status === 200 && convApin?.pinned && occurrences === 1 && pin2.status === 200 && convAunpin && !convAunpin.pinned ? 'PASS' : 'FAIL',
  };
  if (report.chat.pin.statut === 'FAIL') issue('CVAL-CH2', 'P1', '/chat', JSON.stringify(report.chat.pin));

  // E. Changement de conversation (avant archive/delete)
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);
  const convBItem = page.locator('text=TEST-VALIDATION-B').first();
  if (await convBItem.isVisible().catch(() => false)) {
    await convBItem.click();
    await page.waitForTimeout(2000);
    const msgB = await page.locator('text=message B1 pour la validation').first().isVisible().catch(() => false);
    await page.locator('text=TEST-VALIDATION-A-RENOMMÉ').first().click();
    await page.waitForTimeout(2000);
    const msgA = await page.locator('text=message A1 pour la validation').first().isVisible().catch(() => false);
    const crossContamination = await page.locator('text=message B1 pour la validation').first().isVisible().catch(() => false);
    report.chat.switch = {
      conv_b_content: msgB,
      conv_a_content: msgA,
      no_mixing: !crossContamination,
      statut: msgB && msgA && !crossContamination ? 'PASS' : 'FAIL',
    };
    await shot(page, 'chat_switch.png');
    if (report.chat.switch.statut === 'FAIL') issue('CVAL-CH5', 'P1', '/chat', JSON.stringify(report.chat.switch));
  } else {
    report.chat.switch = { statut: 'FAIL', detail: 'conv B invisible' };
    issue('CVAL-CH5', 'P1', '/chat', 'conversation B invisible');
  }

  // C. Archiver (conv B)
  const arch = await api(`/api/conversations/${TEST_CONV_B}/archive`, { method: 'POST' });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);
  const bGone = !(await page.locator('text=TEST-VALIDATION-B').first().isVisible().catch(() => false));
  const activeList = await api('/api/conversations?archived=false&limit=100');
  const stillActive = (activeList.body?.conversations || []).some((c) => c.id === TEST_CONV_B);
  const archivedList = await api('/api/conversations?archived=true&limit=100');
  const inArchive = (archivedList.body?.conversations || []).some((c) => c.id === TEST_CONV_B);
  await shot(page, 'chat_archive.png');
  report.chat.archive = {
    archive_http: arch.status,
    removed_from_sidebar: bGone,
    absent_active_api: !stillActive,
    present_archived_api: inArchive,
    statut: arch.status === 200 && bGone && !stillActive && inArchive ? 'PASS' : 'FAIL',
  };
  if (report.chat.archive.statut === 'FAIL') issue('CVAL-CH3', 'P1', '/chat', JSON.stringify(report.chat.archive));

  // D. Supprimer (conv A — créée pour ce test)
  const delA = await api(`/api/conversations/${TEST_CONV_A}`, { method: 'DELETE' });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3000);
  const aGone = !(await page.locator('text=TEST-VALIDATION-A-RENOMMÉ').first().isVisible().catch(() => false));
  const detailAfterDel = await api(`/api/conversations/${TEST_CONV_A}`);
  report.chat.delete = {
    delete_http: delA.status,
    gone_from_sidebar: aGone,
    detail_after_delete_http: detailAfterDel.status,
    statut: delA.status === 200 && aGone && detailAfterDel.status === 404 ? 'PASS' : 'FAIL',
  };
  await shot(page, 'chat_delete.png');
  if (report.chat.delete.statut === 'FAIL') issue('CVAL-CH4', 'P1', '/chat', JSON.stringify(report.chat.delete));

  // erreur backend simulée : PATCH sur id inexistant → l'API doit renvoyer 404
  const notFound = await api('/api/conversations/99999999', { method: 'PATCH', body: JSON.stringify({ title: 'x' }) });
  report.chat.error_backend_patch_unknown = { http: notFound.status, statut: notFound.status === 404 ? 'PASS' : 'FAIL' };

  // Nettoyage : suppression conv B (archivée)
  const delB = await api(`/api/conversations/${TEST_CONV_B}`, { method: 'DELETE' });
  report.chat.cleanup = { conv_b_delete_http: delB.status };

  report.chat.console_errors = b.console_errors.slice(0, 10);
  report.chat.bad_status = b.bad_status.filter((s) => ![401, 404].includes(s.status)).slice(0, 10);
  await ctx.close();
}

// ─────────────────────────────────────────────────────────────────
async function zoneResponsive(browser) {
  const sizes = [
    { w: 768, h: 1024, label: '768' },
    { w: 1024, h: 768, label: '1024' },
  ];
  const pages = ['/chat', '/dashboard', '/tasks', '/contacts', '/mission', '/control'];
  for (const size of sizes) {
    report.responsive[size.label] = {};
    const ctx = await browser.newContext({ viewport: { width: size.w, height: size.h } });
    await ctx.addCookies([{ name: 'jarvis_session', value: PROD_TOKEN, domain: 'localhost', path: '/', httpOnly: true, sameSite: 'Strict' }]);
    const page = await ctx.newPage();
    const b = {};
    collectors(page, b);
    for (const route of pages) {
      await page.goto(`${MAIN_BASE}${route}`, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(2500);
      const metrics = await page.evaluate(() => {
        const doc = document.documentElement;
        const hscroll = doc.scrollWidth > doc.clientWidth + 2;
        // éléments interactifs hors viewport horizontal
        const btns = [...document.querySelectorAll('button, a, input, textarea')];
        const clipped = btns.filter((el) => {
          const r = el.getBoundingClientRect();
          return r.width > 0 && (r.right > doc.clientWidth + 4 && r.left < doc.clientWidth);
        }).length;
        const bodyEmpty = (document.body.innerText || '').trim().length < 10;
        return { hscroll, clipped_interactive: clipped, body_empty: bodyEmpty, scrollWidth: doc.scrollWidth, clientWidth: doc.clientWidth };
      });
      const name = `responsive_${size.label}_${route.replace('/', '') || 'home'}.png`;
      await shot(page, name);
      const ok = !metrics.hscroll && !metrics.body_empty;
      report.responsive[size.label][route] = { ...metrics, screenshot: name, statut: ok ? 'PASS' : 'FAIL' };
      if (!ok) issue(`CVAL-R-${size.label}-${route}`, 'P2', route, `hscroll=${metrics.hscroll} vide=${metrics.body_empty} (scroll ${metrics.scrollWidth}/${metrics.clientWidth})`);
    }
    await ctx.close();
  }
}

// ─────────────────────────────────────────────────────────────────
async function zoneRoutes(browser) {
  const routes = ['/calendar', '/map', '/documents', '/analytics', '/search', '/data', '/logs', '/voice', '/voice-debug', '/monitoring', '/mobile', '/control'];
  const interactions = {
    '/calendar': async (p) => { const b = p.locator('button').filter({ hasText: /aujourd|mois|semaine|>|</i }).first(); if (await b.isVisible().catch(() => false)) await b.click(); },
    '/map': async (p) => { const b = p.locator('button').first(); if (await b.isVisible().catch(() => false)) await b.hover(); },
    '/documents': async (p) => { const b = p.locator('button, [role="tab"]').nth(1); if (await b.isVisible().catch(() => false)) await b.click(); },
    '/analytics': async (p) => { const b = p.locator('button').filter({ hasText: /7|30|jour|semaine/i }).first(); if (await b.isVisible().catch(() => false)) await b.click(); },
    '/search': async (p) => { const i = p.locator('input').first(); if (await i.isVisible().catch(() => false)) { await i.fill('test'); await p.waitForTimeout(1200); } },
    '/data': async (p) => { await p.mouse.wheel(0, 400); },
    '/logs': async (p) => { const b = p.locator('button').filter({ hasText: /rafra|refresh|filtre|erreur/i }).first(); if (await b.isVisible().catch(() => false)) await b.click(); },
    '/voice': async (p) => { await p.waitForTimeout(1000); },
    '/voice-debug': async (p) => { const b = p.locator('button').first(); if (await b.isVisible().catch(() => false)) await b.click(); },
    '/monitoring': async (p) => { await p.mouse.wheel(0, 300); },
    '/mobile': async (p) => { await p.mouse.wheel(0, 200); },
    '/control': async (p) => { const b = p.locator('button').filter({ hasText: /logs|rafra|status/i }).first(); if (await b.isVisible().catch(() => false)) await b.click(); },
  };

  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addCookies([{ name: 'jarvis_session', value: PROD_TOKEN, domain: 'localhost', path: '/', httpOnly: true, sameSite: 'Strict' }]);

  for (const route of routes) {
    const page = await ctx.newPage();
    const b = {};
    collectors(page, b);
    const wrongOrigin = [];
    page.on('request', (r) => {
      const u = r.url();
      if (/localhost:(8000|8081|3000|5173)|127\.0\.0\.1:(8000|8081|3000|5173)/.test(u)) wrongOrigin.push(u.slice(0, 140));
    });
    let ok200 = false;
    try {
      const resp = await page.goto(`${MAIN_BASE}${route}`, { waitUntil: 'domcontentloaded', timeout: 20000 });
      ok200 = resp?.status() === 200;
    } catch (e) {
      b.console_errors.push('goto failed: ' + String(e).slice(0, 120));
    }
    await page.waitForTimeout(3000);
    try { await interactions[route]?.(page); } catch {}
    await page.waitForTimeout(2000);
    // refresh direct
    await page.reload({ waitUntil: 'domcontentloaded' }).catch(() => {});
    await page.waitForTimeout(2000);
    const bodyLen = await page.evaluate(() => (document.body.innerText || '').trim().length).catch(() => 0);
    const realErrors = b.console_errors.filter((t) => !/favicon|Download the React DevTools/i.test(t));
    const badApi = b.bad_status.filter((s) => ![401].includes(s.status) && !s.url.includes('favicon'));
    const name = `route_${route.replace('/', '')}.png`;
    await shot(page, name);
    const statut = ok200 && bodyLen > 10 && realErrors.length === 0 && wrongOrigin.length === 0 ? 'PASS' : realErrors.length > 0 || wrongOrigin.length > 0 ? 'FAIL' : 'PARTIAL';
    report.routes[route] = {
      http_200: ok200,
      body_len: bodyLen,
      console_errors: realErrors.slice(0, 6),
      bad_status: badApi.slice(0, 6),
      wrong_origin_calls: wrongOrigin.slice(0, 6),
      screenshot: name,
      statut,
    };
    if (statut === 'FAIL') issue(`CVAL-RT${route}`, badApi.some((s) => s.status >= 500) ? 'P1' : 'P2', route, JSON.stringify({ errors: realErrors.slice(0, 3), badApi: badApi.slice(0, 3), wrongOrigin: wrongOrigin.slice(0, 2) }));
    await page.close();
  }
  await ctx.close();
}

// ─────────────────────────────────────────────────────────────────
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    console.log('=== ZONE 1 : AUTH ===');
    await zoneAuth(browser);
    console.log('=== ZONE 2 : CHAT ===');
    await zoneChat(browser);
    console.log('=== ZONE 3 : RESPONSIVE ===');
    await zoneResponsive(browser);
    console.log('=== ZONE 4 : ROUTES ===');
    await zoneRoutes(browser);
  } finally {
    await browser.close();
  }
  fs.writeFileSync(path.join(ROOT, 'artifacts', 'complement_report.json'), JSON.stringify(report, null, 2));
  console.log('=== RÉSUMÉ ===');
  console.log(JSON.stringify({
    auth: Object.fromEntries(Object.entries(report.auth).map(([k, v]) => [k, v.statut || v])),
    chat: Object.fromEntries(Object.entries(report.chat).filter(([, v]) => v && v.statut).map(([k, v]) => [k, v.statut])),
    responsive: Object.fromEntries(Object.entries(report.responsive).map(([k, v]) => [k, Object.fromEntries(Object.entries(v).map(([r, m]) => [r, m.statut]))])),
    routes: Object.fromEntries(Object.entries(report.routes).map(([k, v]) => [k, v.statut])),
    issues: report.issues.length,
  }, null, 2));
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
