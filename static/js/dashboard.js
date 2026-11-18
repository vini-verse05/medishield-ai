/*
 * Draws the three charts on the administrator dashboard.
 * The numbers come from the server as JSON inside <script id="dash-data">,
 * so this file only decides how they look. Chart.js is loaded from a CDN.
 */
(function () {
  "use strict";

  var el = document.getElementById("dash-data");
  if (!el) { return; }
  var d = JSON.parse(el.textContent);

  // If the CDN is unreachable (for example offline), say so instead of leaving blank boxes.
  if (typeof Chart === "undefined") {
    document.querySelectorAll(".chart-box").forEach(function (box) {
      box.innerHTML = '<p class="small muted mb-0">Charts could not load ' +
                      '(Chart.js is loaded from a CDN and needs internet access).</p>';
    });
    return;
  }

  // Colours match the palette in style.css
  var COLORS = {
    accent: "#0e7a7a", accentFill: "rgba(14,122,122,0.12)",
    danger: "#c2412d", dangerFill: "rgba(194,65,45,0.12)",
    neutral: "#c5d0da", reviewed: "#8fa3b5",
    text: "#4b5b6b", grid: "#e6ecf1"
  };

  // Shared look for every chart
  Chart.defaults.font.family = '"Public Sans", system-ui, -apple-system, "Segoe UI", sans-serif';
  Chart.defaults.color = COLORS.text;
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.boxWidth = 8;

  var labels = d.days.map(function (s) { return s.slice(5); });   // "2026-11-18" -> "11-18"
  var levels = d.levels.map(function (s) { return s.charAt(0).toUpperCase() + s.slice(1); });

  // Y axis that starts at zero and only shows whole numbers (counts)
  var countAxis = { beginAtZero: true, ticks: { precision: 0 }, grid: { color: COLORS.grid } };
  var plainX = { grid: { display: false } };

  // 1. Events per day, with failed or denied events drawn on top
  new Chart(document.getElementById("chartActivity"), {
    type: "line",
    data: {
      labels: labels,
      datasets: [
        { label: "All events", data: d.events, borderColor: COLORS.accent,
          backgroundColor: COLORS.accentFill, fill: true, tension: 0.25, pointRadius: 2 },
        { label: "Failed / denied", data: d.failures, borderColor: COLORS.danger,
          backgroundColor: COLORS.dangerFill, tension: 0.25, pointRadius: 2 }
      ]
    },
    options: { responsive: true, maintainAspectRatio: false,
               scales: { x: plainX, y: countAxis } }
  });

  // 2. Normal versus flagged user-days, stacked per day
  new Chart(document.getElementById("chartFlagged"), {
    type: "bar",
    data: {
      labels: labels,
      datasets: [
        { label: "Normal user-days", data: d.normal_days, backgroundColor: COLORS.neutral },
        { label: "Flagged user-days", data: d.flagged_days, backgroundColor: COLORS.danger }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: { x: { stacked: true, grid: { display: false } },
                y: { stacked: true, beginAtZero: true, ticks: { precision: 0 },
                     grid: { color: COLORS.grid } } }
    }
  });

  // 3. Alerts by risk level, split into new and reviewed
  new Chart(document.getElementById("chartRisk"), {
    type: "bar",
    data: {
      labels: levels,
      datasets: [
        { label: "New", data: d.alerts_new, backgroundColor: COLORS.accent },
        { label: "Reviewed", data: d.alerts_reviewed, backgroundColor: COLORS.reviewed }
      ]
    },
    options: { responsive: true, maintainAspectRatio: false,
               scales: { x: plainX, y: countAxis } }
  });
})();
