(function () {
  "use strict";
  var el = document.getElementById("dash-data");
  if (!el) { return; }
  var d = JSON.parse(el.textContent);

  if (typeof Chart === "undefined") {
    document.querySelectorAll(".chart-box").forEach(function (box) {
      box.innerHTML = '<p class="small text-muted mb-0">Charts could not load ' +
                      '(Chart.js is loaded from a CDN and needs internet access).</p>';
    });
    return;
  }

  var labels = d.days.map(function (s) { return s.slice(5); }); // MM-DD
  var levels = d.levels.map(function (s) { return s.charAt(0).toUpperCase() + s.slice(1); });
  var countAxis = { beginAtZero: true, ticks: { precision: 0 } };

  new Chart(document.getElementById("chartActivity"), {
    type: "line",
    data: {
      labels: labels,
      datasets: [
        { label: "All events", data: d.events, borderColor: "#0d6efd",
          backgroundColor: "rgba(13,110,253,0.12)", fill: true, tension: 0.25 },
        { label: "Failed / denied", data: d.failures, borderColor: "#dc3545",
          backgroundColor: "rgba(220,53,69,0.12)", tension: 0.25 }
      ]
    },
    options: { responsive: true, maintainAspectRatio: false, scales: { y: countAxis } }
  });

  new Chart(document.getElementById("chartFlagged"), {
    type: "bar",
    data: {
      labels: labels,
      datasets: [
        { label: "Normal user-days", data: d.normal_days, backgroundColor: "#adb5bd" },
        { label: "Flagged user-days", data: d.flagged_days, backgroundColor: "#dc3545" }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: { x: { stacked: true }, y: { stacked: true, beginAtZero: true,
                                           ticks: { precision: 0 } } }
    }
  });

  new Chart(document.getElementById("chartRisk"), {
    type: "bar",
    data: {
      labels: levels,
      datasets: [
        { label: "New", data: d.alerts_new, backgroundColor: "#0d6efd" },
        { label: "Reviewed", data: d.alerts_reviewed, backgroundColor: "#198754" }
      ]
    },
    options: { responsive: true, maintainAspectRatio: false, scales: { y: countAxis } }
  });
})();