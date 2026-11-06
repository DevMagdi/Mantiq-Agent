#!/usr/bin/env node
/**
 * Run the Playwright suite with the Chromium bundled in `@sparticuz/chromium`.
 *
 * Why this exists: the Playwright download CDN (`cdn.playwright.dev`) is blocked in some
 * networks, but the npm registry is reachable — and @sparticuz/chromium ships a complete
 * Chromium (plus the Amazon Linux 2023 shared libraries it needs) through npm. This script
 * prepares the binary, inflates the system libraries, exports the two environment variables the
 * config reads (`PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`, `PLAYWRIGHT_CHROMIUM_ARGS`,
 * `LD_LIBRARY_PATH`), and then execs `playwright test` with the remaining argv.
 *
 * Usage: `npm run e2e:local -- e2e/auth.spec.ts` (or just `npm run e2e:local`).
 */
import { spawn } from "node:child_process";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

const sparticuz = (await import("@sparticuz/chromium")).default;
const { inflate } = await import("@sparticuz/chromium");

// On Amazon Linux 2023 the bundled al2023 libraries are already extracted and
// LD_LIBRARY_PATH is set up by the package itself; elsewhere we inflate them manually.
const binDir = join(here, "..", "node_modules", "@sparticuz", "chromium", "bin");
let al2023 = "/";
try {
  al2023 = await inflate(join(binDir, "al2023.tar.br"));
} catch {
  // already inflated or not needed (AL2023 path) — /tmp/al2023 exists from a previous run
  al2023 = join(tmpdir(), "al2023");
}

const executablePath = await sparticuz.executablePath();
// The package ships `--headless='shell'` with embedded quotes (puppeteer strips them); clean for exec argv.
// `--single-process` and `--no-zygote` exist for AWS Lambda (which forbids prctl); on a normal Linux
// runner they make the browser crash intermittently under an e2e suite, so they are dropped here.
// Set SPARTICUZ_LAMBVERSION=1 to keep the exact Lambda argument set.
const lambda = process.env.SPARTICUZ_LAMBDA === "1";
const cleanArgs = sparticuz.args
  .map((arg) => arg.replace(/'/g, ""))
  .filter((arg) => lambda || (arg !== "--single-process" && arg !== "--no-zygote"));
// CI runners ship a small /dev/shm: Chromium crashes without this, and nothing anywhere needs shm.
if (!cleanArgs.includes("--disable-dev-shm-usage")) cleanArgs.push("--disable-dev-shm-usage");

const env = { ...process.env };
const ldPath = join(al2023, "lib");
env.LD_LIBRARY_PATH = env.LD_LIBRARY_PATH ? `${ldPath}:${env.LD_LIBRARY_PATH}` : ldPath;
env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH = executablePath;
env.PLAYWRIGHT_CHROMIUM_ARGS = cleanArgs.join(" ");

const child = spawn(process.execPath, [join(here, "..", "node_modules", "playwright", "cli.js"), "test", ...process.argv.slice(2)], {
  stdio: "inherit",
  env,
});
child.on("exit", (code) => process.exit(code ?? 1));
