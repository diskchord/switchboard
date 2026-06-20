const state = {
  bootstrap: null,
  conversations: [],
  conversationCategory: "inbox",
  hasMoreConversations: true,
  isLoadingConversations: false,
  conversationRequestSeq: 0,
  openConversationSeq: 0,
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
  searchTimer: null,
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
  selectedConversationIds: new Set(),
  conversationPressTimer: null,
  conversationPressTargetId: null,
  conversationSwipe: null,
  suppressConversationClickUntil: 0,
  reactionTargetId: null,
  reactionPressTimer: null,
  reactionPressPointerId: null,
  reactionPressStartX: 0,
  reactionPressStartY: 0,
  reactionLongPressTriggered: false,
  sendPressTimer: null,
  sendHoldTriggered: false,
  pullRefresh: null,
  isRefreshingFromPull: false,
  layoutResizeObserver: null,
  audioContext: null,
  audioUnlocked: false,
  latestInboundSoundKey: "",
  receiveSoundPrimed: false,
  soundSettings: {
    sendEnabled: true,
    sendTone: "ascending",
    receiveMode: "auto",
    receiveTone: "chime",
    volume: 0.45,
  },
  twoFactor: null,
  twoFactorSetup: null,
  twoFactorBackupCodes: [],
  language: "en",
  hotkeysEnabled: true,
  hotkeys: {},
};

const els = {
  appShell: document.querySelector(".app-shell"),
  threadPane: document.querySelector(".thread-pane"),
  threadHeader: document.querySelector(".thread-header"),
  conversationList: document.querySelector("#conversationList"),
  conversationSearch: document.querySelector("#conversationSearch"),
  conversationSearchClear: document.querySelector("#conversationSearchClear"),
  pullRefreshIndicator: document.querySelector("#pullRefreshIndicator"),
  statStrip: document.querySelector("#statStrip"),
  categoryTabs: document.querySelectorAll(".category-tab"),
  themeToggle: document.querySelector("#themeToggle"),
  settingsButton: document.querySelector("#settingsButton"),
  settingsModal: document.querySelector("#settingsModal"),
  settingsForm: document.querySelector("#settingsForm"),
  settingsNav: document.querySelector("#settingsNav"),
  securitySettings: document.querySelector("#securitySettings"),
  settingsSections: document.querySelector("#settingsSections"),
  settingsClose: document.querySelector("#settingsClose"),
  settingsCancel: document.querySelector("#settingsCancel"),
  databaseDownloadButton: document.querySelector("#databaseDownloadButton"),
  logoutButton: document.querySelector("#logoutButton"),
  statsButton: document.querySelector("#statsButton"),
  statsModal: document.querySelector("#statsModal"),
  statsBody: document.querySelector("#statsBody"),
  statsPeriod: document.querySelector("#statsPeriod"),
  statsClose: document.querySelector("#statsClose"),
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
  contactNameModal: document.querySelector("#contactNameModal"),
  contactNameModalForm: document.querySelector("#contactNameModalForm"),
  contactNameModalInput: document.querySelector("#contactNameModalInput"),
  contactNameModalClose: document.querySelector("#contactNameModalClose"),
  contactNameModalCancel: document.querySelector("#contactNameModalCancel"),
  scheduleModal: document.querySelector("#scheduleModal"),
  scheduleForm: document.querySelector("#scheduleForm"),
  scheduleTime: document.querySelector("#scheduleTime"),
  scheduleClose: document.querySelector("#scheduleClose"),
  scheduleCancel: document.querySelector("#scheduleCancel"),
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
  mobilePanelButton: document.querySelector("#mobilePanelButton"),
  toggleDetailsButton: document.querySelector("#toggleDetailsButton"),
  detailRail: document.querySelector(".detail-rail"),
  mobileDetailCloseButton: document.querySelector("#mobileDetailCloseButton"),
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
  reactionMenu: document.querySelector("#reactionMenu"),
  columnResizers: document.querySelectorAll(".column-resizer"),
  selectionToolbar: document.querySelector("#selectionToolbar"),
  selectionCount: document.querySelector("#selectionCount"),
  bulkReadButton: document.querySelector("#bulkReadButton"),
  bulkUnreadButton: document.querySelector("#bulkUnreadButton"),
  bulkHideButton: document.querySelector("#bulkHideButton"),
  selectionCancelButton: document.querySelector("#selectionCancelButton"),
  toast: document.querySelector("#toast"),
};

const COLUMN_WIDTHS_KEY = "textingColumnWidths";
const THEME_KEY = "textingTheme";
const SCHEDULE_TIME_KEY = "textingScheduleTime";
const STATS_PERIOD_KEY = "textingStatsPeriod";
const PENDING_MESSAGE_STATUSES = new Set(["queued", "sending", "accepted", "sent", "finalized"]);
const FOREGROUND_STALE_MS = 20_000;
const MIN_AUTO_REFRESH_SECONDS = 5;
const SEND_HOLD_MS = 550;
const SEND_NOW_SYMBOL = "➤";
const SCHEDULE_SEND_SYMBOL = "◷";
const STATS_PERIOD_OPTIONS = [
  { value: "all", labelKey: "stats.period_all" },
  { value: "today", labelKey: "stats.period_today" },
  { value: "7d", labelKey: "stats.period_7d" },
  { value: "last_week", labelKey: "stats.period_last_week" },
  { value: "30d", labelKey: "stats.period_30d" },
  { value: "this_month", labelKey: "stats.period_this_month" },
  { value: "last_month", labelKey: "stats.period_last_month" },
  { value: "ytd", labelKey: "stats.period_ytd" },
  { value: "last_year", labelKey: "stats.period_last_year" },
];
const SOUND_TONES = new Set(["ascending", "chime", "pop", "bell"]);
const REACTION_INVISIBLE_PATTERN = /[\u200B-\u200F\u202A-\u202E\u2060\uFEFF]/g;
const REACTION_SPACING_PATTERN = /[\u00A0\u1680\u180E\u2000-\u200A\u202F\u205F\u3000]/g;
const REACTION_WORDS = new Map([
  ["liked", "👍"],
  ["loved", "❤️"],
  ["disliked", "👎"],
  ["laughed at", "😂"],
  ["emphasized", "‼️"],
  ["questioned", "❓"],
]);
const MESSAGE_REACTION_OPTIONS = [
  { action: "liked", icon: "👍", labelKey: "message.react_like" },
  { action: "loved", icon: "❤️", labelKey: "message.react_love" },
  { action: "laughed at", icon: "😂", labelKey: "message.react_laugh" },
  { action: "emphasized", icon: "‼️", labelKey: "message.react_emphasize" },
  { action: "questioned", icon: "❓", labelKey: "message.react_question" },
  { action: "disliked", icon: "👎", labelKey: "message.react_dislike" },
];
const REACTION_SYMBOLS = new Set(["👍", "👎", "❤️", "❤", "😂", "🤣", "😆", "‼️", "!!", "?", "❓"]);
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
  toggle_details: () => toggleDetailsPanel(),
  next_thread: () => openAdjacentConversation(1),
  previous_thread: () => openAdjacentConversation(-1),
};
const I18N = {
  en: {
    "app.title": "Switchboard",
    "settings.title": "Settings",
    "settings.description": "Changes are saved and written to .env when available.",
    "settings.close": "Close settings",
    "settings.empty": "No settings are available.",
    "settings.saved": "Settings saved.",
    "settings.download_database": "Download DB",
    "settings.sign_out": "Sign out",
    "security.title": "Security",
    "security.account": "Account",
    "security.account_help": "Change the sign-in name or set a new password.",
    "security.username": "Username",
    "security.new_password": "New password",
    "security.confirm_password": "Confirm password",
    "security.save_account": "Save account",
    "security.account_saved": "Account updated.",
    "security.2fa": "Two-factor authentication",
    "security.2fa_enabled": "Enabled",
    "security.2fa_disabled": "Off",
    "security.2fa_env": "Configured in .env",
    "security.current_password": "Current password",
    "security.current_password_placeholder": "Current password",
    "security.auth_code": "Authenticator or backup code",
    "security.auth_code_placeholder": "123456 or backup code",
    "security.start_setup": "Add 2FA",
    "security.verify_enable": "Verify and enable",
    "security.regenerate_backup": "New backup codes",
    "security.disable": "Disable 2FA",
    "security.setup_secret": "Authenticator secret",
    "security.setup_uri": "Authenticator URI",
    "security.scan_qr": "Scan this QR code",
    "security.manual_secret": "Manual setup code",
    "security.backup_codes": "Backup codes",
    "security.backup_codes_note": "Save these now. They will not be shown again.",
    "security.setup_help": "Enter this secret in your authenticator app, then type the current code.",
    "security.unavailable": "Set up app sign-in before enabling 2FA.",
    "security.enabled": "Two-factor authentication is enabled.",
    "security.disabled": "Two-factor authentication is off.",
    "security.env_note": "This is controlled by .env and cannot be disabled from Settings.",
    "security.enabled_toast": "2FA enabled.",
    "security.disabled_toast": "2FA disabled.",
    "security.backup_regenerated": "Backup codes regenerated.",
    "security.disable_confirm": "Disable two-factor authentication?",
    "security.copy": "Copy",
    "security.copied": "Copied.",
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
    "search.clear": "Clear search",
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
    "stats.title": "Stats",
    "stats.description": "Message and conversation totals.",
    "stats.close": "Close stats",
    "stats.loading": "Loading stats...",
    "stats.empty": "No data in this period.",
    "stats.period": "Period",
    "stats.period_all": "All time",
    "stats.period_today": "Today",
    "stats.period_7d": "Last 7 days",
    "stats.period_last_week": "Last week",
    "stats.period_30d": "Last 30 days",
    "stats.period_this_month": "This month",
    "stats.period_last_month": "Last month",
    "stats.period_ytd": "This year so far",
    "stats.period_last_year": "Last year",
    "stats.totals": "Totals",
    "stats.by_status": "By status",
    "stats.by_source": "By source",
    "stats.by_type": "By type",
    "stats.by_direction": "By direction",
    "stats.recent": "Recent days",
    "stats.timeline": "Activity",
    "stats.timeline_by": "{metric} by {bucket}",
    "stats.bucket_hour": "hour",
    "stats.bucket_day": "day",
    "stats.bucket_month": "month",
    "stats.inbox_conversations": "Inbox",
    "stats.hidden_conversations": "Hidden",
    "stats.unread_conversations": "Unread",
    "stats.inbound_messages": "Inbound",
    "stats.outbound_messages": "Outbound",
    "stats.voicemails": "Voicemails",
    "stats.failed_messages": "Failed",
    "stats.pending_messages": "Pending",
    "refresh.pull": "Pull to refresh",
    "refresh.release": "Release to refresh",
    "refresh.refreshing": "Refreshing...",
    "refresh.updated": "Updated.",
    "selection.actions": "Selection actions",
    "selection.count": "{count} selected",
    "selection.none": "Select at least one thread.",
    "selection.updated": "{count} updated.",
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
    "panel.close": "Close panel",
    "contact.name": "Name",
    "contact.rename": "Rename",
    "contact.name_placeholder": "Contact name",
    "contact.enter_name": "Enter a contact name.",
    "contact.saved_synced": "Saved to contacts.",
    "contact.saved_local": "Saved locally. Configure contact sync to publish changes.",
    "recipient.add": "Add recipient",
    "recipient.needs_phone": "Recipient needs a phone number.",
    "recipient.add_one": "Add a recipient.",
    "recipient.text_number": "New text to {number}",
    "messages.aria": "Messages",
    "messages.empty": "No messages yet.",
    "messages.load_older": "Load older ({count})",
    "message.reaction_preview": "Reacted {icon}",
    "message.reaction_title": "{name} reacted {icon}",
    "message.react": "React",
    "message.react_like": "Like",
    "message.react_love": "Love",
    "message.react_laugh": "Laugh",
    "message.react_emphasize": "Emphasize",
    "message.react_question": "Question",
    "message.react_dislike": "Dislike",
    "message.react_requires_text": "You can react to text messages.",
    "message.react_failed": "Could not send reaction.",
    "message.send_failed": "Send failed",
    "message.delivery_unconfirmed": "Delivery unconfirmed",
    "message.voicemail": "Voicemail",
    "composer.from": "From",
    "composer.message": "Message",
    "composer.media_urls": "Media URLs",
    "composer.upload": "Upload media",
    "composer.send": "Send",
    "composer.requires_content": "Message text or attachment is required.",
    "schedule.title": "Schedule send",
    "schedule.send_at": "Send at",
    "schedule.queue": "Queue",
    "schedule.queued": "Message queued.",
    "schedule.queued_for": "Queued for {time}",
    "schedule.cancel": "Cancel",
    "schedule.cancelled": "Scheduled message canceled.",
    "schedule.choose_time": "Choose a send time.",
    "schedule.future_time": "Choose a future time.",
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
    "identities.autoreply": "Vacation reply",
    "identities.autoreply_enabled": "Enable",
    "identities.autoreply_message": "Auto-reply message",
    "identities.autoreply_placeholder": "Thanks for reaching out. I'm away right now and will reply when I can.",
    "identities.autoreply_cooldown": "Cooldown hours",
    "identities.autoreply_on": "On",
    "identities.autoreply_off": "Off",
    "identities.discard_confirm": "Discard unsaved number changes?",
    "voice.heading": "Calls",
    "voice.forwarding": "Call forwarding",
    "voice.forward_calls": "Forward incoming calls",
    "voice.forward_to": "Forward to",
    "voice.forward_timeout": "Ring seconds",
    "voice.voicemail": "Record voicemail",
    "voice.voicemail_heading": "Voicemail",
    "voice.greeting": "Voicemail greeting",
    "voice.greeting_recording": "Greeting recording",
    "voice.upload_greeting": "Upload recording",
    "voice.greeting_uploaded": "Greeting recording uploaded.",
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
    "status.scheduled": "Scheduled",
    "status.sending": "Sending",
    "status.sent": "Sent",
    "status.delivered": "Delivered",
    "status.received": "Received",
    "status.imported": "Imported",
  },
  es: {
    "app.title": "Switchboard",
    "settings.title": "Ajustes",
    "settings.description": "Los cambios se guardan y se escriben en .env cuando es posible.",
    "settings.close": "Cerrar ajustes",
    "settings.empty": "No hay ajustes disponibles.",
    "settings.saved": "Ajustes guardados.",
    "settings.download_database": "Descargar DB",
    "settings.sign_out": "Cerrar sesión",
    "security.title": "Seguridad",
    "security.account": "Cuenta",
    "security.account_help": "Cambia el usuario de inicio de sesión o define una contraseña nueva.",
    "security.username": "Usuario",
    "security.new_password": "Contraseña nueva",
    "security.confirm_password": "Confirmar contraseña",
    "security.save_account": "Guardar cuenta",
    "security.account_saved": "Cuenta actualizada.",
    "security.2fa": "Autenticación de dos factores",
    "security.2fa_enabled": "Activa",
    "security.2fa_disabled": "Inactiva",
    "security.2fa_env": "Configurada en .env",
    "security.current_password": "Contraseña actual",
    "security.current_password_placeholder": "Contraseña actual",
    "security.auth_code": "Código o código de respaldo",
    "security.auth_code_placeholder": "123456 o respaldo",
    "security.start_setup": "Agregar 2FA",
    "security.verify_enable": "Verificar y activar",
    "security.regenerate_backup": "Nuevos códigos",
    "security.disable": "Desactivar 2FA",
    "security.setup_secret": "Secreto del autenticador",
    "security.setup_uri": "URI del autenticador",
    "security.scan_qr": "Escanea este código QR",
    "security.manual_secret": "Código manual",
    "security.backup_codes": "Códigos de respaldo",
    "security.backup_codes_note": "Guárdalos ahora. No se mostrarán otra vez.",
    "security.setup_help": "Ingresa este secreto en tu app autenticadora y escribe el código actual.",
    "security.unavailable": "Configura el inicio de sesión antes de activar 2FA.",
    "security.enabled": "La autenticación de dos factores está activa.",
    "security.disabled": "La autenticación de dos factores está inactiva.",
    "security.env_note": "Esto está controlado por .env y no se puede desactivar desde Ajustes.",
    "security.enabled_toast": "2FA activada.",
    "security.disabled_toast": "2FA desactivada.",
    "security.backup_regenerated": "Códigos de respaldo regenerados.",
    "security.disable_confirm": "¿Desactivar la autenticación de dos factores?",
    "security.copy": "Copiar",
    "security.copied": "Copiado.",
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
    "search.clear": "Borrar búsqueda",
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
    "stats.title": "Estadísticas",
    "stats.description": "Totales de mensajes y conversaciones.",
    "stats.close": "Cerrar estadísticas",
    "stats.loading": "Cargando estadísticas...",
    "stats.empty": "No hay datos en este período.",
    "stats.period": "Período",
    "stats.period_all": "Todo el tiempo",
    "stats.period_today": "Hoy",
    "stats.period_7d": "Últimos 7 días",
    "stats.period_last_week": "Semana pasada",
    "stats.period_30d": "Últimos 30 días",
    "stats.period_this_month": "Este mes",
    "stats.period_last_month": "Mes pasado",
    "stats.period_ytd": "Este año hasta ahora",
    "stats.period_last_year": "Año pasado",
    "stats.totals": "Totales",
    "stats.by_status": "Por estado",
    "stats.by_source": "Por origen",
    "stats.by_type": "Por tipo",
    "stats.by_direction": "Por dirección",
    "stats.recent": "Días recientes",
    "stats.timeline": "Actividad",
    "stats.timeline_by": "{metric} por {bucket}",
    "stats.bucket_hour": "hora",
    "stats.bucket_day": "día",
    "stats.bucket_month": "mes",
    "stats.inbox_conversations": "Entrada",
    "stats.hidden_conversations": "Ocultos",
    "stats.unread_conversations": "No leídos",
    "stats.inbound_messages": "Entrantes",
    "stats.outbound_messages": "Salientes",
    "stats.voicemails": "Buzones de voz",
    "stats.failed_messages": "Fallidos",
    "stats.pending_messages": "Pendientes",
    "refresh.pull": "Tira para actualizar",
    "refresh.release": "Suelta para actualizar",
    "refresh.refreshing": "Actualizando...",
    "refresh.updated": "Actualizado.",
    "selection.actions": "Acciones de selección",
    "selection.count": "{count} seleccionados",
    "selection.none": "Selecciona al menos un hilo.",
    "selection.updated": "{count} actualizados.",
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
    "panel.close": "Cerrar panel",
    "contact.name": "Nombre",
    "contact.rename": "Renombrar",
    "contact.name_placeholder": "Nombre del contacto",
    "contact.enter_name": "Escribe un nombre de contacto.",
    "contact.saved_synced": "Guardado en contactos.",
    "contact.saved_local": "Guardado localmente. Configura sincronización de contactos para publicarlo.",
    "recipient.add": "Agregar destinatario",
    "recipient.needs_phone": "El destinatario necesita un número de teléfono.",
    "recipient.add_one": "Agrega un destinatario.",
    "recipient.text_number": "Nuevo mensaje a {number}",
    "messages.aria": "Mensajes",
    "messages.empty": "Aún no hay mensajes.",
    "messages.load_older": "Cargar anteriores ({count})",
    "message.reaction_preview": "Reaccionó {icon}",
    "message.reaction_title": "{name} reaccionó {icon}",
    "message.react": "Reaccionar",
    "message.react_like": "Me gusta",
    "message.react_love": "Me encanta",
    "message.react_laugh": "Risa",
    "message.react_emphasize": "Énfasis",
    "message.react_question": "Pregunta",
    "message.react_dislike": "No me gusta",
    "message.react_requires_text": "Puedes reaccionar a mensajes de texto.",
    "message.react_failed": "No se pudo enviar la reacción.",
    "message.send_failed": "Envío fallido",
    "message.delivery_unconfirmed": "Entrega sin confirmar",
    "message.voicemail": "Buzón de voz",
    "composer.from": "De",
    "composer.message": "Mensaje",
    "composer.media_urls": "URLs de media",
    "composer.upload": "Subir media",
    "composer.send": "Enviar",
    "composer.requires_content": "Se requiere texto o adjunto.",
    "schedule.title": "Programar envío",
    "schedule.send_at": "Enviar a las",
    "schedule.queue": "Encolar",
    "schedule.queued": "Mensaje encolado.",
    "schedule.queued_for": "En cola para {time}",
    "schedule.cancel": "Cancelar",
    "schedule.cancelled": "Mensaje programado cancelado.",
    "schedule.choose_time": "Elige una hora de envío.",
    "schedule.future_time": "Elige una hora futura.",
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
    "identities.autoreply": "Respuesta de ausencia",
    "identities.autoreply_enabled": "Activar",
    "identities.autoreply_message": "Mensaje automático",
    "identities.autoreply_placeholder": "Gracias por escribir. Estoy ausente ahora y responderé cuando pueda.",
    "identities.autoreply_cooldown": "Horas de espera",
    "identities.autoreply_on": "Activa",
    "identities.autoreply_off": "Inactiva",
    "identities.discard_confirm": "¿Descartar los cambios no guardados del número?",
    "voice.heading": "Llamadas",
    "voice.forwarding": "Reenvío de llamadas",
    "voice.forward_calls": "Reenviar llamadas entrantes",
    "voice.forward_to": "Reenviar a",
    "voice.forward_timeout": "Segundos de timbre",
    "voice.voicemail": "Grabar buzón de voz",
    "voice.voicemail_heading": "Buzón de voz",
    "voice.greeting": "Saludo del buzón",
    "voice.greeting_recording": "Grabación del saludo",
    "voice.upload_greeting": "Subir grabación",
    "voice.greeting_uploaded": "Grabación del saludo subida.",
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
    "status.scheduled": "Programado",
    "status.sending": "Enviando",
    "status.sent": "Enviado",
    "status.delivered": "Entregado",
    "status.received": "Recibido",
    "status.imported": "Importado",
  },
  fr: {
    "app.title": "Switchboard",
    "settings.title": "Réglages",
    "settings.description": "Les changements sont enregistrés et écrits dans .env quand c'est possible.",
    "settings.close": "Fermer les réglages",
    "settings.empty": "Aucun réglage disponible.",
    "settings.saved": "Réglages enregistrés.",
    "settings.download_database": "Télécharger DB",
    "settings.sign_out": "Se déconnecter",
    "security.title": "Sécurité",
    "security.account": "Compte",
    "security.account_help": "Modifiez le nom de connexion ou définissez un nouveau mot de passe.",
    "security.username": "Nom d'utilisateur",
    "security.new_password": "Nouveau mot de passe",
    "security.confirm_password": "Confirmer le mot de passe",
    "security.save_account": "Enregistrer le compte",
    "security.account_saved": "Compte mis à jour.",
    "security.2fa": "Authentification à deux facteurs",
    "security.2fa_enabled": "Active",
    "security.2fa_disabled": "Inactive",
    "security.2fa_env": "Configurée dans .env",
    "security.current_password": "Mot de passe actuel",
    "security.current_password_placeholder": "Mot de passe actuel",
    "security.auth_code": "Code ou code de secours",
    "security.auth_code_placeholder": "123456 ou code de secours",
    "security.start_setup": "Ajouter 2FA",
    "security.verify_enable": "Vérifier et activer",
    "security.regenerate_backup": "Nouveaux codes",
    "security.disable": "Désactiver 2FA",
    "security.setup_secret": "Secret d'authentification",
    "security.setup_uri": "URI d'authentification",
    "security.scan_qr": "Scannez ce code QR",
    "security.manual_secret": "Code de configuration manuel",
    "security.backup_codes": "Codes de secours",
    "security.backup_codes_note": "Enregistrez-les maintenant. Ils ne seront plus affichés.",
    "security.setup_help": "Entrez ce secret dans votre app d'authentification, puis saisissez le code actuel.",
    "security.unavailable": "Configurez la connexion avant d'activer 2FA.",
    "security.enabled": "L'authentification à deux facteurs est active.",
    "security.disabled": "L'authentification à deux facteurs est inactive.",
    "security.env_note": "Ceci est contrôlé par .env et ne peut pas être désactivé dans Réglages.",
    "security.enabled_toast": "2FA activée.",
    "security.disabled_toast": "2FA désactivée.",
    "security.backup_regenerated": "Codes de secours régénérés.",
    "security.disable_confirm": "Désactiver l'authentification à deux facteurs ?",
    "security.copy": "Copier",
    "security.copied": "Copié.",
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
    "search.clear": "Effacer la recherche",
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
    "stats.title": "Stats",
    "stats.description": "Totaux des messages et conversations.",
    "stats.close": "Fermer les stats",
    "stats.loading": "Chargement des stats...",
    "stats.empty": "Aucune donnée pour cette période.",
    "stats.period": "Période",
    "stats.period_all": "Tout le temps",
    "stats.period_today": "Aujourd'hui",
    "stats.period_7d": "7 derniers jours",
    "stats.period_last_week": "Semaine dernière",
    "stats.period_30d": "30 derniers jours",
    "stats.period_this_month": "Ce mois-ci",
    "stats.period_last_month": "Mois dernier",
    "stats.period_ytd": "Cette année à ce jour",
    "stats.period_last_year": "Année dernière",
    "stats.totals": "Totaux",
    "stats.by_status": "Par statut",
    "stats.by_source": "Par source",
    "stats.by_type": "Par type",
    "stats.by_direction": "Par direction",
    "stats.recent": "Jours récents",
    "stats.timeline": "Activité",
    "stats.timeline_by": "{metric} par {bucket}",
    "stats.bucket_hour": "heure",
    "stats.bucket_day": "jour",
    "stats.bucket_month": "mois",
    "stats.inbox_conversations": "Boîte",
    "stats.hidden_conversations": "Masqués",
    "stats.unread_conversations": "Non lus",
    "stats.inbound_messages": "Entrants",
    "stats.outbound_messages": "Sortants",
    "stats.voicemails": "Messages vocaux",
    "stats.failed_messages": "Échecs",
    "stats.pending_messages": "En attente",
    "refresh.pull": "Tirez pour actualiser",
    "refresh.release": "Relâchez pour actualiser",
    "refresh.refreshing": "Actualisation...",
    "refresh.updated": "Actualisé.",
    "selection.actions": "Actions de sélection",
    "selection.count": "{count} sélectionnés",
    "selection.none": "Sélectionnez au moins un fil.",
    "selection.updated": "{count} mis à jour.",
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
    "panel.close": "Fermer le panneau",
    "contact.name": "Nom",
    "contact.rename": "Renommer",
    "contact.name_placeholder": "Nom du contact",
    "contact.enter_name": "Entrez un nom de contact.",
    "contact.saved_synced": "Enregistré dans les contacts.",
    "contact.saved_local": "Enregistré localement. Configurez la synchronisation pour publier les changements.",
    "recipient.add": "Ajouter un destinataire",
    "recipient.needs_phone": "Le destinataire doit avoir un numéro de téléphone.",
    "recipient.add_one": "Ajoutez un destinataire.",
    "recipient.text_number": "Nouveau message à {number}",
    "messages.aria": "Messages",
    "messages.empty": "Aucun message pour le moment.",
    "messages.load_older": "Charger les anciens ({count})",
    "message.reaction_preview": "Réaction {icon}",
    "message.reaction_title": "{name} a réagi {icon}",
    "message.react": "Réagir",
    "message.react_like": "J'aime",
    "message.react_love": "J'adore",
    "message.react_laugh": "Rire",
    "message.react_emphasize": "Souligner",
    "message.react_question": "Question",
    "message.react_dislike": "Je n'aime pas",
    "message.react_requires_text": "Vous pouvez réagir aux messages texte.",
    "message.react_failed": "Impossible d'envoyer la réaction.",
    "message.send_failed": "Envoi échoué",
    "message.delivery_unconfirmed": "Livraison non confirmée",
    "message.voicemail": "Messagerie vocale",
    "composer.from": "De",
    "composer.message": "Message",
    "composer.media_urls": "URL de médias",
    "composer.upload": "Téléverser un média",
    "composer.send": "Envoyer",
    "composer.requires_content": "Un message ou une pièce jointe est requis.",
    "schedule.title": "Programmer l'envoi",
    "schedule.send_at": "Envoyer à",
    "schedule.queue": "Mettre en file",
    "schedule.queued": "Message mis en file.",
    "schedule.queued_for": "En file pour {time}",
    "schedule.cancel": "Annuler",
    "schedule.cancelled": "Message programmé annulé.",
    "schedule.choose_time": "Choisissez une heure d'envoi.",
    "schedule.future_time": "Choisissez une heure future.",
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
    "identities.autoreply": "Réponse d'absence",
    "identities.autoreply_enabled": "Activer",
    "identities.autoreply_message": "Message automatique",
    "identities.autoreply_placeholder": "Merci pour votre message. Je suis absent et répondrai dès que possible.",
    "identities.autoreply_cooldown": "Heures d'attente",
    "identities.autoreply_on": "Active",
    "identities.autoreply_off": "Inactive",
    "identities.discard_confirm": "Ignorer les modifications non enregistrées du numéro ?",
    "voice.heading": "Appels",
    "voice.forwarding": "Transfert d'appel",
    "voice.forward_calls": "Transférer les appels entrants",
    "voice.forward_to": "Transférer vers",
    "voice.forward_timeout": "Secondes de sonnerie",
    "voice.voicemail": "Enregistrer un message vocal",
    "voice.voicemail_heading": "Messagerie vocale",
    "voice.greeting": "Annonce du répondeur",
    "voice.greeting_recording": "Enregistrement de l'annonce",
    "voice.upload_greeting": "Téléverser l'enregistrement",
    "voice.greeting_uploaded": "Enregistrement de l'annonce téléversé.",
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
    "status.scheduled": "Programmé",
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

function updateDetailsControls() {
  const overlayMode = isDetailsOverlayLayout();
  const overlayOpen = document.body.classList.contains("details-overlay-open");
  const collapsed = document.body.classList.contains("details-collapsed");
  const visible = overlayMode ? overlayOpen : !collapsed;
  els.toggleDetailsButton.textContent = visible ? t("panel.collapse_label") : t("panel.expand_label");
  els.toggleDetailsButton.setAttribute("aria-pressed", visible ? "true" : "false");
  els.toggleDetailsButton.setAttribute("aria-label", visible ? t("panel.hide") : t("panel.show"));
  els.toggleDetailsButton.title = visible ? t("panel.hide") : t("panel.show");
  if (els.mobilePanelButton) {
    els.mobilePanelButton.setAttribute("aria-pressed", visible ? "true" : "false");
    els.mobilePanelButton.setAttribute("aria-label", visible ? t("panel.hide") : t("panel.show"));
    els.mobilePanelButton.title = visible ? t("panel.hide") : t("panel.show");
  }
  if (els.mobileDetailCloseButton) {
    els.mobileDetailCloseButton.title = t("panel.close");
    els.mobileDetailCloseButton.setAttribute("aria-label", t("panel.close"));
  }
}

function setDetailsCollapsed(collapsed) {
  document.body.classList.toggle("details-collapsed", collapsed);
  localStorage.setItem("detailsCollapsedDefaultHidden", collapsed ? "1" : "0");
  updateDetailsControls();
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

function isDetailsOverlayLayout() {
  return window.matchMedia("(max-width: 1120px)").matches;
}

function isDetailsOverlayOpen() {
  return document.body.classList.contains("details-overlay-open");
}

function setDetailsOverlayOpen(open, { restoreFocus = false } = {}) {
  const shouldOpen = Boolean(open) && isDetailsOverlayLayout();
  document.body.classList.toggle("details-overlay-open", shouldOpen);
  if (els.detailRail) {
    if (shouldOpen) {
      els.detailRail.setAttribute("role", "dialog");
      els.detailRail.setAttribute("aria-modal", "true");
      els.detailRail.setAttribute("aria-label", t("panel.label"));
    } else {
      els.detailRail.removeAttribute("role");
      els.detailRail.removeAttribute("aria-modal");
      els.detailRail.removeAttribute("aria-label");
    }
  }
  updateDetailsControls();
  syncNativePullRefreshEnabled();
  if (shouldOpen) {
    requestAnimationFrame(() => els.mobileDetailCloseButton?.focus());
  } else if (restoreFocus) {
    (isDetailsOverlayLayout() ? els.mobilePanelButton : els.toggleDetailsButton)?.focus();
  }
}

function closeDetailsOverlay(options = {}) {
  if (!isDetailsOverlayOpen()) return false;
  setDetailsOverlayOpen(false, options);
  return true;
}

function toggleDetailsPanel() {
  if (isDetailsOverlayLayout()) {
    setDetailsOverlayOpen(!isDetailsOverlayOpen(), { restoreFocus: true });
    return;
  }
  setDetailsCollapsed(!document.body.classList.contains("details-collapsed"));
}

function syncDetailsLayout() {
  if (!isDetailsOverlayLayout() && isDetailsOverlayOpen()) {
    setDetailsOverlayOpen(false);
    return;
  }
  updateDetailsControls();
  syncNativePullRefreshEnabled();
}

function setMobileThreadOpen(open) {
  document.body.classList.toggle("mobile-thread-open", Boolean(open));
  requestAnimationFrame(updateComposerOffset);
  syncNativePullRefreshEnabled();
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
  closeDetailsOverlay();
  setMobileThreadOpen(false);
  if (!fromHistory && !isDesktopLayout() && history.state?.textingApp === true && history.state.view === "thread") {
    history.back();
  }
}

function handleNavigationPop(event) {
  if (isDesktopLayout()) return;
  if (els.conversationSearch?.value && clearConversationSearch()) {
    replaceNavigationState("list");
    return;
  }
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
  if (els.conversationSearch?.value && clearConversationSearch()) {
    return true;
  }
  if (!els.statsModal.classList.contains("hidden")) {
    closeStats();
    return true;
  }
  if (state.selectedConversationIds.size) {
    clearConversationSelection();
    return true;
  }
  if (closeDetailsOverlay()) return true;
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
  const composerHeight = Math.ceil(els.composer.getBoundingClientRect().height || 0);
  const headerHeight = Math.ceil(els.threadHeader?.getBoundingClientRect().height || 0);
  const shellRect = els.appShell?.getBoundingClientRect();
  const messagesRect = els.messages?.getBoundingClientRect();
  if (composerHeight > 0) {
    els.threadPane.style.setProperty("--mobile-composer-height", `${composerHeight}px`);
    els.appShell?.style.setProperty("--thread-composer-height", `${composerHeight}px`);
  }
  if (headerHeight > 0) {
    els.appShell?.style.setProperty("--thread-header-height", `${headerHeight}px`);
  }
  if (shellRect && messagesRect) {
    const messagesTop = Math.max(0, Math.ceil(messagesRect.top - shellRect.top));
    const messagesBottom = Math.max(0, Math.ceil(shellRect.bottom - messagesRect.bottom));
    els.appShell?.style.setProperty("--thread-messages-top", `${messagesTop}px`);
    els.appShell?.style.setProperty("--thread-messages-bottom", `${messagesBottom}px`);
  }
}

function isDetailColumnVisible() {
  const detailRail = document.querySelector(".detail-rail");
  return !isDetailsOverlayLayout() && detailRail && getComputedStyle(detailRail).display !== "none";
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
    if (element.dataset.i18nDynamic === "true" || element === els.threadKind || element === els.threadTitle) return;
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
  renderStatsPeriodOptions();
}

function restoreThreadHeaderAfterStaticTranslations() {
  if (state.currentConversation || els.threadPane.classList.contains("recipients-visible")) {
    renderThreadHeader();
    return;
  }
  if (state.currentConversationId) {
    const conversation = state.conversations.find((item) => item.id === state.currentConversationId);
    if (conversation) {
      els.threadKind.textContent = conversation.kind === "group" ? t("thread.group") : t("thread.direct");
      els.threadTitle.textContent = conversation.title || t("thread.conversation");
    }
    return;
  }
  els.threadKind.textContent = t("thread.conversation");
  els.threadTitle.textContent = t("thread.select");
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

function settingBool(key, fallback = false) {
  const value = String(bootstrapSettingValue(key, fallback ? "1" : "0")).trim().toLowerCase();
  return ["1", "true", "yes", "on"].includes(value);
}

function configureSounds() {
  const sendTone = String(bootstrapSettingValue("sounds.send_tone", "ascending")).trim().toLowerCase();
  const receiveTone = String(bootstrapSettingValue("sounds.receive_tone", "chime")).trim().toLowerCase();
  const receiveMode = String(bootstrapSettingValue("sounds.receive_mode", "auto")).trim().toLowerCase();
  const rawVolume = Number(bootstrapSettingValue("sounds.volume", "45"));
  state.soundSettings = {
    sendEnabled: settingBool("sounds.send_enabled", true),
    sendTone: SOUND_TONES.has(sendTone) ? sendTone : "ascending",
    receiveMode: ["auto", "on", "off"].includes(receiveMode) ? receiveMode : "auto",
    receiveTone: SOUND_TONES.has(receiveTone) ? receiveTone : "chime",
    volume: clamp(Number.isFinite(rawVolume) ? rawVolume : 45, 0, 100) / 100,
  };
}

function configureComposerDisplay() {
  document.body.classList.toggle("composer-counter-hidden", !settingBool("behavior.show_composer_counter", true));
}

function ntfyNotificationsActive() {
  return settingBool("notifications.ntfy_enabled", false) && Boolean(String(bootstrapSettingValue("notifications.ntfy_endpoint", "")).trim());
}

function receiveSoundEnabled() {
  const mode = state.soundSettings.receiveMode;
  if (mode === "on") return true;
  if (mode === "off") return false;
  return !ntfyNotificationsActive();
}

function audioContext() {
  const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextCtor) return null;
  if (!state.audioContext) {
    state.audioContext = new AudioContextCtor();
  }
  return state.audioContext;
}

function unlockAudio() {
  const context = audioContext();
  if (!context) return;
  context.resume?.().catch(() => {});
  state.audioUnlocked = true;
}

function toneSequence(name) {
  if (name === "chime") {
    return [
      { frequency: 659.25, duration: 0.08, gain: 0.38 },
      { frequency: 987.77, duration: 0.15, gain: 0.26, gap: 0.025 },
    ];
  }
  if (name === "pop") {
    return [
      { frequency: 349.23, duration: 0.045, gain: 0.36 },
      { frequency: 523.25, duration: 0.07, gain: 0.26, gap: 0.018 },
    ];
  }
  if (name === "bell") {
    return [
      { frequency: 783.99, duration: 0.1, gain: 0.32 },
      { frequency: 1174.66, duration: 0.17, gain: 0.22, gap: 0.035 },
    ];
  }
  return [
    { frequency: 440, duration: 0.055, gain: 0.34 },
    { frequency: 554.37, duration: 0.055, gain: 0.3, gap: 0.018 },
    { frequency: 659.25, duration: 0.085, gain: 0.25, gap: 0.018 },
  ];
}

function playTone(name) {
  const context = audioContext();
  if (!context) return;
  context.resume?.().catch(() => {});
  const volume = clamp(state.soundSettings.volume || 0, 0, 1);
  if (volume <= 0) return;
  let cursor = context.currentTime + 0.015;
  for (const note of toneSequence(name)) {
    cursor += note.gap || 0;
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(note.frequency, cursor);
    gain.gain.setValueAtTime(0.0001, cursor);
    gain.gain.linearRampToValueAtTime(volume * note.gain, cursor + 0.012);
    gain.gain.exponentialRampToValueAtTime(0.0001, cursor + note.duration);
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start(cursor);
    oscillator.stop(cursor + note.duration + 0.02);
    cursor += note.duration;
  }
}

function playMessageSound(kind) {
  if (kind === "send") {
    if (!state.soundSettings.sendEnabled) return;
    playTone(state.soundSettings.sendTone);
    return;
  }
  if (kind === "receive" && receiveSoundEnabled()) {
    playTone(state.soundSettings.receiveTone);
  }
}

function applyRuntimeSettings() {
  state.language = resolveLanguage(bootstrapSettingValue("ui.language", "auto"));
  configureHotkeys();
  configureAutoRefresh();
  configureSounds();
  configureComposerDisplay();
  applyStaticTranslations();
  restoreThreadHeaderAfterStaticTranslations();
  applyTheme(document.documentElement.dataset.theme || "light", { persist: false });
  updateDetailsControls();
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
    if (response.status === 401) {
      window.location.href = `/login?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
    }
    if (response.status === 503 && String(payload.error || "").includes("sign-in is not configured")) {
      window.location.href = "/login?setup=1";
    }
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

function settingsAnchor(name) {
  return `settings-${String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "section"}`;
}

function renderSettingsNav(sections) {
  if (!els.settingsNav) return;
  const items = [{ name: t("security.title"), anchor: "securitySettings" }].concat(
    sections.map((section) => ({ name: section.name, anchor: settingsAnchor(section.name) })),
  );
  els.settingsNav.innerHTML = items
    .map(
      (item, index) => `
        <button class="settings-nav-button ${index === 0 ? "active" : ""}" type="button" data-settings-anchor="${escapeHtml(item.anchor)}">
          ${escapeHtml(item.name)}
        </button>`,
    )
    .join("");
}

function backupCodeList(codes = []) {
  if (!codes.length) return "";
  return `
    <div class="two-factor-codes" aria-label="${escapeHtml(t("security.backup_codes"))}">
      ${codes.map((code) => `<code>${escapeHtml(code)}</code>`).join("")}
    </div>`;
}

function renderTwoFactorSetup() {
  const setup = state.twoFactorSetup;
  if (!setup) return "";
  const qrImage = setup.qr_svg
    ? `<img class="two-factor-qr" src="${escapeHtml(setup.qr_svg)}" alt="${escapeHtml(t("security.scan_qr"))}" />`
    : `<div class="two-factor-qr two-factor-qr-empty">${escapeHtml(t("security.manual_secret"))}</div>`;
  return `
    <div class="two-factor-setup">
      <div class="two-factor-setup-hero">
        ${qrImage}
        <div class="two-factor-setup-copy">
          <strong>${escapeHtml(t("security.scan_qr"))}</strong>
          <p class="setting-help">${escapeHtml(t("security.setup_help"))}</p>
          <div class="two-factor-secret-row">
            <span>
              <strong>${escapeHtml(t("security.manual_secret"))}</strong>
              <code>${escapeHtml(setup.secret || "")}</code>
            </span>
            <button class="small-button" type="button" data-copy-two-factor="${escapeHtml(setup.secret || "")}">${escapeHtml(t("security.copy"))}</button>
          </div>
        </div>
      </div>
      <div class="two-factor-backups">
        <strong>${escapeHtml(t("security.backup_codes"))}</strong>
        <p class="setting-help">${escapeHtml(t("security.backup_codes_note"))}</p>
        ${backupCodeList(setup.backup_codes || [])}
      </div>
      <label class="two-factor-inline-field">
        <span>${escapeHtml(t("security.auth_code"))}</span>
        <input class="two-factor-setup-code" type="text" inputmode="numeric" autocomplete="one-time-code" placeholder="${escapeHtml(t("security.auth_code_placeholder"))}" />
      </label>
      <div class="two-factor-actions">
        <button class="small-button done-button" type="button" data-two-factor-action="enable">${escapeHtml(t("security.verify_enable"))}</button>
        <button class="small-button" type="button" data-two-factor-action="cancel-setup">${escapeHtml(t("common.cancel"))}</button>
      </div>
    </div>`;
}

function renderAccountSettings(status = state.twoFactor) {
  const username = status?.username || "";
  const disabled = status?.auth_disabled ? "disabled" : "";
  return `
    <div class="setting-field account-card">
      <span>
        <strong>${escapeHtml(t("security.account"))}</strong>
        <small>${escapeHtml(t("security.account_help"))}</small>
      </span>
      <div class="account-settings-grid">
        <label class="two-factor-inline-field">
          <span>${escapeHtml(t("security.username"))}</span>
          <input class="account-username" type="text" autocomplete="username" value="${escapeHtml(username)}" ${disabled} />
        </label>
        <label class="two-factor-inline-field">
          <span>${escapeHtml(t("security.current_password"))}</span>
          <input class="account-current-password" type="password" autocomplete="current-password" ${disabled} />
        </label>
        <label class="two-factor-inline-field">
          <span>${escapeHtml(t("security.new_password"))}</span>
          <input class="account-new-password" type="password" autocomplete="new-password" ${disabled} />
        </label>
        <label class="two-factor-inline-field">
          <span>${escapeHtml(t("security.confirm_password"))}</span>
          <input class="account-confirm-password" type="password" autocomplete="new-password" ${disabled} />
        </label>
      </div>
      <div class="two-factor-actions">
        <button class="small-button done-button" type="button" data-account-action="save" ${disabled}>${escapeHtml(t("security.save_account"))}</button>
      </div>
    </div>`;
}

function renderTwoFactorBackupCodes() {
  if (!state.twoFactorBackupCodes.length) return "";
  return `
    <div class="two-factor-backups two-factor-backups-new">
      <strong>${escapeHtml(t("security.backup_codes"))}</strong>
      <p class="setting-help">${escapeHtml(t("security.backup_codes_note"))}</p>
      ${backupCodeList(state.twoFactorBackupCodes)}
    </div>`;
}

function renderTwoFactorSettings(status = state.twoFactor) {
  if (!els.securitySettings) return;
  const enabled = Boolean(status?.enabled);
  const available = Boolean(status?.available);
  const envManaged = Boolean(status?.env_managed);
  const canDisable = Boolean(status?.can_disable);
  const sourceLabel = envManaged ? t("security.2fa_env") : enabled ? t("security.2fa_enabled") : t("security.2fa_disabled");
  const statusText = enabled ? t("security.enabled") : t("security.disabled");
  const envNote = envManaged ? `<p class="setting-help">${escapeHtml(t("security.env_note"))}</p>` : "";
  const passwordField = `
    <label class="two-factor-inline-field">
      <span>${escapeHtml(t("security.current_password"))}</span>
      <input class="two-factor-current-password" type="password" autocomplete="current-password" placeholder="${escapeHtml(t("security.current_password_placeholder"))}" />
    </label>`;
  const codeField = `
    <label class="two-factor-inline-field">
      <span>${escapeHtml(t("security.auth_code"))}</span>
      <input class="two-factor-current-code" type="text" autocomplete="one-time-code" placeholder="${escapeHtml(t("security.auth_code_placeholder"))}" />
    </label>`;

  let body = "";
  if (!available) {
    body = `<p class="setting-help">${escapeHtml(t("security.unavailable"))}</p>`;
  } else if (state.twoFactorSetup) {
    body = renderTwoFactorSetup();
  } else if (enabled) {
    body = `
      <p class="setting-help">${escapeHtml(statusText)}</p>
      ${envNote}
      ${renderTwoFactorBackupCodes()}
      ${passwordField}
      ${codeField}
      <div class="two-factor-actions">
        <button class="small-button" type="button" data-two-factor-action="backup-codes">${escapeHtml(t("security.regenerate_backup"))}</button>
        <button class="small-button danger-button" type="button" data-two-factor-action="disable" ${canDisable ? "" : "disabled"}>${escapeHtml(t("security.disable"))}</button>
      </div>`;
  } else {
    body = `
      <p class="setting-help">${escapeHtml(statusText)}</p>
      ${renderTwoFactorBackupCodes()}
      ${passwordField}
      <div class="two-factor-actions">
        <button class="small-button done-button" type="button" data-two-factor-action="setup">${escapeHtml(t("security.start_setup"))}</button>
      </div>`;
  }

  els.securitySettings.innerHTML = `
    <div class="settings-section-heading">
      <h3>${escapeHtml(t("security.title"))}</h3>
      <span class="settings-pill ${enabled ? "on" : ""}">${escapeHtml(sourceLabel)}</span>
    </div>
    ${renderAccountSettings(status)}
    <div class="setting-field two-factor-card">
      <span>
        <strong>${escapeHtml(t("security.2fa"))}</strong>
        <small>${escapeHtml(sourceLabel)}</small>
      </span>
      ${body}
    </div>`;
}

function renderSettings(payload = state.bootstrap?.settings) {
  const sections = payload?.sections || [];
  renderSettingsNav(sections);
  renderTwoFactorSettings();
  if (!sections.length) {
    els.settingsSections.innerHTML = `<div class="empty-state">${escapeHtml(t("settings.empty"))}</div>`;
    return;
  }
  els.settingsSections.innerHTML = sections
    .map(
      (section) => `
        <section class="settings-section" id="${escapeHtml(settingsAnchor(section.name))}">
          <div class="settings-section-heading">
            <h3>${escapeHtml(section.name)}</h3>
          </div>
          <div class="settings-fields">
            ${(section.fields || []).map(renderSettingField).join("")}
          </div>
        </section>`,
    )
    .join("");
}

function voiceGreetingPreview(url) {
  if (!url) return "";
  return `<audio class="voice-greeting-preview" controls preload="metadata" src="${escapeHtml(url)}"></audio>`;
}

function renderVoiceGreetingSettingField(field, key, source, help) {
  return `
    <div class="setting-field setting-upload-field">
      <span>
        <strong>${escapeHtml(field.label)}</strong>
        <small>${source}</small>
        ${help}
      </span>
      <input
        class="setting-voice-greeting-url"
        data-setting-key="${key}"
        data-setting-type="url"
        type="url"
        value="${escapeHtml(field.value || "")}"
        autocomplete="off"
      />
      <div class="voice-upload-row">
        <label class="small-button voice-greeting-upload-control">
          <span>${escapeHtml(t("voice.upload_greeting"))}</span>
          <input class="hidden-file-input setting-voice-greeting-file" type="file" accept="audio/*" />
        </label>
        ${voiceGreetingPreview(field.value || "")}
      </div>
    </div>`;
}

function renderSettingField(field) {
  const key = escapeHtml(field.key);
  const source = escapeHtml(settingSourceLabel(field));
  const help = field.help ? `<p class="setting-help">${escapeHtml(field.help)}</p>` : "";
  if (field.key === "voice.voicemail_greeting_media_url") {
    return renderVoiceGreetingSettingField(field, key, source, help);
  }
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
    const [payload, twoFactor] = await Promise.all([api("/api/settings"), api("/api/auth/2fa")]);
    if (state.bootstrap) {
      state.bootstrap.settings = payload;
    }
    state.twoFactor = twoFactor;
    state.twoFactorSetup = null;
    state.twoFactorBackupCodes = [];
    renderSettings(payload);
    els.settingsModal.classList.remove("hidden");
    els.settingsModal.focus();
  } catch (error) {
    toast(error.message);
  }
}

function closeSettings() {
  els.settingsModal.classList.add("hidden");
  state.twoFactorSetup = null;
  state.twoFactorBackupCodes = [];
}

function downloadDatabase() {
  window.location.href = "/api/database/download";
}

async function signOut() {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } finally {
    window.location.href = "/login";
  }
}

function accountPayload() {
  return {
    username: els.securitySettings?.querySelector(".account-username")?.value || "",
    password: els.securitySettings?.querySelector(".account-current-password")?.value || "",
    new_password: els.securitySettings?.querySelector(".account-new-password")?.value || "",
    confirm_password: els.securitySettings?.querySelector(".account-confirm-password")?.value || "",
  };
}

async function saveAccountSettings() {
  const controls = els.securitySettings?.querySelectorAll(".account-card input, .account-card button") || [];
  controls.forEach((control) => {
    control.disabled = true;
  });
  try {
    const payload = await api("/api/auth/account", {
      method: "POST",
      body: JSON.stringify(accountPayload()),
    });
    state.twoFactor = { ...(state.twoFactor || {}), ...(payload.auth || {}) };
    renderTwoFactorSettings();
    toast(t("security.account_saved"));
  } catch (error) {
    toast(error.message);
  } finally {
    controls.forEach((control) => {
      control.disabled = false;
    });
  }
}

function twoFactorPassword() {
  return els.securitySettings?.querySelector(".two-factor-current-password")?.value || "";
}

function twoFactorCurrentCode() {
  return els.securitySettings?.querySelector(".two-factor-current-code")?.value || "";
}

function setTwoFactorBusy(busy) {
  els.securitySettings?.querySelectorAll("input, textarea, button").forEach((control) => {
    control.disabled = busy;
  });
}

async function refreshTwoFactorStatus() {
  state.twoFactor = await api("/api/auth/2fa");
  renderTwoFactorSettings();
}

async function startTwoFactorSetup() {
  const password = twoFactorPassword();
  setTwoFactorBusy(true);
  try {
    const payload = await api("/api/auth/2fa/setup", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    state.twoFactor = payload.status;
    state.twoFactorSetup = payload.setup;
    state.twoFactorBackupCodes = [];
    renderTwoFactorSettings();
    els.securitySettings?.querySelector(".two-factor-setup-code")?.focus();
  } catch (error) {
    toast(error.message);
  } finally {
    setTwoFactorBusy(false);
  }
}

async function enableTwoFactor() {
  const code = els.securitySettings?.querySelector(".two-factor-setup-code")?.value || "";
  setTwoFactorBusy(true);
  try {
    const payload = await api("/api/auth/2fa/enable", {
      method: "POST",
      body: JSON.stringify({ setup_token: state.twoFactorSetup?.setup_token || "", code }),
    });
    state.twoFactor = payload.status;
    state.twoFactorSetup = null;
    state.twoFactorBackupCodes = [];
    renderTwoFactorSettings();
    toast(t("security.enabled_toast"));
  } catch (error) {
    toast(error.message);
  } finally {
    setTwoFactorBusy(false);
  }
}

async function regenerateBackupCodes() {
  const password = twoFactorPassword();
  setTwoFactorBusy(true);
  try {
    const payload = await api("/api/auth/2fa/backup-codes", {
      method: "POST",
      body: JSON.stringify({ password }),
    });
    state.twoFactor = payload.status;
    state.twoFactorSetup = null;
    state.twoFactorBackupCodes = payload.backup_codes || [];
    renderTwoFactorSettings();
    toast(t("security.backup_regenerated"));
  } catch (error) {
    toast(error.message);
  } finally {
    setTwoFactorBusy(false);
  }
}

async function disableTwoFactor() {
  if (!confirm(t("security.disable_confirm"))) return;
  const password = twoFactorPassword();
  const secondFactor = twoFactorCurrentCode();
  setTwoFactorBusy(true);
  try {
    const payload = await api("/api/auth/2fa/disable", {
      method: "POST",
      body: JSON.stringify({ password, second_factor: secondFactor }),
    });
    state.twoFactor = payload.status;
    state.twoFactorSetup = null;
    state.twoFactorBackupCodes = [];
    renderTwoFactorSettings();
    toast(t("security.disabled_toast"));
  } catch (error) {
    toast(error.message);
  } finally {
    setTwoFactorBusy(false);
  }
}

async function copyTwoFactorValue(value) {
  try {
    await navigator.clipboard.writeText(value);
    toast(t("security.copied"));
  } catch {
    toast(value);
  }
}

async function handleTwoFactorAction(action) {
  if (action === "setup") await startTwoFactorSetup();
  if (action === "enable") await enableTwoFactor();
  if (action === "backup-codes") await regenerateBackupCodes();
  if (action === "disable") await disableTwoFactor();
  if (action === "cancel-setup") {
    state.twoFactorSetup = null;
    await refreshTwoFactorStatus();
  }
}

async function handleAccountAction(action) {
  if (action === "save") await saveAccountSettings();
}

function statsLabel(key) {
  const normalized = String(key || "").toLowerCase();
  const i18nKey = `stats.${normalized}`;
  if (I18N.en[i18nKey]) return t(i18nKey);
  if (I18N.en[`status.${normalized}`]) return t(`status.${normalized}`);
  return normalized.replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function validStatsPeriod(value) {
  const normalized = String(value || "all");
  return STATS_PERIOD_OPTIONS.some((option) => option.value === normalized) ? normalized : "all";
}

function renderStatsPeriodOptions() {
  if (!els.statsPeriod) return;
  const current = validStatsPeriod(els.statsPeriod.value || localStorage.getItem(STATS_PERIOD_KEY));
  els.statsPeriod.innerHTML = STATS_PERIOD_OPTIONS.map(
    (option) => `<option value="${escapeHtml(option.value)}">${escapeHtml(t(option.labelKey))}</option>`,
  ).join("");
  els.statsPeriod.value = current;
}

function currentStatsPeriod() {
  return validStatsPeriod(els.statsPeriod?.value || localStorage.getItem(STATS_PERIOD_KEY));
}

async function loadStats() {
  const period = currentStatsPeriod();
  localStorage.setItem(STATS_PERIOD_KEY, period);
  els.statsBody.innerHTML = `<div class="empty-state">${escapeHtml(t("stats.loading"))}</div>`;
  try {
    renderStats(await api(`/api/stats?period=${encodeURIComponent(period)}`));
  } catch (error) {
    toast(error.message);
  }
}

function renderStatsList(items = []) {
  if (!items.length) return `<div class="empty-state">${escapeHtml(t("stats.empty"))}</div>`;
  return items
    .map(
      ([label, value]) => `
        <div class="stat-detail">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value ?? 0)}</strong>
        </div>`,
    )
    .join("");
}

function statsCount(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
}

function statsChartBucketParts(bucket, bucketType = "day") {
  const value = String(bucket || "");
  let match = null;
  if (bucketType === "hour") {
    match = value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2})$/);
    if (match) {
      return {
        year: Number(match[1]),
        month: Number(match[2]),
        day: Number(match[3]),
        hour: Number(match[4]),
        minute: 0,
      };
    }
  } else if (bucketType === "month") {
    match = value.match(/^(\d{4})-(\d{2})$/);
    if (match) {
      return { year: Number(match[1]), month: Number(match[2]), day: 1, hour: 12, minute: 0 };
    }
  } else {
    match = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (match) {
      return {
        year: Number(match[1]),
        month: Number(match[2]),
        day: Number(match[3]),
        hour: 12,
        minute: 0,
      };
    }
  }
  return null;
}

function statsBucketYear(bucket, bucketType = "day") {
  return statsChartBucketParts(bucket, bucketType)?.year || "";
}

function statsBucketUnitLabel(bucketType = "day") {
  return t(`stats.bucket_${bucketType}`) || bucketType;
}

function formatStatsChartBucket(bucket, bucketType = "day", full = false, optionsOverride = {}) {
  const value = String(bucket || "");
  const includeYear = Boolean(full || optionsOverride.includeYear);
  const dateParts = statsChartBucketParts(bucket, bucketType);
  if (!dateParts) return value;
  let options = { month: "short", day: "numeric", timeZone: "UTC" };
  if (bucketType === "hour") {
    options = {
      ...(full ? { month: "short", day: "numeric" } : {}),
      ...(includeYear && full ? { year: "numeric" } : {}),
      hour: "numeric",
      hour12: state.language === "en",
      timeZone: "UTC",
    };
  } else if (bucketType === "month") {
    options = { month: "short", ...(includeYear ? { year: "numeric" } : {}), timeZone: "UTC" };
  } else {
    options = { ...(full ? { weekday: "short" } : {}), ...(includeYear ? { year: "numeric" } : {}), month: "short", day: "numeric", timeZone: "UTC" };
  }
  return new Intl.DateTimeFormat(localeForLanguage(), options).format(dateForDisplay(dateParts));
}

function normalizeStatsTimeline(payload) {
  if (payload?.timeline?.points?.length) {
    return {
      bucket: payload.timeline.bucket || "day",
      points: payload.timeline.points,
    };
  }
  const points = (payload?.recent_days || [])
    .filter((item) => item?.day)
    .map((item) => ({
      bucket: item.day,
      count: item.count,
      inbound: item.inbound,
      outbound: item.outbound,
    }))
    .reverse();
  return { bucket: "day", points };
}

function renderStatsTimelineChart(timeline = {}) {
  const bucketType = timeline.bucket || "day";
  const chartPoints = (timeline.points || [])
    .filter((item) => item?.bucket)
    .map((item) => ({
      bucket: item.bucket,
      count: Math.max(0, statsCount(item.count)),
      inbound: Math.max(0, statsCount(item.inbound)),
      outbound: Math.max(0, statsCount(item.outbound)),
    }));
  if (!chartPoints.length) return `<div class="empty-state">${escapeHtml(t("stats.empty"))}</div>`;

  const years = chartPoints.map((item) => statsBucketYear(item.bucket, bucketType)).filter(Boolean);
  const spansYears = new Set(years).size > 1;
  const height = spansYears ? 280 : 250;
  const padLeft = 46;
  const padRight = 22;
  const padTop = 22;
  const padBottom = spansYears ? 64 : 44;
  const pointSpacing = bucketType === "month" ? 24 : bucketType === "hour" ? 36 : 30;
  const width = Math.max(820, padLeft + padRight + Math.max(1, chartPoints.length - 1) * pointSpacing);
  const plotWidth = width - padLeft - padRight;
  const plotHeight = height - padTop - padBottom;
  const plotBottom = height - padBottom;
  const maxCount = Math.max(1, ...chartPoints.map((item) => item.count));
  const totalCount = chartPoints.reduce((sum, item) => sum + item.count, 0);
  const dateRange = `${formatStatsChartBucket(chartPoints[0].bucket, bucketType, true)} - ${formatStatsChartBucket(
    chartPoints[chartPoints.length - 1].bucket,
    bucketType,
    true,
  )}`;
  const coordinates = chartPoints.map((item, index) => {
    const x = chartPoints.length === 1 ? padLeft + plotWidth / 2 : padLeft + (index / (chartPoints.length - 1)) * plotWidth;
    const y = padTop + (1 - item.count / maxCount) * plotHeight;
    return { ...item, x, y };
  });
  const linePoints = coordinates.map((item) => `${item.x.toFixed(2)},${item.y.toFixed(2)}`).join(" ");
  const labelEvery = Math.max(1, Math.ceil(chartPoints.length / (spansYears ? 12 : 6)));
  const gridLines = [0, 0.25, 0.5, 0.75, 1]
    .map((ratio) => {
      const y = padTop + (1 - ratio) * plotHeight;
      const value = Math.round(maxCount * ratio);
      return `
        <line class="stats-chart-grid-line" x1="${padLeft}" y1="${y.toFixed(2)}" x2="${width - padRight}" y2="${y.toFixed(2)}"></line>
        <text class="stats-chart-y-label" x="${padLeft - 10}" y="${(y + 4).toFixed(2)}">${escapeHtml(value)}</text>`;
    })
    .join("");
  const axisTitles = `
    <text class="stats-chart-axis-title stats-chart-y-title" x="${padLeft}" y="17">${escapeHtml(t("stats.texts"))}</text>
    <text class="stats-chart-axis-title stats-chart-x-title" x="${(padLeft + plotWidth / 2).toFixed(2)}" y="${
      spansYears ? height - 48 : height - 30
    }">${escapeHtml(statsBucketUnitLabel(bucketType))}</text>`;
  const yearGuides = spansYears
    ? (() => {
        const labels = [];
        const separators = [];
        let groupStart = 0;
        const addYearLabel = (startIndex, endIndex) => {
          const year = statsBucketYear(coordinates[startIndex]?.bucket, bucketType);
          if (!year) return;
          const left =
            startIndex === 0 ? padLeft : (coordinates[startIndex - 1].x + coordinates[startIndex].x) / 2;
          const right =
            endIndex === coordinates.length - 1
              ? width - padRight
              : (coordinates[endIndex].x + coordinates[endIndex + 1].x) / 2;
          labels.push(`
            <text class="stats-chart-year-label" x="${((left + right) / 2).toFixed(2)}" y="${height - 10}">
              ${escapeHtml(year)}
            </text>`);
        };
        for (let index = 1; index < coordinates.length; index += 1) {
          const previousYear = statsBucketYear(coordinates[index - 1].bucket, bucketType);
          const currentYear = statsBucketYear(coordinates[index].bucket, bucketType);
          if (!previousYear || previousYear === currentYear) continue;
          const x = (coordinates[index - 1].x + coordinates[index].x) / 2;
          separators.push(
            `<line class="stats-chart-year-line" x1="${x.toFixed(2)}" y1="${padTop}" x2="${x.toFixed(2)}" y2="${plotBottom}"></line>`,
          );
          addYearLabel(groupStart, index - 1);
          groupStart = index;
        }
        addYearLabel(groupStart, coordinates.length - 1);
        return separators.join("") + labels.join("");
      })()
    : "";
  const axisLabels = coordinates
    .map((item, index) => {
      if (index !== 0 && index !== coordinates.length - 1 && index % labelEvery !== 0) return "";
      const includeYear = !spansYears && bucketType !== "hour" && (index === 0 || index === coordinates.length - 1);
      return `
        <text class="stats-chart-x-label" x="${item.x.toFixed(2)}" y="${spansYears ? height - 29 : height - 12}">
          ${escapeHtml(formatStatsChartBucket(item.bucket, bucketType, false, { includeYear }))}
        </text>`;
    })
    .join("");
  const dots = coordinates
    .map(
      (item) => `
        <circle class="stats-chart-point" cx="${item.x.toFixed(2)}" cy="${item.y.toFixed(2)}" r="4"></circle>`,
    )
    .join("");
  const tooltips = coordinates
    .map((item) => {
      const bucketLabel = formatStatsChartBucket(item.bucket, bucketType, true);
      const countLabel = `${item.count} ${t("stats.texts")}`;
      const detailLabel = `${item.inbound} ${t("stats.inbound_messages")} / ${item.outbound} ${t("stats.outbound_messages")}`;
      const label = `${bucketLabel}: ${countLabel} (${detailLabel})`;
      const tooltipWidth = 248;
      const tooltipHeight = 52;
      const tooltipX = clamp(item.x - tooltipWidth / 2, padLeft, width - padRight - tooltipWidth);
      let tooltipY = item.y - tooltipHeight - 10;
      if (tooltipY < 6) tooltipY = item.y + 12;
      if (tooltipY + tooltipHeight > plotBottom) tooltipY = Math.max(6, plotBottom - tooltipHeight);
      return `
        <g class="stats-chart-tooltip-group" tabindex="0" aria-label="${escapeHtml(label)}">
          <circle class="stats-chart-hit-area" cx="${item.x.toFixed(2)}" cy="${item.y.toFixed(2)}" r="11"></circle>
          <circle class="stats-chart-hover-point" cx="${item.x.toFixed(2)}" cy="${item.y.toFixed(2)}" r="4"></circle>
          <g class="stats-chart-tooltip" transform="translate(${tooltipX.toFixed(2)} ${tooltipY.toFixed(2)})">
            <rect width="${tooltipWidth}" height="${tooltipHeight}" rx="8"></rect>
            <text class="stats-chart-tooltip-title" x="11" y="17">${escapeHtml(bucketLabel)}</text>
            <text class="stats-chart-tooltip-count" x="11" y="33">${escapeHtml(countLabel)}</text>
            <text class="stats-chart-tooltip-detail" x="11" y="47">${escapeHtml(detailLabel)}</text>
          </g>
        </g>`;
    })
    .join("");

  return `
    <div class="stats-chart">
      <div class="stats-chart-summary">
        <strong>${escapeHtml(totalCount)}</strong>
        <span>${escapeHtml(t("stats.timeline_by", { metric: t("stats.texts"), bucket: statsBucketUnitLabel(bucketType) }))} · ${escapeHtml(dateRange)}</span>
      </div>
      <div class="stats-chart-plot">
        <svg class="stats-chart-svg" style="min-width:${width}px" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(
          `${totalCount} ${t("stats.texts")} ${dateRange}`,
        )}">
          ${gridLines}
          ${yearGuides}
          ${axisTitles}
          <polyline class="stats-chart-line" points="${linePoints}"></polyline>
          <g class="stats-chart-point-layer">${dots}</g>
          ${axisLabels}
          <g class="stats-chart-tooltip-layer">${tooltips}</g>
        </svg>
      </div>
    </div>`;
}

function renderStats(payload) {
  const totals = payload?.totals || {};
  const totalItems = [
    [t("stats.threads"), totals.conversations],
    [t("stats.inbox_conversations"), totals.inbox_conversations],
    [t("stats.hidden_conversations"), totals.hidden_conversations],
    [t("stats.unread_conversations"), totals.unread_conversations],
    [t("stats.texts"), totals.messages],
    [t("stats.inbound_messages"), totals.inbound_messages],
    [t("stats.outbound_messages"), totals.outbound_messages],
    [t("stats.voicemails"), totals.voicemails],
    [t("stats.failed_messages"), totals.failed_messages],
    [t("stats.pending_messages"), totals.pending_messages],
    [t("stats.media"), totals.attachments],
    [t("stats.people"), totals.contacts],
  ];
  const statusItems = (payload?.by_status || []).map((item) => [statsLabel(item.status), item.count]);
  const sourceItems = (payload?.by_source || []).map((item) => [statsLabel(item.source), item.count]);
  const typeItems = (payload?.by_type || []).map((item) => [statsLabel(item.message_type), item.count]);
  const directionItems = (payload?.by_direction || []).map((item) => [statsLabel(item.direction), item.count]);
  const timeline = normalizeStatsTimeline(payload);
  els.statsBody.innerHTML = `
    <section class="stats-section">
      <h3>${escapeHtml(t("stats.totals"))}</h3>
      <div class="stats-grid">${renderStatsList(totalItems)}</div>
    </section>
    <section class="stats-section">
      <h3>${escapeHtml(t("stats.by_status"))}</h3>
      <div class="stats-grid">${renderStatsList(statusItems)}</div>
    </section>
    <section class="stats-section">
      <h3>${escapeHtml(t("stats.by_source"))}</h3>
      <div class="stats-grid">${renderStatsList(sourceItems)}</div>
    </section>
    <section class="stats-section">
      <h3>${escapeHtml(t("stats.by_type"))}</h3>
      <div class="stats-grid">${renderStatsList(typeItems)}</div>
    </section>
    <section class="stats-section">
      <h3>${escapeHtml(t("stats.by_direction"))}</h3>
      <div class="stats-grid">${renderStatsList(directionItems)}</div>
    </section>
    <section class="stats-section">
      <h3>${escapeHtml(t("stats.timeline"))}</h3>
      ${renderStatsTimelineChart(timeline)}
    </section>`;
}

async function openStats() {
  renderStatsPeriodOptions();
  els.statsModal.classList.remove("hidden");
  els.statsModal.focus();
  await loadStats();
}

function closeStats() {
  els.statsModal.classList.add("hidden");
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
    renderBootstrap({ forceIdentities: true });
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

function updateVoiceGreetingPreview(container, url) {
  if (!container) return;
  const row = container.querySelector(".voice-upload-row");
  const oldWrap = container.querySelector(".voice-greeting-preview-wrap");
  let preview = row?.querySelector(".voice-greeting-preview") || container.querySelector(".voice-greeting-preview");
  if (!url) {
    if (preview) preview.remove();
    if (oldWrap) oldWrap.remove();
    return;
  }
  if (!preview) {
    preview = document.createElement("audio");
    preview.className = "voice-greeting-preview";
    preview.controls = true;
    preview.preload = "metadata";
  }
  if (row && preview.parentElement !== row) {
    row.append(preview);
  }
  if (oldWrap) {
    oldWrap.remove();
  }
  preview.src = url;
}

async function uploadVoiceGreetingFile(input) {
  const file = input.files?.[0];
  if (!file) return;
  const container = input.closest(".identity-card, .setting-field");
  const urlInput = container?.querySelector(".identity-voice-greeting-media-url, .setting-voice-greeting-url");
  const control = input.closest(".voice-greeting-upload-control");
  input.disabled = true;
  if (control) control.classList.add("disabled");
  try {
    const uploaded = await uploadMediaFile(file);
    if (urlInput) urlInput.value = uploaded.url;
    updateVoiceGreetingPreview(container, uploaded.url);
    updateIdentityDirtyState(urlInput || input);
    toast(t("voice.greeting_uploaded"));
  } catch (error) {
    toast(error.message);
  } finally {
    input.disabled = false;
    input.value = "";
    if (control) control.classList.remove("disabled");
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

function cleanReactionText(value) {
  return String(value || "")
    .normalize("NFC")
    .replace(REACTION_INVISIBLE_PATTERN, "")
    .replace(REACTION_SPACING_PATTERN, " ")
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t\f\v]+/g, " ")
    .replace(/[ \t\f\v]*\n[ \t\f\v]*/g, "\n")
    .trim();
}

function hasEmojiLikeSymbol(value) {
  return [...String(value || "")].some((char) => {
    const code = char.codePointAt(0);
    return (
      (code >= 0x1f000 && code <= 0x1faff) ||
      (code >= 0x2600 && code <= 0x27bf) ||
      code === 0x203c ||
      code === 0x2049
    );
  });
}

function normalizeReactionIcon(value) {
  const icon = cleanReactionText(value);
  const wordIcon = REACTION_WORDS.get(icon.toLowerCase());
  if (wordIcon) return wordIcon;
  if (icon === "❤") return "❤️";
  if (REACTION_SYMBOLS.has(icon) || hasEmojiLikeSymbol(icon)) return icon;
  return "";
}

function parseMessageReaction(message) {
  if (!message?.text || (message.attachments || []).length) return null;
  const text = cleanReactionText(message.text);
  let match = text.match(/^(.{1,18}?)\s+to\s+[“"]([\s\S]+)[”"]$/iu);
  if (match) {
    const icon = normalizeReactionIcon(match[1]);
    const targetText = cleanReactionText(match[2]);
    if (icon && targetText) return { icon, targetText };
  }
  match = text.match(/^(liked|loved|disliked|laughed at|emphasized|questioned)\s+[“"]([\s\S]+)[”"]$/iu);
  if (match) {
    const icon = normalizeReactionIcon(match[1]);
    const targetText = cleanReactionText(match[2]);
    if (icon && targetText) return { icon, targetText };
  }
  return null;
}

function reactionComparableText(value) {
  return cleanReactionText(value).toLowerCase();
}

function findReactionTargetMessage(messages, reactionMessage, targetText) {
  const comparableTarget = reactionComparableText(targetText);
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const candidate = messages[index];
    if (!candidate?.text) continue;
    if (candidate.from_number && reactionMessage.from_number && candidate.from_number === reactionMessage.from_number) {
      continue;
    }
    if (reactionComparableText(candidate.text) === comparableTarget) return candidate;
  }
  return null;
}

function messagesWithInlineReactions(messages) {
  const visibleMessages = [];
  messages.forEach((rawMessage) => {
    const reaction = parseMessageReaction(rawMessage);
    const target = reaction ? findReactionTargetMessage(visibleMessages, rawMessage, reaction.targetText) : null;
    if (target) {
      target.inline_reactions.push({
        ...reaction,
        id: rawMessage.id,
        from_display: rawMessage.from_display,
        from_number: rawMessage.from_number,
      });
      return;
    }
    visibleMessages.push({ ...rawMessage, inline_reactions: [...(rawMessage.inline_reactions || [])] });
  });
  return visibleMessages;
}

function participantDisplay(participant) {
  const phone = phoneDisplay(participant.phone_number);
  if (!participant.display || participant.display === phone || participant.display === participant.phone_number) {
    return phone;
  }
  return `${participant.display} ${phone}`;
}

function normalizeDraftPhone(raw) {
  const text = String(raw || "").trim();
  const digits = String(raw || "").replace(/\D/g, "");
  if (!digits) return text;
  if (digits.length === 10) return `+1${digits}`;
  if (digits.length === 11 && digits.startsWith("1")) return `+${digits}`;
  if (text.startsWith("+") || digits.length >= 7) return `+${digits}`;
  return text;
}

function isUsableDraftPhone(phone) {
  return phone.startsWith("+") && phone.replace(/\D/g, "").length >= 7;
}

function pendingRecipientPhone() {
  if (state.currentConversation) return "";
  const raw = els.recipientInput.value.trim();
  if (!raw) return "";
  const phone = normalizeDraftPhone(raw);
  return isUsableDraftPhone(phone) ? phone : "";
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

function isContactNameModalOpen() {
  return Boolean(els.contactNameModal && !els.contactNameModal.classList.contains("hidden"));
}

function closeContactNameModal({ restoreFocus = false } = {}) {
  if (!els.contactNameModal) return;
  els.contactNameModal.classList.add("hidden");
  if (restoreFocus) {
    els.threadTitle?.focus();
  }
}

function openContactNameModal(participant) {
  if (!participant || !els.contactNameModal || !els.contactNameModalInput) return;
  els.contactNameForm.classList.add("hidden");
  els.contactNameModalInput.value = participantSavedName(participant);
  els.contactNameModal.classList.remove("hidden");
  requestAnimationFrame(() => {
    els.contactNameModalInput.focus();
    els.contactNameModalInput.select();
  });
}

function valueIsTruthy(value) {
  if (value === true || value === 1) return true;
  return ["1", "true", "yes", "on"].includes(String(value ?? "").trim().toLowerCase());
}

function conversationIsRead(conversation) {
  if (!conversation) return false;
  if (conversation.manual_unread_at) return false;
  const lastDirection = String(conversation.last_direction || "").toLowerCase();
  if (lastDirection && lastDirection !== "inbound") return true;
  const last = conversation?.last_occurred_at || conversation?.last_message_at || conversation?.sort_at || "";
  const dealt = conversation?.dealt_with_at || "";
  if (last && dealt && dealt >= last) return true;
  if (conversation.needs_attention !== undefined && conversation.needs_attention !== null && conversation.needs_attention !== "") {
    return !valueIsTruthy(conversation.needs_attention);
  }
  return false;
}

function setContactNameEditor(visible) {
  const participant = currentDirectParticipant();
  if (!participant || !visible) {
    els.contactNameForm.classList.add("hidden");
    closeContactNameModal();
    return;
  }
  if (!isDesktopLayout()) {
    openContactNameModal(participant);
    return;
  }
  closeContactNameModal();
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

function renderBootstrap({ forceIdentities = false } = {}) {
  const stats = state.bootstrap.stats || {};
  const previousFromNumber = els.fromNumber.value;
  const showSummaryStats = settingBool("behavior.show_summary_stats", true);
  els.statStrip.hidden = !showSummaryStats;
  els.statStrip.innerHTML = showSummaryStats
    ? [
        [t("stats.threads"), stats.conversations],
        [t("stats.texts"), stats.messages],
        [t("stats.media"), stats.attachments],
        [t("stats.people"), stats.contacts],
      ]
        .map(
          ([label, value]) => `
            <button class="stat" type="button" aria-label="${escapeHtml(`${t("stats.title")}: ${label}`)}">
              <strong>${escapeHtml(value ?? 0)}</strong>
              <span>${escapeHtml(label)}</span>
            </button>`,
        )
        .join("")
    : "";

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

  if (!forceIdentities && hasDirtyIdentityChanges()) {
    updateIdentityDirtyStates();
  } else {
    renderIdentities();
  }
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

function updateConversationSearchClear() {
  els.conversationSearchClear.hidden = !els.conversationSearch.value;
}

function scheduleConversationSearchLoad() {
  clearTimeout(state.searchTimer);
  updateConversationSearchClear();
  state.searchTimer = setTimeout(() => {
    loadConversations({ append: false }).catch((error) => toast(error.message));
  }, 180);
}

function clearConversationSearch({ focus = false } = {}) {
  if (!els.conversationSearch.value) {
    updateConversationSearchClear();
    if (focus) els.conversationSearch.focus();
    return false;
  }
  clearTimeout(state.searchTimer);
  els.conversationSearch.value = "";
  updateConversationSearchClear();
  loadConversations({ append: false }).catch((error) => toast(error.message));
  if (focus) els.conversationSearch.focus();
  return true;
}

function identitySnapshotFromIdentity(identity = {}) {
  return {
    label: String(identity.label || ""),
    color: String(identity.color || "").toLowerCase(),
    autoreply_enabled: Boolean(identity.autoreply_enabled),
    autoreply_message: String(identity.autoreply_message || ""),
    autoreply_cooldown_hours: String(Number(identity.autoreply_cooldown_hours) || 24),
    voice_forwarding_enabled: Boolean(identity.voice_forwarding_enabled),
    voice_forward_to_number: String(identity.voice_forward_to_number || ""),
    voice_forward_timeout_seconds: String(Number(identity.voice_forward_timeout_seconds) || 20),
    voice_voicemail_enabled: identity.voice_voicemail_enabled === false ? false : true,
    voice_voicemail_greeting: String(identity.voice_voicemail_greeting || ""),
    voice_voicemail_greeting_media_url: String(identity.voice_voicemail_greeting_media_url || ""),
  };
}

function identitySnapshotFromCard(card) {
  return {
    label: card.querySelector(".identity-label")?.value || "",
    color: String(card.querySelector(".identity-color")?.value || "").toLowerCase(),
    autoreply_enabled: Boolean(card.querySelector(".identity-autoreply-enabled")?.checked),
    autoreply_message: card.querySelector(".identity-autoreply-message")?.value || "",
    autoreply_cooldown_hours: String(Number(card.querySelector(".identity-autoreply-cooldown-hours")?.value) || 24),
    voice_forwarding_enabled: Boolean(card.querySelector(".identity-voice-forwarding-enabled")?.checked),
    voice_forward_to_number: card.querySelector(".identity-voice-forward-to")?.value || "",
    voice_forward_timeout_seconds: String(Number(card.querySelector(".identity-voice-forward-timeout")?.value) || 20),
    voice_voicemail_enabled: Boolean(card.querySelector(".identity-voice-voicemail-enabled")?.checked),
    voice_voicemail_greeting: card.querySelector(".identity-voice-greeting")?.value || "",
    voice_voicemail_greeting_media_url: card.querySelector(".identity-voice-greeting-media-url")?.value || "",
  };
}

function isIdentityCardDirty(card) {
  if (!card?.dataset?.id) return false;
  const identity = (state.bootstrap?.identities || []).find((item) => String(item.id) === String(card.dataset.id));
  if (!identity) return false;
  return JSON.stringify(identitySnapshotFromCard(card)) !== JSON.stringify(identitySnapshotFromIdentity(identity));
}

function updateIdentityDirtyState(target) {
  const card = target?.closest?.(".identity-card");
  if (!card) return;
  card.classList.toggle("identity-dirty", isIdentityCardDirty(card));
}

function dirtyIdentityCards() {
  return [...els.identityList.querySelectorAll(".identity-card")].filter(isIdentityCardDirty);
}

function hasDirtyIdentityChanges() {
  return dirtyIdentityCards().length > 0;
}

function updateIdentityDirtyStates() {
  els.identityList.querySelectorAll(".identity-card").forEach((card) => {
    card.classList.toggle("identity-dirty", isIdentityCardDirty(card));
  });
}

function discardIdentityChanges() {
  renderIdentities();
}

function confirmDiscardIdentityChanges() {
  if (!hasDirtyIdentityChanges()) return true;
  if (!window.confirm(t("identities.discard_confirm"))) return false;
  discardIdentityChanges();
  return true;
}

function renderIdentities() {
  els.identityList.innerHTML = (state.bootstrap.identities || [])
    .map(
      (identity) => {
        const autoreplyEnabled = identity.autoreply_enabled ? "checked" : "";
        const autoreplyOpen = identity.autoreply_enabled ? "open" : "";
        const autoreplyStatus = identity.autoreply_enabled ? t("identities.autoreply_on") : t("identities.autoreply_off");
        const cooldown = Number(identity.autoreply_cooldown_hours) || 24;
        const voiceForwardingEnabled = Boolean(identity.voice_forwarding_enabled);
        const voiceVoicemailEnabled = identity.voice_voicemail_enabled === false ? false : true;
        const voiceForwarding = voiceForwardingEnabled ? "checked" : "";
        const voiceVoicemail = voiceVoicemailEnabled ? "checked" : "";
        const voiceForwardingStatus = voiceForwardingEnabled ? t("identities.autoreply_on") : t("identities.autoreply_off");
        const voiceVoicemailStatus = voiceVoicemailEnabled ? t("identities.autoreply_on") : t("identities.autoreply_off");
        const voiceTimeout = Number(identity.voice_forward_timeout_seconds) || 20;
        const voiceGreetingMediaUrl = identity.voice_voicemail_greeting_media_url || "";
        return `
        <article class="identity-card ${identity.autoreply_enabled ? "autoreply-on" : ""}" data-id="${identity.id}">
          <label class="swatch color-swatch" style="background:${escapeHtml(identity.color)}" title="${escapeHtml(t("identities.color"))}">
            <input class="identity-color" type="color" value="${escapeHtml(identity.color)}" aria-label="${escapeHtml(t("identities.color"))}" />
          </label>
          <div class="identity-main">
            <input class="identity-label" value="${escapeHtml(identity.label)}" aria-label="${escapeHtml(t("identities.label"))}" />
            <div class="identity-phone">${escapeHtml(phoneDisplay(identity.phone_number))}</div>
          </div>
          <button class="icon-button save-identity" title="${escapeHtml(t("common.save"))}" aria-label="${escapeHtml(t("common.save"))}">✓</button>
          <details class="identity-autoreply" ${autoreplyOpen}>
            <summary class="identity-autoreply-summary">
              <strong>${escapeHtml(t("identities.autoreply"))}</strong>
              <span class="identity-autoreply-status">${escapeHtml(autoreplyStatus)}</span>
            </summary>
            <div class="identity-autoreply-body">
              <label class="identity-autoreply-toggle">
                <input class="identity-autoreply-enabled" type="checkbox" ${autoreplyEnabled} />
                <span>${escapeHtml(t("identities.autoreply_enabled"))}</span>
              </label>
              <textarea
                class="identity-autoreply-message"
                rows="3"
                aria-label="${escapeHtml(t("identities.autoreply_message"))}"
                placeholder="${escapeHtml(t("identities.autoreply_placeholder"))}"
              >${escapeHtml(identity.autoreply_message || "")}</textarea>
              <label class="identity-autoreply-cooldown">
                <span>${escapeHtml(t("identities.autoreply_cooldown"))}</span>
                <input class="identity-autoreply-cooldown-hours" type="number" min="1" step="1" value="${escapeHtml(cooldown)}" />
              </label>
            </div>
          </details>
          <details class="identity-voice identity-call-forwarding ${voiceForwardingEnabled ? "voice-on" : ""}">
            <summary class="identity-autoreply-summary">
              <strong>${escapeHtml(t("voice.forwarding"))}</strong>
              <span class="identity-autoreply-status">${escapeHtml(voiceForwardingStatus)}</span>
            </summary>
            <div class="identity-voice-body">
              <label class="identity-autoreply-toggle">
                <input class="identity-voice-forwarding-enabled" type="checkbox" ${voiceForwarding} />
                <span>${escapeHtml(t("voice.forward_calls"))}</span>
              </label>
              <label class="identity-voice-field">
                <span>${escapeHtml(t("voice.forward_to"))}</span>
                <input class="identity-voice-forward-to" type="text" value="${escapeHtml(identity.voice_forward_to_number || "")}" placeholder="+15551234567 or sip:alice@example.com" />
              </label>
              <label class="identity-voice-field">
                <span>${escapeHtml(t("voice.forward_timeout"))}</span>
                <input class="identity-voice-forward-timeout" type="number" min="5" max="120" step="1" value="${escapeHtml(voiceTimeout)}" />
              </label>
            </div>
          </details>
          <details class="identity-voice identity-voicemail ${voiceVoicemailEnabled ? "voice-on" : ""}">
            <summary class="identity-autoreply-summary">
              <strong>${escapeHtml(t("voice.voicemail_heading"))}</strong>
              <span class="identity-autoreply-status">${escapeHtml(voiceVoicemailStatus)}</span>
            </summary>
            <div class="identity-voice-body">
              <label class="identity-autoreply-toggle">
                <input class="identity-voice-voicemail-enabled" type="checkbox" ${voiceVoicemail} />
                <span>${escapeHtml(t("voice.voicemail"))}</span>
              </label>
              <label class="identity-voice-field">
                <span>${escapeHtml(t("voice.greeting"))}</span>
                <textarea class="identity-voice-greeting" rows="2">${escapeHtml(identity.voice_voicemail_greeting || "")}</textarea>
              </label>
              <div class="identity-voice-field voice-greeting-upload-field">
                <span>${escapeHtml(t("voice.greeting_recording"))}</span>
                <input class="identity-voice-greeting-media-url" type="url" value="${escapeHtml(voiceGreetingMediaUrl)}" placeholder="https://example.com/greeting.mp3" />
                <div class="voice-upload-row">
                  <label class="small-button voice-greeting-upload-control">
                    <span>${escapeHtml(t("voice.upload_greeting"))}</span>
                    <input class="hidden-file-input identity-voice-greeting-file" type="file" accept="audio/*" />
                  </label>
                  ${voiceGreetingPreview(voiceGreetingMediaUrl)}
                </div>
              </div>
            </div>
          </details>
        </article>`;
      },
    )
    .join("");
}

function renderConversations() {
  const items = state.conversations
    .map((conversation) => {
      const active = conversation.id === state.currentConversationId ? "active" : "";
      const selected = state.selectedConversationIds.has(Number(conversation.id));
      const selectedClass = selected ? "selected" : "";
      const selectingClass = state.selectedConversationIds.size ? "selecting" : "";
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
      const preview = failedPreview || messagePreviewText(conversation) || (lastStatusLabel ? lastStatusLabel : "");
      return `
        <button class="conversation-item ${active} ${selectedClass} ${selectingClass} ${newMessageClass} ${failedClass}" data-id="${conversation.id}" aria-pressed="${selected ? "true" : "false"}">
          <span class="avatar conversation-selector" role="checkbox" aria-checked="${selected ? "true" : "false"}" title="${escapeHtml(t("selection.actions"))}">
            ${selected ? "✓" : escapeHtml(initials(title))}
          </span>
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

function selectedConversationIds() {
  return [...state.selectedConversationIds].filter((id) => Number.isFinite(id) && id > 0);
}

function updateSelectionToolbar() {
  const count = state.selectedConversationIds.size;
  document.body.classList.toggle("conversation-selecting", count > 0);
  els.selectionToolbar.classList.toggle("hidden", count === 0);
  els.selectionCount.textContent = t("selection.count", { count });
  syncNativePullRefreshEnabled();
}

function clearConversationSelection() {
  if (!state.selectedConversationIds.size) return;
  state.selectedConversationIds.clear();
  updateSelectionToolbar();
  renderConversationsPreservingScroll();
}

function toggleConversationSelection(id, selected) {
  const conversationId = Number(id);
  if (!conversationId) return;
  const nextSelected = selected ?? !state.selectedConversationIds.has(conversationId);
  if (nextSelected) {
    state.selectedConversationIds.add(conversationId);
  } else {
    state.selectedConversationIds.delete(conversationId);
  }
  updateSelectionToolbar();
  renderConversationsPreservingScroll();
}

async function bulkConversationAction(action) {
  const ids = selectedConversationIds();
  if (!ids.length) {
    toast(t("selection.none"));
    return;
  }
  const controls = [els.bulkReadButton, els.bulkUnreadButton, els.bulkHideButton, els.selectionCancelButton];
  controls.forEach((control) => {
    control.disabled = true;
  });
  try {
    const payload = await api("/api/conversations/bulk", {
      method: "POST",
      body: JSON.stringify({ action, conversation_ids: ids }),
    });
    clearConversationSelection();
    state.bootstrap = await api("/api/bootstrap");
    applyRuntimeSettings();
    renderBootstrap();
    await loadConversations({ append: false });
    if (state.currentConversationId && ids.includes(state.currentConversationId) && action === "hide") {
      const next = state.conversations[0];
      if (next) {
        await openConversation(next.id);
      } else {
        startNewConversation();
      }
    } else if (state.currentConversationId) {
      await refreshCurrentConversationStatus();
    }
    toast(t("selection.updated", { count: payload.updated ?? ids.length }));
  } catch (error) {
    toast(error.message);
  } finally {
    controls.forEach((control) => {
      control.disabled = false;
    });
  }
}

function clearConversationPressTimer() {
  if (state.conversationPressTimer) {
    clearTimeout(state.conversationPressTimer);
    state.conversationPressTimer = null;
  }
  state.conversationPressTargetId = null;
}

async function hideConversationFromList(id) {
  const conversationId = Number(id);
  if (!conversationId) return;
  try {
    await api(`/api/conversations/${conversationId}/archive`, {
      method: "POST",
      body: JSON.stringify({ archived: true }),
    });
    state.conversations = state.conversations.filter((conversation) => conversation.id !== conversationId);
    state.selectedConversationIds.delete(conversationId);
    updateSelectionToolbar();
    renderConversationsPreservingScroll();
    state.bootstrap = await api("/api/bootstrap");
    applyRuntimeSettings();
    renderBootstrap();
    if (state.currentConversationId === conversationId) {
      const next = state.conversations[0];
      if (next) {
        await openConversation(next.id);
      } else {
        startNewConversation();
      }
    }
    toast(t("conversation.archived"));
  } catch (error) {
    toast(error.message);
  }
}

function resetConversationSwipe() {
  const swipe = state.conversationSwipe;
  if (swipe?.item) {
    swipe.item.style.transform = "";
    swipe.item.classList.remove("swiping");
  }
  state.conversationSwipe = null;
}

function beginConversationGesture(event) {
  const item = event.target.closest(".conversation-item");
  if (!item || event.button > 0 || event.target.closest("input, button:not(.conversation-item), textarea, select")) return;
  const id = Number(item.dataset.id);
  if (!id) return;
  clearConversationPressTimer();
  state.conversationPressTargetId = id;
  state.conversationPressTimer = window.setTimeout(() => {
    state.suppressConversationClickUntil = Date.now() + 500;
    toggleConversationSelection(id, true);
    clearConversationPressTimer();
  }, 520);
  if (event.pointerType === "touch" || !isDesktopLayout()) {
    state.conversationSwipe = {
      id,
      item,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      dx: 0,
      dragging: false,
    };
  }
}

function moveConversationGesture(event) {
  const swipe = state.conversationSwipe;
  if (!swipe || swipe.pointerId !== event.pointerId) return;
  const dx = event.clientX - swipe.startX;
  const dy = event.clientY - swipe.startY;
  swipe.dx = dx;
  if (!swipe.dragging && dx > 12 && Math.abs(dx) > Math.abs(dy) * 1.25) {
    swipe.dragging = true;
    clearConversationPressTimer();
  }
  if (!swipe.dragging) {
    if (Math.abs(dx) > 8 || Math.abs(dy) > 8) clearConversationPressTimer();
    return;
  }
  event.preventDefault();
  const offset = clamp(dx, 0, 104);
  swipe.item.classList.add("swiping");
  swipe.item.style.transform = `translate3d(${offset}px, 0, 0)`;
}

function endConversationGesture(event) {
  clearConversationPressTimer();
  const swipe = state.conversationSwipe;
  if (!swipe || swipe.pointerId !== event.pointerId) return;
  const shouldHide = swipe.dragging && swipe.dx > 82;
  const id = swipe.id;
  resetConversationSwipe();
  if (shouldHide) {
    state.suppressConversationClickUntil = Date.now() + 500;
    hideConversationFromList(id);
  }
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

function mergeConversationIntoLoadedState(conversation) {
  if (!conversation?.id) return false;
  const conversationId = Number(conversation.id);
  const update = { ...conversation, id: conversationId };
  if (state.currentConversationId === conversationId) {
    const currentBase = Number(state.currentConversation?.id) === conversationId ? state.currentConversation : {};
    state.currentConversation = { ...currentBase, ...update };
  }
  mergeConversationIntoList(update);
  return state.currentConversationId === conversationId;
}

function markLoadedConversationRead(conversationId) {
  const id = Number(conversationId);
  if (!id) return;
  const mark = (conversation) => {
    if (!conversation || Number(conversation.id) !== id) return conversation;
    return {
      ...conversation,
      dealt_with_at:
        conversation.last_occurred_at ||
        conversation.last_message_at ||
        conversation.sort_at ||
        conversation.dealt_with_at ||
        "",
      manual_unread_at: null,
      needs_attention: 0,
    };
  };
  state.currentConversation = mark(state.currentConversation);
  state.conversations = state.conversations.map(mark);
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
      const isManual = contact.kind === "manual";
      const label = !isManual && contact.label ? ` ${contact.label}` : "";
      const title = isManual ? t("recipient.text_number", { number: contact.phone_display }) : contact.display_name;
      const detail = isManual ? "" : `${contact.phone_display}${label}`;
      return `
        <button
          class="recipient-suggestion ${isManual ? "manual-recipient" : ""} ${active ? "active" : ""}"
          id="recipient-suggestion-${index}"
          role="option"
          type="button"
          aria-selected="${active ? "true" : "false"}"
          data-suggestion-index="${index}"
        >
          <strong>${escapeHtml(title)}</strong>
          ${detail ? `<span>${escapeHtml(detail)}</span>` : ""}
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
  addRecipient(contact.phone_number, contact.kind === "manual" ? "" : contact.display_name);
  return true;
}

async function searchRecipientSuggestions() {
  const term = els.recipientInput.value.trim();
  if (state.currentConversation || !term) {
    clearRecipientSuggestions();
    return;
  }
  const seq = ++state.recipientSuggestionSeq;
  const manualPhone = pendingRecipientPhone();
  const manualSuggestion =
    manualPhone && !state.recipientDraft.includes(manualPhone)
      ? {
          kind: "manual",
          phone_number: manualPhone,
          phone_display: phoneDisplay(manualPhone),
          display_name: "",
          label: "",
        }
      : null;
  try {
    const payload = await api(`/api/contacts?q=${encodeURIComponent(term)}`);
    if (seq !== state.recipientSuggestionSeq) return;
    const selected = new Set(state.recipientDraft);
    const seen = new Set();
    if (manualSuggestion) seen.add(manualSuggestion.phone_number);
    const contactSuggestions = (payload.contacts || []).filter((contact) => {
      if (!contact.phone_number || selected.has(contact.phone_number) || seen.has(contact.phone_number)) return false;
      seen.add(contact.phone_number);
      return true;
    });
    state.recipientSuggestions = manualSuggestion ? [manualSuggestion, ...contactSuggestions] : contactSuggestions;
    state.recipientSuggestionIndex = state.recipientSuggestions.length ? 0 : -1;
    renderRecipientSuggestions();
  } catch (error) {
    if (manualSuggestion && seq === state.recipientSuggestionSeq) {
      state.recipientSuggestions = [manualSuggestion];
      state.recipientSuggestionIndex = 0;
      renderRecipientSuggestions();
      return;
    }
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

function isAudioAttachment(attachment, url) {
  const contentType = (attachment.content_type || "").split(";", 1)[0].trim().toLowerCase();
  return contentType.startsWith("audio/") || /\.(aac|flac|m4a|mp3|oga|ogg|opus|wav)([?#].*)?$/i.test(url || attachment.filename || "");
}

function pdfViewerUrl(url) {
  return `${url}${String(url).includes("#") ? "&" : "#"}toolbar=1&navpanes=0`;
}

function renderAttachment(attachment) {
  const url = mediaUrl(attachment);
  if (!url) return "";
  const contentType = attachment.content_type || "";
  const mediaKey = escapeHtml(normalizedMediaKey(url));
  if (isImageAttachment(attachment, url)) {
    const caption = attachment.filename || t("attachment.image");
    return `<a href="${escapeHtml(url)}" class="image-attachment" data-lightbox-src="${escapeHtml(url)}" data-lightbox-caption="${escapeHtml(caption)}" target="_blank"><img src="${escapeHtml(url)}" alt="" loading="lazy" /></a>`;
  }
  if (contentType.startsWith("video/")) {
    return `<video src="${escapeHtml(url)}" data-media-key="${mediaKey}" controls preload="metadata"></video>`;
  }
  if (isAudioAttachment(attachment, url)) {
    return `<audio src="${escapeHtml(url)}" data-media-key="${mediaKey}" controls preload="metadata"></audio>`;
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

function reactionActorName(reaction) {
  return reaction.from_display || phoneDisplay(reaction.from_number) || t("conversation.unknown");
}

function groupedReactions(reactions) {
  const groups = new Map();
  reactions.forEach((reaction) => {
    const key = reaction.icon;
    if (!groups.has(key)) groups.set(key, { icon: reaction.icon, count: 0, names: new Set() });
    const group = groups.get(key);
    group.count += 1;
    group.names.add(reactionActorName(reaction));
  });
  return [...groups.values()];
}

function reactionGroupName(group) {
  const names = [...group.names];
  if (names.length <= 2) return names.join(", ");
  return `${names.slice(0, 2).join(", ")} +${names.length - 2}`;
}

function renderMessageReactions(message) {
  const reactions = message.inline_reactions || [];
  if (!reactions.length) return "";
  return `<div class="message-reactions">${groupedReactions(reactions)
    .map((group) => {
      const title = t("message.reaction_title", { name: reactionGroupName(group), icon: group.icon });
      return `<span class="message-reaction" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">
        <span class="message-reaction-icon">${escapeHtml(group.icon)}</span>
        ${group.count > 1 ? `<span class="message-reaction-count">${escapeHtml(group.count)}</span>` : ""}
      </span>`;
    })
    .join("")}</div>`;
}

function reactionPreviewText(text) {
  const reaction = parseMessageReaction({ text, attachments: [] });
  return reaction ? t("message.reaction_preview", { icon: reaction.icon }) : text;
}

function isVoicemailMessage(message) {
  return String(message?.message_type || message?.last_message_type || "").toLowerCase() === "voicemail";
}

function messagePreviewText(conversation) {
  const text = reactionPreviewText(conversation.last_text) || "";
  if (!isVoicemailMessage(conversation)) return text;
  return text ? `${t("message.voicemail")}: ${text}` : t("message.voicemail");
}

function normalizedMediaKey(url) {
  const value = String(url || "");
  if (!value) return "";
  try {
    return new URL(value, window.location.href).href;
  } catch {
    return value;
  }
}

function mediaElementKey(element) {
  return element.dataset.mediaKey || normalizedMediaKey(element.currentSrc || element.getAttribute("src") || "");
}

function captureMessageMediaPlayback() {
  const states = new Map();
  const occurrences = new Map();
  els.messages.querySelectorAll("audio, video").forEach((element) => {
    const key = mediaElementKey(element);
    if (!key) return;
    const index = occurrences.get(key) || 0;
    occurrences.set(key, index + 1);
    if (!states.has(key)) states.set(key, []);
    states.get(key)[index] = {
      currentTime: Number.isFinite(element.currentTime) ? element.currentTime : 0,
      playing: !element.paused && !element.ended,
      muted: element.muted,
      volume: element.volume,
      playbackRate: element.playbackRate,
    };
  });
  return states;
}

function restoreMediaCurrentTime(element, currentTime) {
  if (!Number.isFinite(currentTime) || currentTime <= 0) return;
  const apply = () => {
    try {
      if (Math.abs(element.currentTime - currentTime) > 0.25) {
        element.currentTime = currentTime;
      }
    } catch {
      // Some browsers reject currentTime changes until metadata is ready.
    }
  };
  if (element.readyState >= 1) {
    apply();
  } else {
    element.addEventListener("loadedmetadata", apply, { once: true });
  }
}

function resumeRestoredMedia(element, state) {
  const resume = () => {
    requestAnimationFrame(() => {
      restoreMediaCurrentTime(element, state.currentTime);
      const playResult = element.play();
      if (playResult?.catch) playResult.catch(() => {});
    });
  };
  if (element.readyState >= 1) {
    resume();
  } else {
    element.addEventListener("loadedmetadata", resume, { once: true });
  }
}

function restoreMessageMediaPlayback(states) {
  if (!states?.size) return;
  const occurrences = new Map();
  els.messages.querySelectorAll("audio, video").forEach((element) => {
    const key = mediaElementKey(element);
    if (!key || !states.has(key)) return;
    const index = occurrences.get(key) || 0;
    occurrences.set(key, index + 1);
    const state = states.get(key)[index];
    if (!state) return;
    element.muted = state.muted;
    element.volume = state.volume;
    element.playbackRate = state.playbackRate;
    if (state.playing) {
      resumeRestoredMedia(element, state);
    } else {
      restoreMediaCurrentTime(element, state.currentTime);
    }
  });
}

function renderMessages(messages, scrollMode = "bottom") {
  const wasNearBottom = isNearMessageBottom();
  const mediaPlayback = captureMessageMediaPlayback();
  const visibleMessages = messagesWithInlineReactions(messages);
  if (!visibleMessages.length) {
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
    visibleMessages
      .map((message) => {
      const day = formatDay(message.occurred_at);
      const divider = day !== lastDay ? `<div class="day-divider">${escapeHtml(day)}</div>` : "";
      lastDay = day;
      const messageAttachments = message.attachments || [];
      const attachments = messageAttachments.map(renderAttachment).join("");
      const attachmentGridClass = messageAttachments.some((attachment) => isAudioAttachment(attachment, mediaUrl(attachment)))
        ? "attachment-grid audio-attachment-grid"
        : "attachment-grid";
      const reactions = renderMessageReactions(message);
      const voicemailLabel = isVoicemailMessage(message)
        ? `<div class="message-type-label">${escapeHtml(t("message.voicemail"))}</div>`
        : "";
      const statusKind = message.status_kind || "neutral";
      const statusLabel = localizedStatusLabel(message.status, message.status_label || message.status || "");
      const statusDetail = message.status_detail || "";
      const scheduledMessageId = message.scheduled_message_id ? String(message.scheduled_message_id) : "";
      const isQueuedScheduledMessage = Boolean(scheduledMessageId) && message.source === "scheduled" && message.status === "scheduled";
      const queuedDetail = isQueuedScheduledMessage
        ? `<div class="message-queue-detail">${escapeHtml(t("schedule.queued_for", { time: formatTime(message.occurred_at, true) }))}</div>`
        : "";
      const cancelScheduledAction = isQueuedScheduledMessage
        ? `<button class="message-inline-action" type="button" data-cancel-scheduled-id="${escapeHtml(scheduledMessageId)}">${escapeHtml(t("schedule.cancel"))}</button>`
        : "";
      const bubbleStyle =
        message.direction === "outbound" && message.identity_color
          ? ` style="--message-out:${escapeHtml(message.identity_color)}"`
          : "";
      const canReact = messageCanBeReactedTo(message);
      const messageId = message.id !== undefined && message.id !== null ? String(message.id) : "";
      const failureDetail =
        statusKind === "failed" || statusKind === "warning"
          ? `<div class="message-error ${statusKind}">
              <strong>${statusKind === "failed" ? escapeHtml(t("message.send_failed")) : escapeHtml(t("message.delivery_unconfirmed"))}</strong>
              <span>${escapeHtml(statusDetail || statusLabel)}</span>
            </div>`
          : "";
      return `
        ${divider}
        <article class="message-row ${message.direction} ${statusKind} ${canReact ? "reactable" : ""}" data-message-id="${escapeHtml(messageId)}"${bubbleStyle}>
          <div class="message-stack">
            <div class="message-bubble">
              ${voicemailLabel}
              ${attachments ? `<div class="${attachmentGridClass}">${attachments}</div>` : ""}
              ${message.text ? `<div class="message-text">${escapeHtml(message.text)}</div>` : ""}
              ${queuedDetail}
              ${failureDetail}
              <div class="message-meta">
                <span>${escapeHtml(message.from_display || phoneDisplay(message.from_number))}</span>
                <time>${escapeHtml(formatTime(message.occurred_at))}</time>
                <span class="message-status ${escapeHtml(statusKind)}" title="${escapeHtml(statusDetail)}">${escapeHtml(statusLabel)}</span>
                ${cancelScheduledAction}
              </div>
            </div>
            ${reactions}
          </div>
        </article>`;
      })
      .join("");
  restoreMessageMediaPlayback(mediaPlayback);
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
  const media = [...els.messages.querySelectorAll("img, video, audio")];
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

function messageById(id) {
  const targetId = String(id || "");
  if (!targetId) return null;
  return state.messages.find((message) => String(message.id) === targetId) || null;
}

function messageCanBeReactedTo(message) {
  if (!message?.id || message.direction !== "inbound") return false;
  if (message.source === "scheduled" || message.source === "optimistic") return false;
  if (!cleanReactionText(message.text || "")) return false;
  return !parseMessageReaction(message);
}

function reactionTargetFromEvent(event) {
  if (event.target.closest("a, button, input, textarea, select, audio, video, object, [data-lightbox-src], .message-reaction")) {
    return null;
  }
  const row = event.target.closest(".message-row[data-message-id]");
  if (!row) return null;
  return { row, message: messageById(row.dataset.messageId) };
}

function closeReactionMenu() {
  state.reactionTargetId = null;
  state.reactionLongPressTriggered = false;
  if (!els.reactionMenu) return;
  els.reactionMenu.classList.add("hidden");
  els.reactionMenu.style.removeProperty("left");
  els.reactionMenu.style.removeProperty("top");
  els.reactionMenu.style.removeProperty("visibility");
}

function reactionMenuIsOpen() {
  return Boolean(els.reactionMenu && !els.reactionMenu.classList.contains("hidden"));
}

function positionReactionMenu(row) {
  if (!els.reactionMenu || !row) return;
  const bubble = row.querySelector(".message-bubble") || row;
  const bubbleRect = bubble.getBoundingClientRect();
  els.reactionMenu.style.visibility = "hidden";
  els.reactionMenu.classList.remove("hidden");
  requestAnimationFrame(() => {
    const menuRect = els.reactionMenu.getBoundingClientRect();
    const gap = 8;
    let top = bubbleRect.top - menuRect.height - gap;
    if (top < gap) {
      top = bubbleRect.bottom + gap;
    }
    top = clamp(top, gap, Math.max(gap, window.innerHeight - menuRect.height - gap));
    const preferredLeft =
      row.classList.contains("outbound") ? bubbleRect.right - menuRect.width : bubbleRect.left;
    const left = clamp(preferredLeft, gap, Math.max(gap, window.innerWidth - menuRect.width - gap));
    els.reactionMenu.style.left = `${left}px`;
    els.reactionMenu.style.top = `${top}px`;
    els.reactionMenu.style.visibility = "";
  });
}

function openReactionMenu(row, message) {
  if (!els.reactionMenu) return;
  if (!messageCanBeReactedTo(message)) {
    if (!message || message.direction !== "inbound" || parseMessageReaction(message)) return;
    toast(t("message.react_requires_text"));
    return;
  }
  state.reactionTargetId = String(message.id);
  els.reactionMenu.innerHTML = MESSAGE_REACTION_OPTIONS.map(
    (option) => `
      <button class="reaction-option" type="button" role="menuitem" data-reaction-action="${escapeHtml(option.action)}" title="${escapeHtml(
        t(option.labelKey),
      )}" aria-label="${escapeHtml(t(option.labelKey))}">
        <span>${escapeHtml(option.icon)}</span>
      </button>`,
  ).join("");
  positionReactionMenu(row);
}

function reactionMessageText(action, message) {
  const targetText = cleanReactionText(message?.text || "");
  const verb = `${String(action || "").slice(0, 1).toUpperCase()}${String(action || "").slice(1)}`;
  return `${verb} “${targetText}”`;
}

function removeOptimisticMessage(optimisticId, scrollMode = "preserve") {
  const previousLength = state.messages.length;
  state.messages = state.messages.filter((message) => message.id !== optimisticId);
  if (state.messages.length !== previousLength) {
    renderMessages(state.messages, scrollMode);
  }
}

async function sendReaction(action) {
  const message = messageById(state.reactionTargetId);
  if (!messageCanBeReactedTo(message)) {
    closeReactionMenu();
    toast(t("message.react_requires_text"));
    return;
  }
  const draft = {
    conversation_id: state.currentConversationId,
    from_number: els.fromNumber.value,
    to_numbers: currentRecipients(),
    text: reactionMessageText(action, message),
    media_urls: [],
  };
  if (!draft.to_numbers.length) {
    toast(t("recipient.add_one"));
    return;
  }
  closeReactionMenu();
  const optimisticMessageId = applyOptimisticOutgoingMessage(draft, null, { scrollMode: "preserve" });
  try {
    const payload = await api("/api/messages", {
      method: "POST",
      body: JSON.stringify(draft),
    });
    playMessageSound("send");
    if (payload.conversation && Number(payload.conversation.id) === state.currentConversationId) {
      mergeConversationIntoLoadedState(payload.conversation);
      markLoadedConversationRead(state.currentConversationId);
      renderThreadHeader();
      renderConversations();
    }
    await loadConversations({ preserveScroll: true });
    if (state.currentConversationId === Number(draft.conversation_id)) {
      await refreshCurrentConversationStatus({ knownChanged: true, force: true });
    }
  } catch (error) {
    removeOptimisticMessage(optimisticMessageId, "preserve");
    showComposerError(error.message || t("message.react_failed"));
    toast(error.message || t("message.react_failed"));
  }
}

function clearReactionPressTimer() {
  if (state.reactionPressTimer) {
    clearTimeout(state.reactionPressTimer);
    state.reactionPressTimer = null;
  }
  state.reactionPressPointerId = null;
}

function beginMessageReactionPress(event) {
  if (event.button !== undefined && event.button !== 0) return;
  const target = reactionTargetFromEvent(event);
  if (!target || !messageCanBeReactedTo(target.message)) return;
  clearReactionPressTimer();
  state.reactionLongPressTriggered = false;
  state.reactionPressPointerId = event.pointerId;
  state.reactionPressStartX = event.clientX;
  state.reactionPressStartY = event.clientY;
  state.reactionPressTimer = setTimeout(() => {
    state.reactionLongPressTriggered = true;
    openReactionMenu(target.row, target.message);
  }, 520);
}

function moveMessageReactionPress(event) {
  if (state.reactionPressPointerId !== event.pointerId) return;
  const dx = event.clientX - state.reactionPressStartX;
  const dy = event.clientY - state.reactionPressStartY;
  if (Math.hypot(dx, dy) > 10) {
    clearReactionPressTimer();
  }
}

function endMessageReactionPress(event) {
  if (state.reactionPressPointerId !== event.pointerId) return;
  clearReactionPressTimer();
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
  const statsOpen = !els.statsModal.classList.contains("hidden");
  if (statsOpen) {
    if (event.key === "Escape") {
      closeStats();
      event.preventDefault();
    }
    return;
  }
  if (isContactNameModalOpen()) {
    if (event.key === "Escape") {
      closeContactNameModal({ restoreFocus: true });
      event.preventDefault();
    }
    return;
  }
  const scheduleOpen = !els.scheduleModal.classList.contains("hidden");
  if (scheduleOpen) {
    if (event.key === "Escape") {
      closeScheduleModal();
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
  if (event.key === "Escape" && !els.reactionMenu?.classList.contains("hidden")) {
    closeReactionMenu();
    event.preventDefault();
    return;
  }
  if (event.key === "Escape" && closeDetailsOverlay({ restoreFocus: true })) {
    event.preventDefault();
    return;
  }
  if (event.key === "Escape" && state.selectedConversationIds.size) {
    clearConversationSelection();
    event.preventDefault();
    return;
  }
  if (event.key === "Escape" && els.conversationSearch.value && !isEditableKeyTarget(event.target)) {
    clearConversationSearch();
    event.preventDefault();
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

function conversationPayloadMatchesState(payload) {
  return JSON.stringify(payload?.conversation || null) === JSON.stringify(state.currentConversation || null);
}

function messagePayloadMatchesState(payload) {
  return (
    JSON.stringify(payload?.messages || []) === JSON.stringify(state.messages || []) &&
    Boolean(payload?.has_more) === Boolean(state.hasMoreMessages) &&
    Number(payload?.older_count || 0) === Number(state.olderCount || 0)
  );
}

function inboundSoundKey(occurredAt, id) {
  const timestamp = String(occurredAt || "");
  if (!timestamp) return "";
  return `${timestamp}|${String(id || 0).padStart(12, "0")}`;
}

function latestInboundSoundKeyFromConversations(conversations = []) {
  return conversations.reduce((latest, conversation) => {
    if (String(conversation.last_direction || "").toLowerCase() !== "inbound") return latest;
    const key = inboundSoundKey(conversation.last_occurred_at || conversation.sort_at, conversation.last_message_id || conversation.id);
    return key > latest ? key : latest;
  }, "");
}

function latestInboundSoundKeyFromMessages(messages = []) {
  return messages.reduce((latest, message) => {
    if (String(message.direction || "").toLowerCase() !== "inbound") return latest;
    const key = inboundSoundKey(message.occurred_at, message.id);
    return key > latest ? key : latest;
  }, "");
}

function trackInboundSoundKey(key, { play = false } = {}) {
  if (!key) return;
  if (!state.receiveSoundPrimed) {
    state.latestInboundSoundKey = key;
    state.receiveSoundPrimed = true;
    return;
  }
  if (key <= state.latestInboundSoundKey) return;
  state.latestInboundSoundKey = key;
  if (play) {
    playMessageSound("receive");
  }
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
        soundForNew: true,
      });
    }
    if (conversationChanged && state.currentConversationId === conversationId) {
      await refreshCurrentConversationStatus({ knownChanged: true, force });
    }
  } finally {
    state.autoRefreshInFlight = false;
    scheduleAutoRefresh();
  }
}

async function refreshCurrentConversationStatus({ knownChanged = false, force = false } = {}) {
  if (!state.currentConversationId || state.statusPollInFlight) return;
  state.statusPollInFlight = true;
  const conversationId = state.currentConversationId;
  const shouldStickToBottom = isNearMessageBottom();
  const limit = Math.max(80, state.messages.length || 0);
  try {
    if (!knownChanged && !force) {
      const refreshPayload = await api(`/api/refresh${refreshQuery()}`);
      const previous = state.refreshTokens || {};
      const next = refreshPayload.tokens || {};
      if (Object.keys(previous).length && previous.conversation === next.conversation) {
        state.lastThreadRefreshAt = Date.now();
        return;
      }
      state.refreshTokens = next;
      if (!Object.keys(previous).length && !next.conversation) {
        state.lastThreadRefreshAt = Date.now();
        return;
      }
    }
    const payload = await api(`/api/conversations/${conversationId}/messages?limit=${limit}`);
    if (state.currentConversationId !== conversationId) return;
    const conversationChanged = force || !conversationPayloadMatchesState(payload);
    const messagesChanged = force || !messagePayloadMatchesState(payload);
    if (!conversationChanged && !messagesChanged) {
      state.lastThreadRefreshAt = Date.now();
      return;
    }
    mergeConversationIntoLoadedState(payload.conversation);
    state.lastThreadRefreshAt = Date.now();
    if (messagesChanged) {
      trackInboundSoundKey(latestInboundSoundKeyFromMessages(payload.messages), { play: true });
      state.messages = payload.messages;
      state.hasMoreMessages = payload.has_more;
      state.olderCount = payload.older_count;
    }
    if (conversationChanged || messagesChanged) {
      renderThreadHeader();
    }
    if (messagesChanged) {
      renderMessages(state.messages, shouldStickToBottom ? "bottom" : "preserve");
    }
    if (payload.conversation) {
      renderConversations();
    }
  } finally {
    state.statusPollInFlight = false;
    scheduleStatusPoll();
  }
}

function canUsePullRefresh() {
  return (
    isDetailsOverlayLayout() &&
    !document.body.classList.contains("mobile-thread-open") &&
    !document.body.classList.contains("details-overlay-open") &&
    !state.selectedConversationIds.size &&
    !state.isRefreshingFromPull &&
    els.conversationList.scrollTop <= 0
  );
}

function nativePullRefreshBridge() {
  const bridge = window.SwitchboardAndroid;
  return bridge && typeof bridge.setPullRefreshEnabled === "function" ? bridge : null;
}

function syncNativePullRefreshEnabled() {
  const bridge = nativePullRefreshBridge();
  if (!bridge) return;
  bridge.setPullRefreshEnabled(canUsePullRefresh() || state.isRefreshingFromPull);
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
    await pollForChanges({ force });
  } finally {
    state.foregroundRefreshInFlight = false;
  }
}

function setPullRefreshState(status, distance = 0) {
  if (!els.pullRefreshIndicator) return;
  if (nativePullRefreshBridge()) {
    els.pullRefreshIndicator.classList.remove("visible", "ready", "refreshing");
    els.pullRefreshIndicator.textContent = "";
    els.pullRefreshIndicator.style.transform = "translate3d(-50%, 0, 0)";
    return;
  }
  const visible = status !== "idle";
  const offset = Math.min(Math.max(distance, 0), 78);
  els.pullRefreshIndicator.classList.toggle("visible", visible);
  els.pullRefreshIndicator.classList.toggle("ready", status === "ready");
  els.pullRefreshIndicator.classList.toggle("refreshing", status === "refreshing");
  els.pullRefreshIndicator.textContent = "";
  els.pullRefreshIndicator.style.transform = visible
    ? `translate3d(-50%, ${offset}px, 0)`
    : "translate3d(-50%, 0, 0)";
}

async function refreshFromPull() {
  if (state.isRefreshingFromPull) return true;
  state.isRefreshingFromPull = true;
  syncNativePullRefreshEnabled();
  setPullRefreshState("refreshing", 72);
  try {
    await refreshForegroundData({ force: true });
    await loadConversations({ append: false, preserveScroll: true });
    toast(t("refresh.updated"));
  } catch (error) {
    toast(error.message);
  } finally {
    state.isRefreshingFromPull = false;
    syncNativePullRefreshEnabled();
    window.setTimeout(() => setPullRefreshState("idle"), 250);
  }
  return true;
}

window.textingRefreshFromNativePull = () => {
  if (!canUsePullRefresh()) return false;
  refreshFromPull();
  return true;
};

function beginPullRefresh(event) {
  if (!event.touches || event.touches.length !== 1 || !isDetailsOverlayLayout()) return;
  if (nativePullRefreshBridge()) return;
  if (!canUsePullRefresh()) return;
  state.pullRefresh = {
    startY: event.touches[0].clientY,
    distance: 0,
    active: false,
  };
}

function movePullRefresh(event) {
  const pull = state.pullRefresh;
  if (!pull || !event.touches || event.touches.length !== 1) return;
  const dy = event.touches[0].clientY - pull.startY;
  if (dy <= 0) return;
  if (els.conversationList.scrollTop > 0) {
    state.pullRefresh = null;
    setPullRefreshState("idle");
    return;
  }
  pull.distance = Math.pow(dy, 0.82);
  pull.active = pull.distance > 12;
  if (!pull.active) return;
  event.preventDefault();
  setPullRefreshState(pull.distance > 68 ? "ready" : "pulling", pull.distance);
}

function endPullRefresh() {
  const pull = state.pullRefresh;
  state.pullRefresh = null;
  if (!pull?.active) {
    setPullRefreshState("idle");
    return;
  }
  if (pull.distance > 68) {
    refreshFromPull();
  } else {
    setPullRefreshState("idle");
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
    syncDetailsLayout();
    applyColumnWidths();
    updateComposerOffset();
  });
  window.visualViewport?.addEventListener("resize", updateComposerOffset);
  if (window.ResizeObserver) {
    state.layoutResizeObserver = new ResizeObserver(() => updateComposerOffset());
    [els.threadHeader, els.messages, els.composer].filter(Boolean).forEach((element) => {
      state.layoutResizeObserver.observe(element);
    });
  }
}

async function loadConversations({ append = false, preserveScroll = false, limit = 80, soundForNew = false } = {}) {
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
      trackInboundSoundKey(latestInboundSoundKeyFromConversations(state.conversations), { play: soundForNew });
    }
    state.hasMoreConversations = payload.has_more;
  } finally {
    if (requestSeq === state.conversationRequestSeq) {
      state.isLoadingConversations = false;
      renderConversations();
      if (!append) {
        els.conversationList.scrollTop = preserveScroll ? previousScrollTop : 0;
      }
      syncNativePullRefreshEnabled();
    }
  }
}

async function openConversation(id, options = {}) {
  const updateHistory = options.updateHistory !== false;
  clearStatusPoll();
  const conversationId = Number(id);
  if (!conversationId) return;
  const requestSeq = ++state.openConversationSeq;
  const previousConversationId = state.currentConversationId;
  const shouldOpenBeforeLoad = isDesktopLayout() || document.body.classList.contains("mobile-thread-open");
  const selectionChanged = previousConversationId !== conversationId;
  if (selectionChanged) {
    state.currentConversationId = conversationId;
    renderConversations();
    scrollActiveConversationIntoView();
  }
  if (shouldOpenBeforeLoad) {
    setMobileThreadOpen(true);
  }
  let payload;
  try {
    payload = await api(`/api/conversations/${id}/messages?limit=80`);
  } catch (error) {
    if (requestSeq === state.openConversationSeq && selectionChanged) {
      state.currentConversationId = previousConversationId;
      renderConversations();
      scrollActiveConversationIntoView();
    }
    throw error;
  }
  if (requestSeq !== state.openConversationSeq) return;
  state.currentConversationId = conversationId;
  state.currentConversation = payload.conversation;
  state.messages = payload.messages;
  state.hasMoreMessages = payload.has_more;
  state.olderCount = payload.older_count;
  trackInboundSoundKey(latestInboundSoundKeyFromMessages(state.messages));
  state.lastThreadRefreshAt = Date.now();
  selectFromNumber(preferredReplyIdentity(state.currentConversation, state.messages));
  renderConversations();
  scrollActiveConversationIntoView();
  renderThreadHeader();
  renderMessages(state.messages, "bottom");
  if (updateHistory) {
    pushMobileThreadState({ conversationId });
  }
  if (!shouldOpenBeforeLoad) {
    setMobileThreadOpen(true);
  }
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
  const listIndex = state.conversations.findIndex((conversation) => Number(conversation.id) === conversationId);
  const previousListConversation = listIndex >= 0 ? { ...state.conversations[listIndex] } : null;
  const previousCurrentConversation =
    state.currentConversation && Number(state.currentConversation.id) === conversationId ? { ...state.currentConversation } : null;
  const timestamp = localIsoTimestamp();
  const optimisticBase = previousCurrentConversation || previousListConversation || { id: conversationId };
  const lastMessageTime =
    optimisticBase.last_occurred_at || optimisticBase.last_message_at || optimisticBase.sort_at || timestamp;
  mergeConversationIntoLoadedState({
    ...optimisticBase,
    id: conversationId,
    dealt_with_at: dealt ? lastMessageTime : optimisticBase.dealt_with_at || "",
    manual_unread_at: dealt ? null : timestamp,
    needs_attention: dealt ? 0 : 1,
  });
  if (state.conversationCategory === "unread" && dealt) {
    state.conversations = state.conversations.filter((conversation) => Number(conversation.id) !== conversationId);
  }
  renderConversations();
  renderThreadHeader();
  els.dealtButton.disabled = true;
  try {
    const payload = await api(`/api/conversations/${conversationId}/dealt`, {
      method: "POST",
      body: JSON.stringify({ dealt }),
    });
    const isCurrent = mergeConversationIntoLoadedState(payload.conversation);
    state.bootstrap = await api("/api/bootstrap");
    applyRuntimeSettings();
    renderBootstrap();
    if (state.conversationCategory === "unread" && dealt) {
      state.conversations = state.conversations.filter((conversation) => Number(conversation.id) !== conversationId);
    }
    renderConversations();
    if (isCurrent) {
      renderThreadHeader();
    }
    if (!silent) {
      toast(dealt ? t("conversation.marked_read") : t("conversation.marked_unread"));
    }
  } catch (error) {
    const currentIndex = state.conversations.findIndex((conversation) => Number(conversation.id) === conversationId);
    if (previousListConversation) {
      if (currentIndex >= 0) {
        state.conversations[currentIndex] = previousListConversation;
      } else {
        const insertAt = listIndex >= 0 ? Math.min(listIndex, state.conversations.length) : 0;
        state.conversations.splice(insertAt, 0, previousListConversation);
      }
    } else if (currentIndex >= 0) {
      state.conversations.splice(currentIndex, 1);
    }
    if (state.currentConversationId === conversationId) {
      state.currentConversation = previousCurrentConversation;
    }
    toast(error.message);
    renderConversations();
    renderThreadHeader();
  }
}

async function toggleCurrentConversationRead() {
  const conversation =
    state.currentConversation && Number(state.currentConversation.id) === Number(state.currentConversationId)
      ? state.currentConversation
      : state.conversations.find((item) => Number(item.id) === Number(state.currentConversationId));
  const shouldMarkRead = !conversationIsRead(conversation);
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
  state.openConversationSeq += 1;
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
  if (!isUsableDraftPhone(phone)) {
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

function commitPendingRecipientInput() {
  if (state.currentConversation && Number(state.currentConversation.id) === Number(state.currentConversationId)) return true;
  const raw = els.recipientInput.value.trim();
  if (!raw) return true;
  const phone = normalizeDraftPhone(raw);
  if (!isUsableDraftPhone(phone)) {
    toast(t("recipient.needs_phone"));
    return false;
  }
  addRecipient(phone);
  return true;
}

function currentRecipients() {
  if (state.currentConversation && Number(state.currentConversation.id) === Number(state.currentConversationId)) {
    return (state.currentConversation.participants || [])
      .filter((p) => p.role === "participant")
      .map((p) => p.phone_number);
  }
  return state.recipientDraft;
}

function currentComposerDraft() {
  const text = els.messageText.value.trim();
  const mediaUrls = els.mediaUrls.value
    .split(/[\n,]+/)
    .map((x) => x.trim())
    .filter(Boolean);
  mediaUrls.push(...state.uploadedMedia.map((item) => item.url).filter(Boolean));
  return {
    conversation_id: state.currentConversationId,
    from_number: els.fromNumber.value,
    to_numbers: currentRecipients(),
    text,
    media_urls: mediaUrls,
  };
}

function validateComposerDraft(draft) {
  if (!draft.to_numbers.length) {
    toast(t("recipient.add_one"));
    return false;
  }
  if (!draft.text && !draft.media_urls.length) {
    showComposerError(t("composer.requires_content"));
    toast(t("composer.requires_content"));
    return false;
  }
  return true;
}

function clearComposerDraft() {
  els.messageText.value = "";
  els.mediaUrls.value = "";
  state.uploadedMedia = [];
  renderUploadedMedia();
  updateMessageCounter();
}

function composerSnapshot() {
  return {
    text: els.messageText.value,
    mediaUrls: els.mediaUrls.value,
    uploadedMedia: state.uploadedMedia.map((item) => ({ ...item })),
  };
}

function composerIsEmpty() {
  return !els.messageText.value && !els.mediaUrls.value && !state.uploadedMedia.length;
}

function restoreComposerSnapshot(snapshot, { onlyIfEmpty = true } = {}) {
  if (!snapshot || (onlyIfEmpty && !composerIsEmpty())) return;
  els.messageText.value = snapshot.text || "";
  els.mediaUrls.value = snapshot.mediaUrls || "";
  state.uploadedMedia = (snapshot.uploadedMedia || []).map((item) => ({ ...item }));
  renderUploadedMedia();
  updateMessageCounter();
}

function filenameFromUrl(value) {
  const raw = String(value || "");
  if (!raw) return "";
  try {
    const url = new URL(raw, window.location.href);
    return decodeURIComponent(url.pathname.split("/").filter(Boolean).pop() || "");
  } catch {
    return decodeURIComponent(raw.split(/[?#]/, 1)[0].split("/").filter(Boolean).pop() || "");
  }
}

function localIsoTimestamp(date = new Date()) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(
    date.getMinutes(),
  )}:${pad(date.getSeconds())}`;
}

function optimisticAttachmentsFromDraft(draft, uploadedMedia = []) {
  const uploadedByUrl = new Map(uploadedMedia.filter((item) => item.url).map((item) => [item.url, item]));
  return draft.media_urls.map((url, index) => {
    const upload = uploadedByUrl.get(url) || {};
    return {
      id: `optimistic-attachment-${Date.now()}-${index}`,
      remote_url: url,
      filename: upload.original_filename || upload.filename || filenameFromUrl(url) || t("attachment.file"),
      content_type: upload.content_type || "",
      source: upload.url ? "upload" : "remote",
    };
  });
}

function applyOptimisticOutgoingMessage(draft, snapshot, { scrollMode = "bottom" } = {}) {
  const timestamp = localIsoTimestamp();
  const identity = activeIdentity();
  const optimisticId = `optimistic-${Date.now()}`;
  const message = {
    id: optimisticId,
    conversation_id: draft.conversation_id,
    direction: "outbound",
    from_number: draft.from_number,
    from_display: identity?.label || phoneDisplay(draft.from_number),
    to_numbers: [...draft.to_numbers],
    text: draft.text,
    attachments: optimisticAttachmentsFromDraft(draft, snapshot?.uploadedMedia || []),
    message_type: draft.media_urls.length ? "mms" : "sms",
    occurred_at: timestamp,
    source: "optimistic",
    status: "sending",
    status_kind: "neutral",
    status_label: t("status.sending"),
    status_detail: "",
    identity_label: identity?.label || "",
    identity_color: identity?.color || "",
  };
  state.messages = [...state.messages, message];
  renderMessages(state.messages, scrollMode);
  if (draft.conversation_id) {
    mergeConversationIntoLoadedState({
      id: draft.conversation_id,
      last_text: draft.text,
      last_message_type: message.message_type,
      last_direction: "outbound",
      last_occurred_at: timestamp,
      sort_at: timestamp,
      last_status: "sending",
      last_status_kind: "neutral",
      last_status_label: t("status.sending"),
      last_status_detail: "",
      needs_attention: 0,
      manual_unread_at: null,
    });
    markLoadedConversationRead(draft.conversation_id);
    renderThreadHeader();
    renderConversations();
  }
  return optimisticId;
}

function markOptimisticMessageFailed(optimisticId, detail, conversationId) {
  const failureDetail = detail || t("message.send_failed");
  const hasOptimisticMessage = state.messages.some((message) => message.id === optimisticId);
  state.messages = state.messages.map((message) => {
    if (message.id !== optimisticId) return message;
    return {
      ...message,
      status: "delivery_failed",
      status_kind: "failed",
      status_label: t("status.delivery_failed"),
      status_detail: failureDetail,
    };
  });
  if (hasOptimisticMessage) {
    renderMessages(state.messages, "bottom");
  }
  if (conversationId) {
    mergeConversationIntoLoadedState({
      id: conversationId,
      last_status: "delivery_failed",
      last_status_kind: "failed",
      last_status_label: t("status.delivery_failed"),
      last_status_detail: failureDetail,
    });
    renderThreadHeader();
    renderConversations();
  }
}

async function sendCurrentMessage() {
  if (!commitPendingRecipientInput()) return;
  const draft = currentComposerDraft();
  if (!validateComposerDraft(draft)) return;
  const snapshot = composerSnapshot();
  const optimisticMessageId = applyOptimisticOutgoingMessage(draft, snapshot);
  clearComposerDraft();
  showComposerError("");
  els.sendButton.disabled = true;
  try {
    const payload = await api("/api/messages", {
      method: "POST",
      body: JSON.stringify(draft),
    });
    playMessageSound("send");
    if (payload.conversation && Number(payload.conversation.id) === state.currentConversationId) {
      mergeConversationIntoLoadedState(payload.conversation);
      markLoadedConversationRead(state.currentConversationId);
      renderThreadHeader();
      renderConversations();
    } else if (state.currentConversationId) {
      markLoadedConversationRead(state.currentConversationId);
      renderThreadHeader();
      renderConversations();
    }
    await loadConversations({ preserveScroll: true });
    if (state.currentConversationId) {
      await openConversation(state.currentConversationId);
    } else {
      const first = state.conversations[0];
      if (first) await openConversation(first.id);
    }
  } catch (error) {
    markOptimisticMessageFailed(optimisticMessageId, error.message, draft.conversation_id);
    restoreComposerSnapshot(snapshot);
    showComposerError(error.message);
    toast(error.message);
  } finally {
    els.sendButton.disabled = false;
  }
}

function dateTimeLocalValue(date) {
  const pad = (value) => String(value).padStart(2, "0");
  return [
    date.getFullYear(),
    "-",
    pad(date.getMonth() + 1),
    "-",
    pad(date.getDate()),
    "T",
    pad(date.getHours()),
    ":",
    pad(date.getMinutes()),
  ].join("");
}

function defaultScheduleDate() {
  const date = new Date(Date.now() + 60 * 60 * 1000);
  date.setSeconds(0, 0);
  const minutes = date.getMinutes();
  const rounded = Math.ceil(minutes / 5) * 5;
  date.setMinutes(rounded);
  return date;
}

function savedScheduleTime() {
  const value = localStorage.getItem(SCHEDULE_TIME_KEY) || "";
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime()) || date <= new Date()) return "";
  return value;
}

function rememberScheduleTime(value) {
  if (value) {
    localStorage.setItem(SCHEDULE_TIME_KEY, value);
  }
}

function setScheduleModalOpen(open) {
  els.scheduleModal.classList.toggle("hidden", !open);
  if (open) {
    const defaultDate = defaultScheduleDate();
    els.scheduleTime.min = dateTimeLocalValue(new Date(Date.now() + 60 * 1000));
    const activeValue = els.scheduleTime.value || savedScheduleTime();
    if (activeValue && new Date(activeValue) > new Date()) {
      els.scheduleTime.value = activeValue;
    } else {
      els.scheduleTime.value = dateTimeLocalValue(defaultDate);
    }
    requestAnimationFrame(() => els.scheduleTime.focus());
  }
}

function openScheduleModal() {
  if (!commitPendingRecipientInput()) return;
  const draft = currentComposerDraft();
  if (!validateComposerDraft(draft)) return;
  showComposerError("");
  setScheduleModalOpen(true);
}

function closeScheduleModal() {
  setScheduleModalOpen(false);
}

async function scheduleCurrentMessage(event) {
  event.preventDefault();
  if (!commitPendingRecipientInput()) return;
  const draft = currentComposerDraft();
  if (!validateComposerDraft(draft)) return;
  if (!els.scheduleTime.value) {
    toast(t("schedule.choose_time"));
    return;
  }
  const scheduledDate = new Date(els.scheduleTime.value);
  if (Number.isNaN(scheduledDate.getTime()) || scheduledDate <= new Date()) {
    toast(t("schedule.future_time"));
    return;
  }
  showComposerError("");
  els.sendButton.disabled = true;
  try {
    const payload = await api("/api/messages/schedule", {
      method: "POST",
      body: JSON.stringify({
        ...draft,
        scheduled_for: scheduledDate.toISOString(),
      }),
    });
    rememberScheduleTime(els.scheduleTime.value);
    closeScheduleModal();
    clearComposerDraft();
    await loadConversations({ preserveScroll: true });
    if (payload.conversation_id) {
      await openConversation(payload.conversation_id);
    }
    toast(t("schedule.queued"));
  } catch (error) {
    showComposerError(error.message);
    toast(error.message);
  } finally {
    els.sendButton.disabled = false;
  }
}

async function cancelScheduledMessage(scheduledId, button) {
  const id = Number(scheduledId);
  if (!id) return;
  if (button) button.disabled = true;
  try {
    const payload = await api(`/api/messages/schedule/${encodeURIComponent(id)}/cancel`, {
      method: "POST",
      body: "{}",
    });
    toast(t("schedule.cancelled"));
    await loadConversations({
      preserveScroll: true,
      limit: Math.max(80, state.conversations.length || 0),
    });
    const conversationId = Number(payload.conversation_id || state.currentConversationId || 0);
    if (conversationId && state.currentConversationId === conversationId) {
      await refreshCurrentConversationStatus();
    }
  } catch (error) {
    toast(error.message);
    if (button) button.disabled = false;
  }
}

function clearSendPressTimer() {
  if (state.sendPressTimer) {
    clearTimeout(state.sendPressTimer);
    state.sendPressTimer = null;
  }
}

function setSendSchedulingIndicator(active) {
  els.sendButton.classList.toggle("is-scheduling", active);
  els.sendButton.textContent = active ? SCHEDULE_SEND_SYMBOL : SEND_NOW_SYMBOL;
  const label = active ? t("schedule.title") : t("composer.send");
  els.sendButton.title = label;
  els.sendButton.setAttribute("aria-label", label);
}

function startSendPress(event) {
  if (event.pointerType === "mouse" && event.button !== 0) return;
  if (els.sendButton.disabled) return;
  clearSendPressTimer();
  state.sendHoldTriggered = false;
  setSendSchedulingIndicator(true);
  state.sendPressTimer = window.setTimeout(() => {
    state.sendPressTimer = null;
    state.sendHoldTriggered = true;
    setSendSchedulingIndicator(false);
    openScheduleModal();
  }, SEND_HOLD_MS);
}

function endSendPress() {
  clearSendPressTimer();
  if (!state.sendHoldTriggered) {
    setSendSchedulingIndicator(false);
  }
}

async function saveIdentity(card) {
  const id = card.dataset.id;
  const label = card.querySelector(".identity-label").value;
  const color = card.querySelector(".identity-color").value;
  const autoreplyEnabled = card.querySelector(".identity-autoreply-enabled").checked;
  const autoreplyMessage = card.querySelector(".identity-autoreply-message").value;
  const autoreplyCooldownHours = card.querySelector(".identity-autoreply-cooldown-hours").value;
  const voiceForwardingEnabled = card.querySelector(".identity-voice-forwarding-enabled").checked;
  const voiceForwardToNumber = card.querySelector(".identity-voice-forward-to").value;
  const voiceForwardTimeoutSeconds = card.querySelector(".identity-voice-forward-timeout").value;
  const voiceVoicemailEnabled = card.querySelector(".identity-voice-voicemail-enabled").checked;
  const voiceVoicemailGreeting = card.querySelector(".identity-voice-greeting").value;
  const voiceVoicemailGreetingMediaUrl = card.querySelector(".identity-voice-greeting-media-url").value;
  try {
    const payload = await api(`/api/identities/${id}`, {
      method: "PUT",
      body: JSON.stringify({
        label,
        color,
        is_active: true,
        autoreply_enabled: autoreplyEnabled,
        autoreply_message: autoreplyMessage,
        autoreply_cooldown_hours: autoreplyCooldownHours,
        voice_forwarding_enabled: voiceForwardingEnabled,
        voice_forward_to_number: voiceForwardToNumber,
        voice_forward_timeout_seconds: voiceForwardTimeoutSeconds,
        voice_voicemail_enabled: voiceVoicemailEnabled,
        voice_voicemail_greeting: voiceVoicemailGreeting,
        voice_voicemail_greeting_media_url: voiceVoicemailGreetingMediaUrl,
      }),
    });
    const index = state.bootstrap.identities.findIndex((item) => item.id === payload.identity.id);
    if (index >= 0) state.bootstrap.identities[index] = payload.identity;
    applyIdentityToLoadedState(payload.identity);
    renderBootstrap({ forceIdentities: true });
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
  const form = isContactNameModalOpen() ? els.contactNameModalForm : els.contactNameForm;
  const input = isContactNameModalOpen() ? els.contactNameModalInput : els.contactNameInput;
  const displayName = input.value.trim();
  if (!participant) return;
  if (!displayName) {
    toast(t("contact.enter_name"));
    return;
  }
  const controls = form.querySelectorAll("input, button");
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
      mergeConversationIntoLoadedState(payload.conversation);
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
  document.addEventListener("pointerdown", unlockAudio, { once: true, passive: true });
  document.addEventListener("keydown", unlockAudio, { once: true });
  bindColumnResizers();
  els.mobileBackButton.addEventListener("click", () => {
    closeMobileThread();
  });
  els.themeToggle.addEventListener("click", () => {
    const current = document.documentElement.dataset.theme === "dark" ? "dark" : "light";
    applyTheme(current === "dark" ? "light" : "dark");
  });
  els.settingsButton.addEventListener("click", () => {
    if (confirmDiscardIdentityChanges()) openSettings();
  });
  els.settingsClose.addEventListener("click", closeSettings);
  els.settingsCancel.addEventListener("click", closeSettings);
  els.settingsModal.addEventListener("click", (event) => {
    if (event.target === els.settingsModal) closeSettings();
  });
  els.settingsNav.addEventListener("click", (event) => {
    const button = event.target.closest("[data-settings-anchor]");
    if (!button) return;
    els.settingsNav.querySelectorAll(".settings-nav-button").forEach((item) => item.classList.toggle("active", item === button));
    document.getElementById(button.dataset.settingsAnchor)?.scrollIntoView({ block: "start", behavior: "smooth" });
  });
	  els.securitySettings.addEventListener("click", (event) => {
	    const accountButton = event.target.closest("[data-account-action]");
	    if (accountButton) {
	      handleAccountAction(accountButton.dataset.accountAction).catch((error) => toast(error.message));
	      return;
	    }
	    const copyButton = event.target.closest("[data-copy-two-factor]");
	    if (copyButton) {
      copyTwoFactorValue(copyButton.dataset.copyTwoFactor || "");
      return;
    }
    const actionButton = event.target.closest("[data-two-factor-action]");
    if (actionButton) {
      handleTwoFactorAction(actionButton.dataset.twoFactorAction).catch((error) => toast(error.message));
    }
  });
  els.settingsSections.addEventListener("change", (event) => {
    const voiceGreetingFile = event.target.closest(".setting-voice-greeting-file");
    if (voiceGreetingFile) uploadVoiceGreetingFile(voiceGreetingFile);
  });
  els.settingsForm.addEventListener("submit", saveSettings);
  els.databaseDownloadButton.addEventListener("click", downloadDatabase);
  els.logoutButton.addEventListener("click", signOut);
  els.statsButton.addEventListener("click", openStats);
  els.statsClose.addEventListener("click", closeStats);
  els.statsPeriod.addEventListener("change", () => {
    loadStats();
  });
  els.statsModal.addEventListener("click", (event) => {
    if (event.target === els.statsModal) closeStats();
  });
  els.statStrip.addEventListener("click", (event) => {
    if (event.target.closest(".stat")) openStats();
  });
  els.selectionCancelButton.addEventListener("click", clearConversationSelection);
  els.bulkReadButton.addEventListener("click", () => bulkConversationAction("read"));
  els.bulkUnreadButton.addEventListener("click", () => bulkConversationAction("unread"));
  els.bulkHideButton.addEventListener("click", () => bulkConversationAction("hide"));
  els.conversationList.addEventListener("click", (event) => {
    if (Date.now() < state.suppressConversationClickUntil) {
      event.preventDefault();
      return;
    }
    const button = event.target.closest(".conversation-item");
    if (!button) return;
    if (event.target.closest(".conversation-selector") || state.selectedConversationIds.size) {
      event.preventDefault();
      toggleConversationSelection(button.dataset.id);
      return;
    }
    if (state.threadNavigationInFlight) return;
    state.threadNavigationInFlight = true;
    openConversation(button.dataset.id)
      .catch((error) => toast(error.message))
      .finally(() => {
        state.threadNavigationInFlight = false;
      });
  });
  els.conversationList.addEventListener("pointerdown", beginConversationGesture);
  els.conversationList.addEventListener("pointermove", moveConversationGesture);
  els.conversationList.addEventListener("pointerup", endConversationGesture);
  els.conversationList.addEventListener("pointercancel", (event) => {
    clearConversationPressTimer();
    resetConversationSwipe(event);
  });
  els.conversationList.addEventListener("touchstart", beginPullRefresh, { passive: true });
  els.conversationList.addEventListener("touchmove", movePullRefresh, { passive: false });
  els.conversationList.addEventListener("touchend", endPullRefresh);
  els.conversationList.addEventListener("touchcancel", endPullRefresh);
  els.conversationSearch.addEventListener("input", () => {
    scheduleConversationSearchLoad();
  });
  els.conversationSearch.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (clearConversationSearch()) {
      event.preventDefault();
      event.stopPropagation();
    }
  });
  els.conversationSearchClear.addEventListener("click", () => {
    clearConversationSearch({ focus: true });
  });
  els.categoryTabs.forEach((tab) => {
    tab.addEventListener("click", async () => {
      clearConversationSelection();
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
    syncNativePullRefreshEnabled();
    const remaining =
      els.conversationList.scrollHeight - els.conversationList.scrollTop - els.conversationList.clientHeight;
    if (remaining < 600) {
      loadConversations({ append: true }).catch((error) => toast(error.message));
    }
  });
  els.newConversationButton.addEventListener("click", startNewConversation);
  els.mobilePanelButton?.addEventListener("click", toggleDetailsPanel);
  els.threadTitle.addEventListener("click", openContactRename);
  els.threadTitle.addEventListener("keydown", (event) => {
    if (!["Enter", " "].includes(event.key)) return;
    if (!currentDirectParticipant()) return;
    event.preventDefault();
    openContactRename();
  });
  els.contactNameToggle.addEventListener("click", openContactRename);
  els.contactNameCancel.addEventListener("click", () => setContactNameEditor(false));
  els.contactNameForm.addEventListener("submit", (event) => {
    event.preventDefault();
    saveCurrentContactName();
  });
  els.contactNameModalClose?.addEventListener("click", () => closeContactNameModal({ restoreFocus: true }));
  els.contactNameModalCancel?.addEventListener("click", () => closeContactNameModal({ restoreFocus: true }));
  els.contactNameModal?.addEventListener("click", (event) => {
    if (event.target === els.contactNameModal) closeContactNameModal({ restoreFocus: true });
  });
  els.contactNameModalForm?.addEventListener("submit", (event) => {
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
  els.sendButton.addEventListener("pointerdown", startSendPress);
  els.sendButton.addEventListener("pointerup", endSendPress);
  els.sendButton.addEventListener("pointerleave", endSendPress);
  els.sendButton.addEventListener("pointercancel", endSendPress);
  els.sendButton.addEventListener("contextmenu", (event) => event.preventDefault());
  els.sendButton.addEventListener("click", (event) => {
    if (state.sendHoldTriggered) {
      event.preventDefault();
      state.sendHoldTriggered = false;
      return;
    }
    sendCurrentMessage();
  });
  els.scheduleForm.addEventListener("submit", scheduleCurrentMessage);
  els.scheduleClose.addEventListener("click", closeScheduleModal);
  els.scheduleCancel.addEventListener("click", closeScheduleModal);
  els.scheduleModal.addEventListener("click", (event) => {
    if (event.target === els.scheduleModal) closeScheduleModal();
  });
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
  els.messages.addEventListener("pointerdown", beginMessageReactionPress);
  els.messages.addEventListener("pointermove", moveMessageReactionPress);
  els.messages.addEventListener("pointerup", endMessageReactionPress);
  els.messages.addEventListener("pointerleave", endMessageReactionPress);
  els.messages.addEventListener("pointercancel", endMessageReactionPress);
  els.messages.addEventListener("click", (event) => {
    if (event.target.closest("#loadOlderButton")) {
      loadOlderMessages().catch((error) => toast(error.message));
      return;
    }
    const cancelScheduledButton = event.target.closest("[data-cancel-scheduled-id]");
    if (cancelScheduledButton) {
      event.preventDefault();
      cancelScheduledMessage(cancelScheduledButton.dataset.cancelScheduledId, cancelScheduledButton).catch((error) => toast(error.message));
      return;
    }
    const imageLink = event.target.closest("[data-lightbox-src]");
    if (imageLink) {
      event.preventDefault();
      openLightbox(imageLink.dataset.lightboxSrc);
      return;
    }
    if (state.reactionLongPressTriggered) {
      event.preventDefault();
      state.reactionLongPressTriggered = false;
      return;
    }
    const reactionTarget = reactionTargetFromEvent(event);
    if (reactionTarget) {
      event.preventDefault();
      if (reactionMenuIsOpen()) {
        closeReactionMenu();
        return;
      }
      openReactionMenu(reactionTarget.row, reactionTarget.message);
    }
  });
  els.reactionMenu?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-reaction-action]");
    if (!button) return;
    event.preventDefault();
    sendReaction(button.dataset.reactionAction).catch((error) => {
      toast(error.message || t("message.react_failed"));
    });
  });
  document.addEventListener("pointerdown", (event) => {
    if (!reactionMenuIsOpen()) return;
    if (event.target.closest("#reactionMenu")) return;
    if (event.target.closest(".message-row[data-message-id]")) return;
    closeReactionMenu();
  });
  document.addEventListener("click", (event) => {
    if (!reactionMenuIsOpen()) return;
    if (event.target.closest("#reactionMenu")) return;
    if (event.target.closest(".message-row[data-message-id]")) return;
    closeReactionMenu();
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
  window.addEventListener("beforeunload", (event) => {
    if (!hasDirtyIdentityChanges()) return;
    event.preventDefault();
    event.returnValue = "";
  });
  window.addEventListener("popstate", handleNavigationPop);
  els.toggleDetailsButton.addEventListener("click", toggleDetailsPanel);
  els.mobileDetailCloseButton?.addEventListener("click", () => {
    setDetailsOverlayOpen(false, { restoreFocus: true });
  });
  els.detailRail.addEventListener("click", (event) => {
    const tab = event.target.closest(".tab");
    if (tab) {
      if (tab.dataset.tab !== "identities" && !confirmDiscardIdentityChanges()) return;
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
      closeDetailsOverlay();
    }
  });
  els.detailRail.addEventListener("input", (event) => {
    const identityField = event.target.closest(".identity-card input, .identity-card textarea");
    if (identityField) updateIdentityDirtyState(identityField);
    const colorInput = event.target.closest(".identity-color");
    if (!colorInput) return;
    const swatch = colorInput.closest(".color-swatch");
    if (swatch) swatch.style.background = colorInput.value;
  });
  els.detailRail.addEventListener("change", (event) => {
    const identityField = event.target.closest(".identity-card input, .identity-card textarea");
    if (identityField) updateIdentityDirtyState(identityField);
    const voiceGreetingFile = event.target.closest(".identity-voice-greeting-file");
    if (voiceGreetingFile) uploadVoiceGreetingFile(voiceGreetingFile);
  });
  els.syncContactsButton.addEventListener("click", async () => {
    if (!confirmDiscardIdentityChanges()) return;
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
  updateConversationSearchClear();
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
