(function () {
  const CUE_STORAGE_KEY = "cue_info_shown";
  const TOAST_MS = 5000;
  const ACTIVE = new Set(["queued", "processing"]);

  let jobStatuses = new Map();
  let jobsSeeded = false;
  let toastTimer = null;
  let toastQueue = [];
  let toastShowing = false;

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function hideToast() {
    const el = document.getElementById("toast");
    if (el) el.classList.add("toast--hidden");
  }

  function bindToastClose(onClose) {
    const closeBtn = document.getElementById("toast-close");
    if (!closeBtn) return;
    closeBtn.onclick = function () {
      if (toastTimer) clearTimeout(toastTimer);
      hideToast();
      toastShowing = false;
      if (onClose) onClose();
      else drainToastQueue();
    };
  }

  function presentToast(html, extraClass) {
    const el = document.getElementById("toast");
    const body = document.getElementById("toast-body");
    if (!el || !body) return;
    el.classList.remove("toast--hidden", "toast--done", "toast--error", "toast--cancelled");
    if (extraClass) el.classList.add(extraClass);
    body.innerHTML = html;
    bindToastClose(function () {
      drainToastQueue();
    });
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      hideToast();
      toastShowing = false;
      drainToastQueue();
    }, TOAST_MS);
  }

  function drainToastQueue() {
    if (toastShowing || !toastQueue.length) return;
    toastShowing = true;
    const item = toastQueue.shift();
    presentToast(item.html, item.extraClass);
  }

  function enqueueToast(html, extraClass) {
    toastQueue.push({ html: html, extraClass: extraClass || "" });
    drainToastQueue();
  }

  function showCueToast(html) {
    if (sessionStorage.getItem(CUE_STORAGE_KEY)) return;
    sessionStorage.setItem(CUE_STORAGE_KEY, "1");
    toastQueue.unshift({ html: html, extraClass: "" });
    drainToastQueue();
  }

  function formatCueToast(data) {
    const files = (data.resolved || data.files || []).join(", ");
    let html = "<strong>CUE загружен</strong><br>" + escapeHtml(data.cue) + " → " + escapeHtml(files);
    if (data.tracks) html += "<br>Треков: " + escapeHtml(data.tracks);
    if (data.multi_file) {
      html += "<br>Несколько файлов — доступна пакетная обработка";
    } else {
      html += "<br>Сплит или обработка образа — блок CUE ниже";
    }
    return html;
  }

  function tryCueToastFromPayload() {
    const node = document.getElementById("cue-toast-payload");
    if (!node) return;
    try {
      const data = JSON.parse(node.textContent);
      showCueToast(formatCueToast(data));
    } catch (_e) { /* ignore */ }
    node.remove();
  }

  function formatJobDoneToast(row) {
    const id = row.dataset.jobId;
    const name = row.dataset.jobFilename || id;
    const status = row.dataset.jobStatus;
    const dur = row.dataset.jobDuration;

    if (status === "done") {
      let html = "<strong>Готово</strong><br>#" + escapeHtml(id) + " · " + escapeHtml(name);
      if (dur) html += "<br><span class=\"toast-hint\">" + escapeHtml(dur) + " аудио</span>";
      return { html: html, extraClass: "toast--done" };
    }
    if (status === "cancelled") {
      return {
        html: "<strong>Прервано</strong><br>#" + escapeHtml(id) + " · " + escapeHtml(name),
        extraClass: "toast--cancelled",
      };
    }
    if (status === "failed") {
      const err = row.dataset.jobError || "ошибка";
      return {
        html:
          "<strong>Ошибка</strong><br>#" + escapeHtml(id) + " · " + escapeHtml(name) +
          "<br><span class=\"toast-hint\">" + escapeHtml(err) + "</span>",
        extraClass: "toast--error",
      };
    }
    return null;
  }

  function scanJobsTable(root) {
    const rows = root.querySelectorAll("tr[data-job-id]");
    for (const row of rows) {
      const id = row.dataset.jobId;
      const status = row.dataset.jobStatus;
      if (!id || !status) continue;

      const prev = jobStatuses.get(id);
      if (jobsSeeded && prev && ACTIVE.has(prev) && !ACTIVE.has(status)) {
        const toast = formatJobDoneToast(row);
        if (toast) enqueueToast(toast.html, toast.extraClass);
      }
      jobStatuses.set(id, status);
    }
    jobsSeeded = true;
  }

  document.body.addEventListener("htmx:afterSwap", function (ev) {
    const target = ev.detail && ev.detail.target;
    if (!target) return;
    if (target.id === "process-panel") tryCueToastFromPayload();
    if (target.id === "jobs-panel") scanJobsTable(target);
  });

  document.addEventListener("DOMContentLoaded", function () {
    tryCueToastFromPayload();
    const panel = document.getElementById("jobs-panel");
    if (panel) scanJobsTable(panel);
  });
})();
