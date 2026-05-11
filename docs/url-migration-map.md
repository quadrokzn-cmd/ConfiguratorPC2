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

## UI-3 (план) — «Настройки» в `/settings/*`

Планируется переезд:

| Сейчас (портал)           | После UI-3                  |
|---|---|
| `/admin/users`            | `/settings/users`           |
| `/admin/backups`          | `/settings/backups`         |
| `/admin/audit`            | `/settings/audit-log`       |

Старые `/admin/users*` и т.д. получат внутренние 301 на новые URL.

## UI-4 (план) — перенос Конфигуратора в `/configurator/*`

| Сейчас (конфигуратор `config.quadro.tatar`)  | После UI-4 (портал)        |
|---|---|
| `/`                       | `/configurator/`            |
| `/query`                  | `/configurator/query`       |
| `/projects`               | `/configurator/projects`    |
| `/project/{id}`           | `/configurator/project/{id}` |
| `/history`                | `/configurator/history`     |
| `/admin` (dashboard)      | `/configurator/admin`?      |
| `/admin/budget`           | `/configurator/budget`?     |
| `/admin/queries`          | `/configurator/queries`?    |
| `/project/{id}/export/*`  | `/configurator/project/{id}/export/*` |

Все 301-редиректы будут жить пока `config.quadro.tatar` не упразднён
(UI-5).

## UI-5 (план) — финальная зачистка

- Удаляются 301-обработчики из `app/main.py`.
- Удаляется сам `app/main.py`, `Dockerfile`, `railway.json`,
  `app/templates/`.
- Railway-сервисы `configurator` (prod) и `configurator-preprod`
  останавливаются и удаляются.
- DNS-записи `config.quadro.tatar` и `config-preprod.quadro.tatar`
  либо удаляются, либо переключаются на постоянный 301 на портал
  через Railway custom domain.

После UI-5 проект работает на одном FastAPI (`portal/main.py`), на
одном Railway-сервисе per environment, на одном поддомене
(`app.quadro.tatar` / `app-preprod.quadro.tatar`).
