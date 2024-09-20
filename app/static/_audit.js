const fs = require("fs");
global.window = {};
global.navigator = { language: "en-US" };
global.document = {
  readyState: "complete",
  addEventListener() {},
  querySelector() { return null; },
  querySelectorAll() { return []; },
  documentElement: {},
};
require("./i18n.js");
const I = global.window.i18n;
const refs = [...fs
  .readFileSync("app.js", "utf8")
  .matchAll(/\b(?:T|POO|POO2|POO3|P)\(\s*["']([^"']+)["']/g)].map((m) => m[1]);
const keys = [...new Set(refs)];
const audit = (lang) => {
  const d = (I.dict || {})[lang] || {};
  return keys.filter((k) => d[k] === undefined || d[k] === null || d[k] === "");
};
const flat = Object.keys(I.dict || {});
for (const l of flat) {
  const miss = audit(l);
  console.log(
    l.padEnd(3),
    (keys.length - miss.length) + "/" + keys.length,
    "resolve | MISSING:",
    miss.length ? miss.join(", ") : "none"
  );
}
