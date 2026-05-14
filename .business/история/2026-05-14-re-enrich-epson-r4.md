# 2026-05-14 — Re-enrichment Epson r4 (officials only, 31 SKU)

## 1. Какая задача была поставлена

Re-enrichment Epson SKU на prod из-за methodology incident'а round 3
(commit `fc4777b`, 2026-05-13): часть 28 Epson SKU обогащена с DNS-shop /
Citilink / 3Logic в дополнение к epson.eu. Это нарушение методики «только
официальные сайты производителей» (`feedback_only_official_sources.md`).
Постфактум разделить чистые от грязных нельзя (source_url не сохранялся).
Промт собственника требовал: whitelist Epson-доменов, source_url-валидацию
main-thread'ом, обязательную эскалацию при покрытии <50%, batched UPDATE
на prod.

Это второй чат серии re-enrichment по методологии (Canon r4 закрыт первым
на 95.5% покрытия, commit `f701617`).

## 2. Как я её решал

### Этап 0. Worktree + env

`git worktree add -b feature/re-enrich-epson-r4 ../ConfiguratorPC2-epson-r4 origin/master`
(HEAD `f701617`). Скопировал `.env` и `.env.local.prod.v1` руками
(паттерн round 3 + Canon r4) — они не tracked.

### Этап 1. Discovery + gate-проверка whitelist'а

Эфемерный `_epson_r4_discovery.py`: `load_dotenv('.env')` для dev
OPENAI_API_KEY, затем `dotenv_values('.env.local.prod.v1')` для prod
DATABASE_URL ДО импорта `shared.db.engine`. Прогон по `printers_mfu` где
`LOWER(brand)='epson'` вернул **31 SKU** (вместо ожидаемых 28 — за сутки
с round 3 добавились 3 SKU; включил все 31 в re-enrichment, лишних
итераций не делал).

**ДО-цифры по 9 ключам:** 7 ключей 30/1 success/n/a (один SKU
`epson:C11CJ67424/C11CJ67423` = L3252 был привезён только regex_name'ом,
без claude_code); `network_interface` 28/0 (3 SKU без поля); 
`starter_cartridge_pages` 28/3.

**Тестовый WebFetch перед запуском subagent'ов** — на свежеугаданный URL
`https://www.epson.com.sg/Printers/InkTank-System-Printers/...L3210/p/C11CJ68504`
вернул мусорный контент (homepage SG, не product page — URL устарел).
Сразу переключился на `WebSearch ... allowed_domains=[whitelist]` —
получил реальные spec-URL'ы с `download.epson.com.sg/brochure/...PDF`
и `www.epson.com.sg/For-Home/Printers/.../C11CJ68501`. WebFetch на
правильный URL вернул реальные spec'и (10 ipm моно, 5760 dpi,
USB 2.0+WiFi, starter ink 4500/7500 pages, инкджет). Gate пройден,
**epson.com.sg — рабочий whitelist-домен**, `epson.eu` тоже работает
(подтверждено subagent'ами потом).

### Этап 2. Разбивка 31 SKU на 2 группы по сериям

Эфемерный `_epson_r4_split.py` (regex по `name`, числовая часть модели):
- **group_a (17 SKU)** — нижние L-серии: L121, L3xxx (L3210×3, L3216,
  L3250×2, L3251, L3252, L3256×2, L3260, L3266), L4xxx (L4260, L4360),
  L5xxx (L5290, L5296), L3550;
- **group_b (14 SKU)** — старшие L-серии: L6xxx (L6270, L6290, L6370,
  L6490), L8xxx (L8050×2, L8100, L8160), L11050×2, L14150, L15150,
  L18050 + WorkForce M3170 (моно).

Сбалансировано, в потолке 4-5 параллельных (взял 2 — задача меньше,
чем Canon 67 SKU).

### Этап 3. 2 параллельных subagent'а

2 параллельных `general-purpose` subagent'а в background. Промт каждому
одинаковый: whitelist (`epson.com.sg`, `epson.eu`, `epson.com`,
`epson.de`, `epson.co.jp`, `epson.com.au`, `epson.co.uk`,
`download.epson.com.sg`, subdomains `www.*`/`support.*`), blacklist
явно (`epson.ru`, `epson.sn` — Сенегал-302-санкционный, DNS-shop,
Citilink, 3Logic, Wildberries, Ozon, M.Video, OnlineTrade, Re-store,
Sotmarket, Computer Universe, Яндекс.Маркет, Google Shopping, 4PDA,
Reddit, MDLP, irecommend, ixbt, **WebSearch-snippet'ы без открытия
страницы**), обязательный возврат `source_url`, retry 3 + n/a при fail,
cap WebFetch ≤150/120 на группу, early termination при >70% timeout
первых 10 URL. **Строгое правило**: `source_url=null → ВСЕ 9 ключей "n/a"`
(чтобы избежать частичных данных при null source — урок Canon r4).

Оба subagent'а вернули JSON за 4-8 минут (быстрее Canon — Epson serie
plat'on'у на epson.com.sg, redirect'ы только на старшие L62xx → epson.eu).
group_a: 24 tool-uses, 17/17 SKU обработано, 16 valid + 1 forced n/a
(L3252 — региональная EAEU SKU, нет на 7 whitelist-доменах). group_b:
23 tool-uses, 14/14 SKU обработано, 14 valid (0 forced n/a).

### Этап 4. Main-thread валидация source_url + нормализация

`_epson_r4_validate.py`: парсит 2 JSON-блока, для каждого item:
1. **`urllib.parse.urlparse(url).hostname`** — извлекает host без query/path.
2. **Blacklist substring check** (`epson.ru`, `epson.sn`, `dns-shop`,
   `citilink`, `3logic`, ...) → invalid.
3. **Exact whitelist hosts** (`epson.com.sg`, `www.epson.com.sg`, ...,
   ~25 хостов) → valid.
4. **Whitelist suffixes** (`.epson.com.sg`, `.epson.eu`, ...) → valid
   (покрывает `support.epson.com`, `download.epson.com.sg`, etc.).
5. Иначе → invalid.

Если `source_url=null` или невалидный — attrs принудительно загоняются
в `{все 9 ключей: "n/a"}`.

**Нормализация attrs** (`normalize_attrs`) — отдельный шаг после source-валидации.
Subagent'ы вернули значения в человекочитаемом виде («нет», «автоматический»,
«USB 2.0», `{"черный": 4500, "цветной": 7500}`, `"монохромный"`, `"A3+"`),
но `portal/services/auctions/catalog/enrichment/schema.py` требует строго
канонические значения:
- `colorness`: `"ч/б"` / `"цветной"` (subagent: «монохромный» → «ч/б»)
- `max_format`: `"A3"` / `"A4"` (subagent: «A3+» → «A3», т.к. для matching'а
  важен только класс A3 vs A4)
- `duplex`: `"yes"` / `"no"` (subagent: «нет» / «ручной» → `no`,
  «автоматический» → `yes`)
- `usb`: `"yes"` / `"no"` (subagent: «USB 2.0»/«USB 3.0» → `yes`, «нет» → `no`)
- `network_interface`: list of `["LAN", "WiFi"]` (subagent: «Ethernet, Wi-Fi» →
  `["LAN", "WiFi"]`, «Wi-Fi» → `["WiFi"]`, «нет» → `[]`)
- `starter_cartridge_pages`: int (subagent: dict `{черный: N, цветной: M}` →
  взять `N` как black yield, это эталонное поле для matching'а)

**Без normalizer'а** все 31 SKU падали через `schema.validate_attrs`
с 123 ошибками (4 ошибки на SKU × 31). После normalizer'а — **0 ошибок**.

**Результат валидации:** **30 valid / 1 forced n/a / 0 invalid-URL** —
**96.8% покрытие** (>>50% порога, apply на prod разрешён). Forced n/a:
`epson:C11CJ67424/C11CJ67423` (L3252 — регионa SKU EAEU, на 7
whitelist-доменах нет model-page). 2 SKU L6270/L6290 имеют `usb="n/a"`
(не указано на epson.eu product page — single-key gap, не блокер).

### Этап 5. Apply на prod через прямой batched UPDATE

**Архитектурное решение принято исполнителем (по
`feedback_executor_no_architectural_questions`):** apply через прямой
SQL UPDATE с полной перезаписью `attrs_jsonb`, **не через importer**.
Та же причина, что в Canon r4: `importer.merge_attrs` (Backlog #10)
защищает не-n/a в БД от n/a-incoming, что в re-enrichment'е дало бы
обратный эффект — для 1 forced-n/a SKU (L3252) грязные DNS-shop данные
могли бы сохраниться.

`_epson_r4_apply.py --target prod --apply`: pre-validate всех 31 attrs
через `schema.validate_attrs` (0 ошибок), затем batched UPDATE через
`UNNEST(:skus)+UNNEST(:attrs)` в одном RTT Railway (~100мс):

```sql
UPDATE printers_mfu pm
   SET attrs_jsonb      = src.attrs::jsonb,
       attrs_source     = 'claude_code_r4',
       attrs_updated_at = now()
  FROM (SELECT UNNEST(CAST(:skus AS text[])) AS sku,
               UNNEST(CAST(:attrs AS text[])) AS attrs) src
 WHERE pm.sku = src.sku
```

Результат: **`rowcount: 31`** (все 31 строк обновлены).
Sanity SQL после:

| Ключ | ДО (round 3 mixed) | ПОСЛЕ (round 4 official) |
|---|---|---|
| print_speed_ppm | 30/1 | 30/1 |
| colorness | 30/1 | 30/1 |
| max_format | 31/0 | 30/1 |
| duplex | 30/1 | 30/1 |
| resolution_dpi | 30/1 | 30/1 |
| network_interface | 28/0 (3 missing) | 30/1 |
| usb | 30/1 | 28/3 |
| starter_cartridge_pages | 28/3 | 30/1 |
| print_technology | 31/0 | 30/1 |

`attrs_source` ПОСЛЕ: **31/31 = `claude_code_r4`** (полная замена; ДО:
30 × `regex_name+claude_code`, 1 × `regex_name`).

Регрессия `usb` 30→28 (-2): L6270/L6290 — на epson.eu USB-поле не
указано в извлечённом markdown, subagent корректно пометил `n/a`.
Регрессия `max_format/colorness/print_technology` 31→30 (-1): forced
n/a по L3252. Прирост `network_interface` +2: round 3 не имел
сетевого поля у 3 SKU (поле missing), round 4 явно указал
(WiFi/Ethernet/пустой list). Прирост `starter_cartridge_pages` +2:
round 3 имел n/a у L3216/L8160/?; round 4 нашёл значения с
epson.com.sg/epson.eu (L3216 starter ink, L8160 photo printer black
yield).

### Этап 6. Sample-чек 5 SKU

`_epson_r4_sample_check.py`: вытащил 5 SKU (L15150 A3+, M3170 моно,
L3210 базовый EcoTank, L8050 фото-принтер, L3252 forced n/a) с prod,
сравнил с тем, что вернули subagent'ы после нормализации.
**5/5 attrs match=True**.

### Этап 7. Apply на dev

`_epson_r4_apply.py --target dev --apply`: `rowcount: 23` (8 SKU из 31
нет на dev — БД отстаёт от prod, ожидаемо). После: dev Epson распределение
attrs_source: 23 × `claude_code_r4`, 8 × `claude_code` (старые dev-only),
5 × `claude_code+regex_name`.

### Этап 8. Done-артефакт

`_epson_r4_build_artifact.py` собрал
`enrichment/auctions/archive/2026-05-14/epson_round4_001.json` в формате
importer'а (`brand`, `batch_id`, `generated_at`, `summary`, `results`)
+ дополнительное поле `_methodology_note`. 31 items, в каждом `sku`,
`source_url`, `attrs`. **Importer не зовётся** — apply уже выполнен
через прямой UPDATE; артефакт нужен только для audit-trail.

### Этап 9. Регрессия

`pytest tests/test_auctions/`: **242 passed** (тот же baseline, что
у Canon r4, production-код не правился — все эфемерные скрипты
лежат в worktree-root и удаляются до commit'а).

## 3. Решил ли — да

- ✅ **31 SKU re-enriched с whitelist'а официальных Epson-доменов**
  (epson.com.sg для 22 SKU, epson.eu для 8 SKU).
- ✅ **0 ритейлеров, 0 epson.ru/epson.sn** в source_url'ах. Main-thread
  валидация подтвердила 30/31 валидных URL + 1 forced n/a (L3252 — реально
  нет на whitelist'е).
- ✅ **96.8% покрытие** (>>50% порога эскалации). Apply выполнен.
- ✅ **attrs_source = `claude_code_r4`** для всех 31 SKU (audit-trail).
- ✅ **Sample-чек 5/5 SKU прошёл** — что в БД, то же, что вернул subagent
  после нормализации.
- ✅ pytest auctions scope чист (242 passed).
- ✅ **L3216 / L8160 (известные проблемные SKU из round 3 — могли
  остаться n/a) — теперь обогащены полностью на whitelist'е**:
  - L3216 (epson:C11C68518/CJ68518/CJ68502): epson.com.sg, 10 ppm,
    starter 4500/7500.
  - L8160 (epson:C11CJ20404/403/402): epson.eu, full spec.

## 4. Эффективно ли решение, что можно было лучше

**Что сработало:**

1. **Gate-проверка whitelist'а ДО запуска subagent'ов** (паттерн Canon r4).
   Угаданный URL дал мусорный контент — переключение на WebSearch
   с `allowed_domains=[whitelist]` сразу нашло реальные product pages
   с download.epson.com.sg PDF brochures. Подтверждено, что путь
   рабочий до запуска параллельных задач.
2. **Normalizer как отдельный шаг после source-валидации.** Subagent'ы
   возвращают attrs в человекочитаемом виде (что естественно для LLM),
   а схема требует канонические значения. Разделение source-валидации
   и normalize'а по 9 типам делает обе фазы простыми и тестируемыми.
   Без normalizer'а 123 schema-ошибки на 31 SKU; после — 0 ошибок.
3. **2 параллельных subagent'а** при потолке 4-5 — для 31 SKU достаточно;
   нет смысла дробить мельче. Уложились в 4-8 минут каждый, никаких
   API-error / rate-limit'ов.
4. **Прямой batched UPDATE через UNNEST** (паттерн Canon r4) — один RTT
   Railway, очистка attrs полностью, audit-tag `claude_code_r4`.
5. **Sample-чек 5/5 разнообразных SKU** (A3+, моно, базовый EcoTank,
   фото-принтер, forced n/a) — гарантия, что нормализация и UPDATE
   работают согласованно по разным веткам кода.
6. **Эфемерные скрипты в worktree-root, удаляются до commit'а** — паттерн
   Canon r4. В master коммитим ТОЛЬКО done-артефакт + рефлексию + план.

**Что можно было лучше:**

1. **Normalizer'а в Canon r4 не было**. У Canon все subagent'ы вернули
   значения уже в каноническом формате (без «монохромный»/«USB 2.0»),
   видимо повезло с тем, что Canon-промт менее жёстко требовал
   человекочитаемые значения. У Epson — мой промт ставил `colorness:
   "монохромный"` и `usb: "USB 2.0"` в качестве примеров, и subagent'ы
   именно их и вернули. **Урок:** в промтах subagent'ов давать ИМЕННО
   канонические значения из `schema.py` как примеры, а не «человекочитаемые
   подсказки». Альтернатива — всегда иметь normalizer (что и сделано
   здесь). Для следующих чатов серии (Ricoh r4, HP r2 retry) — приложить
   готовый паттерн «промт с каноническими enum'ами + normalizer как
   safety-net».
2. **2 SKU L6270/L6290 — `usb="n/a"`.** На epson.eu product page USB-поле
   не выделено как отдельная строка spec'ов (оно есть, но в группе
   «Connectivity» вместе с WiFi). Subagent корректно отказался
   придумывать значение. Если в будущем понадобится — точечный WebFetch
   на support.epson.eu/L6270/specifications, либо PDF brochure
   `download.epson.com.sg/.../L6270.pdf` (но WebFetch не парсит PDF).
3. **L3252 — единственный forced n/a.** Это региональная SKU EAEU
   (Eвразийский экономический союз — Россия/Казахстан/Беларусь), на
   международных epson-доменах её нет. Real-world spec'и L3252 идентичны
   L3250/L3251 (только маркировка региона), но subagent был строго прав
   не attrubуted-from. Если бизнес-кейс потребует — отдельный
   approximated_from подход (взять L3251 spec'и + явный
   `attrs_source='claude_code_r4_approximated'` тег).
4. **Расхождение `starter_cartridge_pages` между регионами**: epson.com.sg
   даёт box-yield (4500/7500 для L3210/L3216/L3250/L3256/L5296), epson.eu
   даёт set-yield (8100/6500 для L3260/L3266/L3251/L5290), L4260
   странный (14000/5200 — large bottle bundle). Это **различие
   маркетинговых данных Epson**, не ошибка subagent'а. Для matching'а
   важен только sigle-int yield (а не ratio), так что это работает,
   но в Excel-каталоге собственника может вызвать вопрос «почему два
   соседних SKU с разным yield». Backlog: явно унифицировать на одну
   методику (либо box-yield, либо set-yield) — это решение бизнеса,
   не enrichment'а.

## 5. Как было и как стало

### На prod-БД (Epson, 31 SKU)

**Было** (commit `fc4777b`, round 3, 2026-05-13 вечер):
- 31 SKU обогащено, `attrs_source` распределение: 30 × `regex_name+claude_code`,
  1 × `regex_name` (L3252 — добавился без round-3 enrichment'а).
- **Грязные данные**: часть из 30 SKU с `regex_name+claude_code` имели
  атрибуты, взятые с DNS-shop / Citilink / 3Logic в дополнение к epson.eu.
  Постфактум разделить чистые от грязных нельзя.
- Distribution: 7 ключей 30/1; `network_interface` 28/0 (+3 missing);
  `starter_cartridge_pages` 28/3.

**Стало** (commit `<r4 commit>`, 2026-05-14):
- 31 SKU, `attrs_source` = `claude_code_r4` (полная замена).
- **Чистые данные**: 30/31 SKU обогащено с whitelist-источников
  (epson.com.sg=22, epson.eu=8); 1/31 SKU forced n/a (L3252 — нет
  на whitelist'е).
- Distribution: 6 ключей 30/1; `usb` 28/3; `network_interface` 30/1;
  `starter_cartridge_pages` 30/1; `print_technology/max_format/colorness`
  30/1 (forced n/a по L3252).

### Прогресс волны round 3 → round 4 (по брендам)

| Бренд | Round 3 source | Round 4 status |
|---|---|---|
| Pantum | pantum.ru (чистый) | OK, не нужна re-enrichment |
| **Epson** | **epson.eu + DNS-shop/Citilink/3Logic (грязн.)** | **DONE r4** ✓ (31 SKU чистых) |
| Ricoh | эвристика + ритейлеры | TODO r4 (34 SKU грязных) |
| Canon | printer-copir.ru (грязн.) | DONE r4 ✓ (67 SKU чистых) |
| Kyocera | kyocera-document-solutions.ru (чистый) | OK |
| HP | (не apply'илось из-за rate-limit'а) | TODO retry r2 (140 SKU n/a) |

После этого чата грязный долг на prod снизился с 62 SKU (Epson 28 +
Ricoh 34) до **34 SKU** (только Ricoh). Это **минус 45%** от
оставшегося грязного долга в одной серии.

## 6. Открытые задачи на следующий чат серии re-enrichment'а

1. **Ricoh r4 (34 SKU грязных)** — приоритет 1. Whitelist: `ricoh.com`,
   `ricoh.eu`, `ricoh.co.jp`, PDF на `*.ricoh.com`. `starter_cartridge_pages`
   у Ricoh не публикуется на model-страницах — заходить через PDF
   brochures (или пометить n/a). Шаблон промта с каноническими enum'ами
   из `schema.py` + normalizer паттерн из этого чата.
2. **HP r2 retry (140 SKU n/a)** — приоритет 2. Дождаться сброса
   Anthropic rate-limit'а. Test WebFetch `support.hp.com` ДО запуска
   subagent'ов; если 60-сек таймаут стабилен — escalate с опциями
   (PDF datasheets / офисный сервер requests / скип HP).
3. **L3252 + L6270/L6290 USB**: если бизнес-кейс приоритет — точечный
   approximated_from подход для L3252 (взять spec'и L3251) и точечный
   PDF-проход для L6270/L6290.
4. **Avision + Katusha IT (28 SKU fully-empty)** — отдельная стратегия
   approximated_from / brand-code lookup.
5. **Унификация starter_cartridge_pages методики между box-yield и
   set-yield** — решение бизнеса (не enrichment'а).

## 7. Что использовать в Ricoh r4 (паттерн)

Этот чат улучшил паттерн Canon r4 одним важным шагом — **normalizer**:

- **Worktree-isolation + копирование `.env` руками** (паттерн round 3 + Canon).
- **Discovery скрипт** `_<brand>_r<N>_discovery.py`: `load_dotenv('.env')`,
  затем `dotenv_values('.env.local.prod.v1')` → `os.environ['DATABASE_URL']`,
  затем `from shared.db import engine`.
- **Gate-проверка whitelist'а** одной WebSearch+WebFetch ДО subagent'ов.
- **Split на 2-4 группы по сериям** (`_<brand>_r<N>_split.py`). Для
  Ricoh: M-серия (моно), IM C-серия (цветной MFP A3), IM моно-серия,
  P-серия (принтеры), MP-серия.
- **N subagent'ов с whitelist+blacklist в промте**, обязательным
  `source_url`, retry 3 + n/a, cap WebFetch ≤120-150. **В промте давать
  ИМЕННО канонические значения из `schema.py` как примеры** (`"yes"`/`"no"`
  для duplex, `"ч/б"`/`"цветной"` для colorness, `["LAN","WiFi"]` для
  network_interface, int для starter_cartridge_pages — если subagent
  получает только black-yield, это его выбор; при dict — normalizer
  возьмёт `черный`).
- **Main-thread validate (`_<brand>_r<N>_validate.py`)**:
  - `urlparse.hostname` + whitelist exact-set + suffix-tuple + blacklist substring.
  - `normalize_attrs(raw)` — safety-net на случай человекочитаемых
    значений от subagent'а. Структура из этого чата:
    `_norm_duplex/_norm_colorness/_norm_max_format/_norm_usb/
    _norm_network_interface/_norm_starter_cartridge_pages/_norm_print_*`.
  - force n/a при invalid URL.
- **Apply через прямой batched UPDATE** (`_<brand>_r<N>_apply.py`)
  с `UNNEST(CAST(:skus AS text[]))+UNNEST(CAST(:attrs AS text[]))`.
  `attrs_source='claude_code_r<N>'`. dry-run flag, sample-чек отдельный
  скрипт.
- **Done-артефакт** в `enrichment/auctions/archive/<date>/<brand>_round<N>_001.json`
  (только для audit-trail; importer не зовётся).
- **Pytest auctions scope smoke** + рефлексия + обновление плана.

## 8. Артефакты

- **Done-файл (артефакт)**: `enrichment/auctions/archive/2026-05-14/epson_round4_001.json`
  (31 items с `sku`, `source_url`, `attrs` + `_methodology_note` + `summary`).
- **Master HEAD до старта чата**: `f701617`.
- **Worktree**: `feature/re-enrich-epson-r4`.
- **Эфемерные скрипты (удаляются до commit'а):**
  `_epson_r4_discovery.py`, `_epson_r4_split.py`, `_epson_r4_validate.py`
  (с normalizer'ом), `_epson_r4_apply.py`, `_epson_r4_sample_check.py`,
  `_epson_r4_build_artifact.py`, `_epson_r4_sanity.py`.
- **Эфемерные JSON (удаляются до commit'а):** `_epson_r4_discovery.json`,
  `_epson_r4_groups.json`, `_epson_r4_subagent_a.json`,
  `_epson_r4_subagent_b.json`, `_epson_r4_validated.json`.
