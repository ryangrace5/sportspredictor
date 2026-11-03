/*
 * BZ Betz daylight theme — no external sunrise API and no location uploads.
 * The browser asks for geolocation in Auto mode, keeps coordinates only in
 * memory, and falls back to device appearance if access is unavailable.
 */
(function () {
  "use strict";

  const STORAGE_KEY = "bzbetz-theme-preference";
  const ROOT = document.documentElement;
  const DEVICE_DARK = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
  const RADIANS = Math.PI / 180;
  const SUNRISE_ALTITUDE = -0.833 * RADIANS; // upper edge + atmospheric refraction

  function solarAltitude(date, latitude, longitude) {
    if (!(date instanceof Date) || !Number.isFinite(date.getTime()) ||
        !Number.isFinite(latitude) || !Number.isFinite(longitude) ||
        Math.abs(latitude) > 90 || Math.abs(longitude) > 180) {
      return NaN;
    }
    // Solar position from the Julian day, ecliptic longitude, declination,
    // right ascension and local hour angle. Longitude is positive east.
    const days = date.getTime() / 86400000 - 0.5 + 2440588 - 2451545;
    const anomaly = RADIANS * (357.5291 + 0.98560028 * days);
    const longitudeSun = anomaly +
      RADIANS * (1.9148 * Math.sin(anomaly) + 0.02 * Math.sin(2 * anomaly) +
      0.0003 * Math.sin(3 * anomaly)) + RADIANS * 102.9372 + Math.PI;
    const obliquity = 23.4397 * RADIANS;
    const rightAscension = Math.atan2(
      Math.sin(longitudeSun) * Math.cos(obliquity), Math.cos(longitudeSun)
    );
    const declination = Math.asin(Math.sin(longitudeSun) * Math.sin(obliquity));
    const hourAngle = RADIANS * (280.16 + 360.9856235 * days + longitude) -
      rightAscension;
    const lat = latitude * RADIANS;
    return Math.asin(
      Math.sin(lat) * Math.sin(declination) +
      Math.cos(lat) * Math.cos(declination) * Math.cos(hourAngle)
    );
  }

  function isDaylight(date, latitude, longitude) {
    const angle = solarAltitude(date, latitude, longitude);
    return Number.isFinite(angle) ? angle >= SUNRISE_ALTITUDE : null;
  }

  function storedMode() {
    try {
      const mode = window.localStorage.getItem(STORAGE_KEY);
      return mode === "light" || mode === "dark" ? mode : "auto";
    } catch (_) {
      return "auto";
    }
  }

  let mode = storedMode();
  let coordinates = null;
  let requesting = false;
  let locationUnavailable = false;
  let lastLocationCheck = 0;

  function deviceTheme() {
    return DEVICE_DARK && DEVICE_DARK.matches ? "dark" : "light";
  }

  function updateAppearance() {
    let theme, status;
    if (mode === "light" || mode === "dark") {
      theme = mode;
      status = mode === "light" ? "Light mode · manual" : "Dark mode · manual";
    } else {
      const daylight = coordinates
        ? isDaylight(new Date(), coordinates.latitude, coordinates.longitude)
        : null;
      theme = daylight === null ? deviceTheme() : daylight ? "light" : "dark";
      status = daylight === null
        ? (requesting ? "Finding local sunrise and sunset…"
          : locationUnavailable ? "Location unavailable · using device theme"
          : "Using device theme until location is available")
        : daylight ? "Daylight at your location" : "Nighttime at your location";
    }

    ROOT.dataset.theme = theme;
    ROOT.style.colorScheme = theme;
    const label = document.getElementById("themeStatus");
    if (label) label.textContent = status;
    const select = document.getElementById("themeMode");
    if (select && select.value !== mode) select.value = mode;
  }

  // Run in <head> before stylesheets are evaluated to avoid a light flash.
  updateAppearance();

  function requestLocation() {
    if (mode !== "auto" || requesting) return;
    if (!navigator.geolocation || !window.isSecureContext) {
      locationUnavailable = true;
      coordinates = null;
      updateAppearance();
      return;
    }
    requesting = true;
    lastLocationCheck = Date.now();
    updateAppearance();
    navigator.geolocation.getCurrentPosition(
      function (position) {
        requesting = false;
        coordinates = {
          latitude: position.coords.latitude,
          longitude: position.coords.longitude
        };
        locationUnavailable = false;
        updateAppearance();
      },
      function () {
        requesting = false;
        coordinates = null;
        locationUnavailable = true;
        updateAppearance();
      },
      { enableHighAccuracy: false, maximumAge: 15 * 60 * 1000, timeout: 10000 }
    );
  }

  function setMode(nextMode) {
    mode = ["auto", "light", "dark"].includes(nextMode) ? nextMode : "auto";
    try { window.localStorage.setItem(STORAGE_KEY, mode); } catch (_) {}
    if (mode === "auto") requestLocation();
    updateAppearance();
  }

  function init() {
    const select = document.getElementById("themeMode");
    if (select) {
      select.value = mode;
      select.addEventListener("change", function () { setMode(select.value); });
    }
    updateAppearance();
    if (mode === "auto") requestLocation();

    // Check daylight transitions while the app remains open, and recheck
    // location periodically for devices moving between time zones/cities.
    window.setInterval(function () {
      if (mode !== "auto") return;
      updateAppearance();
      if (!locationUnavailable && Date.now() - lastLocationCheck >= 30 * 60 * 1000) {
        requestLocation();
      }
    }, 60 * 1000);

    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") {
        updateAppearance();
        if (mode === "auto" && !locationUnavailable &&
            Date.now() - lastLocationCheck >= 30 * 60 * 1000) {
          requestLocation();
        }
      }
    });
    if (DEVICE_DARK) {
      const changed = function () {
        if (mode === "auto" && !coordinates) updateAppearance();
      };
      if (DEVICE_DARK.addEventListener) DEVICE_DARK.addEventListener("change", changed);
      else if (DEVICE_DARK.addListener) DEVICE_DARK.addListener(changed);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  // Pure functions are exported solely for dependency-free Node unit tests.
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { solarAltitude, isDaylight };
  }
})();
