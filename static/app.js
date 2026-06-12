const state = {
  bootstrap: null,
  conversations: [],
  conversationCategory: "inbox",
  hasMoreConversations: true,
  isLoadingConversations: false,
  conversationRequestSeq: 0,
  currentConversationId: null,
  currentConversation: null,
  messages: [],
  hasMoreMessages: false,
  olderCount: 0,
  statusPollTimer: null,
  statusPollInFlight: false,
  lightboxImages: [],
  lightboxIndex: 0,
  recipientDraft: [],
  recipientDraftLabels: {},
  recipientSuggestions: [],
  recipientSuggestionIndex: -1,
  recipientSuggestionSeq: 0,
  draftMatchSeq: 0,
  columnWidths: { left: 340, right: 330 },
};

const els = {
  appShell: document.querySelector(".app-shell"),
  threadPane: document.querySelector(".thread-pane"),
  conversationList: document.querySelector("#conversationList"),
  conversationSearch: document.querySelector("#conversationSearch"),
  statStrip: document.querySelector("#statStrip"),
  categoryTabs: document.querySelectorAll(".category-tab"),
  themeToggle: document.querySelector("#themeToggle"),
  settingsButton: document.querySelector("#settingsButton"),
  settingsModal: document.querySelector("#settingsModal"),
  settingsForm: document.querySelector("#settingsForm"),
  settingsSections: document.querySelector("#settingsSections"),
  settingsClose: document.querySelector("#settingsClose"),
  settingsCancel: document.querySelector("#settingsCancel"),
  themeColor: document.querySelector("#themeColor"),
  fromNumber: document.querySelector("#fromNumber"),
  threadKind: document.querySelector("#threadKind"),
  threadTitle: document.querySelector("#threadTitle"),
  mobileBackButton: document.querySelector("#mobileBackButton"),
  participantLine: document.querySelector("#participantLine"),
  contactNameToggle: document.querySelector("#contactNameToggle"),
  contactNameForm: document.querySelector("#contactNameForm"),
  contactNameInput: document.querySelector("#contactNameInput"),
  contactNameCancel: document.querySelector("#contactNameCancel"),
  recipientBar: document.querySelector("#recipientBar"),
  recipientChips: document.querySelector("#recipientChips"),
  recipientInput: document.querySelector("#recipientInput"),
  recipientSuggestions: document.querySelector("#recipientSuggestions"),
  messages: document.querySelector("#messages"),
  composer: document.querySelector(".composer"),
  messageText: document.querySelector("#messageText"),
  mediaUrls: document.querySelector("#mediaUrls"),
  composerError: document.querySelector("#composerError"),
  sendButton: document.querySelector("#sendButton"),
  dealtButton: document.querySelector("#dealtButton"),
  archiveButton: document.querySelector("#archiveButton"),
  newConversationButton: document.querySelector("#newConversationButton"),
  toggleDetailsButton: document.querySelector("#toggleDetailsButton"),
  identityList: document.querySelector("#identityList"),
  contactSearch: document.querySelector("#contactSearch"),
  contactResults: document.querySelector("#contactResults"),
  syncContactsButton: document.querySelector("#syncContactsButton"),
  lightbox: document.querySelector("#lightbox"),
  lightboxImage: document.querySelector("#lightboxImage"),
  lightboxCaption: document.querySelector("#lightboxCaption"),
  lightboxClose: document.querySelector("#lightboxClose"),
  lightboxPrev: document.querySelector("#lightboxPrev"),
  lightboxNext: document.querySelector("#lightboxNext"),
  columnResizers: document.querySelectorAll(".column-resizer"),
  toast: document.querySelector("#toast"),
};

const COLUMN_WIDTHS_KEY = "textingColumnWidths";
const THEME_KEY = "textingTheme";
const PENDING_MESSAGE_STATUSES = new Set(["queued", "sending", "accepted", "sent", "finalized"]);
const COLUMN_LIMITS = {
  leftMin: 260,
  leftMax: 560,
  rightMin: 260,
  rightMax: 520,
  threadMin: 320,
  handleWidth: 6,
};

function setDetailsCollapsed(collapsed) {
  document.body.classList.toggle("details-collapsed", collapsed);
  els.toggleDetailsButton.textContent = "Panel";
  els.toggleDetailsButton.setAttribute("aria-pressed", collapsed ? "true" : "false");
  els.toggleDetailsButton.setAttribute(
    "aria-label",
    collapsed ? "Show sender identities and contacts" : "Hide sender identities and contacts",
  );
  els.toggleDetailsButton.title = collapsed ? "Show sender identities and contacts" : "Hide sender identities and contacts";
  localStorage.setItem("detailsCollapsedDefaultHidden", collapsed ? "1" : "0");
  applyColumnWidths();
}

function applyTheme(theme, { persist = true } = {}) {
  const nextTheme = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = nextTheme;
  if (els.themeColor) {
    els.themeColor.content = nextTheme === "dark" ? "#0c1117" : "#e6e8eb";
  }
  if (els.themeToggle) {
    els.themeToggle.textContent = nextTheme === "dark" ? "☀" : "☾";
    els.themeToggle.title = nextTheme === "dark" ? "Use light mode" : "Use dark mode";
    els.themeToggle.setAttribute("aria-label", els.themeToggle.title);
    els.themeToggle.setAttribute("aria-pressed", nextTheme === "dark" ? "true" : "false");
  }
  if (persist) {
    localStorage.setItem(THEME_KEY, nextTheme);
  }
}

function initializeTheme() {
  const current = document.documentElement.dataset.theme || "light";
  applyTheme(current, { persist: false });
}

function clamp(value, min, max) {
  const upper = Math.max(min, max);
  return Math.min(Math.max(value, min), upper);
}

function isDesktopLayout() {
  return window.matchMedia("(min-width: 761px)").matches;
}

function setMobileThreadOpen(open) {
  document.body.classList.toggle("mobile-thread-open", Boolean(open));
  requestAnimationFrame(updateComposerOffset);
}

function replaceNavigationState(view = "list") {
  if (!history.replaceState) return;
  history.replaceState({ textingApp: true, view }, "", window.location.href);
}

function pushMobileThreadState(detail = {}) {
  if (isDesktopLayout() || !history.pushState) return;
  const current = history.state || {};
  const next = { textingApp: true, view: "thread", ...detail };
  const sameThread =
    current.textingApp === true &&
    current.view === "thread" &&
    current.conversationId === next.conversationId &&
    Boolean(current.draft) === Boolean(next.draft);
  if (!sameThread) {
    history.pushState(next, "", window.location.href);
  }
}

function closeMobileThread({ fromHistory = false } = {}) {
  clearStatusPoll();
  setMobileThreadOpen(false);
  if (!fromHistory && !isDesktopLayout() && history.state?.textingApp === true && history.state.view === "thread") {
    history.back();
  }
}

function handleNavigationPop(event) {
  if (isDesktopLayout()) return;
  const navState = event.state || {};
  if (navState.textingApp === true && navState.view === "thread") {
    if (navState.draft) {
      startNewConversation({ updateHistory: false });
      return;
    }
    const conversationId = Number(navState.conversationId);
    if (conversationId && conversationId !== state.currentConversationId) {
      openConversation(conversationId, { updateHistory: false }).catch((error) => toast(error.message));
      return;
    }
    setMobileThreadOpen(true);
    return;
  }
  closeMobileThread({ fromHistory: true });
}

window.textingCloseThreadForNativeBack = () => {
  closeMobileThread();
  return true;
};

function updateComposerOffset() {
  if (!els.threadPane || !els.composer) return;
  const height = Math.ceil(els.composer.getBoundingClientRect().height || 0);
  if (height > 0) {
    els.threadPane.style.setProperty("--mobile-composer-height", `${height}px`);
  }
}

function isDetailColumnVisible() {
  const detailRail = document.querySelector(".detail-rail");
  return isDesktopLayout() && detailRail && getComputedStyle(detailRail).display !== "none";
}

function clampColumnWidths(widths = state.columnWidths) {
  const shellWidth = els.appShell.clientWidth || window.innerWidth;
  const detailVisible = isDetailColumnVisible();
  const handleSpace = detailVisible ? COLUMN_LIMITS.handleWidth * 2 : COLUMN_LIMITS.handleWidth;
  const available = shellWidth - handleSpace;
  const rightMin = detailVisible ? COLUMN_LIMITS.rightMin : 0;
  const leftMax = Math.min(COLUMN_LIMITS.leftMax, available - COLUMN_LIMITS.threadMin - rightMin);
  const left = clamp(widths.left, COLUMN_LIMITS.leftMin, leftMax);
  const rightMax = Math.min(COLUMN_LIMITS.rightMax, available - left - COLUMN_LIMITS.threadMin);
  const right = clamp(widths.right, COLUMN_LIMITS.rightMin, rightMax);
  return { left, right };
}

function saveColumnWidths() {
  localStorage.setItem(COLUMN_WIDTHS_KEY, JSON.stringify(state.columnWidths));
}

function applyColumnWidths({ persist = false } = {}) {
  state.columnWidths = clampColumnWidths();
  els.appShell.style.setProperty("--conversation-column", `${state.columnWidths.left}px`);
  els.appShell.style.setProperty("--detail-column", `${state.columnWidths.right}px`);
  if (persist) saveColumnWidths();
}

function loadColumnWidths() {
  try {
    const saved = JSON.parse(localStorage.getItem(COLUMN_WIDTHS_KEY) || "{}");
    state.columnWidths = {
      left: Number.isFinite(saved.left) ? saved.left : state.columnWidths.left,
      right: Number.isFinite(saved.right) ? saved.right : state.columnWidths.right,
    };
  } catch {
    state.columnWidths = { left: 340, right: 330 };
  }
  applyColumnWidths();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Request failed: ${response.status}`);
  }
  return payload;
}

function toast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("visible");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => els.toast.classList.remove("visible"), 4200);
}

function settingSourceLabel(field) {
  if (!field.has_value) return "Not set";
  if (field.source === "saved") return "Saved";
  if (field.source === "env") return ".env";
  return "Default";
}

function renderSettings(payload = state.bootstrap?.settings) {
  const sections = payload?.sections || [];
  if (!sections.length) {
    els.settingsSections.innerHTML = `<div class="empty-state">No settings are available.</div>`;
    return;
  }
  els.settingsSections.innerHTML = sections
    .map(
      (section) => `
        <section class="settings-section">
          <h3>${escapeHtml(section.name)}</h3>
          <div class="settings-fields">
            ${(section.fields || []).map(renderSettingField).join("")}
          </div>
        </section>`,
    )
    .join("");
}

function renderSettingField(field) {
  const key = escapeHtml(field.key);
  const source = escapeHtml(settingSourceLabel(field));
  const help = field.help ? `<p class="setting-help">${escapeHtml(field.help)}</p>` : "";
  if (field.type === "bool") {
    const checked = String(field.value || "0") === "1" ? "checked" : "";
    return `
      <label class="setting-field setting-toggle">
        <span>
          <strong>${escapeHtml(field.label)}</strong>
          <small>${source}</small>
          ${help}
        </span>
        <input data-setting-key="${key}" data-setting-type="bool" type="checkbox" ${checked} />
      </label>`;
  }
  if (field.type === "select") {
    return `
      <label class="setting-field">
        <span>
          <strong>${escapeHtml(field.label)}</strong>
          <small>${source}</small>
          ${help}
        </span>
        <select data-setting-key="${key}" data-setting-type="select">
          ${(field.options || [])
            .map((option) => {
              const selected = option.value === field.value ? "selected" : "";
              return `<option value="${escapeHtml(option.value)}" ${selected}>${escapeHtml(option.label)}</option>`;
            })
            .join("")}
        </select>
      </label>`;
  }
  if (field.secret) {
    const placeholder = field.has_value ? `${settingSourceLabel(field)} - leave blank to keep` : "Not set";
    const clearControl = field.has_value && field.source === "saved"
      ? `<label class="setting-clear"><input data-clear-setting="${key}" type="checkbox" /> Clear saved value</label>`
      : "";
    return `
      <label class="setting-field">
        <span>
          <strong>${escapeHtml(field.label)}</strong>
          <small>${source}</small>
          ${help}
        </span>
        <input
          data-setting-key="${key}"
          data-setting-type="secret"
          type="password"
          placeholder="${escapeHtml(placeholder)}"
          autocomplete="off"
        />
        ${clearControl}
      </label>`;
  }
  const inputType = field.type === "number" ? "number" : field.type === "url" ? "url" : "text";
  const min = field.type === "number" ? ` min="0"` : "";
  return `
    <label class="setting-field">
      <span>
        <strong>${escapeHtml(field.label)}</strong>
        <small>${source}</small>
        ${help}
      </span>
      <input
        data-setting-key="${key}"
        data-setting-type="${escapeHtml(field.type)}"
        type="${inputType}"
        value="${escapeHtml(field.value || "")}"
        autocomplete="off"
        ${min}
      />
    </label>`;
}

async function openSettings() {
  try {
    const payload = await api("/api/settings");
    if (state.bootstrap) {
      state.bootstrap.settings = payload;
    }
    renderSettings(payload);
    els.settingsModal.classList.remove("hidden");
    els.settingsModal.focus();
  } catch (error) {
    toast(error.message);
  }
}

function closeSettings() {
  els.settingsModal.classList.add("hidden");
}

async function saveSettings(event) {
  event.preventDefault();
  const settings = {};
  const clear = [];
  els.settingsSections.querySelectorAll("[data-clear-setting]:checked").forEach((input) => {
    clear.push(input.dataset.clearSetting);
  });
  els.settingsSections.querySelectorAll("[data-setting-key]").forEach((input) => {
    const key = input.dataset.settingKey;
    const type = input.dataset.settingType;
    if (type === "secret") {
      if (input.value.trim() && !clear.includes(key)) settings[key] = input.value.trim();
      return;
    }
    if (type === "bool") {
      settings[key] = input.checked;
      return;
    }
    settings[key] = input.value;
  });
  const controls = els.settingsForm.querySelectorAll("input, select, button");
  controls.forEach((control) => {
    control.disabled = true;
  });
  try {
    const payload = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({ settings, clear }),
    });
    if (state.bootstrap) {
      state.bootstrap.settings = payload;
    }
    state.bootstrap = await api("/api/bootstrap");
    renderBootstrap();
    renderThreadHeader();
    renderSettings(state.bootstrap.settings);
    closeSettings();
    toast("Settings saved.");
  } catch (error) {
    toast(error.message);
  } finally {
    controls.forEach((control) => {
      control.disabled = false;
    });
  }
}

function showComposerError(message) {
  els.composerError.textContent = message || "";
  els.composerError.classList.toggle("hidden", !message);
  requestAnimationFrame(updateComposerOffset);
}

const MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH_LONG = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];
const WEEKDAY_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

function localTimeParts(value) {
  const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  if (!match) return null;
  return {
    year: Number(match[1]),
    month: Number(match[2]),
    day: Number(match[3]),
    hour: Number(match[4]),
    minute: Number(match[5]),
  };
}

function formatClock(parts) {
  const hour12 = parts.hour % 12 || 12;
  const suffix = parts.hour >= 12 ? "PM" : "AM";
  return `${hour12}:${String(parts.minute).padStart(2, "0")} ${suffix}`;
}

function formatTime(value, compact = false) {
  if (!value) return "";
  const parts = localTimeParts(value);
  if (!parts) return "";
  const month = compact ? MONTH_SHORT[parts.month - 1] : MONTH_LONG[parts.month - 1];
  const separator = compact ? ", " : " at ";
  return `${month} ${parts.day}${separator}${formatClock(parts)}`;
}

function formatDay(value) {
  const parts = localTimeParts(value);
  if (!parts) return "";
  const noonUtc = new Date(Date.UTC(parts.year, parts.month - 1, parts.day, 12));
  const weekday = WEEKDAY_SHORT[noonUtc.getUTCDay()];
  return `${weekday}, ${MONTH_SHORT[parts.month - 1]} ${parts.day}, ${parts.year}`;
}

function initials(name) {
  if (String(name || "").trim().startsWith("+")) {
    const digits = String(name).replace(/\D/g, "");
    return digits.slice(-2) || "?";
  }
  const parts = String(name || "?").trim().split(/\s+/).slice(0, 2);
  return parts.map((p) => p[0]?.toUpperCase() || "").join("") || "?";
}

function phoneDisplay(phone) {
  const digits = String(phone || "").replace(/\D/g, "");
  if (digits.length === 11 && digits.startsWith("1")) {
    return `(${digits.slice(1, 4)}) ${digits.slice(4, 7)}-${digits.slice(7)}`;
  }
  return phone;
}

function participantDisplay(participant) {
  const phone = phoneDisplay(participant.phone_number);
  if (!participant.display || participant.display === phone || participant.display === participant.phone_number) {
    return phone;
  }
  return `${participant.display} ${phone}`;
}

function normalizeDraftPhone(raw) {
  const digits = String(raw || "").replace(/\D/g, "");
  let phone = String(raw || "").trim();
  if (digits.length === 10) phone = `+1${digits}`;
  else if (digits.length === 11 && digits.startsWith("1")) phone = `+${digits}`;
  return phone;
}

function usefulContactName(phone, displayName = "") {
  const phoneText = phoneDisplay(phone);
  const name = String(displayName || "").trim();
  if (!name || name === phone || name === phoneText) return "";
  return name;
}

function draftRecipientDisplay(phone, { includePhone = false } = {}) {
  const name = state.recipientDraftLabels[phone] || "";
  if (!name) return phoneDisplay(phone);
  return includePhone ? `${name} ${phoneDisplay(phone)}` : name;
}

function currentDirectParticipant(conversation = state.currentConversation) {
  const participants = (conversation?.participants || []).filter((participant) => participant.role === "participant");
  return conversation?.kind === "direct" && participants.length === 1 ? participants[0] : null;
}

function participantSavedName(participant) {
  if (!participant) return "";
  const phone = phoneDisplay(participant.phone_number);
  const display = String(participant.display || "").trim();
  if (!display || display === phone || display === participant.phone_number) return "";
  return display;
}

function conversationIsRead(conversation) {
  if (conversation?.manual_unread_at) return false;
  const last = conversation?.last_occurred_at || conversation?.last_message_at || conversation?.sort_at || "";
  const dealt = conversation?.dealt_with_at || "";
  return Boolean(last && dealt && dealt >= last);
}

function setContactNameEditor(visible) {
  const participant = currentDirectParticipant();
  if (!participant || !visible) {
    els.contactNameForm.classList.add("hidden");
    return;
  }
  els.contactNameInput.value = participantSavedName(participant);
  els.contactNameForm.classList.remove("hidden");
  els.contactNameInput.focus();
  els.contactNameInput.select();
}

function activeIdentity() {
  const phone = els.fromNumber.value;
  return state.bootstrap?.identities?.find((identity) => identity.phone_number === phone);
}

function renderBootstrap() {
  const stats = state.bootstrap.stats || {};
  els.statStrip.innerHTML = [
    ["Threads", stats.conversations],
    ["Texts", stats.messages],
    ["Media", stats.attachments],
    ["People", stats.contacts],
  ]
    .map(([label, value]) => `<div class="stat"><strong>${value ?? 0}</strong><span>${label}</span></div>`)
    .join("");

  els.fromNumber.innerHTML = (state.bootstrap.identities || [])
    .filter((identity) => identity.is_active)
    .map(
      (identity) =>
        `<option value="${escapeHtml(identity.phone_number)}">${escapeHtml(identity.label)} · ${escapeHtml(
          phoneDisplay(identity.phone_number),
        )}</option>`,
    )
    .join("");
  if (state.bootstrap.default_identity) {
    els.fromNumber.value = state.bootstrap.default_identity;
  }

  renderIdentities();
  renderCategoryTabs();
}

function renderCategoryTabs() {
  const hiddenCount = state.bootstrap?.stats?.hidden_conversations ?? 0;
  const unreadCount = state.bootstrap?.stats?.unread_conversations ?? 0;
  els.categoryTabs.forEach((tab) => {
    const active = tab.dataset.category === state.conversationCategory;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", active ? "true" : "false");
    if (tab.dataset.category === "hidden") {
      tab.textContent = `Hidden (${hiddenCount})`;
    } else if (tab.dataset.category === "unread") {
      tab.textContent = `Unread (${unreadCount})`;
    } else {
      tab.textContent = "Inbox";
    }
  });
}

function renderIdentities() {
  els.identityList.innerHTML = (state.bootstrap.identities || [])
    .map(
      (identity) => `
        <article class="identity-card" data-id="${identity.id}">
          <div class="swatch" style="background:${escapeHtml(identity.color)}"></div>
          <div class="identity-main">
            <input class="identity-label" value="${escapeHtml(identity.label)}" aria-label="Identity label" />
            <div class="identity-phone">${escapeHtml(phoneDisplay(identity.phone_number))}</div>
            <input class="identity-color" type="color" value="${escapeHtml(identity.color)}" aria-label="Identity color" />
          </div>
          <button class="icon-button save-identity" title="Save" aria-label="Save">✓</button>
        </article>`,
    )
    .join("");
}

function renderConversations() {
  const items = state.conversations
    .map((conversation) => {
      const active = conversation.id === state.currentConversationId ? "active" : "";
      const hasNewMessage = Boolean(conversation.needs_attention) && state.conversationCategory !== "hidden";
      const newMessageClass = hasNewMessage ? "new-message" : "";
      const failedClass = conversation.last_status_kind === "failed" ? "failed-message" : "";
      const title = conversation.title || "Unknown";
      const previewPrefix = conversation.last_direction === "outbound" ? "You: " : "";
      const failedPreview =
        conversation.last_status_kind === "failed"
          ? `Failed: ${conversation.last_status_detail || conversation.last_status_label || "Could not deliver"}`
          : "";
      const preview = failedPreview || conversation.last_text || (conversation.last_status_label ? conversation.last_status_label : "");
      return `
        <button class="conversation-item ${active} ${newMessageClass} ${failedClass}" data-id="${conversation.id}">
          <div class="avatar">${escapeHtml(initials(title))}</div>
          <div class="conversation-copy">
            <div class="conversation-top">
              <strong>${escapeHtml(title)}</strong>
              <time>${escapeHtml(formatTime(conversation.last_occurred_at, true))}</time>
            </div>
            <div class="conversation-preview">${escapeHtml(previewPrefix + preview)}</div>
          </div>
        </button>`;
    })
    .join("");
  const status =
    state.isLoadingConversations && state.conversations.length
      ? `<div class="conversation-status">Loading more...</div>`
      : !state.hasMoreConversations && state.conversations.length
        ? `<div class="conversation-status">First imported thread reached</div>`
        : "";
  els.conversationList.innerHTML = items + status;
}

function renderRecipientDraft() {
  els.recipientChips.innerHTML = state.recipientDraft
    .map(
      (phone) => `
        <span class="chip" title="${escapeHtml(phoneDisplay(phone))}">
          <span>${escapeHtml(draftRecipientDisplay(phone))}</span>
          <button data-remove-recipient="${escapeHtml(phone)}" title="Remove" aria-label="Remove">×</button>
        </span>`,
    )
    .join("");
}

function clearRecipientSuggestions() {
  state.recipientSuggestionSeq += 1;
  state.recipientSuggestions = [];
  state.recipientSuggestionIndex = -1;
  els.recipientSuggestions.innerHTML = "";
  els.recipientSuggestions.classList.add("hidden");
  els.recipientInput.setAttribute("aria-expanded", "false");
  els.recipientInput.removeAttribute("aria-activedescendant");
}

function renderRecipientSuggestions() {
  if (!state.recipientSuggestions.length) {
    clearRecipientSuggestions();
    return;
  }
  els.recipientSuggestions.innerHTML = state.recipientSuggestions
    .map((contact, index) => {
      const active = index === state.recipientSuggestionIndex;
      const label = contact.label ? ` ${contact.label}` : "";
      return `
        <button
          class="recipient-suggestion ${active ? "active" : ""}"
          id="recipient-suggestion-${index}"
          role="option"
          type="button"
          aria-selected="${active ? "true" : "false"}"
          data-suggestion-index="${index}"
        >
          <strong>${escapeHtml(contact.display_name)}</strong>
          <span>${escapeHtml(contact.phone_display)}${escapeHtml(label)}</span>
        </button>`;
    })
    .join("");
  els.recipientSuggestions.classList.remove("hidden");
  els.recipientInput.setAttribute("aria-expanded", "true");
  if (state.recipientSuggestionIndex >= 0) {
    els.recipientInput.setAttribute("aria-activedescendant", `recipient-suggestion-${state.recipientSuggestionIndex}`);
  } else {
    els.recipientInput.removeAttribute("aria-activedescendant");
  }
}

function moveRecipientSuggestion(delta) {
  if (!state.recipientSuggestions.length) return;
  const count = state.recipientSuggestions.length;
  state.recipientSuggestionIndex = (state.recipientSuggestionIndex + delta + count) % count;
  renderRecipientSuggestions();
}

function chooseRecipientSuggestion(index = state.recipientSuggestionIndex) {
  const contact = state.recipientSuggestions[index];
  if (!contact) return false;
  addRecipient(contact.phone_number, contact.display_name);
  return true;
}

async function searchRecipientSuggestions() {
  const term = els.recipientInput.value.trim();
  if (state.currentConversation || !term) {
    clearRecipientSuggestions();
    return;
  }
  const seq = ++state.recipientSuggestionSeq;
  try {
    const payload = await api(`/api/contacts?q=${encodeURIComponent(term)}`);
    if (seq !== state.recipientSuggestionSeq) return;
    const selected = new Set(state.recipientDraft);
    const seen = new Set();
    state.recipientSuggestions = (payload.contacts || []).filter((contact) => {
      if (!contact.phone_number || selected.has(contact.phone_number) || seen.has(contact.phone_number)) return false;
      seen.add(contact.phone_number);
      return true;
    });
    state.recipientSuggestionIndex = state.recipientSuggestions.length ? 0 : -1;
    renderRecipientSuggestions();
  } catch (error) {
    clearRecipientSuggestions();
    toast(error.message);
  }
}

function renderThreadHeader() {
  if (!state.currentConversation) {
    els.threadKind.textContent = "New";
    els.threadTitle.textContent = "New conversation";
    els.participantLine.textContent = state.recipientDraft.map((phone) => draftRecipientDisplay(phone, { includePhone: true })).join(", ");
    els.contactNameToggle.hidden = true;
    setContactNameEditor(false);
    els.recipientBar.classList.remove("hidden");
    els.threadPane.classList.add("recipients-visible");
    els.dealtButton.disabled = true;
    els.archiveButton.disabled = true;
    els.dealtButton.textContent = "Read";
    els.archiveButton.textContent = "Hide";
    els.archiveButton.classList.add("danger-button");
    renderRecipientDraft();
    return;
  }
  const conversation = state.currentConversation;
  const archived = Boolean(conversation.is_archived);
  els.threadKind.textContent = conversation.kind === "group" ? "Group MMS" : "Direct";
  els.threadTitle.textContent = conversation.title || "Conversation";
  els.participantLine.textContent = (conversation.participants || [])
    .filter((p) => p.role === "participant")
    .map(participantDisplay)
    .join(", ");
  const participant = currentDirectParticipant(conversation);
  els.contactNameToggle.hidden = !participant;
  els.contactNameToggle.textContent = participantSavedName(participant) ? "Rename" : "Name";
  if (!participant) setContactNameEditor(false);
  els.recipientBar.classList.add("hidden");
  els.threadPane.classList.remove("recipients-visible");
  els.dealtButton.disabled = false;
  els.archiveButton.disabled = false;
  els.dealtButton.textContent = conversationIsRead(conversation) ? "Unread" : "Read";
  els.archiveButton.textContent = archived ? "Unhide" : "Hide";
  els.archiveButton.classList.toggle("danger-button", !archived);
}

function mediaUrl(attachment) {
  if (attachment.local_path) {
    const filename = attachment.local_path.split("/").pop();
    return `/media/${encodeURIComponent(filename)}`;
  }
  return attachment.remote_url;
}

function isImageAttachment(attachment, url) {
  const contentType = attachment.content_type || "";
  return contentType.startsWith("image/") || /\.(avif|gif|heic|heif|jpe?g|png|webp)(\?.*)?$/i.test(url || "");
}

function renderAttachment(attachment) {
  const url = mediaUrl(attachment);
  if (!url) return "";
  const contentType = attachment.content_type || "";
  if (isImageAttachment(attachment, url)) {
    const caption = attachment.filename || "Image";
    return `<a href="${escapeHtml(url)}" class="image-attachment" data-lightbox-src="${escapeHtml(url)}" data-lightbox-caption="${escapeHtml(caption)}" target="_blank"><img src="${escapeHtml(url)}" alt="" loading="lazy" /></a>`;
  }
  if (contentType.startsWith("video/")) {
    return `<video src="${escapeHtml(url)}" controls preload="metadata"></video>`;
  }
  if (contentType.startsWith("audio/")) {
    return `<audio src="${escapeHtml(url)}" controls preload="metadata"></audio>`;
  }
  return `<a class="attachment-link" href="${escapeHtml(url)}" target="_blank">${escapeHtml(
    attachment.filename || "Attachment",
  )}</a>`;
}

function renderMessages(messages, scrollMode = "bottom") {
  const wasNearBottom = isNearMessageBottom();
  if (!messages.length) {
    els.messages.innerHTML = `<div class="empty-state">No messages yet.</div>`;
    updateComposerOffset();
    watchMessageMediaForBottomStick(scrollMode, wasNearBottom);
    return;
  }
  const oldScrollHeight = els.messages.scrollHeight;
  const oldScrollTop = els.messages.scrollTop;
  let lastDay = "";
  const loadOlder = state.hasMoreMessages
    ? `<div class="older-row"><button class="small-button" id="loadOlderButton">Load older (${state.olderCount})</button></div>`
    : "";
  els.messages.innerHTML =
    loadOlder +
    messages
      .map((message) => {
      const day = formatDay(message.occurred_at);
      const divider = day !== lastDay ? `<div class="day-divider">${escapeHtml(day)}</div>` : "";
      lastDay = day;
      const attachments = (message.attachments || []).map(renderAttachment).join("");
      const statusKind = message.status_kind || "neutral";
      const statusLabel = message.status_label || message.status || "";
      const statusDetail = message.status_detail || "";
      const failureDetail =
        statusKind === "failed" || statusKind === "warning"
          ? `<div class="message-error ${statusKind}">
              <strong>${statusKind === "failed" ? "Send failed" : "Delivery unconfirmed"}</strong>
              <span>${escapeHtml(statusDetail || statusLabel)}</span>
            </div>`
          : "";
      return `
        ${divider}
        <article class="message-row ${message.direction} ${statusKind}">
          <div class="message-bubble">
            ${attachments ? `<div class="attachment-grid">${attachments}</div>` : ""}
            ${message.text ? `<div class="message-text">${escapeHtml(message.text)}</div>` : ""}
            ${failureDetail}
            <div class="message-meta">
              <span>${escapeHtml(message.from_display || phoneDisplay(message.from_number))}</span>
              <time>${escapeHtml(formatTime(message.occurred_at))}</time>
              <span class="message-status ${escapeHtml(statusKind)}" title="${escapeHtml(statusDetail)}">${escapeHtml(statusLabel)}</span>
            </div>
          </div>
        </article>`;
      })
      .join("");
  updateComposerOffset();
  if (scrollMode === "preserve") {
    els.messages.scrollTop = els.messages.scrollHeight - oldScrollHeight + oldScrollTop;
  } else if (scrollMode === "bottom") {
    scrollMessagesToBottom();
  }
  watchMessageMediaForBottomStick(scrollMode, wasNearBottom);
}

function watchMessageMediaForBottomStick(scrollMode, wasNearBottom = false) {
  const shouldStick = scrollMode === "bottom" || wasNearBottom;
  const media = [...els.messages.querySelectorAll("img, video")];
  if (!media.length) return;
  const keepBottomVisible = () => {
    updateComposerOffset();
    if (shouldStick || isNearMessageBottom()) {
      scrollMessagesToBottom();
    }
  };
  media.forEach((item) => {
    if (item.tagName === "IMG" && item.complete) return;
    item.addEventListener("load", keepBottomVisible, { once: true });
    item.addEventListener("loadedmetadata", keepBottomVisible, { once: true });
    item.addEventListener("error", keepBottomVisible, { once: true });
  });
  window.setTimeout(keepBottomVisible, 350);
  window.setTimeout(keepBottomVisible, 1000);
}

function collectLightboxImages() {
  state.lightboxImages = [...els.messages.querySelectorAll("[data-lightbox-src]")].map((link) => ({
    src: link.dataset.lightboxSrc,
    caption: link.dataset.lightboxCaption || "Image",
  }));
}

function renderLightbox() {
  const current = state.lightboxImages[state.lightboxIndex];
  if (!current) return;
  els.lightboxImage.src = current.src;
  els.lightboxCaption.textContent =
    state.lightboxImages.length > 1
      ? `${current.caption} (${state.lightboxIndex + 1} of ${state.lightboxImages.length})`
      : current.caption;
  els.lightboxPrev.hidden = state.lightboxImages.length <= 1;
  els.lightboxNext.hidden = state.lightboxImages.length <= 1;
}

function openLightbox(src) {
  collectLightboxImages();
  const index = state.lightboxImages.findIndex((image) => image.src === src);
  state.lightboxIndex = index >= 0 ? index : 0;
  renderLightbox();
  els.lightbox.classList.remove("hidden");
  els.lightbox.focus();
}

function closeLightbox() {
  els.lightbox.classList.add("hidden");
  els.lightboxImage.removeAttribute("src");
}

function stepLightbox(offset) {
  if (!state.lightboxImages.length) return;
  state.lightboxIndex = (state.lightboxIndex + offset + state.lightboxImages.length) % state.lightboxImages.length;
  renderLightbox();
}

function scrollMessagesToBottom() {
  updateComposerOffset();
  const scroll = () => {
    els.messages.scrollTop = els.messages.scrollHeight;
  };
  requestAnimationFrame(() => {
    scroll();
    requestAnimationFrame(scroll);
    window.setTimeout(scroll, 120);
  });
}

function isNearMessageBottom() {
  return els.messages.scrollHeight - els.messages.scrollTop - els.messages.clientHeight < 120;
}

function isEditableKeyTarget(target) {
  if (!(target instanceof Element)) return false;
  return Boolean(target.closest("input, textarea, select, [contenteditable=''], [contenteditable='true'], [contenteditable='plaintext-only']"));
}

function scrollMessagesWithArrowKey(event) {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return false;
  if (!["ArrowUp", "ArrowDown"].includes(event.key)) return false;
  if (isEditableKeyTarget(event.target)) return false;
  if (!els.messages || els.messages.clientHeight <= 0 || els.messages.scrollHeight <= els.messages.clientHeight) return false;
  const direction = event.key === "ArrowDown" ? 1 : -1;
  const amount = Math.max(72, Math.round(els.messages.clientHeight * 0.16));
  els.messages.scrollBy({
    top: direction * amount,
    behavior: event.repeat ? "auto" : "smooth",
  });
  event.preventDefault();
  return true;
}

function handleGlobalKeydown(event) {
  const settingsOpen = !els.settingsModal.classList.contains("hidden");
  if (settingsOpen) {
    if (event.key === "Escape") {
      closeSettings();
      event.preventDefault();
    }
    return;
  }
  const lightboxOpen = !els.lightbox.classList.contains("hidden");
  if (lightboxOpen) {
    if (event.key === "Escape") closeLightbox();
    if (event.key === "ArrowLeft") stepLightbox(-1);
    if (event.key === "ArrowRight") stepLightbox(1);
    if (["Escape", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) {
      event.preventDefault();
    }
    return;
  }
  scrollMessagesWithArrowKey(event);
}

function hasPendingOutboundMessages() {
  return state.messages.some(
    (message) => message.direction === "outbound" && PENDING_MESSAGE_STATUSES.has(message.status || ""),
  );
}

function clearStatusPoll() {
  if (state.statusPollTimer) {
    clearTimeout(state.statusPollTimer);
    state.statusPollTimer = null;
  }
}

function scheduleStatusPoll() {
  clearStatusPoll();
  if (!state.currentConversationId || !hasPendingOutboundMessages() || document.hidden) return;
  state.statusPollTimer = setTimeout(() => {
    refreshCurrentConversationStatus().catch((error) => toast(error.message));
  }, 5000);
}

async function refreshCurrentConversationStatus() {
  if (!state.currentConversationId || state.statusPollInFlight) return;
  state.statusPollInFlight = true;
  const conversationId = state.currentConversationId;
  const shouldStickToBottom = isNearMessageBottom();
  try {
    const payload = await api(`/api/conversations/${conversationId}/messages?limit=80`);
    if (state.currentConversationId !== conversationId) return;
    state.currentConversation = payload.conversation;
    state.messages = payload.messages;
    state.hasMoreMessages = payload.has_more;
    state.olderCount = payload.older_count;
    renderThreadHeader();
    renderMessages(state.messages, shouldStickToBottom ? "bottom" : "preserve");
    const current = state.conversations.find((conversation) => conversation.id === conversationId);
    if (current) {
      current.needs_attention = payload.conversation.needs_attention;
      current.title = payload.conversation.title;
      current.participants = payload.conversation.participants;
      renderConversations();
    }
  } finally {
    state.statusPollInFlight = false;
    scheduleStatusPoll();
  }
}

function conversationCursor() {
  const last = state.conversations[state.conversations.length - 1];
  if (!last) return "";
  const before = encodeURIComponent(last.sort_at || last.last_message_at || last.updated_at || "");
  return before ? `&before=${before}&before_id=${last.id}` : "";
}

function resizeColumn(side, deltaX, startWidths = state.columnWidths) {
  const next = { ...startWidths };
  if (side === "left") {
    next.left = startWidths.left + deltaX;
  } else {
    next.right = startWidths.right - deltaX;
  }
  state.columnWidths = next;
  applyColumnWidths();
}

function bindColumnResizers() {
  let drag = null;
  els.columnResizers.forEach((handle) => {
    handle.addEventListener("pointerdown", (event) => {
      const side = handle.dataset.resizer;
      if (!isDesktopLayout() || (side === "right" && !isDetailColumnVisible())) return;
      event.preventDefault();
      handle.setPointerCapture(event.pointerId);
      drag = {
        side,
        pointerId: event.pointerId,
        startX: event.clientX,
        startWidths: { ...state.columnWidths },
      };
      document.body.classList.add("resizing-columns");
    });

    handle.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
      const side = handle.dataset.resizer;
      if (!isDesktopLayout() || (side === "right" && !isDetailColumnVisible())) return;
      event.preventDefault();
      const amount = event.shiftKey ? 50 : 20;
      const direction = event.key === "ArrowRight" ? amount : -amount;
      resizeColumn(side, direction);
      saveColumnWidths();
    });
  });

  window.addEventListener("pointermove", (event) => {
    if (!drag) return;
    resizeColumn(drag.side, event.clientX - drag.startX, drag.startWidths);
  });

  const endDrag = (event) => {
    if (!drag) return;
    const handle = [...els.columnResizers].find((item) => item.dataset.resizer === drag.side);
    if (handle?.hasPointerCapture?.(drag.pointerId)) {
      handle.releasePointerCapture(drag.pointerId);
    }
    drag = null;
    document.body.classList.remove("resizing-columns");
    saveColumnWidths();
  };
  window.addEventListener("pointerup", endDrag);
  window.addEventListener("pointercancel", endDrag);
  window.addEventListener("resize", () => {
    applyColumnWidths();
    updateComposerOffset();
  });
  window.visualViewport?.addEventListener("resize", updateComposerOffset);
}

async function loadConversations({ append = false } = {}) {
  if (append && state.isLoadingConversations) return;
  if (append && !state.hasMoreConversations) return;
  const requestSeq = ++state.conversationRequestSeq;
  state.isLoadingConversations = true;
  if (append) renderConversations();
  const query = encodeURIComponent(els.conversationSearch.value || "");
  const hidden = state.conversationCategory === "hidden" ? "1" : "0";
  const unread = state.conversationCategory === "unread" ? "1" : "0";
  const cursor = append ? conversationCursor() : "";
  try {
    const payload = await api(`/api/conversations?limit=80&hidden=${hidden}&unread=${unread}&search=${query}${cursor}`);
    if (requestSeq !== state.conversationRequestSeq) return;
    if (append) {
      const existing = new Set(state.conversations.map((conversation) => conversation.id));
      state.conversations = [
        ...state.conversations,
        ...payload.conversations.filter((conversation) => !existing.has(conversation.id)),
      ];
    } else {
      state.conversations = payload.conversations;
      els.conversationList.scrollTop = 0;
    }
    state.hasMoreConversations = payload.has_more;
  } finally {
    if (requestSeq === state.conversationRequestSeq) {
      state.isLoadingConversations = false;
      renderConversations();
    }
  }
}

async function openConversation(id, options = {}) {
  const updateHistory = options.updateHistory !== false;
  clearStatusPoll();
  setMobileThreadOpen(true);
  const conversationId = Number(id);
  if (updateHistory) {
    pushMobileThreadState({ conversationId });
  }
  state.currentConversationId = conversationId;
  const payload = await api(`/api/conversations/${id}/messages?limit=80`);
  state.currentConversation = payload.conversation;
  state.messages = payload.messages;
  state.hasMoreMessages = payload.has_more;
  state.olderCount = payload.older_count;
  renderConversations();
  renderThreadHeader();
  renderMessages(state.messages, "bottom");
  scheduleStatusPoll();
  if (state.bootstrap?.mark_read_on_open && !conversationIsRead(state.currentConversation)) {
    setCurrentConversationRead(true, { silent: true }).catch((error) => toast(error.message));
  }
}

async function setCurrentConversationArchived(archived) {
  if (!state.currentConversationId) return;
  els.archiveButton.disabled = true;
  try {
    await api(`/api/conversations/${state.currentConversationId}/archive`, {
      method: "POST",
      body: JSON.stringify({ archived }),
    });
    state.bootstrap = await api("/api/bootstrap");
    renderBootstrap();
    await loadConversations({ append: false });
    const next = state.conversations[0];
    if (next) {
      await openConversation(next.id);
    } else {
      startNewConversation();
    }
    toast(archived ? "Archived to Hidden." : "Moved to Inbox.");
  } catch (error) {
    toast(error.message);
    renderThreadHeader();
  }
}

async function setCurrentConversationRead(dealt, { silent = false } = {}) {
  if (!state.currentConversationId) return;
  const conversationId = state.currentConversationId;
  els.dealtButton.disabled = true;
  try {
    const payload = await api(`/api/conversations/${conversationId}/dealt`, {
      method: "POST",
      body: JSON.stringify({ dealt }),
    });
    const isCurrent = state.currentConversationId === conversationId;
    if (isCurrent) {
      state.currentConversation = payload.conversation;
    }
    const current = state.conversations.find((conversation) => conversation.id === conversationId);
    if (current) {
      current.dealt_with_at = payload.conversation.dealt_with_at;
      current.manual_unread_at = payload.conversation.manual_unread_at;
      current.needs_attention = payload.conversation.needs_attention;
    }
    state.bootstrap = await api("/api/bootstrap");
    renderBootstrap();
    if (state.conversationCategory === "unread" && dealt) {
      state.conversations = state.conversations.filter((conversation) => conversation.id !== conversationId);
    }
    renderConversations();
    if (isCurrent) {
      renderThreadHeader();
    }
    if (!silent) {
      toast(dealt ? "Marked read." : "Marked unread.");
    }
  } catch (error) {
    toast(error.message);
    renderThreadHeader();
  }
}

async function toggleCurrentConversationRead() {
  const shouldMarkRead = !conversationIsRead(state.currentConversation);
  await setCurrentConversationRead(shouldMarkRead);
}

async function loadOlderMessages() {
  clearStatusPoll();
  if (!state.currentConversationId || !state.messages.length || !state.hasMoreMessages) return;
  const oldest = state.messages[0];
  const before = encodeURIComponent(oldest.occurred_at);
  const payload = await api(
    `/api/conversations/${state.currentConversationId}/messages?limit=80&before=${before}&before_id=${oldest.id}`,
  );
  state.messages = [...payload.messages, ...state.messages];
  state.hasMoreMessages = payload.has_more;
  state.olderCount = payload.older_count;
  renderMessages(state.messages, "preserve");
  scheduleStatusPoll();
}

function startNewConversation(options = {}) {
  const updateHistory = options.updateHistory !== false;
  clearStatusPoll();
  setMobileThreadOpen(true);
  if (updateHistory) {
    pushMobileThreadState({ draft: true });
  }
  state.currentConversationId = null;
  state.currentConversation = null;
  state.messages = [];
  state.hasMoreMessages = false;
  state.olderCount = 0;
  state.recipientDraft = [];
  state.recipientDraftLabels = {};
  clearRecipientSuggestions();
  state.draftMatchSeq += 1;
  els.messageText.value = "";
  els.mediaUrls.value = "";
  showComposerError("");
  renderConversations();
  renderThreadHeader();
  renderMessages([]);
  els.recipientInput.focus();
}

async function matchExistingGroupDraft() {
  const recipients = [...state.recipientDraft];
  if (state.currentConversation || recipients.length < 2) return;
  const seq = ++state.draftMatchSeq;
  const query = recipients.map((phone) => `recipient=${encodeURIComponent(phone)}`).join("&");
  try {
    const payload = await api(`/api/conversations/match?${query}`);
    const unchanged =
      seq === state.draftMatchSeq &&
      !state.currentConversation &&
      recipients.length === state.recipientDraft.length &&
      recipients.every((phone, index) => phone === state.recipientDraft[index]);
    if (unchanged && payload.conversation?.id) {
      toast("Opened existing group.");
      await openConversation(payload.conversation.id);
    }
  } catch (error) {
    toast(error.message);
  }
}

function addRecipient(raw, displayName = "") {
  const phone = normalizeDraftPhone(raw);
  if (!phone.startsWith("+") || phone.length < 8) {
    toast("Recipient needs a phone number.");
    return;
  }
  const label = usefulContactName(phone, displayName);
  if (!state.recipientDraft.includes(phone)) {
    state.recipientDraft.push(phone);
  }
  if (label) {
    state.recipientDraftLabels[phone] = label;
  }
  els.recipientInput.value = "";
  clearRecipientSuggestions();
  renderRecipientDraft();
  renderThreadHeader();
  matchExistingGroupDraft();
}

function currentRecipients() {
  if (state.currentConversation) {
    return (state.currentConversation.participants || [])
      .filter((p) => p.role === "participant")
      .map((p) => p.phone_number);
  }
  return state.recipientDraft;
}

async function sendCurrentMessage() {
  const text = els.messageText.value.trim();
  const mediaUrls = els.mediaUrls.value
    .split(/[\n,]+/)
    .map((x) => x.trim())
    .filter(Boolean);
  const toNumbers = currentRecipients();
  if (!toNumbers.length) {
    toast("Add a recipient.");
    return;
  }
  showComposerError("");
  els.sendButton.disabled = true;
  try {
    await api("/api/messages", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: state.currentConversationId,
        from_number: els.fromNumber.value,
        to_numbers: toNumbers,
        text,
        media_urls: mediaUrls,
      }),
    });
    els.messageText.value = "";
    els.mediaUrls.value = "";
    await loadConversations();
    if (state.currentConversationId) {
      await openConversation(state.currentConversationId);
    } else {
      const first = state.conversations[0];
      if (first) await openConversation(first.id);
    }
  } catch (error) {
    showComposerError(error.message);
    toast(error.message);
  } finally {
    els.sendButton.disabled = false;
  }
}

async function saveIdentity(card) {
  const id = card.dataset.id;
  const label = card.querySelector(".identity-label").value;
  const color = card.querySelector(".identity-color").value;
  try {
    const payload = await api(`/api/identities/${id}`, {
      method: "PUT",
      body: JSON.stringify({ label, color, is_active: true }),
    });
    const index = state.bootstrap.identities.findIndex((item) => item.id === payload.identity.id);
    if (index >= 0) state.bootstrap.identities[index] = payload.identity;
    renderBootstrap();
    await loadConversations();
    toast("Saved.");
  } catch (error) {
    toast(error.message);
  }
}

async function searchContacts() {
  const q = encodeURIComponent(els.contactSearch.value || "");
  try {
    const payload = await api(`/api/contacts?q=${q}`);
    els.contactResults.innerHTML = payload.contacts
      .map(
        (contact) => `
          <article class="contact-card">
            <strong>${escapeHtml(contact.display_name)}</strong>
            <div class="contact-phone">${escapeHtml(contact.phone_display)} ${escapeHtml(contact.label || "")}</div>
            <button
              class="small-button"
              data-contact-phone="${escapeHtml(contact.phone_number)}"
              data-contact-name="${escapeHtml(contact.display_name)}"
            >Add</button>
          </article>`,
      )
      .join("");
  } catch (error) {
    toast(error.message);
  }
}

async function saveCurrentContactName() {
  const participant = currentDirectParticipant();
  const displayName = els.contactNameInput.value.trim();
  if (!participant) return;
  if (!displayName) {
    toast("Enter a contact name.");
    return;
  }
  const controls = els.contactNameForm.querySelectorAll("input, button");
  controls.forEach((control) => {
    control.disabled = true;
  });
  try {
    const payload = await api("/api/contacts/name", {
      method: "POST",
      body: JSON.stringify({
        conversation_id: state.currentConversationId,
        phone_number: participant.phone_number,
        display_name: displayName,
      }),
    });
    if (payload.conversation) {
      state.currentConversation = payload.conversation;
    }
    setContactNameEditor(false);
    state.bootstrap = await api("/api/bootstrap");
    renderBootstrap();
    await loadConversations({ append: false });
    if (state.currentConversationId) {
      await openConversation(state.currentConversationId);
    } else {
      renderThreadHeader();
    }
    searchContacts();
    toast(payload.synced ? "Saved to contacts." : "Saved locally. Configure contact sync to publish changes.");
  } catch (error) {
    toast(error.message);
  } finally {
    controls.forEach((control) => {
      control.disabled = false;
    });
  }
}

function bindEvents() {
  bindColumnResizers();
  els.mobileBackButton.addEventListener("click", () => {
    closeMobileThread();
  });
  els.themeToggle.addEventListener("click", () => {
    const current = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
    applyTheme(current === "dark" ? "light" : "dark");
  });
  els.settingsButton.addEventListener("click", openSettings);
  els.settingsClose.addEventListener("click", closeSettings);
  els.settingsCancel.addEventListener("click", closeSettings);
  els.settingsModal.addEventListener("click", (event) => {
    if (event.target === els.settingsModal) closeSettings();
  });
  els.settingsForm.addEventListener("submit", saveSettings);
  els.conversationList.addEventListener("click", (event) => {
    const button = event.target.closest(".conversation-item");
    if (button) openConversation(button.dataset.id).catch((error) => toast(error.message));
  });
  let searchTimer;
  els.conversationSearch.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadConversations({ append: false }).catch((error) => toast(error.message)), 180);
  });
  els.categoryTabs.forEach((tab) => {
    tab.addEventListener("click", async () => {
      state.conversationCategory = tab.dataset.category;
      localStorage.setItem("conversationCategory", state.conversationCategory);
      renderCategoryTabs();
      await loadConversations({ append: false });
      if (!isDesktopLayout()) {
        setMobileThreadOpen(false);
        return;
      }
      if (state.conversations[0]) {
        await openConversation(state.conversations[0].id);
      } else {
        startNewConversation();
      }
    });
  });
  els.conversationList.addEventListener("scroll", () => {
    const remaining =
      els.conversationList.scrollHeight - els.conversationList.scrollTop - els.conversationList.clientHeight;
    if (remaining < 600) {
      loadConversations({ append: true }).catch((error) => toast(error.message));
    }
  });
  els.newConversationButton.addEventListener("click", startNewConversation);
  els.contactNameToggle.addEventListener("click", () => setContactNameEditor(true));
  els.contactNameCancel.addEventListener("click", () => setContactNameEditor(false));
  els.contactNameForm.addEventListener("submit", (event) => {
    event.preventDefault();
    saveCurrentContactName();
  });
  let recipientTimer;
  els.recipientInput.addEventListener("input", () => {
    clearTimeout(recipientTimer);
    recipientTimer = setTimeout(searchRecipientSuggestions, 160);
  });
  els.recipientInput.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" && state.recipientSuggestions.length) {
      event.preventDefault();
      moveRecipientSuggestion(1);
      return;
    }
    if (event.key === "ArrowUp" && state.recipientSuggestions.length) {
      event.preventDefault();
      moveRecipientSuggestion(-1);
      return;
    }
    if (event.key === "Escape" && state.recipientSuggestions.length) {
      event.preventDefault();
      clearRecipientSuggestions();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (!chooseRecipientSuggestion()) {
        addRecipient(els.recipientInput.value);
      }
      return;
    }
    if (event.key === ",") {
      event.preventDefault();
      addRecipient(els.recipientInput.value);
    }
  });
  els.recipientSuggestions.addEventListener("pointerover", (event) => {
    const button = event.target.closest("[data-suggestion-index]");
    if (!button) return;
    const index = Number(button.dataset.suggestionIndex);
    if (index === state.recipientSuggestionIndex) return;
    state.recipientSuggestionIndex = index;
    renderRecipientSuggestions();
  });
  els.recipientSuggestions.addEventListener("pointerdown", (event) => {
    const button = event.target.closest("[data-suggestion-index]");
    if (!button) return;
    event.preventDefault();
    chooseRecipientSuggestion(Number(button.dataset.suggestionIndex));
    els.recipientInput.focus();
  });
  document.addEventListener("pointerdown", (event) => {
    if (!event.target.closest("#recipientBar")) {
      clearRecipientSuggestions();
    }
  });
  els.recipientChips.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-recipient]");
    if (!button) return;
    state.recipientDraft = state.recipientDraft.filter((phone) => phone !== button.dataset.removeRecipient);
    delete state.recipientDraftLabels[button.dataset.removeRecipient];
    state.draftMatchSeq += 1;
    renderRecipientDraft();
    renderThreadHeader();
  });
  els.sendButton.addEventListener("click", sendCurrentMessage);
  els.dealtButton.addEventListener("click", toggleCurrentConversationRead);
  els.archiveButton.addEventListener("click", () => {
    const archived = !Boolean(state.currentConversation?.is_archived);
    setCurrentConversationArchived(archived);
  });
  els.messageText.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (els.sendButton.disabled) return;
      sendCurrentMessage();
    }
  });
  els.messageText.addEventListener("input", () => showComposerError(""));
  els.mediaUrls.addEventListener("input", () => showComposerError(""));
  els.messages.addEventListener("click", (event) => {
    if (event.target.closest("#loadOlderButton")) {
      loadOlderMessages().catch((error) => toast(error.message));
      return;
    }
    const imageLink = event.target.closest("[data-lightbox-src]");
    if (imageLink) {
      event.preventDefault();
      openLightbox(imageLink.dataset.lightboxSrc);
    }
  });
  els.lightbox.addEventListener("click", (event) => {
    if (event.target === els.lightbox) closeLightbox();
  });
  els.lightboxClose.addEventListener("click", closeLightbox);
  els.lightboxPrev.addEventListener("click", () => stepLightbox(-1));
  els.lightboxNext.addEventListener("click", () => stepLightbox(1));
  document.addEventListener("keydown", handleGlobalKeydown);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      clearStatusPoll();
    } else {
      scheduleStatusPoll();
    }
  });
  window.addEventListener("popstate", handleNavigationPop);
  els.toggleDetailsButton.addEventListener("click", () => {
    setDetailsCollapsed(!document.body.classList.contains("details-collapsed"));
  });
  document.querySelector(".detail-rail").addEventListener("click", (event) => {
    const tab = event.target.closest(".tab");
    if (tab) {
      document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item === tab));
      document.querySelector("#identitiesPanel").classList.toggle("active", tab.dataset.tab === "identities");
      document.querySelector("#contactsPanel").classList.toggle("active", tab.dataset.tab === "contacts");
    }
    const save = event.target.closest(".save-identity");
    if (save) saveIdentity(save.closest(".identity-card"));
    const contactButton = event.target.closest("[data-contact-phone]");
    if (contactButton) {
      if (state.currentConversation) {
        startNewConversation();
      }
      addRecipient(contactButton.dataset.contactPhone, contactButton.dataset.contactName || "");
    }
  });
  els.syncContactsButton.addEventListener("click", async () => {
    els.syncContactsButton.disabled = true;
    try {
      const payload = await api("/api/contacts/sync", { method: "POST", body: "{}" });
      toast(`Synced ${payload.synced.contacts} contacts.`);
      state.bootstrap = await api("/api/bootstrap");
      renderBootstrap();
      searchContacts();
    } catch (error) {
      toast(error.message);
    } finally {
      els.syncContactsButton.disabled = false;
    }
  });
  let contactTimer;
  els.contactSearch.addEventListener("input", () => {
    clearTimeout(contactTimer);
    contactTimer = setTimeout(searchContacts, 180);
  });
}

async function init() {
  initializeTheme();
  loadColumnWidths();
  replaceNavigationState("list");
  bindEvents();
  updateComposerOffset();
  const savedCategory = localStorage.getItem("conversationCategory");
  state.conversationCategory = ["inbox", "unread", "hidden"].includes(savedCategory) ? savedCategory : "inbox";
  state.bootstrap = await api("/api/bootstrap");
  const savedDetailsState = localStorage.getItem("detailsCollapsedDefaultHidden");
  const detailsDefault = state.bootstrap.details_collapsed_default ?? true;
  setDetailsCollapsed(savedDetailsState === null ? Boolean(detailsDefault) : savedDetailsState === "1");
  renderBootstrap();
  await loadConversations();
  if (isDesktopLayout() && state.conversations[0]) {
    await openConversation(state.conversations[0].id);
  } else if (!state.conversations[0]) {
    startNewConversation();
  } else {
    setMobileThreadOpen(false);
  }
  searchContacts();
}

init().catch((error) => toast(error.message));
