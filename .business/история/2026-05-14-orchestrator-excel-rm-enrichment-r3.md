# Оркестратор-чат 2026-05-13 / 2026-05-14: Excel-каталог, RM Фаза D, enrichment round 3

## 1. Какая задача была поставлена

Продолжение оркестратор-сессии после рефлексии 2026-05-13 серии багфиксов (`7f3ffa1`). По ходу длинного чата собственник сам ставил задачи каскадом — каждая выбиралась из backlog после закрытия предыдущей:

1. Backlog #11 — Excel-выгрузка/загрузка каталога товаров (полный цикл 5 фаз).
2. RM Фаза D — мониторинг первого боевого тика автозагрузки Resurs Media 2026-05-13 07:40 МСК.
3. Орк-апдейт после серии: даты рефлексий, fallback курса, backlog #18 xdist, новое memory-правило.
4. Discovery «почему печать без цен в Excel» (наблюдение собственника после скачивания Excel).
5. Колонки «наличие/доступность» в Excel-каталоге.
6. Round 2 enrichment печатной техники на prod (повторный + новый empty-пул).
7. Round 3 enrichment 5 параллельными чатами (Pantum / Epson+Ricoh / Canon / Kyocera / HP).
8. Расследование методического инцидента — обнаружение использования не-официальных источников в нескольких enrichment-чатах.

## 2. Как я её решал

Через **параллельные Claude Code чаты с worktree-изоляцией** в режиме «промт-под-ключ» (одна команда собственника в новый чат, и чат сам делает всё, что нужно). Хроника:

| Тема | Зона | Запуск | Закрытие | Коммит |
|---|---|---|---|---|
| Excel Фаза 1 (Discovery) | master | первым | первым | `8f6a2bc` |
| Excel Фаза 2 (Export) | worktree | параллельно | первым | `fa28cba` |
| Excel Фаза 3 (Import) | worktree | параллельно | вторым (rebase) | `6e5dc14` |
| RM-monitor Фаза D | worktree | параллельно | третьим (после паузы по xdist contention) | `b32cae2 + 3e2f8a1 + d221e64 + ee60db5` |
| Орк-апдейт даты+fallback+backlog #18 | master | мой | мой | `e400487` |
| Excel Фазы 4-5 + UX прайсы | worktree | следующим | следующим | `ed189cc` |
| Discovery #3 (печать без цен) | worktree | следующим | следующим (нашёл orchestrator-mfu-category bug) | `cf7265c` |
| Колонки наличия | worktree | следующим | следующим | `5b85e05` |
| Round 2 enrichment (empty-пул) | worktree | следующим | следующим | `9e815b5` |
| Round 3 Epson+Ricoh | worktree | параллельно | первым из 5 | `fc4777b` |
| Round 3 Kyocera | worktree | параллельно | вторым (rebase) | `803d877` |
| Round 3 Canon | worktree | параллельно | третьим (rebase) | `36e62d3` |
| Round 3 Pantum | worktree | параллельно | четвёртым (rebase) | `822b695` |
| Round 3 HP — инцидент | worktree | параллельно | отложен | `0063039` |

Между параллельными группами — **серия из 8 параллельных enrichment-чатов** (включая 4 subagent'а внутри HP-re-spawn). В пик одновременно работало 4 чата (потолок `feedback_subagent_parallelism`).

Orchestrator-коммиты с моей стороны: `e400487` (даты + fallback 90.0 + backlog #18) и финальный `0063039` (ff-merge HP-incident-ветки + рефлексия).

## 3. Решил ли — да / частично

**Да** по основным целям:
- Backlog #11 (Excel-каталог) **на 100%** — 5 фаз + UX-правки прайсов + колонки наличия + фикс orchestrator-mfu-category bug.
- RM Фаза D — первый prod-тик Resurs Media отработал успешно после фикса `_CATEGORY_GROUP_MAP` под prod-каталог (pu_id=48, rows_total=151, Notification §4.7 ✓).
- Round 2 enrichment (empty-пул) — 358 → 28 SKU (sokraschenie −92%).
- Round 3 на 4 брендах (Pantum / Epson+Ricoh / Canon / Kyocera) — 220 SKU обогащено на prod.

**Частично:**
- Round 3 HP — отложен полностью (140 SKU не применено на prod, инцидент).
- Round 3 Canon / Epson / Ricoh — **применены на prod, но с грязными источниками** (Canon полностью с printer-copir.ru, Epson частично с DNS-shop/Citilink/3Logic, Ricoh частично с эвристикой). 129 SKU требуют re-enrichment с whitelist'а официальных источников.
- Backlog #16 (false-cooler'ы) / #18 (xdist) / Avision-Katusha empty / Mojibake / 9b / RBAC / #12 / #13 / Green Place — не тронуты.

## 4. Эффективно ли решение, что можно было лучше

**Что сработало:**

- **Параллельные чаты с worktree-изоляцией** — потолок 4-5 одновременно держал темп. Конфликтов в коде 0; конфликты в плане решаются стандартным «оставить все блоки».
- **Промты под ключ** — собственник делал минимум ручных действий: ввод одного промта + чтение рефлексии. Дату 2026-05-13 в каждом промте явно прописывал (memory `feedback_prompt_explicit_date` сработала — RM-monitor правильно датировал свою рефлексию, в отличие от Discovery/Export/Import чатов до создания правила).
- **Discovery-первый подход** в трёх ключевых местах — (а) пользователь увидел пустые цены у МФУ → discovery-чат нашёл orchestrator-mfu-category bug (439 строк правильно пере-классифицированы); (б) round 2 discovery нашёл корневую причину пустых attrs (357 SKU родились 2026-05-10 при первичном импорте ДО интеграции regex_name pass); (в) HP-инцидент — собственник заметил `printer-copir.ru` в Canon-сводке, я подтвердил наблюдение и развернул проверку всех рефлексий round 3.
- **Orchestrator-апдейт `e400487` середины дня** — переименование рефлексий из 2026-05-14 в 2026-05-13, fallback курса в плане, backlog #18 + новое memory. Это «гигиена» оркестратора, которая зафиксировала уроки сразу.

**Что можно было лучше:**

- **Я нормализовал нарушение методики в Canon-сводке.** Собственник пришёл со скриншотом, увидел `printer-copir.ru` в моей фразе «важный паттерн». Это было выражено как достижение, тогда как фактически — нарушение чистоты источников. Memory `feedback_only_official_sources.md` создал ПОСЛЕ инцидента, не ДО. Уроки: (а) при первом упоминании не-официального домена в сводке исполнителя — *стоп*, проверять. (б) Whitelist/blacklist источников должен быть в промте explicitly, не «WebSearch site:canon.ru» как первый шаг с неявным fallback'ом.
- **HP-инцидент в финале.** Subagent'ы в первой попытке HP пошли на giloshop / printerbullet / techhypermart / itcare без source_url, постфактум нельзя было разделить чистые от грязных. Re-spawn с whitelist'ом дал 0% покрытие из-за WebFetch timeout на hp.com (60-сек) и Anthropic-rate-limit на retry-loop'ах 3 subagent'ов. Уроки: (а) для брендов с известной геоблокировкой (HP-санкции в РФ) методика «whitelist + n/a при недоступности» может дать ~0% покрытие; (б) нужно либо отдельный архитектурный шаг (PDF datasheets с CDN, локальный requests/curl на офисном сервере), либо ждать восстановления официальных доменов; (в) 4 subagent'а внутри одного чата + 4 параллельных чата = риск rate-limit подписки, надо учитывать.
- **Грязный prod-долг 129 SKU.** Это негативная последствие первой ошибки в Canon-сводке. Re-enrichment Canon/Epson/Ricoh с whitelist'ом — backlog'а 129 SKU + риск тех же блокировок (canon.ru / canon-europe — 403 Cloudflare).
- **Не запустил `scripts/run_matching.py` после ни одного round 2/3 апплая.** В прошлых чатах (apply backlog #4 2026-05-13) matching раскопал большую дельту (0 → 268 matches). Сейчас 273 SKU обогащено за день — реальный эффект на матчинг неизвестен. Backlog: посмотреть `matches` через сутки.

## 5. Как было / как стало

**Было (старт сессии 2026-05-13 утром):**
- Master HEAD: `7f3ffa1` (рефлексия оркестратор-чата 2026-05-13 серии багфиксов).
- Backlog #11 (Excel-каталог) — 0%, только план.
- RM первый prod-тик 07:40 МСК — статус неизвестен (собственник прислал скриншот «Старый, 30.04.2026» — неверная интерпретация старого pre-prod-snapshot).
- supplier_prices: все MFU-строки писались с `category='printer'` (439 строк ошибочно).
- printers_mfu.attrs_jsonb: 358 SKU полностью пустые (`attrs_jsonb={}`), 99 с regex_name, 39 с claude_code.
- Excel-каталог: ~3220 пустых ячеек у собственника (358 × 9 ключей).
- pytest: 1993 passed.

**Стало (закрытие сессии 2026-05-14):**
- Master HEAD: `0063039` (HP r3 incident report + рефлексия).
- Backlog #11 — 100%: UI, sidebar, download/upload, история операций, audit_log, fallback курса 90.0, autofilter, формула RUB, колонки наличия (Склад/Транзит/Поставщиков), docs/catalog_excel.md, UX-правки прайсов (русификация статусов, кнопка «Выбрать файл», tooltip «Счётчики»).
- RM Фаза D — первый prod-тик отработал, pu_id=48, rows_total=151, Notification §4.7 ✓. Фикс `_CATEGORY_GROUP_MAP` под prod-каталог. Resurs Media полностью в проде.
- supplier_prices — `category='mfu'` для 439 строк (migration 0038). Excel-каталог МФУ: 171/360 SKU с ценой (было 0/360).
- printers_mfu.attrs_jsonb: 28 SKU пустые (Avision 14 + Katusha IT 14), 381 с regex_name, 62 с claude_code, 25 с regex_name+claude_code. По 8/9 ключей у большинства брендов 100% success (Pantum/Kyocera полностью чистые, Canon/Epson/Ricoh с грязью).
- Excel-каталог: ~250 пустых ячеек (28 × 9). Sokraschenie −92%.
- pytest: 2039 passed (+46 тестов).
- 12 коммитов в master + 1 отложенный HP-incident.
- Memory: новые правила `feedback_prompt_explicit_date.md` (даты в промтах) и `feedback_only_official_sources.md` (только официальные источники для enrichment).

## 6. Открытые задачи на следующий оркестратор-чат

**Срочные (грязный prod-долг + отложенное):**

1. **Re-enrichment Canon (67 SKU) — приоритет 1.** Прод-данные с printer-copir.ru. Нужен полный re-enrichment с whitelist'а официальных Canon-доменов (canon.com.cn / canon.de / canon.com.au / canon.co.jp / asia.canon — не RU, не europe, которые 403 Cloudflare). Subagent должен возвращать `source_url`, main-thread валидирует.
2. **Re-enrichment Epson (28 SKU) — приоритет 1.** Прод-данные частично с DNS-shop/Citilink/3Logic. Полный re-enrichment с whitelist'а (epson.eu + epson.com.sg + epson.eu/datasheets — это всё официальные Epson; epson.ru/302→epson.sn не пробовать). 2 SKU остались n/a в round 3 (L3216, L8160) — могут так и остаться.
3. **Re-enrichment Ricoh (34 SKU) — приоритет 1.** Прод-данные с эвристики «серия→spec» + ритейлеры. Полный re-enrichment с whitelist'а (ricoh.com / ricoh.eu / ricoh.co.jp / PDF datasheets `filetype:pdf site:ricoh.com`). `starter_cartridge_pages` у Ricoh не публикуется на model-страницах — заходить через PDF brochures или оставить n/a.
4. **HP retry (140 SKU) — приоритет 2.** HP r3 отложен. Возможные подходы в новом чате: (а) PDF-datasheets через `filetype:pdf site:hp.com` или `site:support.hp.com` (часто на CDN, проходят без 403); (б) HP региональные субдомены с проверкой каждого через WebFetch до выбора; (в) локальный requests/curl на офисном сервере + ручная подгрузка CSV; (г) скип HP до возвращения официального ru-домена. Запускать после сброса Anthropic rate-limit. Pending HP-список SKU сохранён.

**Среднесрочные:**

5. **Avision 14 + Katusha IT 14 fully-empty.** Cryptic names, требуют approximated_from / brand-code lookup стратегии. Отдельный план.
6. **Backlog #16 — SQL-аудит false-cooler'ов** (на prod есть подозрение, что AI ошибочно проставил `supported_sockets` / `max_tdp_watts` у других разветвителей/хабов кроме FS-04 ARGB).
7. **Mojibake в логах** офисного worker'а (косметика, БД не затронута).
8. **Backlog #18 — pytest-xdist DB contention** при параллельных пайплайнах (file-lock или per-process namespace).
9. **Median primary margin на prod = 13.17%** при пороге 15% — обсудить при подходе к Волне 3 (уведомления 9b).
10. **`scripts/run_matching.py` после round 2/3 apply'ев.** Посмотреть, как 273 обогащённых SKU повлияли на matches.

**Крупные блоки:**

11. **Backlog #12** — Excel-выгрузка списка аукционов с фильтрами UI. План `plans/2026-05-13-auctions-excel-export.md`.
12. **Backlog #13** — Логистика ПЭК для аукционных лотов. План `plans/2026-05-13-logistics-pek.md`. Габариты в attrs_jsonb уже зафиксированы (4 ключа в Фазе 1 Excel-каталога).
13. **RBAC для менеджеров** — фильтрация sidebar по `users.permissions`.
14. **9b — уведомления Telegram/Max.**
15. **Green Place fetcher** (единственный из 6 поставщиков без реального загрузчика).

**Долгосрочное:**

16. Расширение справочника на ПК / мониторы / сканеры / ИБП.
17. Парсинг страницы контракта на zakupki после победы.
18. Q4 2026 — этап 2 главной гипотезы (автоучастие в аукционе).

## 7. Memory правила, созданные в этой сессии

- **`feedback_prompt_explicit_date.md`** (создан 2026-05-13 утром): в каждом орк-промте явно указывать сегодняшнюю дату из system context («Сегодня: YYYY-MM-DD»). Зафиксировано после 4 повторов — брифинг → RM-промт → 3 рефлексии исполнителей съехали на +1 день.
- **`feedback_only_official_sources.md`** (создан 2026-05-13 вечером, после Canon/HP-инцидентов): enrichment ТОЛЬКО с официальных сайтов производителей. Никаких ритейлеров/агрегаторов даже как fallback. При блокировке RU-домена → международные домены того же производителя. Subagent возвращает `source_url` для audit-trail; main-thread валидирует. Эскалация при покрытии <50%.

## 8. Артефакты в master за сессию

12 коммитов: `8f6a2bc → fa28cba → b32cae2 → 6e5dc14 → 3e2f8a1 → d221e64 → ee60db5 → e400487 → ed189cc → cf7265c → 5b85e05 → 9e815b5 → fc4777b → 803d877 → 36e62d3 → 822b695 → 0063039` (через `git log --oneline 7f3ffa1..0063039 | wc -l` = 17 коммитов с учётом всех rebase-сливов).

pytest baseline: 1993 → 2039 (+46 тестов).
