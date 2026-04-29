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

ACTION_USER_CREATE         = "user.create"
ACTION_USER_DELETE         = "user.delete"
ACTION_USER_TOGGLE_ACTIVE  = "user.toggle_active"
ACTION_USER_ROLE_CHANGE    = "user.role_change"
ACTION_USER_PERM_CHANGE    = "user.permission_change"
ACTION_USER_PASSWORD_RESET = "user.password_reset"


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


# --- Supplier emails ----------------------------------------------------

ACTION_SUPPLIER_EMAIL = "supplier.email_sent"


# --- Components / catalog ----------------------------------------------

ACTION_COMPONENT_HIDE   = "component.hide"
ACTION_COMPONENT_SHOW   = "component.show"
ACTION_COMPONENT_UPDATE = "component.update"


# --- Backups ------------------------------------------------------------

ACTION_BACKUP_MANUAL   = "backup.manual_run"
ACTION_BACKUP_DOWNLOAD = "backup.download"


# --- Audit log self ----------------------------------------------------

ACTION_AUDIT_VIEW = "audit.view"
