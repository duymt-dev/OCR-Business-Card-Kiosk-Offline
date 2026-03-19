window.addEventListener("load", function () {
  const loader = document.getElementById("loader");
  if (!loader) return;
  setTimeout(function () {
    loader.classList.add("fadeOut");
  }, 300);
});

window.addEventListener("DOMContentLoaded", function () {
  const BREAKPOINT = 1280;
  const body = document.body;
  const toggleButtons = document.querySelectorAll(".sidebar-toggle");
  const screenLinks = document.querySelectorAll("[data-screen-target]");
  const screens = document.querySelectorAll("[data-screen]");

  const globalSearchInput = document.getElementById("globalSearchInput");
  const tableSearchInput = document.getElementById("tableSearchInput");
  const databaseTableBody = document.getElementById("databaseTableBody");
  const databaseEmptyRow = document.getElementById("databaseEmptyRow");
  const recentActivityBody = document.getElementById("recentActivityBody");
  const recentEmptyRow = document.getElementById("recentEmptyRow");
  const exportExcelBtn = document.getElementById("exportExcelBtn");
  const notifCounter = document.getElementById("notifCounter");
  const notificationList = document.getElementById("notificationList");
  const notificationEmpty = document.getElementById("notificationEmpty");
  const notificationMeta = document.getElementById("notificationMeta");
  const logoutBtn = document.getElementById("logoutBtn");
  const editModal = document.getElementById("editModal");
  const confirmModal = document.getElementById("confirmModal");
  const exportModal = document.getElementById("exportModal");
  const editFullName = document.getElementById("editFullName");
  const editCompany = document.getElementById("editCompany");
  const editEmail = document.getElementById("editEmail");
  const editPhone = document.getElementById("editPhone");
  const editTitle = document.getElementById("editTitle");
  const editConfirmBtn = document.getElementById("editConfirmBtn");
  const editCancelBtn = document.getElementById("editCancelBtn");
  const editCloseBtn = document.getElementById("editCloseBtn");
  const exportConfirmBtn = document.getElementById("exportConfirmBtn");
  const exportCancelBtn = document.getElementById("exportCancelBtn");
  const exportCloseBtn = document.getElementById("exportCloseBtn");
  const exportDateFrom = document.getElementById("exportDateFrom");
  const exportDateTo = document.getElementById("exportDateTo");
  const confirmYesBtn = document.getElementById("confirmYesBtn");
  const confirmNoBtn = document.getElementById("confirmNoBtn");
  const confirmModalMessage = document.getElementById("confirmModalMessage");

  const statTotal = document.getElementById("statTotal");
  const statToday = document.getElementById("statToday");
  const statWithEmail = document.getElementById("statWithEmail");
  const statWithPhone = document.getElementById("statWithPhone");
  const recentPrevBtn = document.getElementById("recentPrevBtn");
  const recentNextBtn = document.getElementById("recentNextBtn");
  const recentPageInfo = document.getElementById("recentPageInfo");
  const databasePrevBtn = document.getElementById("databasePrevBtn");
  const databaseNextBtn = document.getElementById("databaseNextBtn");
  const databasePageInfo = document.getElementById("databasePageInfo");

  let manualOverride = false;
  let syncingSearch = false;
  let selectedRegistrationId = null;
  let currentSearchKeyword = "";
  let imageLightboxEl = null;
  let imageLightboxTargetEl = null;
  let currentEditItem = null;
  let confirmCallback = null;
  let confirmCancelCallback = null;
  const LIGHTBOX_ZOOM_SCALE = {
    face: 1.8,
    bcard: 0.7,
    default: 1,
  };
  const PAGE_SIZE = 10;
  const MAX_NOTIFICATION_ROWS = 5;
  const databasePagination = { page: 1, totalPages: 1, total: 0 };
  const recentPagination = { page: 1, totalPages: 1, total: 0 };
  let notificationItems = [];

  function syncSidebarState() {
    if (window.innerWidth <= BREAKPOINT) {
      body.classList.add("sidebar-collapsed");
      return;
    }
    if (!manualOverride) {
      body.classList.remove("sidebar-collapsed");
    }
  }

  function normalizeText(value) {
    return (value || "").trim().toLowerCase();
  }

  function setActiveScreen(screenName) {
    screens.forEach(function (screen) {
      screen.classList.toggle("is-active", screen.dataset.screen === screenName);
    });

    screenLinks.forEach(function (link) {
      link.classList.toggle(
        "active",
        link.getAttribute("data-screen-target") === screenName
      );
    });
  }

  function formatCell(value) {
    return value === null || value === undefined || value === "" ? "-" : String(value);
  }

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function formatRelativeTime(value) {
    if (!value) return "たった今";
    const parsed = Date.parse(String(value).replace(" ", "T"));
    if (Number.isNaN(parsed)) return String(value);
    const diffSec = Math.max(0, Math.floor((Date.now() - parsed) / 1000));
    if (diffSec < 60) return diffSec + "秒前";
    if (diffSec < 3600) return Math.floor(diffSec / 60) + "分前";
    if (diffSec < 86400) return Math.floor(diffSec / 3600) + "時間前";
    return Math.floor(diffSec / 86400) + "日前";
  }

  async function fetchJsonWithAuth(url, options) {
    const res = await fetch(url, options);
    if (res.status === 401) {
      window.location.href = "/login";
      return null;
    }
    return res.json();
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = formatCell(value);
  }

  function setDetailSelected(active) {
    const dot = document.getElementById("detailDot");
    if (!dot) return;
    dot.classList.toggle("active", !!active);
  }

  function openModal(modal) {
    if (!modal) return;
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
  }

  function closeModal(modal) {
    if (!modal) return;
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
  }

  function openConfirm(message, onConfirm, onCancel) {
    if (confirmModalMessage) {
      confirmModalMessage.textContent = message || "Are you sure?";
    }
    confirmCallback = typeof onConfirm === "function" ? onConfirm : null;
    confirmCancelCallback = typeof onCancel === "function" ? onCancel : null;
    openModal(confirmModal);
  }

  function closeConfirm() {
    confirmCallback = null;
    confirmCancelCallback = null;
    closeModal(confirmModal);
  }

  function openEditModal(item) {
    currentEditItem = item || null;
    if (editFullName) editFullName.value = item && item.full_name ? item.full_name : "";
    if (editCompany) editCompany.value = item && item.company ? item.company : "";
    if (editEmail) editEmail.value = item && item.email ? item.email : "";
    if (editPhone) editPhone.value = item && item.phone ? item.phone : "";
    if (editTitle) editTitle.value = item && item.title ? item.title : "";
    openModal(editModal);
  }

  function closeEditModal() {
    currentEditItem = null;
    closeModal(editModal);
  }

  function openExportModal() {
    openModal(exportModal);
  }

  function closeExportModal() {
    closeModal(exportModal);
  }

  async function updateRegistration(regId, payload) {
    const js = await fetchJsonWithAuth(
      "/api/dashboard/registrations/" + encodeURIComponent(regId),
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      }
    );
    return js && js.ok;
  }

  async function deleteRegistration(regId) {
    const js = await fetchJsonWithAuth(
      "/api/dashboard/registrations/" + encodeURIComponent(regId),
      { method: "DELETE" }
    );
    return js && js.ok;
  }

  function ensureDetailImageCard() {
    const panel = document.querySelector(".guest-detail-panel");
    if (!panel) return null;
    let card = document.getElementById("detailImageCard");
    if (!card) {
      card = document.createElement("div");
      card.id = "detailImageCard";
      card.className = "guest-detail-card";
      card.innerHTML =
        "<h6>画像</h6>" +
        "<div id='detailImageGrid' style='display:grid;grid-template-columns:1fr 1fr;gap:10px;'>" +
        "  <figure style='margin:0;'>" +
        "    <figcaption style='font-size:12px;color:#475569;margin-bottom:6px;'>顔</figcaption>" +
        "    <img id='detailFaceImg' alt='Face image' style='width:100%;height:120px;object-fit:cover;border:1px solid #e2e8f0;display:none;background:#f8fafc;cursor:zoom-in;' />" +
        "  </figure>" +
        "  <figure style='margin:0;'>" +
        "    <figcaption style='font-size:12px;color:#475569;margin-bottom:6px;'>名刺</figcaption>" +
        "    <img id='detailBcardImg' alt='Business card image' style='width:100%;height:120px;object-fit:cover;border:1px solid #e2e8f0;display:none;background:#f8fafc;cursor:zoom-in;' />" +
        "  </figure>" +
        "</div>" +
        "<p id='detailImageEmpty' style='margin-top:8px;'>画像がありません。</p>";
      panel.appendChild(card);
    }
    return {
      faceImg: document.getElementById("detailFaceImg"),
      bcardImg: document.getElementById("detailBcardImg"),
      emptyText: document.getElementById("detailImageEmpty"),
    };
  }

  function ensureDetailRawTextCard() {
    const panel = document.querySelector(".guest-detail-panel");
    if (!panel) return null;
    let card = document.getElementById("detailRawTextCard");
    if (!card) {
      card = document.createElement("div");
      card.id = "detailRawTextCard";
      card.className = "guest-detail-card";
      card.innerHTML =
        "<h6>OCR生テキスト</h6>" +
        "<pre id='detailRawText' style='margin:0;white-space:pre-wrap;word-break:break-word;'>-</pre>";
      panel.appendChild(card);
    }
    return {
      rawText: document.getElementById("detailRawText"),
    };
  }

  function buildRegistrationAssetUrl(detail, key, registrationId) {
    const raw = detail && detail[key] ? String(detail[key]).trim() : "";
    if (!raw || !registrationId) return "";
    if (/^https?:\/\//i.test(raw) || raw.startsWith("/registrations/")) return raw;
    const normalized = raw.replaceAll("\\", "/");
    const fileName = normalized.split("/").pop();
    if (!fileName) return "";
    return "/registrations/" + encodeURIComponent(registrationId) + "/" + encodeURIComponent(fileName);
  }

  function applyDetailImage(imgEl, src) {
    if (!imgEl) return false;
    if (!src) {
      imgEl.removeAttribute("src");
      imgEl.style.display = "none";
      return false;
    }
    imgEl.onerror = function () {
      imgEl.style.display = "none";
    };
    imgEl.src = src;
    imgEl.style.display = "block";
    return true;
  }

  function closeImageLightbox() {
    if (!imageLightboxEl) return;
    imageLightboxEl.classList.remove("open");
    imageLightboxEl.setAttribute("aria-hidden", "true");
    if (imageLightboxTargetEl) {
      imageLightboxTargetEl.classList.remove("is-zoomed");
      imageLightboxTargetEl.style.removeProperty("--lightbox-zoom-scale");
      imageLightboxTargetEl.removeAttribute("src");
      imageLightboxTargetEl.alt = "画像プレビュー";
    }
  }

  function ensureImageLightbox() {
    if (imageLightboxEl) return imageLightboxEl;
    const lightbox = document.createElement("div");
    lightbox.id = "dashboardImageLightbox";
    lightbox.className = "image-lightbox";
    lightbox.setAttribute("aria-hidden", "true");
    lightbox.innerHTML =
      "<button type='button' class='image-lightbox-close' aria-label='Close image preview'>&times;</button>" +
      "<img id='dashboardImageLightboxTarget' alt='画像プレビュー' class='image-lightbox-image' />";
    document.body.appendChild(lightbox);
    imageLightboxEl = lightbox;
    imageLightboxTargetEl = document.getElementById("dashboardImageLightboxTarget");

    lightbox.addEventListener("click", function (event) {
      if (
        event.target === lightbox ||
        event.target.classList.contains("image-lightbox-close")
      ) {
        closeImageLightbox();
      }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") closeImageLightbox();
    });
    return lightbox;
  }

  function openImageLightbox(src, alt, imageType) {
    if (!src) return;
    ensureImageLightbox();
    if (!imageLightboxEl || !imageLightboxTargetEl) return;
    const zoomScale =
      LIGHTBOX_ZOOM_SCALE[imageType] || LIGHTBOX_ZOOM_SCALE.default;
    imageLightboxTargetEl.src = src;
    imageLightboxTargetEl.alt = alt || "画像プレビュー";
    imageLightboxTargetEl.style.setProperty("--lightbox-zoom-scale", String(zoomScale));
    imageLightboxTargetEl.classList.add("is-zoomed");
    imageLightboxEl.classList.add("open");
    imageLightboxEl.setAttribute("aria-hidden", "false");
  }

  function bindDetailImageZoom(imgEl, altText, imageType) {
    if (!imgEl || imgEl.dataset.zoomBound === "1") return;
    imgEl.dataset.zoomBound = "1";
    imgEl.addEventListener("click", function () {
      if (!imgEl.src || imgEl.style.display === "none") return;
      openImageLightbox(imgEl.src, altText, imageType);
    });
  }

  function renderDetailImages(item, detailItem) {
    const refs = ensureDetailImageCard();
    if (!refs) return;
    bindDetailImageZoom(refs.faceImg, "Face image preview", "face");
    bindDetailImageZoom(refs.bcardImg, "Business card image preview", "bcard");
    if (!item) {
      applyDetailImage(refs.faceImg, "");
      applyDetailImage(refs.bcardImg, "");
      if (refs.emptyText) refs.emptyText.style.display = "block";
      return;
    }
    const detail = detailItem || item || {};
    const regId = detail.registration_id || item.registration_id || "";
    const faceSrc = buildRegistrationAssetUrl(detail, "face_link", regId);
    const bcardSrc = buildRegistrationAssetUrl(detail, "bcard_link", regId);
    const hasFace = applyDetailImage(refs.faceImg, faceSrc);
    const hasBcard = applyDetailImage(refs.bcardImg, bcardSrc);
    if (refs.emptyText) refs.emptyText.style.display = hasFace || hasBcard ? "none" : "block";
  }

  function renderDetailRawText(item, detailItem) {
    const refs = ensureDetailRawTextCard();
    if (!refs || !refs.rawText) return;
    if (!item) {
      refs.rawText.textContent = "-";
      return;
    }
    const detail = detailItem || item || {};
    refs.rawText.textContent = formatCell(detail.last_bcard_text);
  }

  function renderDetailPanel(item, detailItem) {
    if (!item) {
      setText("detailName", "ゲストを選択");
      setText("detailInfo", "一覧の行をクリックして詳細を表示します。");
      setText("detailCompany", "-");
      setText("detailActivity", "-");
      setDetailSelected(false);
      renderDetailImages(null, null);
      renderDetailRawText(null, null);
      return;
    }

    const detail = detailItem || item;
    setText("detailName", item.full_name || item.registration_id || "不明");
    setText(
      "detailInfo",
      [
        "ID: " + formatCell(item.registration_id),
        "氏名: " + formatCell(item.full_name),
        "会社: " + formatCell(item.company),
        "メール: " + formatCell(item.email),
        "電話: " + formatCell(item.phone),
        "役職: " + formatCell(item.title),
      ].join("\n")
    );
    setText("detailCompany", "-"); // Keep for safety if element still exists in DOM briefly
    setText(
      "detailActivity",
      [
        "登録日時: " + formatCell(item.created_at),
        "登録ID: " + formatCell(item.registration_id),
      ].join("\n")
    );
    setDetailSelected(true);
    renderDetailImages(item, detail);
    renderDetailRawText(item, detail);
  }

  async function loadRegistrationDetail(registrationId) {
    try {
      const js = await fetchJsonWithAuth("/api/dashboard/registrations/" + encodeURIComponent(registrationId));
      if (!js || !js.ok) return null;
      return js.item || null;
    } catch (e) {
      console.error("loadRegistrationDetail error:", e);
      return null;
    }
  }

  function clearSelectedRows() {
    Array.from(databaseTableBody.querySelectorAll("tr.clickable-row.selected")).forEach(function (row) {
      row.classList.remove("selected");
    });
  }

  async function selectRow(row, item) {
    clearSelectedRows();
    row.classList.add("selected");
    selectedRegistrationId = item.registration_id || null;
    renderDetailPanel(item, null);
    if (!selectedRegistrationId) return;

    const detail = await loadRegistrationDetail(selectedRegistrationId);
    if (selectedRegistrationId !== (item.registration_id || null)) return;
    renderDetailPanel(item, detail);
  }

  function cloneRowFromItem(item) {
    const row = document.createElement("tr");
    const cells = [
      item.registration_id,
      item.full_name,
      item.company,
      item.email,
      item.phone,
    ];

    cells.forEach(function (value) {
      const td = document.createElement("td");
      td.textContent = formatCell(value);
      row.appendChild(td);
    });

    const actionTd = document.createElement("td");
    actionTd.className = "table-actions";
    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "action-btn action-edit";
    editBtn.textContent = "修正";
    editBtn.addEventListener("click", function (event) {
      event.stopPropagation();
      openEditModal(item);
    });
    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "action-btn action-delete";
    deleteBtn.textContent = "削除";
    deleteBtn.addEventListener("click", function (event) {
      event.stopPropagation();
      openConfirm("この登録を削除しますか？", async function () {
        const regId = item.registration_id || "";
        if (!regId) return;
        const ok = await deleteRegistration(regId);
        if (!ok) {
          alert("削除に失敗しました。");
          return;
        }
        if (selectedRegistrationId === regId) {
          selectedRegistrationId = null;
        }
        await loadDatabase(currentSearchKeyword, databasePagination.page);
        renderDetailPanel(null, null);
      });
    });
    actionTd.appendChild(editBtn);
    actionTd.appendChild(deleteBtn);
    row.appendChild(actionTd);
    return row;
  }

  function clearGeneratedRows(root) {
    Array.from(root.querySelectorAll("tr.generated-row")).forEach(function (row) {
      row.remove();
    });
  }

  function renderDatabaseTable(items) {
    clearGeneratedRows(databaseTableBody);
    if (!items.length) {
      databaseEmptyRow.style.display = "table-row";
      exportExcelBtn.disabled = true;
      renderDetailPanel(null, null);
      return;
    }

    items.forEach(function (item) {
      const row = cloneRowFromItem(item);
      row.className = "generated-row clickable-row";
      row.addEventListener("click", function () {
        selectRow(row, item);
      });
      databaseTableBody.insertBefore(row, databaseEmptyRow);
    });

    databaseEmptyRow.style.display = "none";
    exportExcelBtn.disabled = false;

    const selectedRow = Array.from(databaseTableBody.querySelectorAll("tr.clickable-row")).find(function (row) {
      return row.firstChild && row.firstChild.textContent === selectedRegistrationId;
    });
    if (selectedRow) {
      selectedRow.classList.add("selected");
    } else {
      renderDetailPanel(null, null);
    }
  }

  function renderRecentActivity(items) {
    clearGeneratedRows(recentActivityBody);
    if (!items.length) {
      recentEmptyRow.style.display = "table-row";
      return;
    }

    items.forEach(function (item) {
      const row = cloneRowFromItem(item);
      row.className = "generated-row";
      recentActivityBody.insertBefore(row, recentEmptyRow);
    });

    recentEmptyRow.style.display = "none";
  }

  async function loadStats() {
    try {
      const js = await fetchJsonWithAuth("/api/dashboard/stats");
      if (!js || !js.ok) return;
      const stats = js.stats || {};
      if (statTotal) statTotal.textContent = formatCell(stats.total);
      if (statToday) statToday.textContent = formatCell(stats.today);
      if (statWithEmail) statWithEmail.textContent = formatCell(stats.with_email);
      if (statWithPhone) statWithPhone.textContent = formatCell(stats.with_phone);
    } catch (e) {
      console.error("loadStats error:", e);
    }
  }

  async function fetchRegistrations(searchKeyword, page, pageSize) {
    const params = new URLSearchParams({
      page: String(page || 1),
      page_size: String(pageSize),
      sort_by: "created_at",
      sort_dir: "desc",
      search: searchKeyword || "",
    });
    const js = await fetchJsonWithAuth("/api/dashboard/registrations?" + params.toString());
    if (!js || !js.ok) {
      return { items: [], page: 1, total_pages: 1, total: 0 };
    }
    return {
      items: Array.isArray(js.items) ? js.items : [],
      page: Number(js.page) || 1,
      total_pages: Math.max(1, Number(js.total_pages) || 1),
      total: Number(js.total) || 0,
    };
  }

  function updatePaginationControls(type) {
    if (type === "database") {
      if (databasePageInfo) {
        databasePageInfo.textContent =
          "Page " + databasePagination.page + " / " + databasePagination.totalPages;
      }
      if (databasePrevBtn) {
        databasePrevBtn.disabled = databasePagination.page <= 1;
      }
      if (databaseNextBtn) {
        databaseNextBtn.disabled = databasePagination.page >= databasePagination.totalPages;
      }
      return;
    }

    if (recentPageInfo) {
      recentPageInfo.textContent =
        "Page " + recentPagination.page + " / " + recentPagination.totalPages;
    }
    if (recentPrevBtn) {
      recentPrevBtn.disabled = recentPagination.page <= 1;
    }
    if (recentNextBtn) {
      recentNextBtn.disabled = recentPagination.page >= recentPagination.totalPages;
    }
  }

  function clearNotificationRows() {
    if (!notificationList) return;
    Array.from(notificationList.querySelectorAll("li.generated-row")).forEach(function (row) {
      row.remove();
    });
  }

  function formatNotificationCounter(total) {
    if (!Number.isFinite(total) || total <= 0) return "0";
    return total > 99 ? "99+" : String(total);
  }

  function groupNotifications(items) {
    const grouped = new Map();
    (Array.isArray(items) ? items : []).forEach(function (item) {
      const name = item.full_name || item.registration_id || "不明なゲスト";
      const company = item.company || "";
      const key = name + "||" + company;
      if (!grouped.has(key)) {
        grouped.set(key, {
          name: name,
          company: company,
          created_at: item.created_at,
          count: 1,
        });
        return;
      }
      grouped.get(key).count += 1;
    });
    return Array.from(grouped.values());
  }

  function renderNotifications(items) {
    if (!notificationList || !notifCounter) return;
    clearNotificationRows();
    const list = Array.isArray(items) ? items : [];
    notifCounter.textContent = formatNotificationCounter(list.length);
    if (!list.length) {
      if (notificationEmpty) notificationEmpty.style.display = "block";
      if (notificationMeta) notificationMeta.style.display = "none";
      return;
    }
    if (notificationEmpty) notificationEmpty.style.display = "none";
    const grouped = groupNotifications(list);
    const visible = grouped.slice(0, MAX_NOTIFICATION_ROWS);

    visible.forEach(function (item) {
      const name = item.name;
      const company = item.company || "";
      const time = formatRelativeTime(item.created_at);
      const countChip = item.count > 1
        ? "<span class='notification-count'>x" + item.count + "</span>"
        : "";
      const li = document.createElement("li");
      li.className = "generated-row";
      li.innerHTML =
        "<a href='javascript:void(0);' class='td-n bdB c-grey-800 cH-blue bgcH-grey-100'>" +
        "  <div class='notification-item-head'>" +
        "    <span class='notification-name'>" + escapeHtml(name) + "</span>" +
        countChip +
        "  </div>" +
        "  <div class='notification-sub'>登録が完了しました" +
        (company ? " (" + escapeHtml(company) + ")" : "") +
        "</div>" +
        "  <div class='notification-time'>" + escapeHtml(time) + "</div>" +
        "</a>";
      notificationList.insertBefore(li, notificationEmpty || null);
    });

    if (notificationMeta) {
      notificationMeta.style.display = "block";
      notificationMeta.textContent =
        visible.length + "件 / 全" + grouped.length + "件のグループ通知を表示中。";
    }
  }

  async function loadNotifications() {
    try {
      const js = await fetchJsonWithAuth("/api/dashboard/notifications?limit=30");
      if (!js || !js.ok) return;
      notificationItems = Array.isArray(js.items) ? js.items : [];
      renderNotifications(notificationItems);
    } catch (e) {
      console.error("loadNotifications error:", e);
    }
  }

  async function loadDatabase(searchKeyword, page) {
    try {
      const payload = await fetchRegistrations(searchKeyword, page || 1, PAGE_SIZE);
      databasePagination.page = payload.page;
      databasePagination.totalPages = payload.total_pages;
      databasePagination.total = payload.total;
      updatePaginationControls("database");
      renderDatabaseTable(payload.items);
    } catch (e) {
      console.error("loadDatabase error:", e);
      databasePagination.page = 1;
      databasePagination.totalPages = 1;
      databasePagination.total = 0;
      updatePaginationControls("database");
      renderDatabaseTable([]);
    }
  }

  async function loadRecentActivity(page) {
    try {
      const payload = await fetchRegistrations("", page || 1, PAGE_SIZE);
      recentPagination.page = payload.page;
      recentPagination.totalPages = payload.total_pages;
      recentPagination.total = payload.total;
      updatePaginationControls("recent");
      renderRecentActivity(payload.items);
    } catch (e) {
      console.error("loadRecentActivity error:", e);
      recentPagination.page = 1;
      recentPagination.totalPages = 1;
      recentPagination.total = 0;
      updatePaginationControls("recent");
      renderRecentActivity([]);
    }
  }

  function syncInputs(sourceInput, targetInput) {
    if (!sourceInput || !targetInput) return;
    syncingSearch = true;
    targetInput.value = sourceInput.value;
    syncingSearch = false;
  }

  function bindSearchInput(input, mirrorInput, shouldOpenDatabase) {
    if (!input) return;
    input.addEventListener("input", async function () {
      if (syncingSearch) return;

      syncInputs(input, mirrorInput);
      const keyword = normalizeText(input.value);
      currentSearchKeyword = input.value;
      databasePagination.page = 1;
      if (shouldOpenDatabase && keyword !== "") {
        setActiveScreen("database");
      }
      await loadDatabase(currentSearchKeyword, 1);
    });
  }

  function syncDetailPanelHeight() {
    const panel = document.querySelector(".guest-detail-panel");
    const tableWrap = document.querySelector(".table-responsive");
    if (!panel || !tableWrap) return;
    if (window.innerWidth <= 640) {
      panel.style.height = "";
      panel.style.maxHeight = "";
      return;
    }
    const targetHeight = tableWrap.offsetHeight;
    if (!targetHeight) return;
    panel.style.height = targetHeight + "px";
    panel.style.maxHeight = targetHeight + "px";
  }

  function exportVisibleRowsToExcel(dateFrom, dateTo) {
    const keyword = currentSearchKeyword || (tableSearchInput && tableSearchInput.value) || "";
    const params = new URLSearchParams({
      search: keyword,
      sort_by: "created_at",
      sort_dir: "desc",
    });
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    window.location.href = "/api/dashboard/export.xlsx?" + params.toString();
  }

  toggleButtons.forEach(function (button) {
    button.addEventListener("click", function (e) {
      e.preventDefault();
      manualOverride = true;
      body.classList.toggle("sidebar-collapsed");
    });
  });

  window.addEventListener("resize", syncSidebarState);
  syncSidebarState();
  window.addEventListener("resize", syncDetailPanelHeight);
  setTimeout(syncDetailPanelHeight, 0);

  screenLinks.forEach(function (link) {
    link.addEventListener("click", function (e) {
      e.preventDefault();
      const target = link.getAttribute("data-screen-target");
      setActiveScreen(target || "dashboard");
    });
  });

  bindSearchInput(globalSearchInput, tableSearchInput, true);
  bindSearchInput(tableSearchInput, globalSearchInput, false);

  if (exportExcelBtn) {
    exportExcelBtn.addEventListener("click", function () {
      openExportModal();
    });
  }

  if (logoutBtn) {
    logoutBtn.addEventListener("click", async function () {
      try {
        await fetch("/api/auth/logout", { method: "POST" });
      } catch (e) {
        console.error("logout error:", e);
      } finally {
        window.location.href = "/login";
      }
    });
  }

  if (editConfirmBtn) {
    editConfirmBtn.addEventListener("click", function () {
      if (!currentEditItem || !currentEditItem.registration_id) return;
      openConfirm("この内容で更新しますか？", async function () {
        const regId = currentEditItem.registration_id;
        const payload = {
          full_name: editFullName ? editFullName.value : "",
          company: editCompany ? editCompany.value : "",
          email: editEmail ? editEmail.value : "",
          phone: editPhone ? editPhone.value : "",
          title: editTitle ? editTitle.value : "",
        };
        const ok = await updateRegistration(regId, payload);
        if (!ok) {
          alert("更新に失敗しました。");
          return;
        }
        closeEditModal();
        await loadDatabase(currentSearchKeyword, databasePagination.page);
        const detail = await loadRegistrationDetail(regId);
        if (detail) {
          renderDetailPanel(detail, detail);
        }
      });
    });
  }

  if (editCancelBtn) {
    editCancelBtn.addEventListener("click", function () {
      openConfirm("編集をキャンセルしますか？", function () {
        closeEditModal();
      });
    });
  }

  if (editCloseBtn) {
    editCloseBtn.addEventListener("click", function () {
      openConfirm("編集をキャンセルしますか？", function () {
        closeEditModal();
      });
    });
  }

  if (exportConfirmBtn) {
    exportConfirmBtn.addEventListener("click", function () {
      const dateFrom = exportDateFrom ? exportDateFrom.value : "";
      const dateTo = exportDateTo ? exportDateTo.value : "";
      closeExportModal();
      openConfirm(
        "この期間でエクスポートしますか？",
        function () {
          exportVisibleRowsToExcel(dateFrom, dateTo);
        },
        function () {
          if (exportDateFrom) exportDateFrom.value = dateFrom;
          if (exportDateTo) exportDateTo.value = dateTo;
          openExportModal();
        }
      );
    });
  }

  if (exportCancelBtn) {
    exportCancelBtn.addEventListener("click", function () {
      const dateFrom = exportDateFrom ? exportDateFrom.value : "";
      const dateTo = exportDateTo ? exportDateTo.value : "";
      closeExportModal();
      openConfirm(
        "エクスポートをキャンセルしますか？",
        function () {
          closeExportModal();
        },
        function () {
          if (exportDateFrom) exportDateFrom.value = dateFrom;
          if (exportDateTo) exportDateTo.value = dateTo;
          openExportModal();
        }
      );
    });
  }

  if (exportCloseBtn) {
    exportCloseBtn.addEventListener("click", function () {
      const dateFrom = exportDateFrom ? exportDateFrom.value : "";
      const dateTo = exportDateTo ? exportDateTo.value : "";
      closeExportModal();
      openConfirm(
        "エクスポートをキャンセルしますか？",
        function () {
          closeExportModal();
        },
        function () {
          if (exportDateFrom) exportDateFrom.value = dateFrom;
          if (exportDateTo) exportDateTo.value = dateTo;
          openExportModal();
        }
      );
    });
  }

  if (confirmYesBtn) {
    confirmYesBtn.addEventListener("click", async function () {
      const cb = confirmCallback;
      closeConfirm();
      if (cb) await cb();
    });
  }

  if (confirmNoBtn) {
    confirmNoBtn.addEventListener("click", function () {
      const cb = confirmCancelCallback;
      closeConfirm();
      if (cb) cb();
    });
  }

  document.querySelectorAll("[data-modal-close]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      closeConfirm();
    });
  });

  if (editModal) {
    editModal.addEventListener("click", function (event) {
      if (event.target === editModal) {
        openConfirm("編集をキャンセルしますか？", function () {
          closeEditModal();
        });
      }
    });
  }

  if (confirmModal) {
    confirmModal.addEventListener("click", function (event) {
      if (event.target === confirmModal) closeConfirm();
    });
  }

  if (exportModal) {
    exportModal.addEventListener("click", function (event) {
      if (event.target === exportModal) {
        const dateFrom = exportDateFrom ? exportDateFrom.value : "";
        const dateTo = exportDateTo ? exportDateTo.value : "";
        closeExportModal();
        openConfirm(
          "エクスポートをキャンセルしますか？",
          function () {
            closeExportModal();
          },
          function () {
            if (exportDateFrom) exportDateFrom.value = dateFrom;
            if (exportDateTo) exportDateTo.value = dateTo;
            openExportModal();
          }
        );
      }
    });
  }

  if (databasePrevBtn) {
    databasePrevBtn.addEventListener("click", async function () {
      if (databasePagination.page <= 1) return;
      databasePagination.page -= 1;
      await loadDatabase(currentSearchKeyword, databasePagination.page);
    });
  }

  if (databaseNextBtn) {
    databaseNextBtn.addEventListener("click", async function () {
      if (databasePagination.page >= databasePagination.totalPages) return;
      databasePagination.page += 1;
      await loadDatabase(currentSearchKeyword, databasePagination.page);
    });
  }

  if (recentPrevBtn) {
    recentPrevBtn.addEventListener("click", async function () {
      if (recentPagination.page <= 1) return;
      recentPagination.page -= 1;
      await loadRecentActivity(recentPagination.page);
    });
  }

  if (recentNextBtn) {
    recentNextBtn.addEventListener("click", async function () {
      if (recentPagination.page >= recentPagination.totalPages) return;
      recentPagination.page += 1;
      await loadRecentActivity(recentPagination.page);
    });
  }

  setActiveScreen("database");
  loadStats();
  loadDatabase("", 1);
  loadRecentActivity(1);
  loadNotifications();
  setTimeout(syncDetailPanelHeight, 100);
  setInterval(async () => {
    // 1. Stats と通知を常に更新
    loadStats();
    loadNotifications();

    // 2. 以下の条件の場合のみ、テーブルを自動リフレッシュ:
    // - 検索キーワードがない場合（閲覧中の結果が消えないようにするため）
    // - ページ 1 の場合（古いデータ閲覧中にページが飛ばないようにするため）
    if (!currentSearchKeyword && databasePagination.page === 1) {
      loadDatabase("", 1);
    }
    if (recentPagination.page === 1) {
      loadRecentActivity(1);
    }
  }, 2000);

  setInterval(function () {
    if (notificationItems.length) {
      renderNotifications(notificationItems);
    }
  }, 30000);
});
