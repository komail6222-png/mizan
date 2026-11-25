/* Execute the shipped scripts and event handlers offline with a small DOM test double.
   A separate Playwright test covers actual browser layout when Chromium is available. */
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.style = {};
    this.attrs = {};
    this.textContent = "";
  }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(key, value) { this.attrs[key] = value; }
  click() { this.onclick?.(); }
  *all() { yield this; for (const child of this.children) yield* child.all(); }
  text() { return [...this.all()].map(x => x.textContent).join(" "); }
  getContext() {
    const coordinates = (...args) => args.forEach(value => assert.ok(Number.isFinite(value)));
    return {
      scale:coordinates, moveTo:coordinates, lineTo:coordinates, fillText:(_, x, y) => coordinates(x, y),
      beginPath() {}, stroke() {}
    };
  }
}

function exhibit(file) {
  const html = fs.readFileSync(file, "utf8");
  assert.ok(!/<(?:script|link|img)\b[^>]*(?:src|href)=["']https?:/i.test(html));
  const data = html.match(/<script type="application\/json" id="data">([\s\S]+?)<\/script>/)[1];
  const script = html.match(/<script>\s*([\s\S]+?)<\/script>/)[1];
  const nodes = Object.fromEntries(["data","view","tabs","runs","download","trust"].map(n => [n, new Element(n)]));
  nodes.data.textContent = data;
  const product = html.match(/<html[^>]*data-product="([^"]+)"/)?.[1];
  const document = {
    documentElement:{dataset:{product}},
    querySelector:s => nodes[s.slice(1)],
    createElement:tag => new Element(tag)
  };
  let exported;
  const objectURLs = {createObjectURL(blob) { exported = blob; return "blob:fixture"; }, revokeObjectURL() {}};
  // The same verification code the browser runs, executed against Node's WebCrypto (Ed25519, SHA-256).
  const context = {document, URLSearchParams, URL:objectURLs, location:{search:""}, Blob, setTimeout() {},
                   crypto:globalThis.crypto, TextEncoder, atob};
  vm.runInNewContext(script, context);
  function items(tag) { return [...nodes.view.all()].filter(x => x.tag === tag); }
  function click(label) {
    const choice = [...nodes.tabs.all(), ...nodes.view.all()].find(x => x.tag === "button" && x.textContent === label);
    assert.ok(choice, label);
    choice.click();
  }
  return {nodes, items, click, packet:JSON.parse(data), sourceText:data, exported:() => exported, verification:context.mizanVerification,
          tamper:async () => (await context.verifyPacket(context.tampered(context.parseRaw(data)))).rows[0]};
}

const full = exhibit("reports/demo/MIZAN_Demo.html");
const {nodes, items, click} = full;
const report = await full.verification;
assert.equal(report.rows.length, 7);
assert.ok(report.rows.every(r => r.ok), JSON.stringify(report.rows.filter(r => !r.ok)));
assert.equal(report.fingerprint, full.packet.run.key_fingerprint);
assert.match(nodes.trust.text(), /Verified in this browser/);
const forged = await full.tamper();
assert.equal(forged.ok, false);
assert.match(forged.why, /differs from the signed receipt/);
assert.equal(items("q").length, 2);
click("Prefer approved schedule");
// Each redrawn control carries a stable key, which the page uses to give focus back to the same control.
const chosen = items("button").find(x => x.textContent === "Prefer approved schedule");
assert.equal(chosen.attrs["data-key"], "switch:1");
assert.equal(chosen.attrs["aria-pressed"], "true");
assert.ok([...nodes.tabs.all()].filter(x => x.tag === "button").every((x, i) => x.attrs["data-key"] === "tab:" + i));
assert.equal(items("q").length, 1);
assert.match(items("q")[0].textContent, /20 kVA/);
click("BASIRA");
for (const im of items("img")) {
  const buf = Buffer.from(im.src.split(",")[1], "base64");
  assert.equal(buf.subarray(1,4).toString(),"PNG");
  assert.equal(buf.readUInt32BE(16),96);
  assert.equal(buf.readUInt32BE(20),96);
}
click("Intact ring");
assert.match(nodes.view.text(),/false positive/);
click("Only 4 calibration parts");
assert.match(nodes.view.text(), /1 \/ 5 = 0.20/);
click("RASID");
const before = nodes.view.text();
click("Risk-neutral");
assert.notEqual(nodes.view.text(), before);
assert.equal(items("canvas").length, 2);
assert.equal(items("tr").length, 25);
nodes.download.click();
const downloaded = await full.exported().text();
assert.equal(downloaded, full.sourceText);
assert.deepEqual(JSON.parse(downloaded), full.packet);
if (process.argv[2] === "--export" && process.argv[3]) fs.writeFileSync(process.argv[3], downloaded);
const spaces = {};
for (const [kind, count] of [["sanad",1],["basira",3],["rasid",3]]) {
  const app = exhibit("spaces/" + kind + "/index.html");
  assert.equal(app.nodes.tabs.children.length,1);
  assert.equal(app.nodes.tabs.children[0].textContent,kind.toUpperCase());
  assert.equal(app.packet.run.receipts.length,count);
  assert.ok(app.items("h1")[0].textContent.length>5);
  const own = await app.verification;
  assert.equal(own.rows.length, count);
  assert.ok(own.rows.every(r => r.ok));
  spaces[kind]=count;
}
console.log(JSON.stringify({status:"PASS", scope:"Node DOM harness with Node WebCrypto: event handlers and in-page receipt verification", spaces,
  signed_rows_exported:7, receipts_verified_in_page:report.rows.length, tampered_copy_rejected:!forged.ok, rasid_intervals:24,
  native_browser_layout:"see scripts/check_exhibits.py"},null,2));
