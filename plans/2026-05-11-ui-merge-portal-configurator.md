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

### UI-4 — перенос самого Конфигуратора [ ]

Перенести из `app/routers/`, `app/services/configurator/`, `app/templates/` в `portal/routers/configurator/`, `portal/services/configurator/`, `portal/templates/configurator/`.

Все URL конфигуратора (NLU-форма, проекты, экспорт КП) переезжают на `app.quadro.tatar/configurator/*`. `config.quadro.tatar` — в режиме 301-редиректа на новые URL.

DoD: NLU-форма работает на новом URL; экспорт КП работает; live-smoke на pre-prod.

### UI-5 — финальная зачистка [ ]

Удалить:
- `app/main.py`, `Dockerfile`, `railway.json`, `app/templates/` (после переноса)
- Railway-сервисы `configurator` (prod) и `configurator-preprod` (pre-prod)
- DNS-записи `config.quadro.tatar` и `config-preprod.quadro.tatar` (через Reg.ru) — оставить как постоянный 301 через Railway custom domain или удалить

После UI-5 проект работает на одном FastAPI (`portal/main.py`), на одном Railway-сервисе (per environment), на одном поддомене (`app.quadro.tatar` / `app-preprod.quadro.tatar`).

DoD: prod/pre-prod работают только через portal-сервис; closed-loop smoke тестирование; запись в plans/история о завершении плана.

## Открытые вопросы

1. **RBAC для менеджеров после UI-5** — какие подразделы скрывать (например, «Настройки → Пользователи» может быть admin-only).
2. **Структура `portal/routers/configurator/`** — оставить плоской или ввести подпапки (`nlu/`, `projects/`, `export/`)?
3. **Будущие модули** — собственник на 2026-05-11 не имеет фиксированного списка; структуру делаем гибкую.

## Итоговый блок

Статус на 2026-05-11: план составлен, решения собственника зафиксированы. **UI-1, UI-2 и UI-3 выполнены**:
- UI-1 — общий sidebar + новое меню + плашка «Аукционы» (pytest 1857 passed);
- UI-2 — перенос «Поставщики», «Комплектующие для ПК», «Очередь маппинга» в `portal/routers/databases/`, 301 со старых `/admin/{suppliers,components,mapping}` (pytest 1875 passed);
- UI-3 — оформление раздела «Настройки» в `portal/routers/settings/`, перевешивание трёх роутеров с `/admin/*` на `/settings/*` внутри портала + 301-обработчики со старых URL (pytest 1886 passed).

Следующий этап — UI-4 (перенос самого Конфигуратора из `app/` в `portal/routers/configurator/`).
