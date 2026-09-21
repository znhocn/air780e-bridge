const fs = require("fs");
const path = require("path");
global.window = {};
global.navigator = { language: "en-US" };
global.document = {
  readyState: "complete",
  addEventListener() {},
  querySelector() { return null; },
  querySelectorAll() { return []; },
  documentElement: {},
};
require(path.join(__dirname, "i18n.js"));
const I = global.window.i18n;
const appSrc = fs.readFileSync(path.join(__dirname, "app.js"), "utf8");
const htmlSrc = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");
const refs = [
  ...appSrc.matchAll(/\b(?:T|POO|POO2|POO3|P)\(\s*["']([^"']+)["']/g),
  ...htmlSrc.matchAll(/data-i18n(?:-title|-ph)?="([^"]+)"/g),
].map((m) => m[1]);
const keys = [...new Set(refs)];
const audit = (lang) => {
  const d = (I.dicts || {})[lang] || {};
  return keys.filter((k) => d[k] === undefined || d[k] === null || d[k] === "");
};
const flat = Object.keys(I.dicts || {});
for (const l of flat) {
  const miss = audit(l);
  console.log(
    l.padEnd(3),
    (keys.length - miss.length) + "/" + keys.length,
    "resolve | MISSING:",
    miss.length ? miss.join(", ") : "none"
  );
}
