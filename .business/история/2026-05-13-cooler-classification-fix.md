# Рефлексия: фикс classification-бага cooler (fan-разветвитель)

**Дата:** 2026-05-13
**Чат:** worktree `feature/cooler-classification-fix` (параллельно с двумя другими чатами на master)
**Исполнитель:** Claude Opus 4.7 (1M context)

## 1. Какая задача была поставлена

Триггер собственника — скриншот теста «Тест с двумя накопителями (12.05.2026
19:21)»: в категорию «Кулер» подбор поставил `FS-04 ARGB` (ID-Cooling, Netlab,
$4 ≈ 303 ₽). Фактически это 4-pin сплиттер питания, а не CPU-кулер.
Класс-баг точечный (multi-storage NLU в том же тесте сработал корректно).

**DoD:** новая эвристика `is_likely_fan_splitter` в `shared/component_filters.py`,
применение в price-loader для автоматического хайда при будущих загрузках,
backfill-скрипт `scripts/hide_fan_splitters_in_cooler.py` (dry-run + --apply),
SQL-аудит, тесты, backfill на pre-prod, обновление плана, рефлексия. Без
миграций схемы, всё через код. Применение на prod — рекомендация, не запускать.

## 2. Как я её решал

1. **Worktree.** Создал `feature/cooler-classification-fix` от `origin/master`,
   скопировал `.env` и `.env.local.preprod.v2` для доступа к локальной и
   pre-prod БД соответственно.
2. **Discovery.** Прочитал `shared/component_filters.py`, `engine/candidates.py`,
   `price_loaders/orchestrator.py`. Обнаружил:
   - `engine/candidates.py::get_cheapest_cooler` уже использует
     `_hidden_filter(cl.is_hidden = FALSE)` — пометки достаточно, правок engine не нужно.
   - В orchestrator при создании скелета `coolers` уже применяется
     `is_likely_case_fan`. Логичное место для добавления нового детектора.
   - Параллельно в shared есть `is_likely_cable_or_adapter`, который ловит
     слова «разветвитель|splitter», но **применяется ТОЛЬКО в
     `scripts/reclassify_non_coolers.py` (ручной запуск), не в orchestrator** —
     поэтому свежезагруженные через прайс позиции типа FS-04 ARGB не хайдились
     автоматически.
3. **SQL-аудит на локальной и pre-prod БД.** Нашёл 4 родственные позиции:
   `FS-04 ARGB` (id=1081), `FS-04` (id=1082), `FS-06 ARGB` (id=1083),
   `ARCTIC Case Fan Hub ACFAN00175A` (id=820). На обеих БД все 4 **уже**
   `is_hidden=TRUE` — захайжены ранее `reclassify_non_coolers.py` через
   `is_likely_cable_or_adapter`. То есть pre-prod чистая, а prod не чистая
   (там скрипт никогда не запускался — собственник как раз видит баг).
4. **Реализация эвристики.** Добавил `is_likely_fan_splitter` в
   `shared/component_filters.py` со специфическими триггерами (разветвитель,
   сплиттер, splitter, удлинитель, fan hub, фан-хаб, PWM hub, fan controller,
   fan switch, multi-fan, 3pin/4pin разъём) и двойным защитным слоем:
   общий `_CPU_COOLER_HINTS` (башня/радиатор/AIO/процессорн) + специфический
   `_FAN_SPLITTER_CPU_GUARDS` (socket/AM4-5/LGA/low-profile/TDP ≥50W).
5. **Применение в orchestrator.** В `_create_skeleton` для `table == "coolers"`
   добавил OR с `is_likely_case_fan`. Теперь при следующей загрузке прайса
   (любым из 6 поставщиков) свежий разветвитель сразу получит `is_hidden=TRUE`.
6. **Backfill-скрипт `scripts/hide_fan_splitters_in_cooler.py`** по образцу
   `reclassify_non_coolers.py`: агрегирует raw_names из supplier_prices,
   защищается дополнительно по `supported_sockets`/`max_tdp_watts`, идемпотентный.
   `--dry-run` по умолчанию, `--apply` для записи; один общий audit-event.
7. **Тесты.** 10 unit-тестов в `tests/test_shared/test_component_filters.py`
   (positive/negative/empty/защитные слои), 1 тест в `test_orchestrator.py`
   (загрузка XLSX-прайса → is_hidden=TRUE), 7 тестов в новом файле
   `test_configurator_hide_fan_splitters.py` (dry-run/find/skip-real/apply/
   engine-skip/idempotent).
8. **Backfill на pre-prod (`--dry-run`).** 0 кандидатов (всё уже захайжено
   ранее) — `--apply` не запускал, нечего делать. Зафиксировал факт в плане.
9. **Полный pytest.** 1744 (без auctions) + 205 (test_auctions) = 1949 passed,
   2 skipped (live), 0 failed.
10. **Обновил `plans/2026-04-23-platforma-i-aukciony.md`** мини-этапом
    2026-05-13.

## 3. Решил ли — да / нет / частично

**Да.** Бизнес-цель закрыта: в любой следующей загрузке прайса свежий
fan-разветвитель/PWM-хаб/fan-контроллер автоматически попадёт в `coolers` с
`is_hidden=TRUE` и не выйдет в подбор. Тестовое покрытие защищает от регрессии.
Backfill готов и может быть применён собственником на prod одной командой.

На pre-prod бэкап не пришлось делать вручную (уже было сделано
`reclassify_non_coolers.py`). На prod применение оставлено собственнику —
по принципу «на prod ничего не делаешь сам».

## 4. Эффективно ли решение, что можно было лучше

**Эффективно:** ушёл от ручных правок в коде engine'а — изменения локализованы
в `shared/component_filters.py` (1 новая функция) + 1 строчка в orchestrator
(OR с существующим детектором). Backfill-скрипт повторил отлаженный паттерн
`reclassify_non_coolers.py` (dry-run/apply/audit/идемпотентность) — переиспользование
архитектуры вместо нового подхода.

**Что было лучше сделать иначе:**

1. **Утёк DSN pre-prod в чат.** При попытке найти имя переменной в
   `.env.local.preprod.v2` через `Grep output_mode=content` я непреднамеренно
   распечатал в системный лог DSN с паролем (`postgresql://...rlwy.net:32320`).
   Это нарушение `feedback_railway_raw_editor_secrets` по сути (хоть и не Raw
   Editor) и общего правила «DSN/пароли в чате не светить». **Урок:**
   при работе с .env-файлами никогда не использовать `output_mode=content`
   на строках, в которых может быть пароль. Лучше — запустить Python-скрипт
   через Bash, который прочитает env и распечатает только список ключей
   (без значений). Зафиксирую в memory отдельным feedback-блоком.

2. **На локальной и pre-prod БД нечего было хайдить** — мог бы это понять
   раньше, если бы проверил `reclassify_non_coolers.py` сразу при discovery.
   Это не повлияло на итог (фикс всё равно нужен — orchestrator должен ловить
   автоматически), но 5 минут сэкономил бы. **Урок:** при «классификационных»
   багах сначала проверять, нет ли уже скрипта/детектора, который частично
   решает задачу.

3. **Дублирование с `is_likely_cable_or_adapter`** — оба детектора ловят слово
   «разветвитель|splitter». Можно было реализовать через `is_likely_cable_or_adapter`
   в orchestrator (просто подключить тот же фильтр). Но задание явно требовало
   отдельную функцию по аналогии с `is_likely_case_fan` — и это оправдано:
   `is_likely_cable_or_adapter` слишком общий (ещё и USB-кабели, и панели,
   и адаптеры), а `is_likely_fan_splitter` узкий и более защищённый —
   меньше риск false-positive на USB-кулерах подсветки.

## 5. Как было и как стало

**Было** (на prod-БД, состояние воспроизведено логически из скриншота
собственника):

- `coolers.FS-04 ARGB` видим (`is_hidden=FALSE`), без `supported_sockets`,
  без `max_tdp_watts`.
- `engine.candidates.get_cheapest_cooler` берёт ОДНУ строку c минимальной
  ценой в USD среди подходящих по сокету и TDP. Поскольку у FS-04 ARGB
  `supported_sockets IS NULL` и `max_tdp_watts IS NULL`, основной WHERE
  (`cl.supported_sockets IS NOT NULL AND cl.max_tdp_watts IS NOT NULL AND
   :sock = ANY(cl.supported_sockets)`) её отсекает.

Подожди — тогда баг сложнее: если эти поля NULL, как FS-04 ARGB вообще
попал в выдачу? Это значит либо у этой записи на prod **уже заполнен**
`supported_sockets`/`max_tdp_watts` (ошибочно — AI-обогащение могло
проставить «универсальный» сокет вроде LGA1700+AM5 и TDP=200W, потому
что в имени бренд ID-Cooling выглядит как CPU-кулер), либо подбор шёл
через `fixed`-ветку без проверки сокета. Это уточнение НЕ ломает решение
(пометка `is_hidden=TRUE` всё равно убирает запись из обеих веток —
там же `_hidden_filter(cl.is_hidden = FALSE)`). Но фиксирую как открытый
вопрос для собственника: на prod проверить `SELECT id, supported_sockets,
max_tdp_watts FROM coolers WHERE sku ILIKE '%FS-04%'`.

**Стало:**

- Свежая загрузка прайса с разветвителем → `is_hidden=TRUE` ставится сразу
  в orchestrator при создании скелета. AI-обогащение в дальнейшем НЕ
  переопределит `is_hidden` (оно работает по атрибутам, а не по флагу).
- Существующие 4 родственные SKU на pre-prod уже `is_hidden=TRUE` —
  закрыто `reclassify_non_coolers.py` ранее. На prod backfill ждёт ручного
  применения собственником через `scripts/hide_fan_splitters_in_cooler.py
  --apply` (рекомендация в плане).
- Полное тестовое покрытие: эвристика, orchestrator, backfill — 1949
  тестов зелёные.

**Файлы:**

- Добавлены: `scripts/hide_fan_splitters_in_cooler.py`,
  `tests/test_portal/test_configurator_hide_fan_splitters.py`.
- Изменены: `shared/component_filters.py`,
  `portal/services/configurator/price_loaders/orchestrator.py`,
  `tests/test_shared/test_component_filters.py`,
  `tests/test_price_loaders/test_orchestrator.py`,
  `plans/2026-04-23-platforma-i-aukciony.md`.

**Следующие шаги для собственника** (рекомендации):

1. `DATABASE_URL=<prod-DSN> python scripts/hide_fan_splitters_in_cooler.py --dry-run`
   → если кандидатов >0 → `--apply`.
2. Перезапустить проблемный тест конфигурации — кулер должен смениться.
3. Если в категории `cooler` на prod ещё есть мусор не-разветвителей
   (термопасты, монтажные комплекты, кабели) — прогнать общий
   `scripts/reclassify_non_coolers.py --confirm --confirm-yes`. Это уже
   существующий скрипт, ловит шире (4 детектора одновременно).
