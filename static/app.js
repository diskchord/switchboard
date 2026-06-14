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
  autoRefreshTimer: null,
  autoRefreshInFlight: false,
  autoRefreshSeconds: 15,
  refreshTokens: null,
  foregroundRefreshInFlight: false,
  lastListRefreshAt: 0,
  lastThreadRefreshAt: 0,
  threadNavigationInFlight: false,
  lightboxImages: [],
  lightboxIndex: 0,
  recipientDraft: [],
  recipientDraftLabels: {},
  recipientSuggestions: [],
  recipientSuggestionIndex: -1,
  recipientSuggestionSeq: 0,
  draftMatchSeq: 0,
  columnWidths: { left: 340, right: 330 },
  uploadedMedia: [],
  isUploadingMedia: false,
  language: "en",
  hotkeysEnabled: true,
  hotkeys: {},
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
  messageCounter: document.querySelector("#messageCounter"),
  mediaUrls: document.querySelector("#mediaUrls"),
  mediaFiles: document.querySelector("#mediaFiles"),
  uploadList: document.querySelector("#uploadList"),
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
const FOREGROUND_STALE_MS = 20_000;
const MIN_AUTO_REFRESH_SECONDS = 5;
const COLUMN_LIMITS = {
  leftMin: 260,
  leftMax: 560,
  rightMin: 260,
  rightMax: 520,
  threadMin: 320,
  handleWidth: 6,
};
const HOTKEY_DEFAULTS = {
  new_conversation: "n",
  focus_search: "/",
  focus_message: "m",
  toggle_read: "r",
  archive: "h",
  toggle_details: "p",
  next_thread: "j",
  previous_thread: "k",
};
const HOTKEY_COMMANDS = {
  new_conversation: () => startNewConversation(),
  focus_search: () => focusAndSelect(els.conversationSearch),
  focus_message: () => focusAndSelect(els.messageText),
  toggle_read: () => toggleCurrentConversationRead(),
  archive: () => {
    if (!state.currentConversation) return;
    setCurrentConversationArchived(!Boolean(state.currentConversation.is_archived));
  },
  toggle_details: () => setDetailsCollapsed(!document.body.classList.contains("details-collapsed")),
  next_thread: () => openAdjacentConversation(1),
  previous_thread: () => openAdjacentConversation(-1),
};
const I18N = {
  en: {
    "app.title": "Switchboard",
    "settings.title": "Settings",
    "settings.description": "Changes are saved locally and override matching .env values.",
    "settings.close": "Close settings",
    "settings.empty": "No settings are available.",
    "settings.saved": "Settings saved.",
    "common.save": "Save",
    "common.cancel": "Cancel",
    "common.remove": "Remove",
    "common.saved": "Saved.",
    "source.not_set": "Not set",
    "source.saved": "Saved",
    "source.env": ".env",
    "source.default": "Default",
    "theme.light": "Use light mode",
    "theme.dark": "Use dark mode",
    "search.placeholder": "Search",
    "category.aria": "Thread category",
    "category.inbox": "Inbox",
    "category.unread": "Unread",
    "category.hidden": "Hidden",
    "category.hidden_count": "Hidden ({count})",
    "category.unread_count": "Unread ({count})",
    "stats.threads": "Threads",
    "stats.texts": "Texts",
    "stats.media": "Media",
    "stats.people": "People",
    "conversation.new": "New conversation",
    "conversation.back": "Back to conversations",
    "conversation.actions": "Conversation actions",
    "conversation.read": "Read",
    "conversation.unread": "Unread",
    "conversation.hide": "Hide",
    "conversation.unhide": "Unhide",
    "conversation.unknown": "Unknown",
    "conversation.you": "You: ",
    "conversation.failed": "Failed: {detail}",
    "conversation.could_not_deliver": "Could not deliver",
    "conversation.loading_more": "Loading more...",
    "conversation.first_imported": "First imported thread reached",
    "conversation.archived": "Archived to Hidden.",
    "conversation.unarchived": "Moved to Inbox.",
    "conversation.marked_read": "Marked read.",
    "conversation.marked_unread": "Marked unread.",
    "conversation.existing_group": "Opened existing group.",
    "thread.conversation": "Conversation",
    "thread.select": "Select a thread",
    "thread.new": "New",
    "thread.group": "Group MMS",
    "thread.direct": "Direct",
    "panel.label": "Panel",
    "panel.expand_label": "Panel ‹",
    "panel.collapse_label": "Panel ›",
    "panel.show": "Show sender identities and contacts",
    "panel.hide": "Hide sender identities and contacts",
    "contact.name": "Name",
    "contact.rename": "Rename",
    "contact.name_placeholder": "Contact name",
    "contact.enter_name": "Enter a contact name.",
    "contact.saved_synced": "Saved to contacts.",
    "contact.saved_local": "Saved locally. Configure contact sync to publish changes.",
    "recipient.add": "Add recipient",
    "recipient.needs_phone": "Recipient needs a phone number.",
    "recipient.add_one": "Add a recipient.",
    "messages.aria": "Messages",
    "messages.empty": "No messages yet.",
    "messages.load_older": "Load older ({count})",
    "message.send_failed": "Send failed",
    "message.delivery_unconfirmed": "Delivery unconfirmed",
    "composer.from": "From",
    "composer.message": "Message",
    "composer.media_urls": "Media URLs",
    "composer.upload": "Upload media",
    "composer.send": "Send",
    "composer.requires_content": "Message text or media URL is required.",
    "upload.default": "Upload",
    "upload.one": "Media uploaded.",
    "upload.many": "{count} files uploaded.",
    "attachment.image": "Image",
    "attachment.file": "Attachment",
    "attachment.pdf": "PDF",
    "tabs.numbers": "Numbers",
    "tabs.contacts": "Contacts",
    "identities.heading": "Sender Identities",
    "identities.label": "Identity label",
    "identities.color": "Identity color",
    "contacts.sync": "Sync",
    "contacts.find": "Find contact",
    "contacts.add": "Add",
    "contacts.synced": "Synced {count} contacts.",
    "lightbox.preview": "Image preview",
    "lightbox.close": "Close image preview",
    "lightbox.previous": "Previous image",
    "lightbox.next": "Next image",
    "lightbox.count": "{caption} ({current} of {total})",
    "status.delivery_failed": "Failed",
    "status.delivery_unconfirmed": "Unconfirmed",
    "status.queued": "Queued",
    "status.sending": "Sending",
    "status.sent": "Sent",
    "status.delivered": "Delivered",
    "status.received": "Received",
    "status.imported": "Imported",
  },
  es: {
    "app.title": "Switchboard",
    "settings.title": "Ajustes",
    "settings.description": "Los cambios se guardan localmente y reemplazan los valores .env correspondientes.",
    "settings.close": "Cerrar ajustes",
    "settings.empty": "No hay ajustes disponibles.",
    "settings.saved": "Ajustes guardados.",
    "common.save": "Guardar",
    "common.cancel": "Cancelar",
    "common.remove": "Quitar",
    "common.saved": "Guardado.",
    "source.not_set": "Sin configurar",
    "source.saved": "Guardado",
    "source.env": ".env",
    "source.default": "Predeterminado",
    "theme.light": "Usar modo claro",
    "theme.dark": "Usar modo oscuro",
    "search.placeholder": "Buscar",
    "category.aria": "Categoría de conversaciones",
    "category.inbox": "Entrada",
    "category.unread": "No leídos",
    "category.hidden": "Ocultos",
    "category.hidden_count": "Ocultos ({count})",
    "category.unread_count": "No leídos ({count})",
    "stats.threads": "Hilos",
    "stats.texts": "Mensajes",
    "stats.media": "Media",
    "stats.people": "Personas",
    "conversation.new": "Nueva conversación",
    "conversation.back": "Volver a conversaciones",
    "conversation.actions": "Acciones de conversación",
    "conversation.read": "Leído",
    "conversation.unread": "No leído",
    "conversation.hide": "Ocultar",
    "conversation.unhide": "Mostrar",
    "conversation.unknown": "Desconocido",
    "conversation.you": "Tú: ",
    "conversation.failed": "Falló: {detail}",
    "conversation.could_not_deliver": "No se pudo entregar",
    "conversation.loading_more": "Cargando más...",
    "conversation.first_imported": "Primer hilo importado alcanzado",
    "conversation.archived": "Archivado en Ocultos.",
    "conversation.unarchived": "Movido a Entrada.",
    "conversation.marked_read": "Marcado como leído.",
    "conversation.marked_unread": "Marcado como no leído.",
    "conversation.existing_group": "Grupo existente abierto.",
    "thread.conversation": "Conversación",
    "thread.select": "Selecciona un hilo",
    "thread.new": "Nuevo",
    "thread.group": "MMS grupal",
    "thread.direct": "Directo",
    "panel.label": "Panel",
    "panel.expand_label": "Panel ‹",
    "panel.collapse_label": "Panel ›",
    "panel.show": "Mostrar identidades y contactos",
    "panel.hide": "Ocultar identidades y contactos",
    "contact.name": "Nombre",
    "contact.rename": "Renombrar",
    "contact.name_placeholder": "Nombre del contacto",
    "contact.enter_name": "Escribe un nombre de contacto.",
    "contact.saved_synced": "Guardado en contactos.",
    "contact.saved_local": "Guardado localmente. Configura sincronización de contactos para publicarlo.",
    "recipient.add": "Agregar destinatario",
    "recipient.needs_phone": "El destinatario necesita un número de teléfono.",
    "recipient.add_one": "Agrega un destinatario.",
    "messages.aria": "Mensajes",
    "messages.empty": "Aún no hay mensajes.",
    "messages.load_older": "Cargar anteriores ({count})",
    "message.send_failed": "Envío fallido",
    "message.delivery_unconfirmed": "Entrega sin confirmar",
    "composer.from": "De",
    "composer.message": "Mensaje",
    "composer.media_urls": "URLs de media",
    "composer.upload": "Subir media",
    "composer.send": "Enviar",
    "composer.requires_content": "Se requiere texto o URL de media.",
    "upload.default": "Subida",
    "upload.one": "Media subida.",
    "upload.many": "{count} archivos subidos.",
    "attachment.image": "Imagen",
    "attachment.file": "Adjunto",
    "attachment.pdf": "PDF",
    "tabs.numbers": "Números",
    "tabs.contacts": "Contactos",
    "identities.heading": "Identidades de envío",
    "identities.label": "Etiqueta de identidad",
    "identities.color": "Color de identidad",
    "contacts.sync": "Sincronizar",
    "contacts.find": "Buscar contacto",
    "contacts.add": "Agregar",
    "contacts.synced": "{count} contactos sincronizados.",
    "lightbox.preview": "Vista de imagen",
    "lightbox.close": "Cerrar vista de imagen",
    "lightbox.previous": "Imagen anterior",
    "lightbox.next": "Imagen siguiente",
    "lightbox.count": "{caption} ({current} de {total})",
    "status.delivery_failed": "Falló",
    "status.delivery_unconfirmed": "Sin confirmar",
    "status.queued": "En cola",
    "status.sending": "Enviando",
    "status.sent": "Enviado",
    "status.delivered": "Entregado",
    "status.received": "Recibido",
    "status.imported": "Importado",
  },
  fr: {
    "app.title": "Switchboard",
    "settings.title": "Réglages",
    "settings.description": "Les changements sont enregistrés localement et remplacent les valeurs .env correspondantes.",
    "settings.close": "Fermer les réglages",
    "settings.empty": "Aucun réglage disponible.",
    "settings.saved": "Réglages enregistrés.",
    "common.save": "Enregistrer",
    "common.cancel": "Annuler",
    "common.remove": "Supprimer",
    "common.saved": "Enregistré.",
    "source.not_set": "Non défini",
    "source.saved": "Enregistré",
    "source.env": ".env",
    "source.default": "Défaut",
    "theme.light": "Utiliser le mode clair",
    "theme.dark": "Utiliser le mode sombre",
    "search.placeholder": "Rechercher",
    "category.aria": "Catégorie de fils",
    "category.inbox": "Boîte",
    "category.unread": "Non lus",
    "category.hidden": "Masqués",
    "category.hidden_count": "Masqués ({count})",
    "category.unread_count": "Non lus ({count})",
    "stats.threads": "Fils",
    "stats.texts": "Messages",
    "stats.media": "Médias",
    "stats.people": "Personnes",
    "conversation.new": "Nouvelle conversation",
    "conversation.back": "Retour aux conversations",
    "conversation.actions": "Actions de conversation",
    "conversation.read": "Lu",
    "conversation.unread": "Non lu",
    "conversation.hide": "Masquer",
    "conversation.unhide": "Afficher",
    "conversation.unknown": "Inconnu",
    "conversation.you": "Vous : ",
    "conversation.failed": "Échec : {detail}",
    "conversation.could_not_deliver": "Impossible de livrer",
    "conversation.loading_more": "Chargement...",
    "conversation.first_imported": "Premier fil importé atteint",
    "conversation.archived": "Archivé dans Masqués.",
    "conversation.unarchived": "Déplacé vers la boîte.",
    "conversation.marked_read": "Marqué comme lu.",
    "conversation.marked_unread": "Marqué comme non lu.",
    "conversation.existing_group": "Groupe existant ouvert.",
    "thread.conversation": "Conversation",
    "thread.select": "Sélectionnez un fil",
    "thread.new": "Nouveau",
    "thread.group": "MMS de groupe",
    "thread.direct": "Direct",
    "panel.label": "Panneau",
    "panel.expand_label": "Panneau ‹",
    "panel.collapse_label": "Panneau ›",
    "panel.show": "Afficher les identités et contacts",
    "panel.hide": "Masquer les identités et contacts",
    "contact.name": "Nom",
    "contact.rename": "Renommer",
    "contact.name_placeholder": "Nom du contact",
    "contact.enter_name": "Entrez un nom de contact.",
    "contact.saved_synced": "Enregistré dans les contacts.",
    "contact.saved_local": "Enregistré localement. Configurez la synchronisation pour publier les changements.",
    "recipient.add": "Ajouter un destinataire",
    "recipient.needs_phone": "Le destinataire doit avoir un numéro de téléphone.",
    "recipient.add_one": "Ajoutez un destinataire.",
    "messages.aria": "Messages",
    "messages.empty": "Aucun message pour le moment.",
    "messages.load_older": "Charger les anciens ({count})",
    "message.send_failed": "Envoi échoué",
    "message.delivery_unconfirmed": "Livraison non confirmée",
    "composer.from": "De",
    "composer.message": "Message",
    "composer.media_urls": "URL de médias",
    "composer.upload": "Téléverser un média",
    "composer.send": "Envoyer",
    "composer.requires_content": "Un message ou une URL de média est requis.",
    "upload.default": "Téléversement",
    "upload.one": "Média téléversé.",
    "upload.many": "{count} fichiers téléversés.",
    "attachment.image": "Image",
    "attachment.file": "Pièce jointe",
    "attachment.pdf": "PDF",
    "tabs.numbers": "Numéros",
    "tabs.contacts": "Contacts",
    "identities.heading": "Identités d'envoi",
    "identities.label": "Libellé d'identité",
    "identities.color": "Couleur d'identité",
    "contacts.sync": "Synchroniser",
    "contacts.find": "Trouver un contact",
    "contacts.add": "Ajouter",
    "contacts.synced": "{count} contacts synchronisés.",
    "lightbox.preview": "Aperçu de l'image",
    "lightbox.close": "Fermer l'aperçu",
    "lightbox.previous": "Image précédente",
    "lightbox.next": "Image suivante",
    "lightbox.count": "{caption} ({current} sur {total})",
    "status.delivery_failed": "Échec",
    "status.delivery_unconfirmed": "Non confirmé",
    "status.queued": "En attente",
    "status.sending": "Envoi",
    "status.sent": "Envoyé",
    "status.delivered": "Livré",
    "status.received": "Reçu",
    "status.imported": "Importé",
  },
};

function t(key, replacements = {}) {
  const messages = I18N[state.language] || I18N.en;
  const fallback = I18N.en[key] || key;
  return String(messages[key] || fallback).replace(/\{(\w+)\}/g, (_match, name) =>
    Object.prototype.hasOwnProperty.call(replacements, name) ? replacements[name] : `{${name}}`,
  );
}

function localizedStatusLabel(status, fallback = "") {
  if (!status) return fallback || "";
  const key = `status.${String(status).toLowerCase()}`;
  return I18N.en[key] ? t(key) : fallback;
}

function setDetailsCollapsed(collapsed) {
  document.body.classList.toggle("details-collapsed", collapsed);
  els.toggleDetailsButton.textContent = collapsed ? t("panel.expand_label") : t("panel.collapse_label");
  els.toggleDetailsButton.setAttribute("aria-pressed", collapsed ? "true" : "false");
  els.toggleDetailsButton.setAttribute(
    "aria-label",
    collapsed ? t("panel.show") : t("panel.hide"),
  );
  els.toggleDetailsButton.title = collapsed ? t("panel.show") : t("panel.hide");
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
    els.themeToggle.title = nextTheme === "dark" ? t("theme.light") : t("theme.dark");
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

window.textingOpenConversationFromNative = (id) => {
  const conversationId = Number(id);
  if (!conversationId) return false;
  openConversation(conversationId, { updateHistory: false }).catch((error) => toast(error.message));
  return true;
};

window.textingRefreshIfStaleFromNative = () => {
  refreshForegroundData().catch((error) => toast(error.message));
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

function bootstrapSettingValue(key, fallback = "") {
  const sections = state.bootstrap?.settings?.sections || [];
  for (const section of sections) {
    const field = (section.fields || []).find((item) => item.key === key);
    if (field) return field.value ?? fallback;
  }
  return fallback;
}

function resolveLanguage(value) {
  const raw = String(value || "auto").toLowerCase();
  if (["en", "es", "fr"].includes(raw)) return raw;
  const browser = String(navigator.language || "en").toLowerCase();
  if (browser.startsWith("es")) return "es";
  if (browser.startsWith("fr")) return "fr";
  return "en";
}

function applyStaticTranslations() {
  document.documentElement.lang = state.language;
  document.title = t("app.title");
  document.querySelectorAll("[data-i18n]").forEach((element) => {
    element.textContent = t(element.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
    element.setAttribute("placeholder", t(element.dataset.i18nPlaceholder));
  });
  document.querySelectorAll("[data-i18n-title]").forEach((element) => {
    element.setAttribute("title", t(element.dataset.i18nTitle));
  });
  document.querySelectorAll("[data-i18n-aria-label]").forEach((element) => {
    element.setAttribute("aria-label", t(element.dataset.i18nAriaLabel));
  });
}

function normalizeHotkey(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (!raw) return "";
  const aliases = {
    cmd: "meta",
    command: "meta",
    option: "alt",
    control: "ctrl",
    return: "enter",
    esc: "escape",
    spacebar: "space",
    " ": "space",
  };
  const parts = raw
    .split("+")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => aliases[part] || part);
  const modifiers = new Set();
  let key = "";
  for (const part of parts) {
    if (["meta", "ctrl", "alt", "shift"].includes(part)) {
      modifiers.add(part);
    } else {
      key = aliases[part] || part;
    }
  }
  if (!key) return "";
  const ordered = ["meta", "ctrl", "alt", "shift"].filter((modifier) => modifiers.has(modifier));
  ordered.push(key);
  return ordered.join("+");
}

function hotkeyFromEvent(event) {
  const modifiers = [];
  if (event.metaKey) modifiers.push("meta");
  if (event.ctrlKey) modifiers.push("ctrl");
  if (event.altKey) modifiers.push("alt");
  if (event.shiftKey) modifiers.push("shift");
  let key = event.key === " " ? "space" : String(event.key || "").toLowerCase();
  if (key === "arrowup") key = "up";
  if (key === "arrowdown") key = "down";
  if (key === "arrowleft") key = "left";
  if (key === "arrowright") key = "right";
  return normalizeHotkey([...modifiers, key].join("+"));
}

function configureHotkeys() {
  state.hotkeysEnabled = String(bootstrapSettingValue("hotkeys.enabled", "1")) === "1";
  state.hotkeys = {};
  for (const [command, fallback] of Object.entries(HOTKEY_DEFAULTS)) {
    const normalized = normalizeHotkey(bootstrapSettingValue(`hotkeys.${command}`, fallback));
    if (normalized) state.hotkeys[normalized] = command;
  }
}

function configureAutoRefresh() {
  const rawSeconds = Number(bootstrapSettingValue("behavior.auto_refresh_seconds", "15"));
  if (!Number.isFinite(rawSeconds) || rawSeconds <= 0) {
    state.autoRefreshSeconds = 0;
    clearAutoRefresh();
    return;
  }
  state.autoRefreshSeconds = Math.max(MIN_AUTO_REFRESH_SECONDS, Math.round(rawSeconds));
  scheduleAutoRefresh();
}

function applyRuntimeSettings() {
  state.language = resolveLanguage(bootstrapSettingValue("ui.language", "auto"));
  configureHotkeys();
  configureAutoRefresh();
  applyStaticTranslations();
  applyTheme(document.documentElement.dataset.theme || "light", { persist: false });
}

function focusAndSelect(element) {
  if (!element) return;
  element.focus();
  element.select?.();
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
  if (!field.has_value) return t("source.not_set");
  if (field.source === "saved") return t("source.saved");
  if (field.source === "env") return t("source.env");
  return t("source.default");
}

function renderSettings(payload = state.bootstrap?.settings) {
  const sections = payload?.sections || [];
  if (!sections.length) {
    els.settingsSections.innerHTML = `<div class="empty-state">${escapeHtml(t("settings.empty"))}</div>`;
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
    applyRuntimeSettings();
    renderBootstrap();
    renderThreadHeader();
    renderConversations();
    renderMessages(state.messages, "preserve");
    renderSettings(state.bootstrap.settings);
    closeSettings();
    toast(t("settings.saved"));
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

const GSM_BASIC =
  "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ" +
  " !\"#¤%&'()*+,-./0123456789:;<=>?" +
  "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà";
const GSM_EXTENDED = "^{}\\[~]|€";

function smsSegmentInfo(text) {
  let units = 0;
  let gsm = true;
  for (const char of text) {
    if (GSM_BASIC.includes(char)) {
      units += 1;
    } else if (GSM_EXTENDED.includes(char)) {
      units += 2;
    } else {
      gsm = false;
      break;
    }
  }
  if (!gsm) {
    units = text.length;
  }
  const singleLimit = gsm ? 160 : 70;
  const multiLimit = gsm ? 153 : 67;
  const segments = units === 0 ? 0 : units <= singleLimit ? 1 : Math.ceil(units / multiLimit);
  const currentLimit = segments <= 1 ? singleLimit : multiLimit * segments;
  return { units, segments, currentLimit, encoding: gsm ? "GSM" : "Unicode" };
}

function updateMessageCounter() {
  const { units, segments, currentLimit, encoding } = smsSegmentInfo(els.messageText.value || "");
  const segmentLabel = segments <= 1 ? "1 SMS" : `${segments} SMS`;
  els.messageCounter.textContent = units === 0 ? `0/${currentLimit}` : `${units}/${currentLimit} · ${segmentLabel}`;
  els.messageCounter.title = `${encoding} text encoding`;
}

function renderUploadedMedia() {
  if (!state.uploadedMedia.length) {
    els.uploadList.classList.add("hidden");
    els.uploadList.innerHTML = "";
    return;
  }
  els.uploadList.innerHTML = state.uploadedMedia
    .map(
      (item, index) => `
        <span class="upload-chip" title="${escapeHtml(item.url)}">
          <span>${escapeHtml(item.original_filename || item.filename || t("upload.default"))}</span>
          <button type="button" data-remove-upload="${index}" title="${escapeHtml(t("common.remove"))}" aria-label="${escapeHtml(t("common.remove"))}">×</button>
        </span>`,
    )
    .join("");
  els.uploadList.classList.remove("hidden");
  requestAnimationFrame(updateComposerOffset);
}

async function uploadMediaFile(file) {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch("/api/uploads", {
    method: "POST",
    body: form,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Upload failed: ${response.status}`);
  }
  return payload;
}

async function uploadSelectedMedia(files) {
  const selected = [...files];
  if (!selected.length) return;
  state.isUploadingMedia = true;
  els.mediaFiles.disabled = true;
  els.sendButton.disabled = true;
  showComposerError("");
  try {
    for (const file of selected) {
      const uploaded = await uploadMediaFile(file);
      state.uploadedMedia.push(uploaded);
      renderUploadedMedia();
    }
    toast(selected.length === 1 ? t("upload.one") : t("upload.many", { count: selected.length }));
  } catch (error) {
    showComposerError(error.message);
    toast(error.message);
  } finally {
    state.isUploadingMedia = false;
    els.mediaFiles.disabled = false;
    els.sendButton.disabled = false;
    els.mediaFiles.value = "";
    updateComposerOffset();
  }
}

function initialConversationIdFromLocation() {
  const params = new URLSearchParams(window.location.search);
  const queryValue = Number(params.get("conversation") || params.get("conversation_id") || "0");
  if (queryValue) return queryValue;
  const hash = String(window.location.hash || "").replace(/^#/, "");
  const hashParams = new URLSearchParams(hash);
  return Number(hashParams.get("conversation") || hashParams.get("conversation_id") || "0");
}

function clearInitialConversationUrl(conversationId) {
  if (!conversationId || !history.replaceState) return;
  const url = new URL(window.location.href);
  url.searchParams.delete("conversation");
  url.searchParams.delete("conversation_id");
  if (url.hash.includes("conversation")) {
    url.hash = "";
  }
  history.replaceState({ textingApp: true, view: "thread", conversationId }, "", url.pathname + url.search + url.hash);
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

function localeForLanguage() {
  if (state.language === "es") return "es";
  if (state.language === "fr") return "fr";
  return "en";
}

function dateForDisplay(parts) {
  return new Date(Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour || 0, parts.minute || 0));
}

function isBeforeCurrentYear(parts) {
  return Number(parts?.year) < new Date().getFullYear();
}

function formatClock(parts) {
  return new Intl.DateTimeFormat(localeForLanguage(), {
    hour: "numeric",
    minute: "2-digit",
    hour12: state.language === "en",
    timeZone: "UTC",
  }).format(dateForDisplay(parts));
}

function formatTime(value, compact = false) {
  if (!value) return "";
  const parts = localTimeParts(value);
  if (!parts) return "";
  const includeYear = isBeforeCurrentYear(parts);
  const month = new Intl.DateTimeFormat(localeForLanguage(), {
    month: compact ? "short" : "long",
    day: "numeric",
    ...(includeYear ? { year: "numeric" } : {}),
    timeZone: "UTC",
  }).format(dateForDisplay(parts));
  return compact ? `${month}, ${formatClock(parts)}` : `${month} ${formatClock(parts)}`;
}

function formatDay(value) {
  const parts = localTimeParts(value);
  if (!parts) return "";
  return new Intl.DateTimeFormat(localeForLanguage(), {
    weekday: "short",
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(dateForDisplay({ ...parts, hour: 12, minute: 0 }));
}

function initials(name) {
  const value = String(name || "").trim();
  const digits = value.replace(/\D/g, "");
  if (digits.length >= 7 && !/[A-Za-z]/.test(value)) {
    return "?";
  }
  const parts = (value || "?").split(/\s+/).slice(0, 2);
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

function openContactRename() {
  const participant = currentDirectParticipant();
  if (!participant) return;
  setContactNameEditor(true);
}

function activeIdentity() {
  const phone = els.fromNumber.value;
  return state.bootstrap?.identities?.find((identity) => identity.phone_number === phone);
}

function activeIdentityPhones() {
  return new Set((state.bootstrap?.identities || []).filter((identity) => identity.is_active).map((identity) => identity.phone_number));
}

function selectFromNumber(phone) {
  if (!phone) return;
  const activePhones = activeIdentityPhones();
  if (activePhones.has(phone)) {
    els.fromNumber.value = phone;
  }
}

function preferredReplyIdentity(conversation = state.currentConversation, messages = state.messages) {
  const activePhones = activeIdentityPhones();
  if (!activePhones.size) return "";
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.direction !== "inbound") continue;
    for (const phone of message.to_numbers || []) {
      if (activePhones.has(phone)) return phone;
    }
  }
  const selfParticipant = (conversation?.participants || []).find(
    (participant) => participant.role === "self" && activePhones.has(participant.phone_number),
  );
  if (selfParticipant) return selfParticipant.phone_number;
  if (activePhones.has(els.fromNumber.value)) return els.fromNumber.value;
  return state.bootstrap?.default_identity || [...activePhones][0] || "";
}

function renderBootstrap() {
  const stats = state.bootstrap.stats || {};
  const previousFromNumber = els.fromNumber.value;
  els.statStrip.innerHTML = [
    [t("stats.threads"), stats.conversations],
    [t("stats.texts"), stats.messages],
    [t("stats.media"), stats.attachments],
    [t("stats.people"), stats.contacts],
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
  selectFromNumber(previousFromNumber || preferredReplyIdentity());

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
      tab.textContent = t("category.hidden_count", { count: hiddenCount });
    } else if (tab.dataset.category === "unread") {
      tab.textContent = t("category.unread_count", { count: unreadCount });
    } else {
      tab.textContent = t("category.inbox");
    }
  });
}

function renderIdentities() {
  els.identityList.innerHTML = (state.bootstrap.identities || [])
    .map(
      (identity) => `
        <article class="identity-card" data-id="${identity.id}">
          <label class="swatch color-swatch" style="background:${escapeHtml(identity.color)}" title="${escapeHtml(t("identities.color"))}">
            <input class="identity-color" type="color" value="${escapeHtml(identity.color)}" aria-label="${escapeHtml(t("identities.color"))}" />
          </label>
          <div class="identity-main">
            <input class="identity-label" value="${escapeHtml(identity.label)}" aria-label="${escapeHtml(t("identities.label"))}" />
            <div class="identity-phone">${escapeHtml(phoneDisplay(identity.phone_number))}</div>
          </div>
          <button class="icon-button save-identity" title="${escapeHtml(t("common.save"))}" aria-label="${escapeHtml(t("common.save"))}">✓</button>
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
      const title = conversation.title || t("conversation.unknown");
      const previewPrefix = conversation.last_direction === "outbound" ? t("conversation.you") : "";
      const lastStatusLabel = localizedStatusLabel(conversation.last_status, conversation.last_status_label);
      const failedPreview =
        conversation.last_status_kind === "failed"
          ? t("conversation.failed", {
              detail: conversation.last_status_detail || lastStatusLabel || t("conversation.could_not_deliver"),
            })
          : "";
      const preview = failedPreview || conversation.last_text || (lastStatusLabel ? lastStatusLabel : "");
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
      ? `<div class="conversation-status">${escapeHtml(t("conversation.loading_more"))}</div>`
      : !state.hasMoreConversations && state.conversations.length
        ? `<div class="conversation-status">${escapeHtml(t("conversation.first_imported"))}</div>`
        : "";
  els.conversationList.innerHTML = items + status;
}

function renderConversationsPreservingScroll() {
  const previousScrollTop = els.conversationList.scrollTop;
  renderConversations();
  els.conversationList.scrollTop = previousScrollTop;
}

function scrollActiveConversationIntoView() {
  requestAnimationFrame(() => {
    const active = els.conversationList.querySelector(".conversation-item.active");
    active?.scrollIntoView({ block: "nearest" });
  });
}

function mergeConversationIntoList(conversation) {
  if (!conversation?.id) return;
  const index = state.conversations.findIndex((item) => item.id === conversation.id);
  if (index >= 0) {
    state.conversations[index] = { ...state.conversations[index], ...conversation };
  }
}

function applyIdentityToLoadedState(identity) {
  if (!identity?.phone_number) return;
  state.messages = state.messages.map((message) => {
    if (message.from_number !== identity.phone_number) return message;
    return {
      ...message,
      from_display: identity.label || message.from_display,
      identity_label: identity.label,
      identity_color: identity.color,
    };
  });
  const updateParticipants = (conversation) => {
    if (!conversation?.participants) return conversation;
    return {
      ...conversation,
      participants: conversation.participants.map((participant) =>
        participant.phone_number === identity.phone_number
          ? { ...participant, display: identity.label || participant.display, color: identity.color }
          : participant,
      ),
    };
  };
  state.currentConversation = updateParticipants(state.currentConversation);
  state.conversations = state.conversations.map(updateParticipants);
}

function renderRecipientDraft() {
  els.recipientChips.innerHTML = state.recipientDraft
    .map(
      (phone) => `
        <span class="chip" title="${escapeHtml(phoneDisplay(phone))}">
          <span>${escapeHtml(draftRecipientDisplay(phone))}</span>
          <button data-remove-recipient="${escapeHtml(phone)}" title="${escapeHtml(t("common.remove"))}" aria-label="${escapeHtml(t("common.remove"))}">×</button>
        </span>`,
    )
    .join("");
}

function renderArchiveButton(archived = false) {
  const label = archived ? t("conversation.unhide") : t("conversation.hide");
  els.archiveButton.textContent = archived ? "↩" : "×";
  els.archiveButton.title = label;
  els.archiveButton.setAttribute("aria-label", label);
  els.archiveButton.classList.toggle("danger-button", !archived);
  els.archiveButton.classList.toggle("unarchive-button", archived);
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
    els.threadKind.textContent = t("thread.new");
    els.threadTitle.textContent = t("conversation.new");
    els.threadTitle.classList.remove("thread-title-clickable");
    els.threadTitle.removeAttribute("role");
    els.threadTitle.removeAttribute("tabindex");
    els.threadTitle.removeAttribute("title");
    els.participantLine.textContent = state.recipientDraft.map((phone) => draftRecipientDisplay(phone, { includePhone: true })).join(", ");
    els.contactNameToggle.hidden = true;
    setContactNameEditor(false);
    els.recipientBar.classList.remove("hidden");
    els.threadPane.classList.add("recipients-visible");
    els.dealtButton.disabled = true;
    els.archiveButton.disabled = true;
    els.dealtButton.textContent = t("conversation.read");
    renderArchiveButton(false);
    renderRecipientDraft();
    return;
  }
  const conversation = state.currentConversation;
  const archived = Boolean(conversation.is_archived);
  els.threadKind.textContent = conversation.kind === "group" ? t("thread.group") : t("thread.direct");
  els.threadTitle.textContent = conversation.title || t("thread.conversation");
  els.participantLine.textContent = (conversation.participants || [])
    .filter((p) => p.role === "participant")
    .map(participantDisplay)
    .join(", ");
  const participant = currentDirectParticipant(conversation);
  els.threadTitle.classList.toggle("thread-title-clickable", Boolean(participant));
  if (participant) {
    els.threadTitle.setAttribute("role", "button");
    els.threadTitle.setAttribute("tabindex", "0");
    els.threadTitle.title = t("contact.rename");
  } else {
    els.threadTitle.removeAttribute("role");
    els.threadTitle.removeAttribute("tabindex");
    els.threadTitle.removeAttribute("title");
  }
  els.contactNameToggle.hidden = !participant || Boolean(participantSavedName(participant));
  els.contactNameToggle.textContent = t("contact.name");
  if (!participant) setContactNameEditor(false);
  els.recipientBar.classList.add("hidden");
  els.threadPane.classList.remove("recipients-visible");
  els.dealtButton.disabled = false;
  els.archiveButton.disabled = false;
  els.dealtButton.textContent = conversationIsRead(conversation) ? t("conversation.unread") : t("conversation.read");
  renderArchiveButton(archived);
}

function mediaUrl(attachment) {
  if (attachment.local_path) {
    const filename = attachment.local_path.split("/").pop();
    if (attachment.source === "upload") {
      return `/uploads/${encodeURIComponent(filename)}`;
    }
    return `/media/${encodeURIComponent(filename)}`;
  }
  return attachment.remote_url;
}

function isImageAttachment(attachment, url) {
  const contentType = attachment.content_type || "";
  return contentType.startsWith("image/") || /\.(avif|gif|heic|heif|jpe?g|png|webp)(\?.*)?$/i.test(url || "");
}

function isPdfAttachment(attachment, url) {
  const contentType = (attachment.content_type || "").split(";", 1)[0].trim().toLowerCase();
  return contentType === "application/pdf" || /\.pdf([?#].*)?$/i.test(url || attachment.filename || "");
}

function pdfViewerUrl(url) {
  return `${url}${String(url).includes("#") ? "&" : "#"}toolbar=1&navpanes=0`;
}

function renderAttachment(attachment) {
  const url = mediaUrl(attachment);
  if (!url) return "";
  const contentType = attachment.content_type || "";
  if (isImageAttachment(attachment, url)) {
    const caption = attachment.filename || t("attachment.image");
    return `<a href="${escapeHtml(url)}" class="image-attachment" data-lightbox-src="${escapeHtml(url)}" data-lightbox-caption="${escapeHtml(caption)}" target="_blank"><img src="${escapeHtml(url)}" alt="" loading="lazy" /></a>`;
  }
  if (contentType.startsWith("video/")) {
    return `<video src="${escapeHtml(url)}" controls preload="metadata"></video>`;
  }
  if (contentType.startsWith("audio/")) {
    return `<audio src="${escapeHtml(url)}" controls preload="metadata"></audio>`;
  }
  if (isPdfAttachment(attachment, url)) {
    const filename = attachment.filename || t("attachment.pdf");
    const safeUrl = escapeHtml(url);
    const safeViewerUrl = escapeHtml(pdfViewerUrl(url));
    const safeFilename = escapeHtml(filename);
    return `<div class="pdf-attachment">
      <a class="pdf-attachment-link" href="${safeUrl}" target="_blank" rel="noopener">${safeFilename}</a>
      <object class="pdf-attachment-viewer" data="${safeViewerUrl}" type="application/pdf" aria-label="${safeFilename}">
        <a class="attachment-link" href="${safeUrl}" target="_blank" rel="noopener">${safeFilename}</a>
      </object>
    </div>`;
  }
  return `<a class="attachment-link" href="${escapeHtml(url)}" target="_blank">${escapeHtml(
    attachment.filename || t("attachment.file"),
  )}</a>`;
}

function renderMessages(messages, scrollMode = "bottom") {
  const wasNearBottom = isNearMessageBottom();
  if (!messages.length) {
    els.messages.innerHTML = `<div class="empty-state">${escapeHtml(t("messages.empty"))}</div>`;
    updateComposerOffset();
    watchMessageMediaForBottomStick(scrollMode, wasNearBottom);
    return;
  }
  const oldScrollHeight = els.messages.scrollHeight;
  const oldScrollTop = els.messages.scrollTop;
  let lastDay = "";
  const loadOlder = state.hasMoreMessages
    ? `<div class="older-row"><button class="small-button" id="loadOlderButton">${escapeHtml(t("messages.load_older", { count: state.olderCount }))}</button></div>`
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
      const statusLabel = localizedStatusLabel(message.status, message.status_label || message.status || "");
      const statusDetail = message.status_detail || "";
      const bubbleStyle =
        message.direction === "outbound" && message.identity_color
          ? ` style="--message-out:${escapeHtml(message.identity_color)}"`
          : "";
      const failureDetail =
        statusKind === "failed" || statusKind === "warning"
          ? `<div class="message-error ${statusKind}">
              <strong>${statusKind === "failed" ? escapeHtml(t("message.send_failed")) : escapeHtml(t("message.delivery_unconfirmed"))}</strong>
              <span>${escapeHtml(statusDetail || statusLabel)}</span>
            </div>`
          : "";
      return `
        ${divider}
        <article class="message-row ${message.direction} ${statusKind}"${bubbleStyle}>
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
    caption: link.dataset.lightboxCaption || t("attachment.image"),
  }));
}

function renderLightbox() {
  const current = state.lightboxImages[state.lightboxIndex];
  if (!current) return;
  els.lightboxImage.src = current.src;
  els.lightboxCaption.textContent =
    state.lightboxImages.length > 1
      ? t("lightbox.count", {
          caption: current.caption,
          current: state.lightboxIndex + 1,
          total: state.lightboxImages.length,
        })
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

function navigateThreadsWithArrowKey(event) {
  if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) return false;
  if (!["ArrowUp", "ArrowDown"].includes(event.key)) return false;
  if (isEditableKeyTarget(event.target)) return false;
  if (!state.currentConversationId || !state.conversations.length) return false;
  event.preventDefault();
  if (state.threadNavigationInFlight) return true;
  const offset = event.key === "ArrowDown" ? 1 : -1;
  const currentIndex = state.conversations.findIndex((conversation) => conversation.id === state.currentConversationId);
  const baseIndex = currentIndex >= 0 ? currentIndex : 0;
  const nextIndex = clamp(baseIndex + offset, 0, state.conversations.length - 1);
  const next = state.conversations[nextIndex];
  if (!next || next.id === state.currentConversationId) return true;
  state.threadNavigationInFlight = true;
  openConversation(next.id)
    .catch((error) => toast(error.message))
    .finally(() => {
      state.threadNavigationInFlight = false;
    });
  return true;
}

function hotkeyCanRunInTarget(event) {
  if (!isEditableKeyTarget(event.target)) return true;
  return event.metaKey || event.ctrlKey || event.altKey;
}

function runHotkey(event) {
  if (!state.hotkeysEnabled || event.defaultPrevented) return false;
  if (!hotkeyCanRunInTarget(event)) return false;
  const command = state.hotkeys[hotkeyFromEvent(event)];
  if (!command) return false;
  const handler = HOTKEY_COMMANDS[command];
  if (!handler) return false;
  event.preventDefault();
  Promise.resolve(handler()).catch((error) => toast(error.message));
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
  if (navigateThreadsWithArrowKey(event)) return;
  runHotkey(event);
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

function clearAutoRefresh() {
  if (state.autoRefreshTimer) {
    clearTimeout(state.autoRefreshTimer);
    state.autoRefreshTimer = null;
  }
}

function scheduleAutoRefresh() {
  clearAutoRefresh();
  if (!state.bootstrap || !state.autoRefreshSeconds || document.hidden) return;
  state.autoRefreshTimer = setTimeout(() => {
    pollForChanges().catch((error) => toast(error.message));
  }, state.autoRefreshSeconds * 1000);
}

function refreshQuery() {
  return state.currentConversationId ? `?conversation_id=${encodeURIComponent(state.currentConversationId)}` : "";
}

async function pollForChanges({ prime = false, force = false } = {}) {
  if (!state.bootstrap || state.autoRefreshInFlight || document.hidden) return;
  state.autoRefreshInFlight = true;
  try {
    const conversationId = state.currentConversationId;
    const payload = await api(`/api/refresh${refreshQuery()}`);
    const previous = state.refreshTokens || {};
    state.refreshTokens = payload.tokens || {};
    if (prime || (!Object.keys(previous).length && !force)) return;

    const listChanged = force || previous.bootstrap !== state.refreshTokens.bootstrap || previous.list !== state.refreshTokens.list;
    const conversationChanged =
      Boolean(conversationId) &&
      state.currentConversationId === conversationId &&
      (force || previous.conversation !== state.refreshTokens.conversation);

    if (listChanged) {
      state.bootstrap = await api("/api/bootstrap");
      applyRuntimeSettings();
      renderBootstrap();
      await loadConversations({
        append: false,
        preserveScroll: true,
        limit: Math.max(80, state.conversations.length || 0),
      });
    }
    if (conversationChanged && state.currentConversationId === conversationId) {
      await refreshCurrentConversationStatus();
    }
  } finally {
    state.autoRefreshInFlight = false;
    scheduleAutoRefresh();
  }
}

async function refreshCurrentConversationStatus() {
  if (!state.currentConversationId || state.statusPollInFlight) return;
  state.statusPollInFlight = true;
  const conversationId = state.currentConversationId;
  const shouldStickToBottom = isNearMessageBottom();
  const limit = Math.max(80, state.messages.length || 0);
  try {
    const payload = await api(`/api/conversations/${conversationId}/messages?limit=${limit}`);
    if (state.currentConversationId !== conversationId) return;
    state.currentConversation = payload.conversation;
    state.messages = payload.messages;
    state.hasMoreMessages = payload.has_more;
    state.olderCount = payload.older_count;
    state.lastThreadRefreshAt = Date.now();
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

async function refreshForegroundData({ force = false } = {}) {
  if (!state.bootstrap || state.foregroundRefreshInFlight || document.hidden) return;
  const now = Date.now();
  const listIsStale = force || now - state.lastListRefreshAt > FOREGROUND_STALE_MS;
  const threadIsStale =
    Boolean(state.currentConversationId) && (force || now - state.lastThreadRefreshAt > FOREGROUND_STALE_MS);
  if (!listIsStale && !threadIsStale) return;
  state.foregroundRefreshInFlight = true;
  try {
    if (listIsStale) {
      state.bootstrap = await api("/api/bootstrap");
      applyRuntimeSettings();
      renderBootstrap();
      await loadConversations({ append: false, preserveScroll: true });
    }
    if (threadIsStale && state.currentConversationId) {
      await refreshCurrentConversationStatus();
    }
  } finally {
    state.foregroundRefreshInFlight = false;
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

async function loadConversations({ append = false, preserveScroll = false, limit = 80 } = {}) {
  if (append && state.isLoadingConversations) return;
  if (append && !state.hasMoreConversations) return;
  const requestSeq = ++state.conversationRequestSeq;
  state.isLoadingConversations = true;
  const previousScrollTop = els.conversationList.scrollTop;
  if (append) renderConversations();
  const query = encodeURIComponent(els.conversationSearch.value || "");
  const hidden = state.conversationCategory === "hidden" ? "1" : "0";
  const unread = state.conversationCategory === "unread" ? "1" : "0";
  const cursor = append ? conversationCursor() : "";
  const pageLimit = clamp(Math.round(Number(limit) || 80), 80, 200);
  try {
    const payload = await api(`/api/conversations?limit=${pageLimit}&hidden=${hidden}&unread=${unread}&search=${query}${cursor}`);
    if (requestSeq !== state.conversationRequestSeq) return;
    if (append) {
      const existing = new Set(state.conversations.map((conversation) => conversation.id));
      state.conversations = [
        ...state.conversations,
        ...payload.conversations.filter((conversation) => !existing.has(conversation.id)),
      ];
    } else {
      state.conversations = payload.conversations;
      state.lastListRefreshAt = Date.now();
    }
    state.hasMoreConversations = payload.has_more;
  } finally {
    if (requestSeq === state.conversationRequestSeq) {
      state.isLoadingConversations = false;
      renderConversations();
      if (!append) {
        els.conversationList.scrollTop = preserveScroll ? previousScrollTop : 0;
      }
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
  state.lastThreadRefreshAt = Date.now();
  selectFromNumber(preferredReplyIdentity(state.currentConversation, state.messages));
  renderConversations();
  scrollActiveConversationIntoView();
  renderThreadHeader();
  renderMessages(state.messages, "bottom");
  scheduleStatusPoll();
  pollForChanges({ prime: true }).catch((error) => toast(error.message));
  if (state.bootstrap?.mark_read_on_open && !conversationIsRead(state.currentConversation)) {
    setCurrentConversationRead(true, { silent: true }).catch((error) => toast(error.message));
  }
}

async function openAdjacentConversation(offset) {
  if (!state.conversations.length) return;
  const currentIndex = state.conversations.findIndex((conversation) => conversation.id === state.currentConversationId);
  const baseIndex = currentIndex >= 0 ? currentIndex : 0;
  const nextIndex = clamp(baseIndex + offset, 0, state.conversations.length - 1);
  const next = state.conversations[nextIndex];
  if (next && next.id !== state.currentConversationId) {
    await openConversation(next.id);
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
    applyRuntimeSettings();
    renderBootstrap();
    await loadConversations({ append: false });
    const next = state.conversations[0];
    if (next) {
      await openConversation(next.id);
    } else {
      startNewConversation();
    }
    toast(archived ? t("conversation.archived") : t("conversation.unarchived"));
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
    applyRuntimeSettings();
    renderBootstrap();
    if (state.conversationCategory === "unread" && dealt) {
      state.conversations = state.conversations.filter((conversation) => conversation.id !== conversationId);
    }
    renderConversations();
    if (isCurrent) {
      renderThreadHeader();
    }
    if (!silent) {
      toast(dealt ? t("conversation.marked_read") : t("conversation.marked_unread"));
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
  state.uploadedMedia = [];
  clearRecipientSuggestions();
  state.draftMatchSeq += 1;
  els.messageText.value = "";
  els.mediaUrls.value = "";
  selectFromNumber(els.fromNumber.value || state.bootstrap?.default_identity);
  renderUploadedMedia();
  updateMessageCounter();
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
      toast(t("conversation.existing_group"));
      await openConversation(payload.conversation.id);
    }
  } catch (error) {
    toast(error.message);
  }
}

function addRecipient(raw, displayName = "") {
  const phone = normalizeDraftPhone(raw);
  if (!phone.startsWith("+") || phone.length < 8) {
    toast(t("recipient.needs_phone"));
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
  mediaUrls.push(...state.uploadedMedia.map((item) => item.url).filter(Boolean));
  const toNumbers = currentRecipients();
  if (!toNumbers.length) {
    toast(t("recipient.add_one"));
    return;
  }
  if (!text && !mediaUrls.length) {
    showComposerError(t("composer.requires_content"));
    toast(t("composer.requires_content"));
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
    state.uploadedMedia = [];
    renderUploadedMedia();
    updateMessageCounter();
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
    applyIdentityToLoadedState(payload.identity);
    renderBootstrap();
    renderThreadHeader();
    renderMessages(state.messages, "preserve");
    renderConversationsPreservingScroll();
    toast(t("common.saved"));
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
            >${escapeHtml(t("contacts.add"))}</button>
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
    toast(t("contact.enter_name"));
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
      mergeConversationIntoList(payload.conversation);
    }
    state.messages = state.messages.map((message) =>
      message.from_number === participant.phone_number ? { ...message, from_display: displayName } : message,
    );
    setContactNameEditor(false);
    state.bootstrap = await api("/api/bootstrap");
    applyRuntimeSettings();
    renderBootstrap();
    renderConversationsPreservingScroll();
    renderThreadHeader();
    renderMessages(state.messages, "preserve");
    searchContacts();
    toast(payload.synced ? t("contact.saved_synced") : t("contact.saved_local"));
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
  els.threadTitle.addEventListener("click", openContactRename);
  els.threadTitle.addEventListener("keydown", (event) => {
    if (!["Enter", " "].includes(event.key)) return;
    if (!currentDirectParticipant()) return;
    event.preventDefault();
    openContactRename();
  });
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
  els.messageText.addEventListener("input", () => {
    updateMessageCounter();
    showComposerError("");
  });
  els.mediaUrls.addEventListener("input", () => showComposerError(""));
  els.mediaFiles.addEventListener("change", () => uploadSelectedMedia(els.mediaFiles.files));
  els.uploadList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-remove-upload]");
    if (!button) return;
    state.uploadedMedia.splice(Number(button.dataset.removeUpload), 1);
    renderUploadedMedia();
  });
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
      clearAutoRefresh();
    } else {
      scheduleStatusPoll();
      scheduleAutoRefresh();
      refreshForegroundData().catch((error) => toast(error.message));
    }
  });
  window.addEventListener("focus", () => {
    refreshForegroundData().catch((error) => toast(error.message));
  });
  window.addEventListener("pageshow", (event) => {
    refreshForegroundData({ force: Boolean(event.persisted) }).catch((error) => toast(error.message));
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
  document.querySelector(".detail-rail").addEventListener("input", (event) => {
    const colorInput = event.target.closest(".identity-color");
    if (!colorInput) return;
    const swatch = colorInput.closest(".color-swatch");
    if (swatch) swatch.style.background = colorInput.value;
  });
  els.syncContactsButton.addEventListener("click", async () => {
    els.syncContactsButton.disabled = true;
    try {
      const payload = await api("/api/contacts/sync", { method: "POST", body: "{}" });
      toast(t("contacts.synced", { count: payload.synced.contacts }));
      state.bootstrap = await api("/api/bootstrap");
      applyRuntimeSettings();
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
  const initialConversationId = initialConversationIdFromLocation();
  replaceNavigationState("list");
  bindEvents();
  updateComposerOffset();
  updateMessageCounter();
  const savedCategory = localStorage.getItem("conversationCategory");
  state.conversationCategory = ["inbox", "unread", "hidden"].includes(savedCategory) ? savedCategory : "inbox";
  state.bootstrap = await api("/api/bootstrap");
  applyRuntimeSettings();
  const savedDetailsState = localStorage.getItem("detailsCollapsedDefaultHidden");
  const detailsDefault = state.bootstrap.details_collapsed_default ?? true;
  setDetailsCollapsed(savedDetailsState === null ? Boolean(detailsDefault) : savedDetailsState === "1");
  renderBootstrap();
  await loadConversations();
  if (initialConversationId) {
    await openConversation(initialConversationId, { updateHistory: false });
    clearInitialConversationUrl(initialConversationId);
  } else if (isDesktopLayout() && state.conversations[0]) {
    await openConversation(state.conversations[0].id);
  } else if (!state.conversations[0]) {
    startNewConversation();
  } else {
    setMobileThreadOpen(false);
  }
  pollForChanges({ prime: true }).catch((error) => toast(error.message));
  searchContacts();
}

init().catch((error) => toast(error.message));
