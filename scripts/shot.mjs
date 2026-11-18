// Drive headless Chrome over the DevTools protocol to screenshot MOLECULAB pages, optionally
// after running some JavaScript (to pick options and press buttons).
//
// No dependencies: Node 24 has a global WebSocket, and Chrome is already on the machine. This
// exists because UI work was being shipped without anyone looking at it.
//
//   node scripts/shot.mjs "#/anatomy" out.png
//   node scripts/shot.mjs "#/anatomy" out.png "document.getElementById('anRun').click()" 6000
//
// Arguments: hash route, output file, optional JS to run after load, optional extra wait in ms.

import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const CHROME = process.env.CHROME_PATH ||
  "C:/Program Files/Google/Chrome/Application/chrome.exe";
const BASE = process.env.MOLECULAB_URL || "http://127.0.0.1:8765";
const [route = "#/", outFile = "shot.png", script = "", extraWait = "3000"] =
  process.argv.slice(2);

const port = 9222 + Math.floor(Math.random() * 400);
const profile = fs.mkdtempSync(path.join(os.tmpdir(), "mlab-shot-"));

const chrome = spawn(CHROME, [
  "--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio",
  "--no-first-run", "--no-default-browser-check",
  `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
  "--window-size=1500,1000", "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function targets() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/list`);
      const list = await r.json();
      const page = list.find((t) => t.type === "page");
      if (page?.webSocketDebuggerUrl) return page;
    } catch { /* chrome still starting */ }
    await sleep(250);
  }
  throw new Error("Chrome did not expose a debugging target.");
}

function cdp(ws) {
  let id = 0;
  const pending = new Map();
  ws.addEventListener("message", (e) => {
    const msg = JSON.parse(e.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
    }
  });
  return (method, params = {}) => new Promise((resolve, reject) => {
    const n = ++id;
    pending.set(n, { resolve, reject });
    ws.send(JSON.stringify({ id: n, method, params }));
    setTimeout(() => {
      if (pending.has(n)) { pending.delete(n); reject(new Error(`${method} timed out`)); }
    }, 120000);
  });
}

let code = 0;
try {
  const target = await targets();
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((res, rej) => {
    ws.addEventListener("open", res, { once: true });
    ws.addEventListener("error", rej, { once: true });
  });
  const send = cdp(ws);

  const errors = [];
  await send("Runtime.enable");
  ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.method === "Runtime.exceptionThrown") {
      errors.push(m.params.exceptionDetails?.exception?.description ||
                  m.params.exceptionDetails?.text || "unknown");
    }
  });
  await send("Page.enable");

  // Wait for the real load event rather than guessing. A fixed sleep screenshotted about:blank.
  const loaded = new Promise((res) => {
    const onMsg = (e) => {
      const m = JSON.parse(e.data);
      if (m.method === "Page.loadEventFired") { ws.removeEventListener("message", onMsg); res(); }
    };
    ws.addEventListener("message", onMsg);
    setTimeout(res, 20000);
  });
  await send("Page.navigate", { url: BASE + "/" + route });
  await loaded;

  // The app is a hash router that renders asynchronously; wait for the view to have content.
  for (let i = 0; i < 40; i++) {
    const r = await send("Runtime.evaluate", {
      expression: "(document.getElementById('view')||{}).innerHTML ? " +
                  "document.getElementById('view').innerHTML.length : 0",
      returnByValue: true,
    });
    if ((r.result?.value || 0) > 400) break;
    await sleep(250);
  }

  if (script) {
    const r = await send("Runtime.evaluate",
                         { expression: script, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) {
      console.error("script error:",
                    r.exceptionDetails.exception?.description || r.exceptionDetails.text);
    } else if (r.result?.value !== undefined) {
      console.log("script ->", JSON.stringify(r.result.value).slice(0, 200));
    }
    await sleep(Number(extraWait));
  }

  const shot = await send("Page.captureScreenshot", { format: "png" });
  fs.writeFileSync(outFile, Buffer.from(shot.data, "base64"));
  console.log(`wrote ${outFile} (${fs.statSync(outFile).size} bytes)`);
  if (errors.length) {
    console.log(`page errors: ${errors.length}`);
    errors.slice(0, 5).forEach((e) => console.log("  " + String(e).split("\n")[0]));
  } else {
    console.log("page errors: none");
  }
  ws.close();
} catch (err) {
  console.error("FAILED:", err.message);
  code = 1;
} finally {
  chrome.kill();
  await sleep(300);
  try { fs.rmSync(profile, { recursive: true, force: true }); } catch { /* locked */ }
}
process.exit(code);
