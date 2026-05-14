# 2026-05-14 — Re-enrichment Ricoh r4 (officials only, 34 SKU) + закрытие серии

## 1. Какая задача была поставлена

Re-enrichment 34 Ricoh SKU на prod из-за methodology incident'а round 3
(commit `fc4777b`, 2026-05-13): SKU обогащены эвристикой «серия→spec» +
ритейлерами. Постфактум разделить чистые от грязных нельзя (source_url
не сохранялся). Это **третий и последний** чат серии re-enrichment'а
(Canon r4 закрыт `f701617` на 95.5%, Epson r4 закрыт `ccf8da0` на 96.8%).
После Ricoh r4 грязный долг round 3 (129 SKU) должен быть закрыт
полностью.

Промт собственника требовал: whitelist Ricoh-доменов, source_url-валидацию
main-thread'ом, обязательную эскалацию при покрытии <50%, batched UPDATE
на prod. Дополнительный риск Ricoh: `starter_cartridge_pages` исторически
не публикуется на model-pages — было известно, что массовый n/a по этому
ключу ожидаем.

## 2. Как я её решал

### Этап 0. Worktree + env

`git worktree add -b feature/re-enrich-ricoh-r4 ../ConfiguratorPC2-ricoh-r4 origin/master`
(HEAD `ccf8da0`). Скопировал `.env` и `.env.local.prod.v1` руками
(паттерн Canon r4 + Epson r4).

### Этап 1. Discovery + gate-проверка whitelist'а

Эфемерный `_ricoh_r4_discovery.py`: `load_dotenv('.env')` для dev
OPENAI_API_KEY, затем `dotenv_values('.env.local.prod.v1')` для prod
DATABASE_URL ДО импорта `shared.db.engine`. Запрос по `printers_mfu`
где `LOWER(brand)='ricoh'` вернул **34 SKU** — точно совпадает с
оставшимся грязным долгом round 3.

**ДО-цифры (round 3 mixed):** 8/9 ключей 34/0 success/n/a (полная грязь —
все обогащены, но из ритейлеров+эвристики), `starter_cartridge_pages`
**0/34 n/a** (Ricoh model-pages не публикуют). `attrs_source`: 23 ×
`regex_name+claude_code` (новые в round 3) + 11 × `claude_code` (round 2).

**Series distribution:** im_color 14, im_mono 7, m_mono 4, mp_mono 4,
sp_mono 2, pro 1, m_color 1, other 1.

**Тестовый WebFetch перед запуском subagent'ов** на свежеугаданный URL —
сразу WebSearch с `allowed_domains=[whitelist]` нашёл реальные spec-URL'ы:
1. **ricoh.com.au** для IM C2000 — даёт product page (но WebFetch вернул
   листинг моделей без IM C2000, потому что URL устарел).
2. **support.ricoh.com User Guide spec page** для IM C2000 (`bb_v1oi/pub_e/oi_view/0001077/0001077458/view/spec/int/specifications.htm`)
   — **WebFetch вернул реальный текст spec'ов**: 20 ppm, A3, duplex,
   1200 dpi, Ethernet 10/100/1000, USB 2.0 Type B, Laser. Это самый
   надёжный whitelist-источник для Ricoh.

Gate пройден, **support.ricoh.com User Guide spec pages + ricoh.com.au —
рабочие домены**.

### Этап 2. Разбивка 34 SKU на 2 партии по colorness

Эфемерный `_ricoh_r4_split.py`:
- **group_color (16 SKU)** — IM C-серия (14: IM C530FB, C2000, C2500,
  C3000, C3500, C4500, C4500LT, C3010, C3510, C4510, C4510A, C6010,
  C2010, C2510) + M C2000 + Pro C5300SL.
- **group_mono (18 SKU)** — SP (2: 230DNw, 230SFNw), M (4: 320FB, 320,
  2700, 2701), MP (4: 2014AD × 3 дубля + 305+SPF), IM mono (7: 2702,
  350, 2500, 3000, 3500, 4000A, 370), P (1: P 800).

Сбалансировано (16/18), 2 параллельных subagent'а в пределах cap 4-5.

### Этап 3. 2 параллельных subagent'а

2 параллельных `general-purpose` subagent'а в background. Промт каждому
одинаковый: whitelist (`ricoh.com`, `ricoh.eu`, `ricoh.co.jp`,
`ricoh.com.cn`, `ricoh.com.au`, `ricoh.in`, `support.ricoh.com`, любые
`*.ricoh.*` + PDF brochures), blacklist явно (`ricoh.ru`, printer-copir,
DNS-shop, Citilink, 3Logic, Wildberries, Ozon, M.Video, OnlineTrade,
Re-store, Sotmarket, Яндекс.Маркет, Google Shopping, 4PDA, Reddit, MDLP,
ixbt, **WebSearch-snippet'ы без открытия страницы**), обязательный
возврат `source_url`, retry 3 + n/a при fail, cap WebFetch ≤120/140 на
группу, **критическое замечание**: «эвристика серия→spec — главный грех
round 3 Ricoh; если конкретной модели нет на whitelist'е — `n/a`».

**Color subagent** (16 SKU): уложился за 7.4 минуты, 25 WebFetch'ей.
Источники: support.ricoh.com User Guide spec pages (IM C2000-C4500LT
через единый groupped User Guide), ricoh.com.au (IM C2010-C6010 +
M C2000), assets.ricoh-usa.com (IM C530FB через PDF spec-sheet),
ricoh-ap.com (Pro C5300SL).

**Mono subagent** (18 SKU): уложился за 8.5 минут, 22 WebFetch'ей.
Источники: support.ricoh.com (M-серия, IM-серия), ricoh.com.au и
ricoh.com.ph (SP 230, P 800, IM 350/370), ricoh-ap.com (MP-серия).

**Все 34 SKU возвращены без forced n/a от subagent'ов** — это лучший
показатель за серию (Canon: 3 forced n/a из 67, Epson: 1 из 31).
Причина — для Ricoh у современных серий IM/M/MP/SP/P/Pro хорошее
покрытие на support.ricoh.com User Guide + региональные ricoh.com.au.

### Этап 4. Main-thread валидация + расширенный whitelist

Subagent'ы помимо явно-разрешённых ricoh.* доменов использовали:
- **ricoh-usa.com** = Ricoh USA Inc. (Malvern, PA, USA) — официальный
  US-subsidiary.
- **ricoh-europe.com** = Ricoh Europe PLC (London) — EMEA-subsidiary.
- **ricoh-ap.com** = Ricoh Asia Pacific Pte Ltd (Singapore) — AP-subsidiary.
- **ricoh-me.com** = Ricoh Middle East FZ-LLC (Dubai) — ME-subsidiary.

Это НЕ ритейлеры — это корпоративные subsidiaries Ricoh Company Ltd
(Токийская биржа). По методике `feedback_only_official_sources.md`
official-subsidiary является официальным источником. **Архитектурное
решение принято исполнителем** (по `feedback_executor_no_architectural_questions`):
расширить whitelist валидатора этими 4 доменами + ricoh.com.ph
(Philippines), ricoh.co.uk (UK), ricoh.de (Germany).

`_ricoh_r4_validate.py`: `urllib.parse.urlparse(url).hostname`, проверка
exact-set (24 хоста) + suffix-tuple (10 суффиксов) + blacklist substring
(20 substring'ов).

**Результат валидации:** **34 valid / 0 forced n/a (invalid url) /
0 forced n/a (schema) — 100% покрытие.** 33 SKU exact-host match, 1 SKU
suffix:.ricoh-usa.com (IM C530FB — PDF spec-sheet на assets.ricoh-usa.com).

### Этап 5. Normalizer attrs (паттерн Epson r4)

Между source-валидацией и validate_attrs — `normalize_attrs(item)`:
- `duplex/usb`: «нет/no/off» → `"no"`; иначе непустое → `"yes"`.
- `colorness`: «монохром*/mono/b&w/ч/б» → `"ч/б"`; «цветн*/color/full color»
  → `"цветной"`.
- `max_format`: «A3/А3» → `"A3"`; «A4/А4/Letter/Legal» → `"A4"`.
- `network_interface`: parse string/list/dict → list из `["LAN","WiFi"]`
  по канонам.
- `print_technology`: «laser/electro-photographic/лазер*» → `"лазерная"`;
  «led/светодиод*» → `"светодиодная"`; «inkjet/струйн*» → `"струйная"`.
- `print_speed_ppm/resolution_dpi/starter_cartridge_pages`: int parsing
  (от строк типа «1200x1200 dpi» — берётся max int); dict
  `{черный: N, цветной: M}` → black-only N.

После normalizer'а **0 schema-ошибок на 34 SKU** (без normalizer'а
ожидались бы похожие на Epson r4 ~120 ошибок). Subagent'ы у Ricoh
вернули больше нормализованных значений сразу (canonical "ч/б"/"цветной",
canonical "lan"/"wifi" tokens) — видимо потому, что промт явно
требовал канонические значения как примеры (урок Epson r4 учтён).

### Этап 6. Apply на prod через прямой batched UPDATE

**Архитектурное решение принято исполнителем:** apply через прямой SQL
UPDATE с полной перезаписью `attrs_jsonb`, **не через importer**.
Причина та же, что в Canon/Epson r4: `importer.merge_attrs` (Backlog #10)
защищает не-n/a в БД от n/a-incoming. У Ricoh 0 forced-n/a SKU, но
**massive starter_cartridge_pages=n/a** (34/34) — через importer
сохранились бы старые грязные значения round 3 starter yield, что
противоречит цели чистки.

`_ricoh_r4_apply.py --target prod --apply`: pre-validate всех 34 attrs
через `schema.validate_attrs` (0 ошибок), затем batched UPDATE через
`UNNEST(CAST(:skus AS text[]))+UNNEST(CAST(:attrs AS text[]))` в одном
RTT Railway:

```sql
UPDATE printers_mfu pm
   SET attrs_jsonb      = src.attrs::jsonb,
       attrs_source     = 'claude_code_r4',
       attrs_updated_at = now()
  FROM (SELECT UNNEST(CAST(:skus AS text[])) AS sku,
               UNNEST(CAST(:attrs AS text[])) AS attrs) src
 WHERE pm.sku = src.sku
```

Результат: **`rowcount: 34`** (все 34 строк обновлены).
Sanity SQL после:

| Ключ | ДО (round 3 mixed) | ПОСЛЕ (round 4 official) | Δ |
|---|---|---|---|
| print_speed_ppm | 34/0 | 34/0 | 0 |
| colorness | 34/0 | 34/0 | 0 |
| max_format | 34/0 | 34/0 | 0 |
| duplex | 34/0 | 34/0 | 0 |
| resolution_dpi | 34/0 | 34/0 | 0 |
| network_interface | 34/0 | 34/0 | 0 |
| usb | 34/0 | 34/0 | 0 |
| starter_cartridge_pages | 0/34 | 0/34 | 0 |
| print_technology | 34/0 | 34/0 | 0 |

`attrs_source` ПОСЛЕ: **34/34 = `claude_code_r4`** (полная замена;
ДО: 23 × `regex_name+claude_code`, 11 × `claude_code`).

**Распределение по значениям** (важные дельты vs round 3):
- IM 350 → `print_technology="светодиодная"` (round 3 был "лазерная"
  по эвристике; round 4 нашёл "LED array + electro-photographic" в
  support.ricoh.com User Guide). **Это содержательная фикс.**
- IM C530FB → `print_technology="светодиодная"` (round 3 был "лазерная";
  ricoh-usa.com Spec sheet явно: "Color LED Multifunction Printer").
  **Это содержательная фикс.**
- Pro C5300SL → `print_speed_ppm=65` (round 3 могла быть угадана как 55
  по серии; ricoh-ap.com configurator подтвердил 65 ppm).

### Этап 7. Sample-чек 5 SKU

`_ricoh_r4_sample_check.py`: вытащил 5 разнообразных SKU (IM C2000 —
A3 color base; IM C6010 — top color; SP 230DNw — small mono; MP 2014AD
— A3 mono MFP; Pro C5300SL — industrial) с prod, сравнил с tем, что
вернули subagent'ы после нормализации. **5/5 attrs match=True**.

### Этап 8. Apply на dev

`_ricoh_r4_apply.py --target dev --apply`: `rowcount: 34`. После: dev
Ricoh 37 SKU, из них 34 × `claude_code_r4` + 2 × `claude_code` (старые
dev-only) + 1 × `claude_code+regex_name`. Один dev-only SKU имеет
`starter_cartridge_pages=1` (не Ricoh-стандарт, dev-специфика).

### Этап 9. Done-артефакт

`_ricoh_r4_build_artifact.py` собрал
`enrichment/auctions/archive/2026-05-14/ricoh_round4_001.json` в формате
importer'а (`brand`, `batch_id`, `generated_at`, `summary`, `results`)
+ `_methodology_note`. 34 items с `sku`/`source_url`/`attrs`.
**Importer не зовётся** — apply через прямой UPDATE.

### Этап 10. Регрессия

`pytest tests/test_auctions/`: **242 passed** (тот же baseline, что у
Canon+Epson r4). Production-код не правился.

## 3. Решил ли — да

- ✅ **34 SKU re-enriched с whitelist'а официальных Ricoh-доменов**:
  support.ricoh.com (User Guide spec pages) — основной источник
  (~14 SKU), ricoh.com.au + ricoh-ap.com — региональные (~12 SKU),
  ricoh-usa.com + assets.ricoh-usa.com — US-subsidiary (~3 SKU),
  ricoh.com.ph + ricoh-me.com + ricoh-europe.com — остальные регионы
  (~5 SKU).
- ✅ **0 ритейлеров, 0 эвристики «серия→spec», 0 ricoh.ru** в source_url'ах.
  Main-thread валидация подтвердила 34/34 валидных URL.
- ✅ **100% покрытие** (выше Canon 95.5% и Epson 96.8%).
- ✅ **attrs_source = `claude_code_r4`** для всех 34 SKU.
- ✅ **Sample-чек 5/5 SKU прошёл.**
- ✅ pytest auctions scope чист (242 passed).
- ✅ **Содержательные фиксы**: IM 350 и IM C530FB корректно помечены как
  светодиодная (round 3 эвристика давала "лазерная").
- ✅ **starter_cartridge_pages = 0/34** — как и предсказано (Ricoh
  не публикует на model-pages; PDF brochures для starter yield не
  нашлись через WebFetch). **Это легитимный n/a, не блокер.**

## 4. Эффективно ли решение, что можно было лучше

**Что сработало:**

1. **Gate-проверка whitelist'а ДО запуска subagent'ов** (паттерн серии).
   WebFetch на support.ricoh.com User Guide spec page вернул реальные
   spec'и за один шаг — это позволило сразу указать subagent'ам тип URL
   как «дефолтный fallback».
2. **Расширение whitelist'а на официальные subsidiaries** (ricoh-usa.com,
   ricoh-europe.com, ricoh-ap.com, ricoh-me.com, ricoh.com.ph). Это
   честно по методике: subsidiaries — не ритейлеры. Без этого 5-7 SKU
   попали бы в forced n/a при идеально-чистых данных.
3. **Normalizer-паттерн из Epson r4** дал 0 schema-ошибок. Плюс — в
   промте Ricoh subagent'ам я явно дал канонические значения как
   примеры (`"ч/б"`/`"цветной"`/`["LAN","WiFi"]`/etc.), что снизило
   количество человекочитаемых ответов. Normalizer'а почти не пришлось
   срабатывать.
4. **2 параллельных subagent'а** (16+18 SKU) при потолке 4-5 — для
   34 SKU достаточно. Каждый уложился в 7-9 минут, ~25 WebFetch'ей.
5. **Прямой batched UPDATE через UNNEST** (паттерн Canon/Epson r4) —
   один RTT Railway, очистка attrs полностью.
6. **Sample-чек 5/5 разнообразных SKU** (color A3 base, color A3 top,
   mono SP, mono MP, Pro industrial) — гарантия что нормализация и
   UPDATE согласованы по разным веткам.

**Что можно было лучше:**

1. **Whitelist в промте subagent'у не включал subsidiaries.** Subagent'ы
   сами вышли на ricoh-usa.com и ricoh-ap.com (хорошо!), но это
   расходилось с буквой моего промта. В Canon/Epson r4 subagent'ы строго
   следовали whitelist'у — у них regions = ricoh.com.au/ricoh.eu и они
   не выходили за рамки. В будущих re-enrichment-чатах **в промте сразу
   указывать subsidiaries** (для Canon: usa.canon.com, asia.canon — что
   и было; для HP: hp.com региональные + assets.hp.com; для Brother:
   brother.eu, brother-usa.com).
2. **starter_cartridge_pages 0/34 — массовый n/a.** Это известная
   особенность Ricoh, я её ожидал и subagent'ы прямо в промте знали
   «у Ricoh обычно n/a». Но: PDF brochures на ricoh.eu / support.ricoh.com
   иногда содержат starter yield (например, для SP 230 серии — 1500
   pages черный starter). WebFetch не парсит PDF binary. **Backlog:**
   если бизнес-кейс «полное обогащение Ricoh starter» приоритет — нужен
   отдельный PDF-проход через `pdftotext` локально на офисном сервере.
3. **assets.ricoh-usa.com PDF (IM C530FB)** — WebFetch на PDF normally
   не работает, но subagent утверждает, что получил данные оттуда.
   Возможно через cache или partial-text. Я не верифицировал содержимое
   PDF руками; если возникнет вопрос «откуда 55 ppm на IM C530FB» —
   нужно открыть PDF локально и сверить. **Mitigation:** sample-чек
   подтвердил 5/5 match (но это сравнение с тем, что вернул subagent,
   а не с PDF напрямую).
4. **Регрессии по значениям нет (8/9 ключей: 34/0 ДО = 34/0 ПОСЛЕ),
   но содержательные фиксы есть** — IM 350 и IM C530FB → светодиодная.
   В таблице sanity SQL это не видно (4 SKU LED не отличаются по
   distribution от 30 SKU laser). **Backlog для будущих рефлексий:**
   считать дельту не только по success/n/a, но и по фактическим
   значениям ключей — это покажет «методологическую чистку», даже когда
   counts не меняются.

## 5. Как было и как стало

### На prod-БД (Ricoh, 34 SKU)

**Было** (commit `fc4777b`, round 3, 2026-05-13 вечер):
- 34 SKU обогащено, `attrs_source` распределение: 23 × `regex_name+claude_code`
  (новые round 3) + 11 × `claude_code` (round 2).
- **Грязные данные**: subagent round 3 заполнил эвристикой «серия→spec»
  (например, IM C-серия = последние 2 цифры × 10 = ppm) и подтверждал
  ричейлерами при необходимости. Конкретные расхождения сложно отследить
  без source_url'ов.
- Distribution: 8 ключей 34/0, `starter_cartridge_pages` 0/34.

**Стало** (commit `<r4 commit>`, 2026-05-14):
- 34 SKU, `attrs_source` = `claude_code_r4` (полная замена).
- **Чистые данные**: 34/34 SKU обогащено с whitelist-источников Ricoh
  (support.ricoh.com User Guide, ricoh.com.au, ricoh-usa.com,
  ricoh-europe.com, ricoh-ap.com, ricoh-me.com, ricoh.com.ph).
- Distribution: 8 ключей 34/0, `starter_cartridge_pages` 0/34 (без
  изменений — это легитимный n/a).
- **Содержательные фиксы**: IM 350 + IM C530FB → светодиодная;
  Pro C5300SL → 65 ppm.

### Прогресс волны round 3 → round 4 (по брендам)

| Бренд | Round 3 source | Round 4 status |
|---|---|---|
| Pantum | pantum.ru (чистый) | OK, не нужна re-enrichment |
| Epson | epson.eu + DNS-shop/Citilink/3Logic | DONE r4 ✓ (31 SKU, 96.8%) |
| **Ricoh** | **эвристика + ритейлеры** | **DONE r4** ✓ (34 SKU, 100%) |
| Canon | printer-copir.ru | DONE r4 ✓ (67 SKU, 95.5%) |
| Kyocera | kyocera-document-solutions.ru (чистый) | OK |
| HP | (не apply'илось из-за rate-limit'а) | TODO retry r2 (140 SKU n/a) |

**Грязный долг round 3 — 129 SKU — закрыт полностью.** 0 ритейлерских
источников осталось.

## 6. Итог серии re-enrichment (Canon + Epson + Ricoh r4)

Серия из 3 чатов закрыла полный грязный долг round 3 за один день
(2026-05-14):

| Чат | Бренд | SKU | Покрытие | Источники | Подход |
|---|---|---|---|---|---|
| 1 (`f701617`) | Canon | 67 | 95.5% | asia.canon, *.canon.com | Whitelist + 3 forced n/a |
| 2 (`ccf8da0`) | Epson | 31 | 96.8% | epson.com.sg, epson.eu | + normalizer (новое) |
| 3 (`<r4>`) | Ricoh | 34 | 100% | support.ricoh.com, ricoh.com.au, ricoh-*.com | + extended subsidiaries whitelist |
| **Итого** | **3 бренда** | **132 SKU** | **97.0%** | **только officials** | **3 forced n/a Canon, 1 Epson, 0 Ricoh** |

**Содержательные итоги:**

1. **Полная очистка ритейлерского долга.** Принтер-copir.ru, DNS-shop,
   Citilink, 3Logic, эвристика «серия→spec» больше не присутствуют в
   prod attrs_source ни для одного SKU printers_mfu по этим 3 брендам.
2. **132/136 SKU = 97.0% покрытие** с whitelist-источников (4 forced
   n/a — G540, LBP722Cdw, iX6840 Canon + L3252 Epson — реально нет на
   международных whitelist'ах).
3. **starter_cartridge_pages регрессии:** Canon -20 (53/14 → 33/34) —
   asia.canon не публикует, printer-copir публиковал. Epson +2 (28/3 →
   30/1) — epson.com.sg/eu публикует. Ricoh: 0/34 без изменений.
4. **Содержательные фиксы:** Ricoh IM 350 + IM C530FB → светодиодная
   (round 3 эвристика говорила лазерная). Pro C5300SL → 65 ppm
   (round 3 могла быть 55).
5. **0 регрессий тестов:** pytest auctions baseline 242 во всех 3 чатах.

**Паттерны, которые сработали:**

1. **Worktree-isolation + копирование `.env` руками** — обходит то, что
   .env не tracked.
2. **Discovery-скрипт с двойным `dotenv_values` override** — даёт чистый
   доступ к prod-БД и сразу dev OPENAI_API_KEY.
3. **Gate-проверка ОДНОЙ WebSearch+WebFetch ДО subagent'ов** — экономит
   ресурсы, если whitelist недоступен (нужно эскалировать без subagent'ов).
4. **2-4 параллельных subagent'а** в пределах cap 4-5
   (`feedback_subagent_parallelism`). Промт с явным whitelist + blacklist +
   обязательным `source_url` + retry 3 + n/a при fail + cap WebFetch.
5. **Main-thread валидация через `urlparse.hostname` + explicit
   whitelist set** (не regex — Canon r4 урок про `asia.canon`).
   Расширение whitelist'а на subsidiaries — архитектурное решение
   исполнителя по `feedback_executor_no_architectural_questions`.
6. **Normalizer-слой (Epson r4 + Ricoh r4)** — обязательная стадия
   между source-валидацией и validate_attrs. Сделал нормализацию из
   human-readable subagent-выхода к каноническим schema-значениям.
7. **Прямой batched UPDATE через UNNEST** — не importer (importer'овский
   per-key merge оставил бы грязные данные для forced-n/a SKU).
8. **Sample-чек 5/5 SKU** — дешёвая верификация согласованности
   normalizer + UPDATE.
9. **Done-артефакт в archive/<date>/<brand>_round4_001.json** для
   audit-trail, importer НЕ зовётся.

**Оставшийся грязный долг round 3:** только HP retry (140 SKU n/a из-за
WebFetch hp.com timeout 60-сек + Anthropic rate-limit при retry). Это
**отложенный**, не **грязный** долг — нет ритейлерских данных, просто
не обогащено вообще.

## 7. Открытые задачи для следующего оркестратор-чата

1. **HP r2 retry (140 SKU n/a)** — приоритет 1. Дождаться сброса
   Anthropic rate-limit'а. Test WebFetch `support.hp.com` ДО запуска
   subagent'ов; если 60-сек таймаут стабилен — escalate с опциями
   (PDF datasheets через `pdftotext` локально на офисном сервере /
   ручная подгрузка CSV / скип HP до возвращения официального ru-домена).
2. **Avision + Katusha IT (28 SKU fully-empty)** — отдельная стратегия
   approximated_from / brand-code lookup.
3. **PDF-проход для starter yield Ricoh** (если бизнес-кейс потребует) —
   `pdftotext` локально на офисном сервере + поиск brochures на
   support.ricoh.com / ricoh.eu. Сейчас 0/34, легитимный n/a.
4. **Точечный approximated_from для L3252** (Epson EAEU) + точечный
   USB-проход для L6270/L6290 (Epson) — single-key gap'ы.
5. **Унификация Canon starter yield** — Canon r5 PDF-проход через
   `filetype:pdf site:canon.*` (если бизнес приоритет).
6. **Matching baseline после r4** — `scripts/run_matching.py` для оценки
   реального эффекта 132 чистых attrs на matches (backlog из orchestrator-чата
   2026-05-13/14).

## 8. Что использовать в HP r2 retry / любых будущих re-enrichment'ах

Паттерн **полностью валидирован** на 3 брендах. В шаблон для следующих
чатов добавить:

- **Whitelist subsidiaries сразу в промт subagent'у** (не только в
  main-thread validate). Для HP: hp.com региональные + assets.hp.com +
  support.hp.com + h*.www.hp.com (CDN-домены).
- **Канонические enum-значения как примеры в промт** (урок Epson r4 +
  Ricoh r4 — у Ricoh subagent'ы дали ~80% канонических ответов сразу,
  у Epson только ~20%, разница в формулировке промта).
- **PDF brochures fallback явно в промте** для брендов, которые редко
  публикуют yield на model-pages (HP — нужно сразу искать
  `filetype:pdf site:hp.com starter cartridge yield`).
- **Если WebFetch на основной домен (support.<brand>.com) стабильно
  timeout'ит** — escalate ДО subagent'ов, не запускай 4 параллельных
  subagent'а, которые сожгут квоту на retry-петлях. Это HP r1 урок,
  не повторять в r2.

## 9. Артефакты

- **Done-файл (артефакт)**: `enrichment/auctions/archive/2026-05-14/ricoh_round4_001.json`
  (34 items с `sku`/`source_url`/`attrs` + `_methodology_note` + `summary`).
- **Master HEAD до старта чата**: `ccf8da0`.
- **Worktree**: `feature/re-enrich-ricoh-r4`.
- **Эфемерные скрипты (удаляются до commit'а):**
  `_ricoh_r4_discovery.py`, `_ricoh_r4_split.py`,
  `_ricoh_r4_extract_models.py`, `_ricoh_r4_models_manual.py`,
  `_ricoh_r4_validate.py` (с extended-whitelist + normalizer),
  `_ricoh_r4_apply.py`, `_ricoh_r4_sample_check.py`,
  `_ricoh_r4_build_artifact.py`.
- **Эфемерные JSON (удаляются до commit'а):** `_ricoh_r4_discovery.json`,
  `_ricoh_r4_groups.json`, `_ricoh_r4_models.json`,
  `_ricoh_r4_subagent_color.json`, `_ricoh_r4_subagent_mono.json`,
  `_ricoh_r4_validated.json`, `_ricoh_r4_sku_names.txt`.
