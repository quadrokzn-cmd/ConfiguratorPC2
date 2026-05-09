# Этап 7/9 слияния QT↔CPC2: permission-ключи для модуля «Аукционы»

**Дата:** 2026-05-08

## 1. Какая задача была поставлена

Добавить разрешения на модуль аукционов в существующую permission-модель
C-PC2. Минимально:

1. Спроектировать структуру ключа `auctions` в `users.permissions JSONB`.
2. Расширить `shared/permissions.py::has_permission` для аукционных проверок.
3. Применить дефолты к существующим пользователям через миграцию.
4. Добавить тесты.

UI настройки прав — на Этап 9. Перенос аукционных роутов с реальными
permission-проверками — на Этап 8.

В брифе предлагалась структура `auctions: {view, edit_status, edit_settings}`
(вложенный JSONB), но с оговоркой «если C-PC2 имеет уже отличную от
ожидаемой структуру permissions — следуй тому, что есть».

## 2. Как я её решал

**Сначала read-only исследование.** Прочитал `shared/permissions.py`,
`shared/user_repo.py`, `portal/routers/admin_users.py`, шаблон
`portal/templates/admin/users.html`, существующие тесты в
`tests/test_portal/test_permissions.py` и `tests/test_portal/test_permission_ui.py`.
Запросил БД `kvadro_tech` (через `PGPASSWORD=postgres psql ... -c '\d users'`
и `SELECT login, role, permissions FROM users`) — выяснил, что:

- `users.permissions JSONB NOT NULL DEFAULT '{}'::jsonb` (миграция 017).
- Структура — **плоский** словарь `{key: bool}`, ключи перечислены в
  `MODULE_KEYS = [configurator, kp_form, auctions, mail_agent, dashboard]`.
- `has_permission(role, perms, key)` — admin → True безусловно;
  manager → `bool(perms.get(key))`.
- В БД 2 пользователя: admin (`{"configurator": true}`) и
  test1/manager (`{configurator: false, kp_form: false, auctions: false, mail_agent: false, dashboard: false}`).
- `auctions` уже присутствует в `MODULE_KEYS` как простой bool-флаг
  (зарезервирован под Этап 9Б.2).

Также проверил `auctions_staging/app/core/auth.py` — там Basic Auth без
RBAC, ровно один уровень доступа: «логин/пароль есть в env-словаре или нет».
Никаких permission-уровней в QT не было.

**Решение по архитектуре.** Выбрал плоскую модель вместо вложенного JSONB:

- **За плоскую модель:** консистентность с существующим стилем
  (`configurator`, `kp_form`, `mail_agent`, `dashboard` — все плоские
  bool-ключи); не нужно менять `has_permission`, `update_permissions`,
  шаблон users.html и UI-чекбоксы; меньше кода → меньше места для багов;
  ключи аукционов независимы (можно дать «менять статусы», не давая
  права видеть страницу — пограничный случай, но без потери выразительности).
- **Против вложенного JSONB:** ввёл бы второй паттерн в проекте без
  бизнес-причины; пришлось бы расширять `has_permission` под
  dot-notation (`auctions.view`); шаблон users.html отрисовывает чекбоксы
  через `{% for key in module_keys %}` — поддержка вложенных нужно было
  бы переписывать; тестов на вложенность пришлось бы писать втрое больше.

Результат — 3 ключа верхнего уровня:

```
auctions               — базовый view (страница /auctions, чтение списка)
auctions_edit_status   — менять статус лота
auctions_edit_settings — править margin_threshold, ktru_watchlist, excluded_regions
```

Ключи независимы. В роутах модуля будем явно проверять связку
`auctions AND auctions_edit_status` где это нужно — это нормальная цена
за плоскую модель.

**Реализация.**

1. `shared/permissions.py`: добавил два новых ключа в `MODULE_KEYS` и
   `MODULE_LABELS`. `has_permission` НЕ трогал — новые ключи работают
   через существующий механизм. Расширил docstring модуля списком всех
   permission-ключей с пояснениями.

2. `migrations/033_users_auctions_permissions.sql`: 6 идемпотентных
   UPDATE-ов (3 ключа × 2 роли). Каждый UPDATE использует
   `permissions || jsonb_build_object(<key>, <bool>)` с условием
   `WHERE NOT (permissions ? <key>)` — обновляет независимо по каждому
   ключу, уже выставленные администратором значения не перезаписывает.

3. Применил миграцию через `python scripts/apply_migrations.py`.

4. **Verify в `kvadro_tech`:**
   - admin: `{"configurator": true}` → `{auctions: true, configurator: true, auctions_edit_status: true, auctions_edit_settings: true}` ✅
   - test1/manager: `{configurator: false, kp_form: false, auctions: false, mail_agent: false, dashboard: false}` →
     к существующим ключам дописались только недостающие
     `auctions_edit_status: true, auctions_edit_settings: false`,
     `auctions: false` сохранился (сознательное действие админа,
     не дефолт) ✅.

5. **Идемпотентность** проверил вручную: `DELETE FROM schema_migrations
   WHERE filename='033_...'` + `apply_migrations.py` ещё раз → ровно те же
   значения в users, `auctions: false` у test1 не перевернулось ✅.

6. Тесты — добавил 5 новых в `tests/test_portal/test_permissions.py`:
   - `test_admin_has_all_auctions_perms` — admin с дефолтным JSONB и
     даже с пустым видит все три аукционных права;
   - `test_manager_default_no_edit_settings` — manager с дефолтным
     JSONB по миграции 033 не имеет edit_settings;
   - `test_manager_without_auctions_keys_has_no_access` — пользователь
     без ключа auctions* в permissions → False (не падает с KeyError);
   - `test_admin_role_overrides_missing_auctions_perm` — admin без
     явных прав в JSONB всё равно True (фиксирует это поведение
     против будущих регрессий);
   - `test_auctions_keys_are_independent` — три ключа независимы:
     view/edit_status/edit_settings можно иметь поодиночке.
   - Существующий `test_module_keys_contains_expected` расширен на
     новые два ключа.

7. Полный прогон pytest: **1514 passed, 2 skipped (live), 0 failed**,
   ~70 секунд. Прирост к мини-фиксу Этапа 6 (1509 → 1514) — ровно
   5 новых auction-permission-тестов.

8. План обновлён буллетом «Этап 7/9 завершён 2026-05-08», эта рефлексия
   создана.

## 3. Решил ли — да / нет / частично

**Решил полностью.** Все DoD выполнены:

- ✅ Миграция 033 применена, идемпотентна.
- ✅ 2 существующих пользователя имеют дефолтные auctions-permissions.
- ✅ `has_permission` корректно отвечает по `auctions/auctions_edit_status/auctions_edit_settings`.
- ✅ Тесты добавлены, **1514 passed**, 0 failed.
- ✅ План + рефлексия обновлены.

Рамки соблюдены: UI прав не трогал (Этап 9), аукционные роуты не
подключал (Этап 8), существующие проверки конфигуратора и портала
не сломаны (1509 старых тестов остались зелёными).

## 4. Эффективно ли решение, что можно было лучше

**Хорошо:**

- Не выдумал новый паттерн ради соответствия брифу — посмотрел в код
  и пошёл по существующему. Это сэкономило ~50% работы (не пришлось
  трогать `has_permission`, `update_permissions`, шаблон, UI-форму).
- Идемпотентность миграции через 6 независимых UPDATE-ов вместо одного
  большого `jsonb_set(..., create_missing=true)`: повторный прогон не
  трогает значения, выставленные руками. Это поймал, когда увидел у
  test1 `"auctions": false` — если бы написал «WHERE NOT (permissions ?
  'auctions') OR ...» (как в первоначальном брифе через `jsonb_set`),
  при пустом миграция всё равно бы перезаписала. Раздельные UPDATE-ы
  чище.
- Отдельные ключи `auctions_edit_status` / `auctions_edit_settings`
  вместо вложенного JSONB позволят админу через UI Этапа 9 раздавать
  тонкие права обычными чекбоксами — не нужен отдельный экран для
  «нюансов аукционов».

**Что можно было лучше:**

- В первоначальной версии миграции я писал условие
  `WHERE NOT (permissions ? 'auctions' AND permissions ? 'auctions_edit_status' AND permissions ? 'auctions_edit_settings')`
  — это могло бы перезаписать одно поле при отсутствии другого. Поймал
  при ручной проверке логики, переписал на 6 независимых UPDATE-ов.
- Можно было сразу проверить идемпотентность через `DELETE FROM
  schema_migrations + apply_migrations.py`, а не глазами читать SQL —
  сделал это в конце, но это надо встраивать в обычный workflow.
- Не делал ассерт-тест против БД (типа «после миграции у admin есть
  все три ключа») — все мои тесты на permissions in-memory. Для этапа
  7 это нормально (миграция один раз идёт по проду + повтор), но при
  будущей переэкспертизе таких миграций имеет смысл сделать
  смоук-тест против тестовой БД. На мониторе — не блокер.

**Архитектурное наследие:** ключи аукционов теперь стабильны и
зафиксированы тестами. Этап 8 при переносе роутов из
`auctions_staging/` будет ставить `Depends(require_permission(...))`
с этими тремя ключами — менять их уже нельзя без миграции данных.

## 5. Как было и как стало

**Было** (после мини-фикса Этапа 6):

- `MODULE_KEYS` = 5 элементов: `configurator, kp_form, auctions,
  mail_agent, dashboard`.
- `auctions` — простой bool, без зернистости.
- 2 пользователя в `kvadro_tech`:
  - admin: `{"configurator": true}` (3 ключа из 5 не выставлены).
  - test1/manager: 5 ключей (все false).
- `has_permission` проверяет один module_key, dot-notation нет.
- 1509 тестов зелёные.

**Стало** (после Этапа 7):

- `MODULE_KEYS` = 7 элементов: добавлены `auctions_edit_status` и
  `auctions_edit_settings`.
- `MODULE_LABELS` дополнен подписями «Аукционы — менять статус лота»,
  «Аукционы — править настройки».
- `shared/permissions.py` имеет docstring со списком всех 7 ключей и
  их назначения.
- `users.permissions` у обоих пользователей содержит три auction-ключа:
  - admin: `{auctions: true, configurator: true, auctions_edit_status: true, auctions_edit_settings: true}`
  - test1/manager: `{kp_form: false, auctions: false, dashboard: false, mail_agent: false, configurator: false, auctions_edit_status: true, auctions_edit_settings: false}`
- Миграция 033 применена, идемпотентна (повторный прогон → no-op).
- 1514 тестов зелёные (+5 новых auction-permission-тестов).
- `has_permission` без изменений — новые ключи работают через
  существующий механизм.
- Этап 8 (перенос аукционных роутов) разблокирован: ключи
  стабильны, дефолты есть, тесты есть.
