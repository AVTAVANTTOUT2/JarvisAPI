// Smoke test du parser briefing.
// Compile briefing-parser.ts via le tsc local de Next.js puis l'importe en ESM.
//
// Usage : cd pwa && node scripts/test-parser.mjs

import { execSync } from 'child_process';
import { existsSync, mkdirSync, readFileSync, rmSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';

const outDir = join(tmpdir(), `jarvis-parser-${Date.now()}`);
mkdirSync(outDir, { recursive: true });

try {
  execSync(
    [
      'node_modules/.bin/tsc',
      'src/lib/briefing-parser.ts',
      '--target ES2022',
      '--module ES2022',
      '--moduleResolution node',
      '--strict false',
      '--skipLibCheck',
      `--outDir ${outDir}`,
    ].join(' '),
    { stdio: 'pipe' }
  );
} catch (e) {
  console.error('tsc transpile failed:');
  console.error(e.stderr?.toString() || e.message);
  process.exit(1);
}

const compiled = join(outDir, 'briefing-parser.js');
if (!existsSync(compiled)) {
  console.error('Compiled output missing:', compiled);
  process.exit(1);
}

const { parseBriefing } = await import(`file://${compiled}`);
const sample = readFileSync('scripts/sample-briefing.txt', 'utf-8');
const sections = parseBriefing(sample);

console.log(`Parsed ${sections.length} sections:\n`);
for (const s of sections) {
  const meta = [];
  if (s.count !== undefined) meta.push(`count=${s.count}`);
  if (s.urgent !== undefined) meta.push(`urgent=${s.urgent}`);
  if (s.items?.length) meta.push(`items=${s.items.length}`);
  console.log(`  - [${s.type.padEnd(10)}] "${s.title}" ${meta.length ? `(${meta.join(', ')})` : ''}`);
  if (s.items?.length) {
    for (const it of s.items.slice(0, 3))
      console.log(`      • ${it.slice(0, 60)}${it.length > 60 ? '...' : ''}`);
  }
}

console.log();
let pass = 0;
let fail = 0;
const found = sections.map((s) => s.type);

function assert(cond, label) {
  if (cond) {
    console.log(`  OK    ${label}`);
    pass++;
  } else {
    console.log(`  FAIL  ${label}`);
    fail++;
  }
}

assert(sections.length >= 5, `>= 5 sections (got ${sections.length})`);
assert(found.includes('emails'), 'has emails section');
assert(found.includes('agenda'), 'has agenda section');
assert(found.includes('priorities'), 'has priorities section');
assert(found.includes('weather'), 'has weather section');
assert(found.includes('messages'), 'has messages section (extracted from prio)');
assert(found.includes('attention'), 'has attention section (Point attention)');

const emails = sections.find((s) => s.type === 'emails');
assert(emails?.count === 12, `emails.count === 12 (got ${emails?.count})`);

const messages = sections.find((s) => s.type === 'messages');
assert(messages?.items?.length >= 5, `messages.items >= 5 (got ${messages?.items?.length})`);

const prio = sections.find((s) => s.type === 'priorities');
assert(prio?.items?.length === 3, `priorities.items === 3 (got ${prio?.items?.length})`);

const noBoldMarkers = !sections.some((s) => s.content.includes('**'));
assert(noBoldMarkers, 'no ** Markdown markers remain');

const noTripleDash = !sections.some((s) => s.content.includes('---'));
assert(noTripleDash, 'no --- separators remain');

// Cleanup
try {
  rmSync(outDir, { recursive: true, force: true });
} catch {
  /* ignore */
}

console.log(`\n=== ${pass} OK · ${fail} FAIL ===\n`);
process.exit(fail > 0 ? 1 : 0);
