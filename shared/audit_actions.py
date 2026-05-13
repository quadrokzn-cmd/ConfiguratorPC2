# Каталог констант action для аудит-лога (Этап 9В.4).
#
# Используем константы вместо строковых литералов, чтобы:
#   - опечатка в имени action ловилась статическим анализатором;
#   - один поиск по имени константы давал все точки записи в коде.
#
# Конвенция именования: '<домен>.<подействие>' — точка как разделитель,
# чтобы UI мог фильтровать по префиксу (например, 'user.*'). Минимально
# глубокая иерархия — 1-2 уровня.

from __future__ import annotations


# --- Authentication -----------------------------------------------------

ACTION_LOGIN_SUCCESS    = "auth.login.success"
ACTION_LOGIN_FAILED     = "auth.login.failed"
ACTION_LOGOUT           = "auth.logout"


# --- Users (управление пользователями в /admin/users портала) ----------

ACTION_USER_CREATE           = "user.create"
ACTION_USER_TOGGLE_ACTIVE    = "user.toggle_active"
ACTION_USER_ROLE_CHANGE      = "user.role_change"
ACTION_USER_PERM_CHANGE      = "user.permission_change"
ACTION_USER_PASSWORD_RESET   = "user.password_reset"
ACTION_USER_DELETE_PERMANENT = "user.delete_permanent"


# --- Projects (конфигуратор) -------------------------------------------

ACTION_PROJECT_CREATE = "project.create"
ACTION_PROJECT_UPDATE = "project.update"
ACTION_PROJECT_DELETE = "project.delete"


# --- Builds / configurations -------------------------------------------

ACTION_BUILD_CREATE     = "build.create"
ACTION_BUILD_REOPTIMIZE = "build.reoptimize"


# --- Exports ------------------------------------------------------------

ACTION_EXPORT_EXCEL = "export.excel"
ACTION_EXPORT_KP    = "export.kp_word"

# Excel-выгрузка каталога (мини-этап 2026-05-14, Фаза 2):
# admin скачивает «Комплектующие_ПК.xlsx» / «Печатная_техника.xlsx».
ACTION_CATALOG_EXCEL_EXPORT = "catalog_excel_export"


# --- Supplier emails ----------------------------------------------------

ACTION_SUPPLIER_EMAIL = "supplier.email_sent"


# --- Components / catalog ----------------------------------------------

ACTION_COMPONENT_HIDE   = "component.hide"
ACTION_COMPONENT_SHOW   = "component.show"
ACTION_COMPONENT_UPDATE = "component.update"


# --- Backups ------------------------------------------------------------

ACTION_BACKUP_MANUAL   = "backup.manual_run"
ACTION_BACKUP_DOWNLOAD = "backup.download"


# --- Catalog Excel import (Фаза 3 плана 2026-05-13) --------------------

ACTION_CATALOG_EXCEL_IMPORT = "catalog_excel_import"


# --- Audit log self ----------------------------------------------------

ACTION_AUDIT_VIEW = "audit.view"


# --- Price uploads (этап 11.2: ручная загрузка прайсов в портале) ------

ACTION_PRICE_UPLOAD_VIEW     = "price_upload.view"
ACTION_PRICE_UPLOAD_START    = "price_upload.start"
ACTION_PRICE_UPLOAD_COMPLETE = "price_upload.complete"
ACTION_PRICE_UPLOAD_FAILED   = "price_upload.failed"


# --- Auto price loads (этап 12.3: автозагрузка прайсов от поставщиков) -

ACTION_AUTO_PRICE_VIEW   = "auto_price.view"
ACTION_AUTO_PRICE_RUN    = "auto_price.run"
ACTION_AUTO_PRICE_TOGGLE = "auto_price.toggle"


# --- Auctions (этап 9a слияния QT↔C-PC2) -------------------------------

ACTION_AUCTION_STATUS_CHANGE     = "auction.status_change"
ACTION_AUCTION_CONTRACT_UPDATE   = "auction.contract_update"
ACTION_AUCTION_NOTE_UPDATE       = "auction.note_update"
ACTION_AUCTION_SETTINGS_UPDATE   = "auction.settings_update"
ACTION_AUCTION_REGION_TOGGLE     = "auction.region_toggle"
ACTION_AUCTION_KTRU_ADD          = "auction.ktru_add"
ACTION_AUCTION_KTRU_TOGGLE       = "auction.ktru_toggle"
ACTION_AUCTION_NOMENCLATURE_EDIT = "auction.nomenclature_edit"
ACTION_AUCTION_ENRICH_REQUEST    = "auction.enrich_request"
