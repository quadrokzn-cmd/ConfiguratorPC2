# Карта URL-миграций Пути B

Документ ведёт реестр URL'ов, которые переезжают между конфигуратором
(`config.quadro.tatar`) и порталом (`app.quadro.tatar`) в рамках пяти
этапов Пути B. Цель — иметь единое место, где можно посмотреть «старый
URL → новый URL → статус» для:

- 301-редиректов в `app/main.py` (на время UI-2..UI-5);
- финального удаления старых обработчиков на UI-5;
- обновления закладок у менеджеров.

План этапов и решения собственника — `plans/2026-05-11-ui-merge-portal-configurator.md`.

## UI-1 (2026-05-11) — общий sidebar, без переноса роутеров

URL'ы не менялись. Изменился только UI-лейбл sidebar:

| Сторона | URL | Старый лейбл | Новый лейбл (UI-1) |
|---|---|---|---|
| portal | `/nomenclature` | «Справочник печати» | «Справочник оргтехники» |
| portal/sidebar | `/admin/components` (ссылка на configurator) | «Компоненты» | «Комплектующие для ПК» |

## UI-2 (2026-05-11) — перенос «Поставщиков», «Комплектующих для ПК», «Очереди маппинга»

| Старый URL (конфигуратор)            | Новый URL (портал)                  | 301-редирект |
|---|---|---|
| `/admin/suppliers`                    | `/databases/suppliers`              | ✓ (`app/main.py`) |
| `/admin/suppliers/new`                | `/databases/suppliers/new`          | ✓ через `{rest:path}` |
| `/admin/suppliers/{id}/edit`          | `/databases/suppliers/{id}/edit`    | ✓ |
| `/admin/suppliers/{id}/toggle`        | `/databases/suppliers/{id}/toggle`  | GET — ✓, POST → 404 |
| `/admin/suppliers/{id}/delete`        | `/databases/suppliers/{id}/delete`  | GET — ✓, POST → 404 |
| `/admin/components`                   | `/databases/components`             | ✓ |
| `/admin/components/{cat}`             | `/databases/components/{cat}`       | ✓ через `{rest:path}` |
| `/admin/components/{cat}/{id}`        | `/databases/components/{cat}/{id}`  | ✓ |
| `/admin/components/{cat}/{id}/edit`   | `/databases/components/{cat}/{id}/edit` | GET — ✓, POST → 404 |
| `/admin/components/{cat}/{id}/toggle-hidden` | `/databases/components/{cat}/{id}/toggle-hidden` | GET — ✓, POST → 404 |
| `/admin/mapping`                      | `/databases/mapping`                | ✓ |
| `/admin/mapping/{row_id}`             | `/databases/mapping/{row_id}`       | ✓ |
| `/admin/mapping/{row_id}/merge`       | `/databases/mapping/{row_id}/merge` | GET — ✓, POST → 404 |
| `/admin/mapping/{row_id}/confirm_as_new` | `/databases/mapping/{row_id}/confirm_as_new` | GET — ✓, POST → 404 |
| `/admin/mapping/{row_id}/defer`       | `/databases/mapping/{row_id}/defer` | GET — ✓, POST → 404 |
| `/admin/mapping/bulk_confirm_new`     | `/databases/mapping/bulk_confirm_new` | GET — ✓, POST → 404 |

> **Про POST.** Catch-all'ы в `app/main.py` ловят только GET. POST-формы
> у старых URL приведут к 404, т.к. сами обработчики удалены. Это OK:
> страницы конфигуратора `/admin/{suppliers,components,mapping}` больше
> не отдаются, поэтому формы с этими `action=...` физически не рендерятся
> ни в одном шаблоне. Если у кого-то старая страница ещё открыта в браузере,
> при сабмите получит 404 — перезагрузит страницу и попадёт на новый URL.

UI-лейблы (только UI, без смены URL и таблиц БД):

| URL | Старый лейбл | Новый лейбл (UI-2) |
|---|---|---|
| `/databases/components` | «Компоненты» | «Комплектующие для ПК» |

## UI-3 (2026-05-11) — перенос «Настроек» в `/settings/*`

Перенос внутри портала: файлы и так жили в `portal/`, переехала только
структура папок (`admin_*` → `routers/settings/*`, `templates/admin/*`
→ `templates/settings/*`) и URL-префиксы.

| Старый URL (портал)                  | Новый URL (портал)                      | 301-редирект |
|---|---|---|
| `/admin/users`                       | `/settings/users`                       | ✓ (`portal/main.py`) |
| `/admin/users/{id}/toggle`           | `/settings/users/{id}/toggle`           | GET — ✓, POST → 404 |
| `/admin/users/{id}/role`             | `/settings/users/{id}/role`             | GET — ✓, POST → 404 |
| `/admin/users/{id}/permissions`      | `/settings/users/{id}/permissions`      | GET — ✓, POST → 404 |
| `/admin/users/{id}/delete-permanent` | `/settings/users/{id}/delete-permanent` | GET — ✓, POST → 404 |
| `/admin/backups`                     | `/settings/backups`                     | ✓ |
| `/admin/backups/create`              | `/settings/backups/create`              | GET — ✓, POST → 404 |
| `/admin/backups/download/{tier}/{filename}` | `/settings/backups/download/{tier}/{filename}` | ✓ |
| `/admin/audit`                       | `/settings/audit-log`                   | ✓ |
| `/admin/audit/export`                | `/settings/audit-log/export`            | ✓ |

> **Про POST.** Catch-all'ы в `portal/main.py` ловят только GET.
> POST-формы у старых URL приведут к 404, т.к. сами обработчики удалены.
> Это OK: страницы `/admin/{users,backups,audit}` больше не отдаются,
> поэтому формы с этими `action=...` физически не рендерятся ни в одном
> шаблоне. Если у кого-то старая страница ещё открыта в браузере,
> при сабмите получит 404 — перезагрузит страницу и попадёт на новый URL.

Также обновлён конфигуратор: `config.quadro.tatar/admin/users` теперь
сразу 302 на `${portal_url}/settings/users` (раньше отдавал
`${portal_url}/admin/users`, что давало двойной hop).

Соседние `/admin/{price-uploads,auto-price-loads,diagnostics,auctions}*`
НЕ задеты — переедут отдельно (после UI-5, вместе с финальной
сортировкой по разделам).

UI-лейблы (sidebar / breadcrumbs) и subsection-ключи:

| URL | Старый лейбл | Новый лейбл / sub-key |
|---|---|---|
| `/settings/users` | «Пользователи» | без изменений (sub-key `users`) |
| `/settings/backups` | «Бэкапы» | без изменений (sub-key `backups`) |
| `/settings/audit-log` | «Журнал действий» | без изменений; sub-key `audit` → `audit-log` |

## UI-4 (2026-05-11) — перенос Конфигуратора в `/configurator/*`

Перенос между сервисами: NLU-форма, проекты, экспорт КП и весь сервисный
слой конфигуратора переехали из `app/` в `portal/routers/configurator/*`,
`portal/services/configurator/*` и `portal/templates/configurator/*`.
В `app/main.py` остался catch-all 301-редирект на `portal/configurator/*`
и admin_router (dashboard, budget, queries — уйдут в UI-5).

| Старый URL (конфигуратор)         | Новый URL (портал)                       | Редирект |
|---|---|---|
| `/`                                | `/configurator/`                         | ✓ (301, `app/main.py` корневой handler) |
| `/{rest:path}` (не /admin/, не /healthz, не /static/) | `/configurator/{rest}` | ✓ (301, catch-all handler) |
| `/query`                           | `/configurator/query`                    | через catch-all |
| `/query/{id}`                      | `/configurator/query/{id}`               | через catch-all |
| `/projects`                        | `/configurator/projects`                 | через catch-all |
| `/project/{id}`                    | `/configurator/project/{id}`             | через catch-all |
| `/project/{id}/new_query`          | `/configurator/project/{id}/new_query`   | через catch-all |
| `/project/{id}/rename` (POST)      | `/configurator/project/{id}/rename`      | POST через catch-all → 404 |
| `/project/{id}/delete` (POST)      | `/configurator/project/{id}/delete`      | POST через catch-all → 404 |
| `/project/{id}/select` (POST)      | `/configurator/project/{id}/select`      | POST через catch-all → 404 |
| `/project/{id}/deselect` (POST)    | `/configurator/project/{id}/deselect`    | POST через catch-all → 404 |
| `/project/{id}/update_quantity`    | `/configurator/project/{id}/update_quantity` | POST через catch-all → 404 |
| `/project/{id}/spec/...`           | `/configurator/project/{id}/spec/...`    | POST через catch-all → 404 |
| `/project/{id}/export/excel`       | `/configurator/project/{id}/export/excel` | через catch-all |
| `/project/{id}/export/kp`          | `/configurator/project/{id}/export/kp`   | через catch-all |
| `/project/{id}/emails/preview`     | `/configurator/project/{id}/emails/preview` | через catch-all |
| `/project/{id}/emails/send` (POST) | `/configurator/project/{id}/emails/send` | POST через catch-all → 404 |
| `/history`                         | `/configurator/history`                  | через catch-all |

> **Про POST.** Catch-all в `app/main.py` ловит только GET. POST-формы
> и AJAX-запросы у старых URL получат 404 — но самих страниц с такими
> action в DOM больше нет (все шаблоны переехали и используют новые
> `/configurator/*` URL). Если у кого-то старая страница ещё открыта
> в браузере, при сабмите получит 404 — перезагрузит страницу, попадёт
> на новый URL и отработает корректно.

**Сохранены в app/ (UI-5 будут удалены):**

| URL                | Что делает                                       |
|---|---|
| `/admin` (GET)      | Dashboard конфигуратора (admin_router)           |
| `/admin/budget`     | Страница бюджета OpenAI                          |
| `/admin/queries`    | Список всех запросов системы                     |
| `/admin/users`      | 302 на `${portal_url}/settings/users` (UI-3)     |
| `/healthz`          | Liveness-проверка для Railway                    |
| `/static/*`         | Статика (app.mount)                              |

**Permission middleware → Depends:**

Глобальная middleware `_enforce_configurator_permission` (была в
`app/main.py`, проверяла `permissions["configurator"]` на каждом
запросе) удалена. Заменена на scoped `Depends(require_configurator_access)`
на трёх роутерах `portal/routers/configurator/*`. Реализация —
`portal/dependencies/configurator_access.py` + exception_handler
для `ConfiguratorAccessDenied` в `portal/main.py`.

| Сценарий                                       | Поведение                                |
|---|---|
| Не залогинен                                   | 302 на `/login` (LoginRequiredRedirect)  |
| Залогинен, нет `permissions['configurator']`   | 302 на `/?denied=configurator`           |
| То же + `Accept: application/json` (без html)  | 403 JSON                                 |
| Залогинен, есть право (или admin)              | 200, нормальная обработка                |

Все 301-редиректы будут жить пока `config.quadro.tatar` не упразднён
(UI-5).

## UI-4.5 (2026-05-11) — перенос auctions/catalog/scheduler из app/ в portal/

URL'ы не менялись. Этап чисто внутренний — перенос Python-модулей и
импортов плюс merge cron-задачи USD/RUB:

| Что переехало | Откуда | Куда |
|---|---|---|
| auctions (ingest/, match/, catalog/) | `app/services/auctions/` | `portal/services/auctions/` |
| catalog (brand_normalizer) | `app/services/catalog/` | `portal/services/catalog/` |
| cron USD/RUB (5 точек в день в МСК) | `app/scheduler.py` | `portal/scheduler.py` (cron-job'ы `cbr_fetch_<HHMM>`) |
| `ensure_initial_rate()` | `app/main.py` startup | `portal/main.py` startup (гейт `_is_enabled()`) |

`app/scheduler.py` удалён. Импорты `from app.services.auctions/catalog ...`
заменены на `from portal.services.auctions/catalog ...` во всём репо
(36 файлов: portal-роутеры, sub-сервисы, scheduler, скрипты,
test_auctions/, test_catalog/, test_portal/).

Активация cron-задач после UI-4.5 — единый флаг: `APP_ENV=production`
(Railway prod/pre-prod) или `RUN_BACKUP_SCHEDULER=1` (ручной запуск).
Старая переменная `RUN_SCHEDULER` больше нигде не считывается (поле
`settings.run_scheduler` пока остаётся в `app/config.py` для совместимости,
удалится в UI-5 вместе с `app/`).

> **Операционно:** на офисном сервере (`D:\AuctionsIngest\ConfiguratorPC2\`)
> после деплоя UI-4.5 нужен `git pull` ДО следующего тика Task Scheduler —
> иначе следующий запуск `scripts/run_auctions_ingest.py` упадёт с
> `ModuleNotFoundError: No module named 'app.services.auctions'`.
> Подробнее — `docs/office-ingest-deploy.md`.

## UI-5 (2026-05-11) — финальная зачистка app/

**Решение собственника 2026-05-11:** Вариант A (полное удаление). URL
admin-страниц `/admin`, `/admin/budget`, `/admin/queries` сохранены —
менеджеров с закладками нет, редиректов с `config.quadro.tatar` не
нужно. Если кто-то откроет старый URL — получит DNS-ошибку (это OK).

| Старый URL (config.quadro.tatar) | Новый URL (app.quadro.tatar) | Редирект |
|---|---|---|
| `/admin` | `/admin` (portal) | ✗ (URL сохранён без редиректа) |
| `/admin/budget` | `/admin/budget` (portal) | ✗ |
| `/admin/queries` | `/admin/queries` (portal) | ✗ |
| любые `/admin/{suppliers,components,mapping}*` | были UI-2 301-handlers в `app/main.py` | удалены вместе с app/main.py |
| `/`, `/{rest}` (UI-4 catch-all) | были 301 на `portal/configurator/*` | удалены вместе с app/main.py |

Что удалено из репо в одном коммите UI-5:

- Папка `app/` целиком: `main.py`, `auth.py`, `config.py`, `database.py`,
  `templating.py`, `routers/admin_router.py`, `templates/admin/*`,
  `templates/_macros/`, `templates/base.html`, `models/`, `schemas/`,
  `services/__init__.py`.
- `Dockerfile`, `railway.json` (использовались только для конфигуратора;
  portal использует `Dockerfile.portal` + `railway.portal.json`).
- `Procfile` (single-app Heroku-стиль, ссылался только на `app.main:app`).
- `scripts/smoke_stage_6_2.py` — dead-код, опирался на `app.main`/
  `app.routers.main_router`/`app.routers.project_router`.
- Тесты `tests/test_web/`: 8 файлов под `TestClient(app/main.py)` —
  `__init__.py`, `conftest.py`, `test_access.py`, `test_admin_budget.py`,
  `test_configurator_redirects.py`, `test_databases_redirects.py`,
  `test_healthz.py`, `test_ui1_sidebar_app.py`. Два файла, у которых
  не было связи с app/ (`test_migration.py`, `test_bootstrap_admin.py`),
  переехали в корень `tests/` через `git mv`.

Что переехало внутри репо:

| Откуда | Куда | Метод |
|---|---|---|
| `app/templates/admin/dashboard.html` | `portal/templates/admin/dashboard.html` | git mv (внутренние URL `/admin/mapping`→`/databases/mapping`, `/query/`→`/configurator/query/`) |
| `app/templates/admin/budget.html` | `portal/templates/admin/budget.html` | git mv |
| `app/templates/admin/all_queries.html` | `portal/templates/admin/all_queries.html` | git mv (внутренние URL `/query/`→`/configurator/query/`) |
| `app/templates/export/kp_template.docx` | `portal/templates/export/kp_template.docx` | git mv |
| `app/templates/export/project_template.xlsx` | `portal/templates/export/project_template.xlsx` | git mv |
| `app/config.Settings` (живой код) | `shared/config.Settings` | новый файл, скопирован минус dead-field `run_scheduler`/`RUN_SCHEDULER` |
| `app/templating.to_rub`/`fmt_rub`/`current_exchange_rate`/`static_url` | `portal/templating.*` (живой код, без re-export'ов из app/) | копия + ленивые импорты `shared.db` и `portal.services.configurator.export.exchange_rate` |
| `app/routers/admin_router.py` (dashboard, budget, queries) | `portal/routers/admin.py` (3 admin-страницы) | новый файл, dashboard_legacy_alias `/admin/dashboard`→301 не сохранён (мёртвая трасса) |

Что заменено в импортах (54 файла, regex-замена):

- `from app.config import settings` → `from shared.config import settings`
- `from app.database import …` → `from shared.db import …`
- `from app.auth import …` → `from shared.auth import …`
- `from app.templating import …` → `from portal.templating import …`

Operational:

- Railway-сервисы `configurator` (prod) и `configurator-preprod` остановлены
  и удаляются собственником вручную через Railway UI.
- DNS-записи `config.quadro.tatar` и `config-preprod.quadro.tatar`
  удаляются собственником в Reg.ru (CNAME → Railway target → no-op
  после удаления сервисов).

После UI-5 проект работает на одном FastAPI (`portal/main.py`), на
одном Railway-сервисе per environment, на одном поддомене
(`app.quadro.tatar` / `app-preprod.quadro.tatar`). План
`plans/2026-05-11-ui-merge-portal-configurator.md` закрыт полностью
(UI-1, UI-2, UI-3, UI-4, UI-4.5, UI-5 — все ✓).
