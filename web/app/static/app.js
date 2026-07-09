(function () {
  const STORAGE_KEY = "cue_info_shown";
  const TOAST_MS = 5000;

  function hideToast() {
    const el = document.getElementById("toast");
    if (el) el.classList.add("toast--hidden");
  }

  function showToast(html) {
    if (sessionStorage.getItem(STORAGE_KEY)) return;
    const el = document.getElementById("toast");
    const body = document.getElementById("toast-body");
    if (!el || !body) return;
    body.innerHTML = html;
    el.classList.remove("toast--hidden");
    sessionStorage.setItem(STORAGE_KEY, "1");
    const timer = setTimeout(hideToast, TOAST_MS);
    const closeBtn = document.getElementById("toast-close");
    if (closeBtn) {
      closeBtn.onclick = function () {
        clearTimeout(timer);
        hideToast();
      };
    }
  }

  function formatCueToast(data) {
    const files = (data.resolved || data.files || []).join(", ");
    let html = "<strong>CUE загружен</strong><br>" + data.cue + " → " + files;
    if (data.tracks) html += "<br>Треков: " + data.tracks;
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
      showToast(formatCueToast(data));
    } catch (_e) { /* ignore */ }
    node.remove();
  }

  document.body.addEventListener("htmx:afterSwap", function (ev) {
    if (ev.detail && ev.detail.target && ev.detail.target.id === "process-panel") {
      tryCueToastFromPayload();
    }
  });

  document.addEventListener("DOMContentLoaded", tryCueToastFromPayload);
})();
