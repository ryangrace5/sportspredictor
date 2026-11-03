"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

// Minimal browser substitute to verify the script safely paints the device
// theme before DOMContentLoaded and handles denied geolocation gracefully.
const handlers = {};
const root = { dataset: {}, style: {} };
const status = { textContent: "" };
const select = {
  value: "auto",
  addEventListener(name, callback) { handlers["select:" + name] = callback; }
};
const preferences = new Map();

global.document = {
  documentElement: root,
  readyState: "loading",
  visibilityState: "visible",
  getElementById(id) {
    return id === "themeStatus" ? status : id === "themeMode" ? select : null;
  },
  addEventListener(name, callback) { handlers[name] = callback; }
};
global.window = {
  isSecureContext: false,
  localStorage: {
    getItem(key) { return preferences.get(key) ?? null; },
    setItem(key, value) { preferences.set(key, value); }
  },
  matchMedia() { return { matches: true, addEventListener() {} }; },
  setInterval() { return 1; }
};
global.navigator = { geolocation: null };

const { solarAltitude, isDaylight } = require("../static/theme.js");

test("uses night mode immediately when device prefers dark before location access", () => {
  assert.equal(root.dataset.theme, "dark");
});

test("uses geographic sunrise and sunset, not a fixed local clock hour", () => {
  // Houston is on Central Standard Time in early November.
  assert.equal(isDaylight(new Date("2026-11-03T18:00:00Z"), 29.76, -95.37), true);
  assert.equal(isDaylight(new Date("2026-11-03T06:00:00Z"), 29.76, -95.37), false);
  // At the same UTC time, Tokyo and Houston can have opposite daylight.
  assert.equal(isDaylight(new Date("2026-11-03T03:00:00Z"), 35.68, 139.69), true);
  assert.equal(isDaylight(new Date("2026-11-03T03:00:00Z"), 29.76, -95.37), false);
});

test("handles polar summer and winter", () => {
  assert.equal(isDaylight(new Date("2026-06-21T00:00:00Z"), 78.22, 15.65), true);
  assert.equal(isDaylight(new Date("2026-12-21T12:00:00Z"), 78.22, 15.65), false);
});

test("invalid coordinates never force arbitrary daylight choices", () => {
  assert.ok(Number.isNaN(solarAltitude(new Date(), 91, 0)));
  assert.equal(isDaylight(new Date(), 91, 0), null);
});

test("denied location falls back to device; Light/Dark overrides persist", () => {
  handlers.DOMContentLoaded();
  assert.equal(root.dataset.theme, "dark");
  assert.match(status.textContent, /Location unavailable/);
  select.value = "light";
  handlers["select:change"]();
  assert.equal(root.dataset.theme, "light");
  assert.equal(preferences.get("bzbetz-theme-preference"), "light");
  select.value = "dark";
  handlers["select:change"]();
  assert.equal(root.dataset.theme, "dark");
  select.value = "auto";
  handlers["select:change"]();
  assert.equal(root.dataset.theme, "dark");
  assert.equal(preferences.get("bzbetz-theme-preference"), "auto");
});

test("each rendered page loads theme JS before CSS and provides the selector", () => {
  const pages = ["index.html", "performance.html", "data_health.html", "layout.html"];
  for (const page of pages) {
    const markup = fs.readFileSync(path.join(__dirname, "..", "templates", page), "utf8");
    const jsIndex = markup.indexOf("theme.js");
    const cssIndex = markup.indexOf("theme.css");
    const baseCssIndex = markup.indexOf("styles.css");
    assert.ok(jsIndex !== -1 && jsIndex < baseCssIndex, page + ": theme initialized early");
    assert.ok(cssIndex > baseCssIndex, page + ": theme overrides last");
    assert.ok(markup.includes('id="themeMode"'), page + ": missing mode selector");
    assert.ok(markup.includes('id="themeStatus"'), page + ": missing theme status");
  }
});
