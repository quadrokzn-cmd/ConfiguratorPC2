# Архитектура UI: общий sidebar портала

Документ описывает структуру верхней навигации `QuadroTech-Suite` после
завершения всех пяти этапов Пути B (UI-1..UI-5; полный план —
`plans/2026-05-11-ui-merge-portal-configurator.md`). После UI-5
(2026-05-11) проект работает на одном FastAPI (`portal/main.py`),
поэтому формулировка «общий sidebar портала и конфигуратора»
исторически устарела — sidebar единственный и живёт в портале.

## Где живёт sidebar

Один партиал: `shared/templates/_partials/sidebar.html`.

- Подключается из `portal/templates/base.html`.
- Совместно с ним используется виджет курса ЦБ —
  `shared/templates/_partials/fx_widget.html`.

Параметр `current_service` остался в партиале для исторической совместимости,
но всегда `'portal'` после UI-5 — кросс-сервисные маркеры ↗ больше не
появляются.

Tailwind содержимое (`tailwind.config.js → content`) включает
`./shared/templates/**/*.html`, чтобы JIT-сборщик подхватывал
утилитарные классы из партиала.

## Структура меню

5 разделов верхнего уровня, всегда видны все 5. Порядок зафиксирован
решением собственника 2026-05-11 (см. `MEMORY.md → project_ui_merge_path_b`):

| Раздел | `data-section` | Подпункты (data-subsection) |
|---|---|---|
| Главная | `home` | — |
| Аукционы | `auctions` | — *(на UI-1 пусто; появятся при необходимости)* |
| Конфигуратор ПК | `configurator` | `new_query`, `projects`, `history` |
| Базы данных | `databases` | `nomenclature`, `prices`, `autoload`, `suppliers`, `components`, `mapping` |
| Настройки | `settings` | `users`, `backups`, `audit-log` |

Подпункты раскрываются только у активного раздела (`active_section`).

## Параметры партиала

`base.html` каждого сервиса задаёт перед `{% include %}`:

| Переменная | Тип | Назначение |
|---|---|---|
| `active_section` | `str` | Один из 5 ключей `home`/`auctions`/`configurator`/`databases`/`settings`. |
| `active_subsection` | `str \| None` | Ключ выделенного подпункта (для подсветки). |
| `current_service` | `str` | `'portal'` или `'configurator'` — на каком сервисе мы сейчас. |
| `user`, `csrf_token` | стандартные | Карточка пользователя и форма logout снизу. |

Используются глобалы (зарегистрированы в обоих `templating.py`):
`portal_url`, `configurator_url`, `current_exchange_rate`, `icon`, `brand_mark`.

### Как партиал определяет ссылки

- **Внутри того же сервиса** — относительный URL (`/admin/users`).
- **Кросс-сервис** — абсолютный (`{{ configurator_url }}/admin/suppliers`).
  Cookie общий (`kt_session` на `.quadro.tatar`), поэтому переход
  сохраняет сессию.
- Кросс-сервисные ссылки помечаются ↗ — менеджер видит, что попадает
  в другой сервис (на UI-1 это стабы; после UI-5 ↗ исчезнут полностью).

### Как `base.html` маппит URL → active_section

**Портал** (`portal/templates/base.html`):

| URL prefix | active_section | active_subsection |
|---|---|---|
| `/auctions*` | `auctions` | `None` |
| `/nomenclature*` | `databases` | `nomenclature` |
| `/admin/price-uploads*` | `databases` | `prices` |
| `/admin/auto-price-loads*` | `databases` | `autoload` |
| `/databases/suppliers*` | `databases` | `suppliers` |
| `/databases/components*` | `databases` | `components` |
| `/databases/mapping*` | `databases` | `mapping` |
| `/settings/users*` *(и `/admin/users*` до 301)* | `settings` | `users` |
| `/settings/backups*` *(и `/admin/backups*` до 301)* | `settings` | `backups` |
| `/settings/audit-log*` *(и `/admin/audit*` до 301)* | `settings` | `audit-log` |
| `/configurator/projects*`, `/configurator/project/*` | `configurator` | `projects` |
| `/configurator/history*` | `configurator` | `history` |
| `/configurator/`, `/configurator`, `/configurator/query*` | `configurator` | `new_query` |
| прочее (включая `/`) | `home` | `None` |

> **UI-2 (2026-05-11):** разделы «Поставщики», «Комплектующие для ПК»
> (бывшие «Компоненты») и «Очередь маппинга» переехали из конфигуратора
> (`config.quadro.tatar/admin/{suppliers,components,mapping}`) в портал
> под префикс `/databases/`. URL-маппинг и подпункты sidebar обновлены.
> Со старых URL стоит 301-редирект (см. ниже). «Прайс-листы» и
> «Автозагрузка» сохранили префикс `/admin/*` — они переедут на префикс
> `/databases/` отдельно (по плану UI-5).

> **UI-3 (2026-05-11):** раздел «Настройки» оформлен как
> `/settings/{users,backups,audit-log}`. Это перевешивание URL внутри
> портала (файлы и так были в portal, переехали из `admin_*` в
> `routers/settings/*`). Старые `/admin/{users,backups,audit}*` отдают
> 301 на новые URL — обработчики в `portal/main.py` (см. ниже),
> точечные по разделу, чтобы не зацепить соседей
> `/admin/{price-uploads,auto-price-loads,diagnostics,auctions}`. Эти
> соседи остаются на `/admin/*` до отдельного этапа (включая
> финальную сортировку по разделам после UI-5). Подпункт «Журнал
> действий» использует `data-subsection="audit-log"` (в UI-2 был
> `audit` — переименовано для соответствия URL).

**Admin-страницы конфигуратора** (`/admin`, `/admin/budget`,
`/admin/queries`) после UI-5 живут в портале (`portal/routers/admin.py`).
На них `active_section='configurator'`, `active_subsection=None` —
это admin-дашборд, у которого нет конкретного подпункта Конфигуратора.

> **UI-4 (2026-05-11):** конфигуратор переехал в портал под префикс
> `/configurator/*`.
>
> **UI-5 (2026-05-11):** папка `app/` удалена. Admin-страницы конфигуратора
> (`/admin`, `/admin/budget`, `/admin/queries`) переехали в
> `portal/routers/admin.py`. Catch-all 301-редиректы с `config.quadro.tatar`
> больше не нужны — собственник 2026-05-11 подтвердил, что менеджеров с
> закладками нет. DNS-записи `config.quadro.tatar`/`config-preprod.quadro.tatar`
> и Railway-сервисы `configurator`/`configurator-preprod` удаляются
> собственником вручную после деплоя UI-5.

## 301-редиректы

### Со стороны конфигуратора (`config.quadro.tatar`)

После **UI-5** (2026-05-11) `config.quadro.tatar` упразднён, никаких
редиректов с этого хоста больше нет. Railway-сервисы `configurator` и
`configurator-preprod` удаляются собственником вручную; DNS-записи в
Reg.ru снимаются.

Историческая справка (что было до UI-5 в `app/main.py`):

- **UI-2** — точечные 301 со старых URL раздела «Базы данных»:
  `/admin/{suppliers,components,mapping}*` → `app.quadro.tatar/databases/{section}*`.
- **UI-3** — `/admin/users` → 302 на `app.quadro.tatar/settings/users`.
- **UI-4** — catch-all 301: `/` и `/{rest:path}` (кроме `/admin/*`,
  `/healthz`, `/static/*`) → `app.quadro.tatar/configurator/{rest}`.
- Admin-страницы конфигуратора `/admin`, `/admin/budget`, `/admin/queries`
  жили в `app/routers/admin_router.py`.

После UI-5 admin-страницы (`/admin`, `/admin/budget`, `/admin/queries`)
переехали в `portal/routers/admin.py` под тот же префикс `/admin/*` —
URL'ы сохранены без редиректов (собственник 2026-05-11 подтвердил, что
менеджеров с закладками нет).

### Внутри портала (`app.quadro.tatar`) — UI-3

На UI-3 раздел «Настройки» переехал с префикса `/admin/*` на `/settings/*`
внутри самого портала. Старые URL отдают 301 на новые (обработчики в
`portal/main.py`):

| Старый URL (портал)                        | Новый URL (портал)                    |
|---|---|
| `/admin/users`                             | `/settings/users`                     |
| `/admin/users/{rest:path}`                 | `/settings/users/{rest:path}`         |
| `/admin/backups`                           | `/settings/backups`                   |
| `/admin/backups/{rest:path}`               | `/settings/backups/{rest:path}`       |
| `/admin/audit`                             | `/settings/audit-log`                 |
| `/admin/audit/{rest:path}`                 | `/settings/audit-log/{rest:path}`     |

Редиректы относительные (не межсервисные) — обработчики возвращают
просто `/settings/...`, без `portal_url`. Хост подставит браузер.

Здесь тоже **три точечных catch-all'а** + корневые handler'ы (а не
один широкий `/admin/{path:path}`), чтобы не задеть соседей:
`/admin/price-uploads`, `/admin/auto-price-loads`, `/admin/diagnostics`,
`/admin/auctions` — они остаются на месте. RBAC и финальная
сортировка по разделам — отдельный этап после UI-5.

Полная таблица переездов URL ведётся в [`url-migration-map.md`](url-migration-map.md).

## Как добавить новый раздел

Когда появится новый модуль и собственник решит, в какую категорию его
положить:

1. **В уже существующий раздел.** Добавить вызов `_sub_link(...)` внутрь
   `{% if active_section == 'databases' %}` (или соответствующего блока)
   в `sidebar.html`. Если URL живёт в портале — `target_service='portal'`,
   если в конфигураторе — `'configurator'`. Обновить `active_subsection`-
   маппинг в нужном `base.html`.

2. **Новый раздел верхнего уровня.** Это решение собственника. Структура
   подразумевает 5 разделов фиксированно (Главная/Аукционы/Конфигуратор/
   Базы данных/Настройки), новые модули предполагаются как подпункты.
   Если всё-таки нужен 6-й раздел — добавить `_section_link(...)` плюс
   блок подпунктов в `sidebar.html`, обновить `_EXPECTED_SECTIONS` в
   тестах `tests/test_portal/test_ui1_sidebar.py` и `test_web/test_ui1_sidebar_app.py`.

## Иконки

Иконки sidebar берутся через `{% from "_macros/icons.html" import icon %}`.
В обоих сервисах поддерживаются (выровнено в UI-1):

- Верхний уровень: `layout-grid`, `gavel`, `cpu`, `database`, `settings`.
- Подпункты: `sparkle`, `folder`, `clock`, `printer`, `truck`, `refresh-cw`,
  `merge`, `users`, `list`.
- Прочее: `log-out`, `trending-up`, `external-link`, `arrow-left`/`right`.

Если в новой ссылке используется иконка, которой нет в
`portal/templates/_macros/icons.html`, её нужно добавить туда — иначе
sidebar сломается (Jinja вернёт fallback-кружок).

## История

- **UI-1 (2026-05-11):** изначальная версия. Sidebar дублируется
  в двух сервисах через общий партиал, кросс-сервисные ссылки —
  абсолютные URL с маркером ↗.
- **UI-2 (2026-05-11):** «Поставщики», «Комплектующие для ПК» и
  «Очередь маппинга» переехали в `portal/routers/databases/` и
  `portal/templates/databases/`. URL'ы — `/databases/{suppliers,components,mapping}`.
  Со старых URL `config.quadro.tatar/admin/{suppliers,components,mapping}*`
  стоит 301-редирект. В sidebar три ссылки стали внутренними (без ↗).
  UI-лейбл «Компоненты» → «Комплектующие для ПК» (URL не меняется).
- **UI-3 (2026-05-11):** «Пользователи», «Бэкапы» и «Журнал действий»
  оформлены как раздел «Настройки» в портале — переехали из
  `portal/routers/admin_{users,backups,audit}.py` в
  `portal/routers/settings/{users,backups,audit_log}.py` и из
  `portal/templates/admin/` в `portal/templates/settings/`. URL'ы —
  `/settings/{users,backups,audit-log}`. Со старых `/admin/{users,
  backups,audit}*` стоит внутрипортальный 301-редирект (см. выше).
  Подпункт sidebar для журнала переименован: `audit` → `audit-log`.
- **UI-4 (2026-05-11):** Конфигуратор ПК переехал в портал под префикс
  `/configurator/*`. Перенесены: 3 роутера (`portal/routers/configurator/{main,projects,export}.py`),
  ~80 файлов сервисов в `portal/services/configurator/` (NLU, engine,
  compatibility, manual_edit, enrichment, auto_price, export, price_loaders,
  spec_*, web_service, openai_service, budget_guard), 7 шаблонов в
  `portal/templates/configurator/` + 4 макроса в `_macros/`. Глобальная
  middleware `_enforce_configurator_permission` заменена на scoped
  `Depends(require_configurator_access)` в `portal/dependencies/configurator_access.py`.
  В `app/main.py` остался catch-all 301-редирект на `portal/configurator/*`
  и admin_router (dashboard/budget/queries). Подпункты Конфигуратора в
  sidebar стали cross-service (portal_url/configurator/*) при рендере
  в app/, и внутренними (без ↗) при рендере в portal.
- **UI-4.5 (2026-05-11):** `app/services/auctions/` и `app/services/catalog/`
  переехали в `portal/services/auctions/` и `portal/services/catalog/`.
  Кросс-импорт `from app.services.auctions/catalog ...` в `portal/services/
  configurator/price_loaders/orchestrator.py` устранён. `app/scheduler.py`
  (cron USD/RUB) перенесён в `portal/scheduler.py` под единый
  `APP_ENV=production` / `RUN_BACKUP_SCHEDULER=1` флаг — `RUN_SCHEDULER`
  больше не нужен. На `config.quadro.tatar` фоновых задач больше нет,
  только редиректы. Структура `portal/services/` финальна: `auctions/`,
  `catalog/`, `configurator/`, `databases/`, `settings/` + плоские
  `backup_service.py`, `dashboard.py`, `auctions_service.py`.
- **UI-5 (2026-05-11):** удалена вся папка `app/` (включая `main.py`,
  `auth.py`, `config.py`, `database.py`, `templating.py`,
  `routers/admin_router.py`, `templates/`), а также `Dockerfile`,
  `railway.json`, `Procfile`. Settings (`Settings` dataclass) переехал
  в `shared/config.py`. Фильтры `to_rub`/`fmt_rub`/`current_exchange_rate`/
  `static_url` теперь живут только в `portal/templating.py` (раньше
  реэкспортировались из `app/templating.py`). Admin-страницы конфигуратора
  (`/admin`, `/admin/budget`, `/admin/queries`) переехали в
  `portal/routers/admin.py` и подключены в `portal/main.py` —
  URL'ы сохранены, редиректов с `config.quadro.tatar` нет (собственник
  подтвердил отсутствие активных закладок у менеджеров). Шаблоны
  экспорта `kp_template.docx`/`project_template.xlsx` перенесены через
  `git mv` в `portal/templates/export/`; `parents[4] / "app" / "templates"`
  в `excel_builder.py`/`kp_builder.py` стало `parents[3] / "templates"`.
  Тесты: удалена папка `tests/test_web/` (8 файлов), две выживших
  единицы — `test_migration.py` и `test_bootstrap_admin.py` — переехали
  в корень `tests/`. Импорты `from app.{config,database,auth,templating}`
  заменены на `from {shared.config, shared.db, shared.auth,
  portal.templating}` в 54 файлах (portal/, scripts/, tests/, shared/db.py).
  После UI-5 `current_service` в sidebar всегда `'portal'`, маркеры ↗
  не появляются (но параметр оставлен в партиале для исторической
  совместимости с layout-логикой).
