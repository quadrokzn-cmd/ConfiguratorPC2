# План: слияние портала и конфигуратора в одно FastAPI (Путь B)

**Дата:** 2026-05-11
**Цель:** объединить два FastAPI-приложения (`portal/` на :8081/app.quadro.tatar и `app/configurator` на :8080/config.quadro.tatar) в одно. Новая верхняя навигация портала: **Главная → Аукционы → Конфигуратор ПК → Базы данных → Настройки**. `config.quadro.tatar` упраздняется и редиректит на `app.quadro.tatar/configurator/`.

**Почему Путь B (а не общий sidebar для двух приложений):** собственник 2026-05-11 указал ориентир «4+ новых модулей за год». При таком темпе двойная архитектура (FastAPI × N модулей × поддомен) не масштабируется — каждый модуль порождает отдельный Docker / Railway-сервис / DNS-конфигурацию. Путь B даёт паттерн «новый модуль = новая папка `portal/routers/<module>/`».

## Принятые решения собственника (2026-05-11)

1. **Путь B** — слияние, не общий sidebar (см. рекомендацию оркестратора).
2. **Порядок в меню:** Главная → Аукционы → Конфигуратор ПК → Базы данных → Настройки. Аукционы выше Конфигуратора.
3. **Подменю:** авто-раскрытие активного раздела, остальные свёрнуты.
4. **Плашки на главной:** Аукционы слева, Конфигуратор ПК справа.
5. **Переименования (только лейблы UI):**
   - «Справочник печати» → «Справочник оргтехники»
   - «Компоненты» → «Комплектующие для ПК»
6. **Перенос разделов из конфигуратора в Базы данных:** Поставщики, Комплектующие для ПК, Очередь маппинга. После переноса в самом конфигураторе остаётся NLU + проекты + экспорт КП.
7. **Раздел Настройки:** Пользователи + Бэкапы + Журнал действий.
8. **RBAC для менеджеров** — отложено на отдельный этап **после** UI-5. Сейчас всё меню одинаково для admin/manager.
9. **Будущие модули:** «на год не знаю» — делаем гибкую структуру `portal/routers/<имя_модуля>/` под любые именования.

## Фазы

### UI-1 — общий sidebar + новое меню + плашки [x]

Файл `shared/templates/_partials/sidebar.html` (новый), подключается обоими приложениями (`portal/templates/base.html` и `app/templates/base.html`). Поведение:

- 5 пунктов всегда видны: Главная / Аукционы / Конфигуратор ПК / Базы данных / Настройки.
- Авто-раскрытие подпунктов только активного раздела.
- В portal — `active_section` определяется по URL роутера; в app — всегда `'configurator'`.
- Подпункты Конфигуратора (в app) видны как раскрытое подменю, кликабельны.
- Подпункты Баз данных и Настроек — на этом этапе ведут на ссылки в `config.quadro.tatar/*` (стабы, переезжают в UI-2/UI-3).
- На главной — рядом с существующей плашкой «Конфигуратор ПК» добавляется плашка «Аукционы» (слева), с иконкой молотка и ссылкой на /auctions.
- Нижняя ссылка «← Конфигуратор» убирается из sidebar портала.

DoD: тесты на рендер sidebar для каждого active_section; тест на наличие плашки «Аукционы» на главной; pytest регрессия `-m "not live"` — без новых failures.

**Артефакты UI-1 (2026-05-11):**

- `shared/templates/_partials/sidebar.html` — общий партиал на 187 строк, параметризован `active_section`/`active_subsection`/`current_service`.
- `portal/templates/base.html`, `app/templates/base.html` — переписаны под включение партиала; в каждом — свой маппинг URL → `active_section`.
- `portal/templates/home.html` — добавлен grid из двух плашек (Аукционы слева, Конфигуратор справа), плейсхолдер «нет модулей» сохранён.
- `portal/templates/_macros/icons.html`, `app/templates/_macros/icons.html` — выровнены: оба содержат `gavel`, `database`, `printer`, `list`, `external-link`, `settings`, `layout-grid`, `sparkle`, `merge`.
- `tailwind.config.js` — `./shared/templates/**/*.html` добавлено в `content`.
- `static/dist/main.css` — пересобран (npm run build:css).
- Тесты: `tests/test_portal/test_ui1_sidebar.py` (13 кейсов), `tests/test_web/test_ui1_sidebar_app.py` (6 кейсов). Обновлены под новую структуру: `test_portal/test_permission_ui.py` (3 кейса), `test_portal/test_dashboard.py` (3 кейса), `test_portal/test_admin_users.py` (1 кейс), `test_web/test_stage9_layout.py` (1 кейс).
- `docs/ui-architecture.md` — описание sidebar-архитектуры и правил расширения.
- pytest регрессия: 1857 passed, 1 skipped, 0 failed.

### UI-2 — перенос Баз данных в portal/ [x]

Перенести из `app/routers/`, `app/services/`, `app/templates/` в `portal/`:
- Поставщики (`/suppliers`)
- Компоненты (`/components`) — переименовать UI-лейбл в «Комплектующие для ПК»
- Очередь маппинга (`/mapping`)

Переименовать UI-лейбл «Справочник печати» → «Справочник оргтехники» (без изменения URL `/nomenclature` или таблицы БД) — закрыто на UI-1.

URL после миграции: всё под `app.quadro.tatar/databases/{suppliers,components,mapping,nomenclature,prices,autoload}`. Старые URL на `config.quadro.tatar/admin/{suppliers,components,mapping}` получают 301-редирект (механизм будет в UI-5; на UI-2 — заглушки через `app/main.py`).

DoD: все три страницы работают на новых URL; pytest test_web/ обновлены под новые пути; редиректы со старого хоста.

**Артефакты UI-2 (2026-05-11):**

- `portal/services/databases/` — новая папка: `supplier_service.py`,
  `component_service.py`, `mapping_service.py` (перенесены без изменений
  логики из `app/services/`).
- `portal/routers/databases/` — новая папка: `suppliers.py`, `components.py`,
  `mapping.py`. Префикс `/databases/{section}`, авторизация
  `require_admin` (как и раньше).
- `portal/templates/databases/` — новая папка: `suppliers_list.html`,
  `supplier_form.html`, `components_list.html`, `component_detail.html`,
  `_components_table.html`, `mapping_list.html`, `mapping_detail.html`.
  Все внутренние URL обновлены на `/databases/...`. Заголовок и хлебные
  крошки страницы «Компоненты» переименованы в «Комплектующие для ПК».
- `portal/templates/_macros/icons.html` — расширен иконками `plus`,
  `mail`, `phone`, `power`, `save`, `trash`, `edit`, `search`, `eye`,
  `eye-off`, `check`, `filter` (нужны страницам «Базы данных»).
  `portal/templates/_macros/pagination.html` — добавлен из app/.
- `portal/main.py` — подключены `databases_suppliers`, `databases_components`,
  `databases_mapping`.
- `app/main.py` — добавлены три пары catch-all 301-редиректов
  (root + `{rest:path}`) для `/admin/{suppliers,components,mapping}`
  на `${PORTAL_URL}/databases/{section}`. Хост из `settings.portal_url`,
  никакого хардкода.
- `app/routers/admin_router.py` — вырезаны обработчики `/admin/suppliers/*`
  и `/admin/components/*`, упрощён до `/admin`, `/admin/users` (редирект),
  `/admin/budget`, `/admin/queries`. `mapping_count` для дашборда —
  inline-COUNT, чтобы не тянуть кросс-сервисный импорт `portal.services`.
- `app/routers/mapping_router.py` — удалён.
- `app/services/{supplier,component,mapping}_service.py` — удалены.
- `app/templates/admin/{suppliers_list,supplier_form,components_list,
  component_detail,_components_table,mapping_list,mapping_detail}.html` —
  удалены.
- `tests/test_mapping_{mpn,capacity}.py`,
  `scripts/recalculate_unmapped_scores.py` — импорты перенесены на
  `portal.services.databases.mapping_service`.
- `shared/templates/_partials/sidebar.html` — три ссылки на «Поставщики»,
  «Комплектующие для ПК», «Очередь маппинга» с `target_service='configurator'`
  → `'portal'`, путь `/admin/{section}` → `/databases/{section}`. Маркер ↗
  на этих ссылках исчез автоматически (теперь они внутренние в портале).
- `portal/templates/base.html` — добавлены три ветки URL → `active_section`
  для `/databases/{suppliers,components,mapping}`.
- Новые тесты:
  - `tests/test_portal/test_databases_suppliers.py` (9 кейсов);
  - `tests/test_portal/test_databases_components_prices.py` (4 кейса);
  - `tests/test_portal/test_databases_components_filters.py` (10 кейсов);
  - `tests/test_portal/test_databases_components_pagination.py` (4 кейса);
  - `tests/test_portal/test_databases_mapping.py` (перенос test_mapping_admin,
    ~30 кейсов);
  - `tests/test_web/test_databases_redirects.py` (10 кейсов).
- Обновлены тесты: `tests/test_portal/test_ui1_sidebar.py` (+5 новых
  кейсов на subsection-подсветку /databases/* и отсутствие ↗ внутри
  раздела «Базы данных» портала); `tests/test_web/test_ui1_sidebar_app.py`
  (+2 кейса на отсутствие старых `/admin/{suppliers,components,mapping}`
  в HTML конфигуратора); `tests/test_web/test_stage9a_2.py`,
  `test_stage9a_2_2.py`, `test_stage9a_2_3.py` — урезаны от блоков,
  переехавших в `test_portal/`.
- Удалены `tests/test_web/test_admin_component_prices.py`,
  `tests/test_web/test_mapping_admin.py` (перенесены).
- `docs/ui-architecture.md` — обновлены маппинг URL→active_section,
  список ↗-меток, добавлена таблица 301-редиректов.
- `docs/url-migration-map.md` — новый файл, единая таблица переездов
  URL по этапам UI-1..UI-5.
- pytest регрессия: **1875 passed, 1 skipped, 0 failed** (UI-1 baseline
  был 1857; +18 новых нетто-кейсов).

### UI-3 — перенос Настроек в portal/ [x]

Создать раздел «Настройки» в portal/. Внутри:
- Пользователи (`/settings/users`)
- Бэкапы (`/settings/backups`)
- Журнал действий (`/settings/audit-log`)

Если «Пользователи» уже в portal — просто перевесить URL в `/settings/users`. Бэкапы и Журнал — переехать или сгруппировать.

DoD: все три страницы работают под `/settings/*`; pytest зелёный.

**Артефакты UI-3 (2026-05-11):**

- `portal/routers/settings/` — новая папка с тремя модулями:
  `users.py` (префикс `/settings`, маршруты `/users`,
  `/users/{id}/...`), `backups.py` (префикс `/settings/backups`),
  `audit_log.py` (префикс `/settings/audit-log`).
- `portal/templates/settings/` — новая папка: `users.html`,
  `backups.html`, `audit_log.html` (внутренние URL обновлены на
  `/settings/...`).
- Удалены старые `portal/routers/admin_{users,backups,audit}.py` и
  `portal/templates/admin/{users,backups,audit}.html`.
- `portal/main.py` — подключение трёх новых роутеров
  (`settings_users`, `settings_backups`, `settings_audit_log`) и
  шесть тонких 301-handler'ов (root + `{rest:path}` для каждого из
  трёх разделов). Импорты `admin_users`, `admin_backups`,
  `admin_audit` удалены.
- `app/routers/admin_router.py` — старый редирект `/admin/users` →
  `${portal_url}/admin/users` обновлён на
  `${portal_url}/settings/users`, чтобы исключить двойной hop
  (`config → portal/admin/users → portal/settings/users`).
- `shared/templates/_partials/sidebar.html` — три подпункта
  «Настройки» переписаны:
  - Топ-ссылка раздела ведёт на `/settings/users` (раньше
    `/admin/users`);
  - `/admin/users` → `/settings/users` (sub-key `users`);
  - `/admin/backups` → `/settings/backups` (sub-key `backups`);
  - `/admin/audit` → `/settings/audit-log` (sub-key переименован
    `audit` → `audit-log` для соответствия URL).
- `portal/templates/base.html` — маппинг URL → `active_section/
  subsection` добавлен для `/settings/{users,backups,audit-log}` (со
  страховочными `/admin/*` fallback'ами на случай, если 301-ответ
  где-то рендерит base.html).
- Переименованы тесты:
  - `tests/test_portal/test_admin_users.py` →
    `test_settings_users.py`;
  - `tests/test_portal/test_admin_audit.py` →
    `test_settings_audit_log.py`;
  - `tests/test_portal/test_admin_role_change.py` →
    `test_settings_role_change.py`;
  - `tests/test_portal/test_admin_user_delete.py` →
    `test_settings_user_delete.py`.
  В каждом файле URL `/admin/{users,audit,backups}` обновлены на
  `/settings/{users,audit-log,backups}`, заголовочные комментарии
  переписаны.
- Обновлены без переименования: `tests/test_portal/test_backups.py`
  (был без `admin_`-префикса), `tests/test_portal/test_dashboard.py`,
  `tests/test_portal/test_ui1_sidebar.py` (sub-key `audit-log` и
  обновлённые URL), `tests/test_portal/test_stage12_5a_html_confirm_modal.py`,
  `tests/test_web/test_access.py` (assert location =
  `/settings/users`).
- Новый файл `tests/test_portal/test_settings_redirects.py` —
  9 кейсов на 301 (`/admin/{users,backups,audit}` + sub-routes) и
  3 негативных кейса (`/admin/price-uploads`, `/admin/auto-price-loads`,
  `/admin/diagnostics` НЕ редиректятся).
- `docs/ui-architecture.md` — раздел «Настройки» помечен переехавшим,
  обновлены маппинг URL → active_section, sub-key `audit` →
  `audit-log`, добавлена таблица внутрипортальных 301-редиректов,
  обновлена история.
- `docs/url-migration-map.md` — заполнена секция UI-3 с полной
  таблицей переездов (root + sub-routes для каждого из трёх
  разделов).
- pytest регрессия: **1886 passed, 1 skipped, 0 failed**
  (UI-2 baseline был 1875; +11 новых нетто-кейсов).

### UI-4 — перенос самого Конфигуратора [x]

Перенести из `app/routers/`, `app/services/`, `app/templates/` в
`portal/routers/configurator/`, `portal/services/configurator/`,
`portal/templates/configurator/`. Все URL конфигуратора (NLU-форма,
проекты, экспорт КП) переезжают на `app.quadro.tatar/configurator/*`.
`config.quadro.tatar` — в режиме 301-редиректа на новые URL.

**Артефакты UI-4 (2026-05-11):**

- `portal/routers/configurator/` — новая папка, 3 модуля:
  - `main.py` (бывший `app/routers/main_router.py`) — `/configurator/`,
    `/configurator/query` (POST), `/configurator/query/{id}`,
    `/configurator/history`.
  - `projects.py` (бывший `app/routers/project_router.py`) — все
    `/configurator/projects` и `/configurator/project/{id}/*` маршруты,
    включая AJAX select/deselect/update_quantity/spec/reoptimize.
  - `export.py` (бывший `app/routers/export_router.py`) — экспорт Excel,
    KP (docx) и emails (preview/send).
  - У всех трёх — `APIRouter(prefix="/configurator",
    dependencies=[Depends(require_configurator_access)])`.
- `portal/services/configurator/` — новая папка, ~80 файлов:
  - `engine/` — бывший `app/services/configurator/` (builder, selector,
    schema, candidates, prices, warnings, pretty).
  - `nlu/`, `compatibility/`, `manual_edit/`, `enrichment/`,
    `auto_price/`, `export/`, `price_loaders/` — перенесены as-is.
  - Плоские модули: `openai_service.py`, `web_service.py`,
    `web_result_view.py`, `spec_naming/service/recalc.py`,
    `budget_guard.py`, `price_loader.py`.
  - **Кросс-импорт** в `price_loaders/orchestrator.py` —
    `from app.services.auctions/catalog ...` остаётся до UI-4.5
    (auctions/catalog не «конфигуратор», переезд отдельным этапом).
- `portal/templates/configurator/` — новая папка, 6 страниц + 4
  макроса (`configuration_block`, `variant_block`, `variant_table`,
  `specification_panel`). Импорты макросов в шаблонах обновлены на
  `configurator/_macros/X.html`. `icons.html` и `pagination.html`
  остаются общими (`portal/templates/_macros/`).
- `portal/dependencies/configurator_access.py` — новый модуль:
  `require_configurator_access` (FastAPI Depends) +
  `ConfiguratorAccessDenied` (внутреннее исключение). Заменяет
  глобальную middleware `_enforce_configurator_permission` из app/main.py.
- `portal/main.py` — подключение трёх новых роутеров (`configurator_main`,
  `configurator_projects`, `configurator_export`) и `exception_handler`
  для `ConfiguratorAccessDenied` → 302 на `/?denied=configurator`.
- `portal/templates/base.html` — добавлены 3 ветки URL → `active_section`
  для `/configurator/projects`, `/configurator/project/*`,
  `/configurator/history`, `/configurator/`, `/configurator/query*`.
  Также добавлен topbar с `{% block breadcrumbs %}` (перенесён из
  `app/templates/base.html`) и `<script common.js>` (нужен страницам
  конфигуратора).
- `portal/templating.py` — `to_rub`/`fmt_rub` фильтры импортированы из
  `app.templating` (configurator-шаблоны их используют).
- `shared/templates/_partials/sidebar.html` — три подпункта
  «Конфигуратор ПК» переписаны: `target_service='configurator'` →
  `'portal'`, путь `/`/`/projects`/`/history` → `/configurator/`/
  `/configurator/projects`/`/configurator/history`. Маркер ↗ исчез
  при рендере в портале (internal links), остался при рендере в app/
  (cross-service).
- `app/main.py` — `_enforce_configurator_permission` middleware удалена,
  добавлены catch-all 301-редиректы: корневой `/` → `portal_url/configurator/`,
  `/{rest:path}` → `portal_url/configurator/{rest}` (исключая `/admin/*`,
  `/healthz`, `/static/*`). Импорты `from app.routers import (main_router,
  project_router, export_router)` удалены — остался только `admin_router`.
- `app/scheduler.py`, `app/templating.py`, `app/routers/admin_router.py`
  — обновлены импорты `from app.services.* import ...` → `from
  portal.services.configurator.* import ...` (cross-import app/ → portal/,
  допустим до UI-5).
- `portal/services/configurator/export/{excel_builder,kp_builder}.py` —
  `parents[3]` → `parents[4]` (глубина файла после переноса +1, шаблоны
  `kp_template.docx`/`project_template.xlsx` пока в `app/templates/export/`).
- `portal/services/configurator/enrichment/{claude_code/exporter,openai_search/fx}.py`
  — `parents[4]` → `parents[5]` (та же причина).
- `app/services/__init__.py` остался пустым; `app/services/auctions/` и
  `app/services/catalog/` — на месте (UI-4.5).
- Тесты:
  - **Перенесено через `git mv`** в `tests/test_portal/test_configurator_*.py`:
    test_web_result_view, test_specification_calc, test_project_routes,
    test_emails_endpoint, test_export_excel, test_variant_table_rendering,
    test_result_page_rendering, test_email_modal_ui, test_stage9_motion,
    test_spec_recalc, test_hide_case_fans, test_stage9_polish,
    test_stage9a_2_1_logo, test_kp_builder, test_stage9a_2_5,
    test_stage9a_2_6, test_query_flow, test_csrf, test_stage9_layout,
    test_stage9a_2_3, test_stage9a_2, test_stage9a_2_2, test_budget.
    URL'ы обновлены через sed: `/projects` → `/configurator/projects`,
    `/project/` → `/configurator/project/`, etc.
  - **Новый** `tests/test_portal/test_configurator_access.py` —
    бывшая configurator-часть `test_access.py` (5 кейсов).
  - **Новый** `tests/test_portal/test_configurator_access_perms.py` —
    замена `test_permission_middleware.py` на тесты
    `require_configurator_access` Depends (6 кейсов).
  - **Новый** `tests/test_web/test_configurator_redirects.py` —
    10 кейсов на catch-all 301 + проверка что `/admin/*`, `/healthz`,
    `/static/*` не редиректятся.
  - **Новый** `tests/test_web/test_admin_budget.py` — admin-часть
    бывшего `test_budget.py` (3 кейса, через `admin_client_app`).
  - **Удалён** `tests/test_web/test_permission_middleware.py` (middleware
    нет, тесты переехали).
  - **Обновлены**: `tests/test_web/test_access.py` (только admin-часть),
    `tests/test_web/test_ui1_sidebar_app.py` (тестим через `/admin`,
    проверяем cross-service подпункты конфигуратора), `tests/test_web/
    test_databases_redirects.py` (admin_client → admin_client_app).
  - **Расширен** `tests/test_portal/conftest.py`: `mock_process_query`
    (теперь патчит `portal.routers.configurator.{main,projects}`),
    алиасы `app_client`/`admin_client`/`manager_client` →
    `portal_client`/`admin_portal_client`/`manager_portal_client`,
    `parse_query_submit_redirect`/`qid_from_submit_redirect`,
    `manager2_user`/`manager_no_perms`.
  - **Заменён** `tests/test_web/conftest.py`: только app/-специфичное —
    `app_client_legacy` (TestClient app/main.py), `admin_user`/`manager_user`
    (для admin-страниц), `admin_client_app`/`manager_client_app`,
    `app_client` (alias на legacy).
- Документация:
  - `docs/ui-architecture.md` — раздел «Конфигуратор» помечен переехавшим,
    маппинг URL → `active_section`/`subsection` дополнен `/configurator/*`,
    в раздел 301-редиректов добавлен блок «UI-4 catch-all», обновлена
    история.
  - `docs/url-migration-map.md` — секция UI-4 переписана из «плана» в
    «выполнено» с детальной таблицей маршрутов и описанием POST→404.
  - `CLAUDE.md` — стек обновлён (FastAPI: один сервис обслуживает всё,
    app/ остался как legacy), структура папок и блок «Сервисы
    конфигуратора» переписаны под новое расположение.
- pytest регрессия: **1882 passed, 1 skipped, 0 failed** (UI-3 baseline
  был 1886; разница −4 — `test_permission_middleware.py` имел 8 кейсов,
  заменён на `test_configurator_access_perms.py` (6 кейсов); admin-часть
  `test_budget.py` отдельный файл; нетто покрытие не уменьшилось).

### UI-4.5 — перенос auctions/catalog/scheduler из app/ в portal/ [x]

Технический пред-этап перед UI-5. Не меняет URL, не трогает RBAC, не
переносит шаблоны — только переезд Python-модулей и устранение
кросс-импортов `app/ → portal/`.

Что переехало:

- `app/services/auctions/` → `portal/services/auctions/` (ingest, match,
  catalog/enrichment). 39+ потребителей: `portal/routers/{auctions,
  admin_auctions,nomenclature}.py`, `portal/scheduler.py`, тесты
  `test_auctions/`, скрипты `scripts/run_auctions_ingest.py`,
  `scripts/run_matching.py`, и др.
- `app/services/catalog/` → `portal/services/catalog/`
  (`brand_normalizer.py`). Потребители: `portal/services/configurator/
  price_loaders/orchestrator.py`, `tests/test_catalog/`, скрипты.
- `app/scheduler.py` (cron USD/RUB) → `portal/scheduler.py` (5 новых
  cron-job'ов `cbr_fetch_<HHMM>` + `ensure_initial_rate()` при старте
  через `portal/main.py`).
- Кросс-импорт `from app.services.auctions/catalog ...` из
  `portal/services/configurator/price_loaders/orchestrator.py` устранён.

DoD: `git mv` сохраняет историю; импорты `from app.services.{auctions,
catalog}` нигде не остались (grep пуст); `app/scheduler.py` удалён;
`app/main.py` без обращений к `app.scheduler`/`init_scheduler`/
`ensure_initial_rate`; pytest регрессия ≥1882 passed; план/история
обновлены; в `docs/office-ingest-deploy.md` появилась процедура
`git pull` на офисном сервере.

**Артефакты UI-4.5 (2026-05-11):**

- `portal/services/auctions/` — новая папка (через `git mv`): `ingest/`
  (8 файлов), `match/` (6 файлов), `catalog/` (cost_base.py + service.py +
  enrichment/ — 4 файла).
- `portal/services/catalog/` — новая папка (через `git mv`):
  `brand_normalizer.py`.
- `portal/scheduler.py` — добавлены:
  - константа `_CBR_CRON_TIMES` (08:30, 13:00, 16:00, 17:00, 18:15 МСК);
  - функция `_job_fetch_cbr()` — тело cron-задачи курса;
  - функция `ensure_initial_rate()` (public, для portal/main.py) —
    при пустой `exchange_rates` синхронно дёргает ЦБ;
  - регистрация 5 cron-job'ов `cbr_fetch_<HHMM>` в `init_scheduler()`;
  - расширена шапка-комментарий + final log-message включает cbr-точки.
- `portal/main.py` — startup-handler вызывает `init_scheduler()` плюс
  (гейтится тем же `_is_enabled()`) `ensure_initial_rate()`. На pytest
  оба гейта `False` — никаких сетевых походов из TestClient'ов.
- `app/scheduler.py` — **удалён**.
- `app/main.py` — убраны импорты `ensure_initial_rate`/`init_scheduler`/
  `shutdown_scheduler` и `@app.on_event("startup"/"shutdown")` для
  scheduler'а. Settings-флаг `settings.run_scheduler` оставлен в
  `app/config.py` без потребителей (удалится в UI-5).
- Импорты `from app.services.{auctions,catalog}` заменены на
  `from portal.services.{auctions,catalog}` в 36 файлах:
  `portal/scheduler.py`, `portal/routers/{auctions,admin_auctions,
  nomenclature}.py`, `portal/services/configurator/price_loaders/
  orchestrator.py`, все внутренние ссылки в перенесённых модулях
  `portal/services/auctions/{ingest,match,catalog}/*.py`, 7 скриптов
  (`scripts/run_auctions_ingest.py`, `run_matching.py`,
  `reparse_cards.py`, `normalize_brands.py`,
  `enrich_printers_mfu_from_names.py`, `auctions_enrich_export.py`,
  `auctions_enrich_import.py`), тесты `tests/test_auctions/*.py`,
  `tests/test_catalog/test_brand_normalizer.py`,
  `tests/test_portal/{test_nomenclature,test_admin_auctions}.py`.
  Дополнительно `tests/test_auctions/test_run_auctions_ingest.py` —
  monkeypatch'и строк-путей обновлены.
- В перенесённых файлах `from app.database import engine` заменён на
  `from shared.db import engine` (canonical-источник — `app.database`
  это просто re-export `shared.db`). Затронуты:
  `portal/services/auctions/catalog/{service,cost_base}.py`,
  `portal/services/auctions/catalog/enrichment/{importer,exporter}.py`.
- `portal/services/configurator/__init__.py` — переписан комментарий
  про кросс-импорт UI-4 (теперь UI-4.5 устранил).
- `CLAUDE.md` — обновлены: блок стека (упоминание UI-4.5), таблица
  «Где что искать» (добавлены `portal/services/auctions/` и
  `portal/services/catalog/`), структура папок, блок «Сервисы
  конфигуратора», объединённый блок «Расписание APScheduler» (одна
  таблица портала, app/scheduler.py больше нет).
- `docs/ui-architecture.md` — шапка ссылается на UI-4.5, в разделе
  «История» пункт UI-4.5 переписан из «плана» в «выполнено».
- `docs/url-migration-map.md` — добавлена секция «UI-4.5 (2026-05-11)»
  с таблицей переездов модулей и operational-предупреждением про
  офисный сервер.
- `docs/office-ingest-deploy.md` — добавлен раздел «Обновление кода на
  офисе после deploy» с процедурой `git pull` и sanity-проверкой
  импорта `from portal.services.auctions.ingest.orchestrator import
  run_ingest_once`.
- pytest регрессия: **1892 passed, 1 skipped, 0 failed** (UI-4 baseline
  был 1882; +10 нетто — это `tests/test_auctions/` и
  `tests/test_catalog/`, которые после устранения кросс-импорта без
  ошибок собираются с `portal.services.{auctions,catalog}`).

### UI-5 — финальная зачистка [ ]

Удалить:
- `app/main.py`, `Dockerfile`, `railway.json`, `app/templates/` (после переноса)
- Railway-сервисы `configurator` (prod) и `configurator-preprod` (pre-prod)
- DNS-записи `config.quadro.tatar` и `config-preprod.quadro.tatar` (через Reg.ru) — оставить как постоянный 301 через Railway custom domain или удалить

После UI-5 проект работает на одном FastAPI (`portal/main.py`), на одном Railway-сервисе (per environment), на одном поддомене (`app.quadro.tatar` / `app-preprod.quadro.tatar`).

DoD: prod/pre-prod работают только через portal-сервис; closed-loop smoke тестирование; запись в plans/история о завершении плана.

## Открытые вопросы

1. **RBAC для менеджеров после UI-5** — какие подразделы скрывать (например, «Настройки → Пользователи» может быть admin-only).
2. ~~**Структура `portal/routers/configurator/`** — оставить плоской или ввести подпапки~~ — Решено в UI-4: плоская структура (`main.py`, `projects.py`, `export.py`).
3. **Будущие модули** — собственник на 2026-05-11 не имеет фиксированного списка; структуру делаем гибкую.
4. ~~**UI-4.5 (новый этап)** — перенос `app/services/auctions/` и
   `app/services/catalog/`~~ — выполнено 2026-05-11, см. блок UI-4.5
   выше. После UI-4.5 кросс-импорт `app/ → portal/` устранён.
5. ~~**Перенос `app/scheduler.py`** (cron USD/RUB 5 раз в день) в
   `portal/scheduler.py`~~ — выполнено в составе UI-4.5. `app/scheduler.py`
   удалён, cron-job'ы `cbr_fetch_<HHMM>` живут в `portal/scheduler.py`
   под флагом `APP_ENV=production` / `RUN_BACKUP_SCHEDULER=1`.
6. **Перенос шаблонов `kp_template.docx` и `project_template.xlsx`** из
   `app/templates/export/` в `portal/templates/export/` или
   `portal/services/configurator/export/templates/` — на UI-5 вместе с
   удалением app/templates/.
7. **Operational на UI-4.5 деплое** — на офисном сервере
   `D:\AuctionsIngest\ConfiguratorPC2\` нужно сделать `git pull` ДО
   следующего тика Task Scheduler. Иначе очередной `scripts/
   run_auctions_ingest.py` упадёт с `ModuleNotFoundError: No module
   named 'app.services.auctions'`. Процедура зафиксирована в
   `docs/office-ingest-deploy.md → «Обновление кода на офисе после
   deploy»`. Оркестратору — попросить собственника сделать pull в
   ближайший RDP-сеанс.

## Итоговый блок

Статус на 2026-05-11: план составлен, решения собственника зафиксированы. **UI-1, UI-2, UI-3, UI-4 и UI-4.5 выполнены**:
- UI-1 — общий sidebar + новое меню + плашка «Аукционы» (pytest 1857 passed);
- UI-2 — перенос «Поставщики», «Комплектующие для ПК», «Очередь маппинга» в `portal/routers/databases/`, 301 со старых `/admin/{suppliers,components,mapping}` (pytest 1875 passed);
- UI-3 — оформление раздела «Настройки» в `portal/routers/settings/`, перевешивание трёх роутеров с `/admin/*` на `/settings/*` внутри портала + 301-обработчики со старых URL (pytest 1886 passed);
- UI-4 — перенос Конфигуратора ПК в `portal/routers/configurator/` + `portal/services/configurator/` (~80 файлов сервисов) + `portal/templates/configurator/`. Глобальная permission-middleware заменена на scoped `Depends(require_configurator_access)`. На `config.quadro.tatar` — catch-all 301 на portal/configurator + legacy admin-страницы (pytest 1882 passed);
- UI-4.5 — перенос `app/services/auctions/` и `app/services/catalog/` в `portal/services/`, перенос cron USD/RUB из `app/scheduler.py` в `portal/scheduler.py` (5 cron-job'ов `cbr_fetch_<HHMM>` + `ensure_initial_rate()` через portal startup); `app/scheduler.py` удалён, кросс-импорт `from app.services.{auctions,catalog} ...` устранён по всему репо (36 файлов).

Следующий этап — **UI-5** (финальная зачистка `app/main.py`, `Dockerfile`, `railway.json`, удаление Railway-сервиса `configurator`).
