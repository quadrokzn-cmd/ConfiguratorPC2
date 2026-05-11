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

### UI-2 — перенос Баз данных в portal/ [ ]

Перенести из `app/routers/`, `app/services/`, `app/templates/` в `portal/`:
- Поставщики (`/suppliers`)
- Компоненты (`/components`) — переименовать UI-лейбл в «Комплектующие для ПК»
- Очередь маппинга (`/mapping`)

Переименовать UI-лейбл «Справочник печати» → «Справочник оргтехники» (без изменения URL `/nomenclature` или таблицы БД).

URL после миграции: всё под `app.quadro.tatar/databases/{suppliers,components,mapping,nomenclature,prices,autoload}`. Старые URL на `config.quadro.tatar/{suppliers,components,mapping}` получают 301-редирект (механизм будет в UI-5; на UI-2 — заглушки через `app/main.py`).

DoD: все три страницы работают на новых URL; pytest test_web/ обновлены под новые пути; редиректы со старого хоста.

### UI-3 — перенос Настроек в portal/ [ ]

Создать раздел «Настройки» в portal/. Внутри:
- Пользователи (`/settings/users`)
- Бэкапы (`/settings/backups`)
- Журнал действий (`/settings/audit-log`)

Если «Пользователи» уже в portal — просто перевесить URL в `/settings/users`. Бэкапы и Журнал — переехать или сгруппировать.

DoD: все три страницы работают под `/settings/*`; pytest зелёный.

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

Статус на 2026-05-11: план составлен, решения собственника зафиксированы. **UI-1 выполнен** (общий sidebar + новое меню + плашка «Аукционы», pytest зелёный). Следующий этап — UI-2 (перенос «Поставщики», «Комплектующие для ПК», «Очередь маппинга» в `portal/routers/databases/`).
