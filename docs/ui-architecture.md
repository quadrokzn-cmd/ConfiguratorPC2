# Архитектура UI: общий sidebar портала и конфигуратора

Документ описывает структуру верхней навигации `QuadroTech-Suite` после
этапов **UI-1** и **UI-2** Пути B (слияние портала и конфигуратора в одно
FastAPI; полный план — `plans/2026-05-11-ui-merge-portal-configurator.md`).

## Где живёт sidebar

Один партиал на оба сервиса: `shared/templates/_partials/sidebar.html`.

- Подключается из `portal/templates/base.html` (для всех страниц портала).
- Подключается из `app/templates/base.html` (для всех страниц конфигуратора).
- Совместно с ним используется виджет курса ЦБ —
  `shared/templates/_partials/fx_widget.html`.

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
| Настройки | `settings` | `users`, `backups`, `audit` |

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
| `/admin/users*` | `settings` | `users` |
| `/admin/backups*` | `settings` | `backups` |
| `/admin/audit*` | `settings` | `audit` |
| прочее (включая `/`) | `home` | `None` |

> **UI-2 (2026-05-11):** разделы «Поставщики», «Комплектующие для ПК»
> (бывшие «Компоненты») и «Очередь маппинга» переехали из конфигуратора
> (`config.quadro.tatar/admin/{suppliers,components,mapping}`) в портал
> под префикс `/databases/`. URL-маппинг и подпункты sidebar обновлены.
> Со старых URL стоит 301-редирект (см. ниже). «Прайс-листы» и
> «Автозагрузка» сохранили префикс `/admin/*` — они переедут на префикс
> `/databases/` отдельно (по плану UI-5).

**Конфигуратор** (`app/templates/base.html`):

Всегда `active_section='configurator'`. Подпункт:

| URL prefix | active_subsection |
|---|---|
| `/`, `/query*` | `new_query` |
| `/projects*`, `/project*` | `projects` |
| `/history*` | `history` |
| прочее (`/admin*` и т.п.) | `None` |

## 301-редиректы со старых URL конфигуратора

После UI-2 в конфигураторе остались только 301-обработчики на месте
переехавших страниц (`app/main.py` около строки 160):

| Старый URL (`config.quadro.tatar`)         | Новый URL (`app.quadro.tatar`)        |
|---|---|
| `/admin/suppliers`                         | `/databases/suppliers`               |
| `/admin/suppliers/{rest:path}`             | `/databases/suppliers/{rest:path}`   |
| `/admin/components`                        | `/databases/components`              |
| `/admin/components/{rest:path}`            | `/databases/components/{rest:path}`  |
| `/admin/mapping`                           | `/databases/mapping`                 |
| `/admin/mapping/{rest:path}`               | `/databases/mapping/{rest:path}`     |

Хост в редиректе берётся из `settings.portal_url` — никакого хардкода
домена нет, на pre-prod это `app-preprod.quadro.tatar`. Все шесть
обработчиков — простые `def`-функции без require_admin: 301 публичный,
любой пользователь (в т.ч. с просроченной сессией) попадёт на новый
URL и уже там пройдёт обычный login-flow.

> **Не зацепляем соседей.** Используются три точечных catch-all'а
> (по одному на каждый раздел) плюс по корневому handler'у — не один
> широкий `/admin/{path:path}`. Это гарантирует, что `/admin`, `/admin/users`
> (редирект 302 на портал), `/admin/auto-price-loads`, `/admin/budget`,
> `/admin/queries` остаются страницами конфигуратора, а не уходят на
> portal/databases.

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

Если в новой ссылке используется иконка, которой нет в обоих файлах
`portal/templates/_macros/icons.html` и `app/templates/_macros/icons.html`,
её нужно добавить **в оба** — иначе в одном из сервисов sidebar
сломается (Jinja вернёт fallback-кружок).

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
- **UI-3..UI-5 (план):** перенос «Настроек» в `/settings/*`, переезд
  самого Конфигуратора в `/configurator/*`, упразднение
  `config.quadro.tatar`. После UI-5 `current_service='portal'` для всего;
  параметр и логика «другого сервиса» в `sidebar.html` упрощаются.
