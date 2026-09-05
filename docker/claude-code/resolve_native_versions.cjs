// Resolve official native releases without executing vendor installers.
async function read(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(15000) });
  if (!response.ok) throw new Error(`Release lookup failed: HTTP ${response.status}`);
  return response.text();
}

async function main() {
  const [cursorInstaller, grokStable] = await Promise.all([
    read('https://cursor.com/install'), read('https://x.ai/cli/stable'),
  ]);
  const cursor = cursorInstaller.match(/https:\/\/downloads\.cursor\.com\/lab\/(\d{4}\.\d{2}\.\d{2}-[a-zA-Z0-9]+)\//);
  const grok = grokStable.trim();
  if (!cursor || !/^\d+\.\d+\.\d+(?:-[A-Za-z0-9._]+)?$/.test(grok)) {
    throw new Error('Invalid native CLI release metadata; image was not rebuilt');
  }
  console.log(`CURSOR_VERSION ${cursor[1]}`);
  console.log(`GROK_BUILD_VERSION ${grok}`);
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
