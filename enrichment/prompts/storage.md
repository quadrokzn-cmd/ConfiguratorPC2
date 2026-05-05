# Обогащение характеристик НАКОПИТЕЛЕЙ через Claude Code

> Перед началом обязательно прочти `_общие_правила.md` — там общие
> требования к источникам, формат ответа и описание полей входного JSON
> (`mpn`, `gtin`, `raw_names`, `current`, `to_fill`).

Этап 11.6.2.6.1a закрывает остаточный длинный хвост видимых SSD/HDD,
у которых regex'ом не разобраны интерфейс / форм-фактор / тип / ёмкость.
Состояние на 2026-05-05 после 11.6.2.6.0b: 1185 видимых storages,
NULL.interface 96, NULL.form_factor 94, NULL.storage_type 8,
NULL.capacity_gb 2.

Топ-NULL по брендам: unknown 45, ExeGate 22, Silicon Power 21,
Apacer 14, A-DATA 12, Transcend 9, Western Digital 7, Samsung 6,
Digma 5, Patriot 4, Crucial 4, MSI 3, Netac 3, Kingston 2, Hikvision 1.

## Целевые поля и их валидация

| Поле           | Тип  | Допустимые значения / диапазон |
|----------------|------|--------------------------------|
| `storage_type` | enum | `"SSD"`, `"HDD"` (NVMe → нормализуется в `"SSD"`) |
| `form_factor`  | enum | `"2.5\""`, `"3.5\""`, `"M.2"`, `"mSATA"` |
| `interface`    | enum | `"SATA"`, `"NVMe"`, `"SAS"` |
| `capacity_gb`  | int  | 1..256000 (то есть 1 ГБ … 256 ТБ) |

> **Валидатор не принимает другие значения** (см.
> `app/services/enrichment/claude_code/validators.py::_v_storage_*`).
> Все «External» / «USB-3.2» / «M.2 2280» / «U.2» / «mini-PCIe» — это
> техдолг расширения валидатора; пока возвращай `null` с конкретной
> reason (см. защитные слои 1 и 2 ниже).

## ⚠ Защитный слой 1: External / USB-SSD

Если в `raw_name` или `model` встречается «External» **в сочетании с
USB / Type-C** (типичные кейсы: A-DATA SC740/SC750/SD620, Silicon Power
PC60/PX10, Samsung T7, SanDisk Extreme Portable, WD My Passport,
Transcend ESD310/ESD380):

* `interface = null` + reason: «USB-интерфейс не входит в текущий enum
  валидатора (SATA/NVMe/SAS), техдолг расширения».
* `form_factor = null` + reason: «External — не входит в текущий enum
  валидатора, техдолг расширения».
* `storage_type` — заполняй как обычно (`"SSD"` для большинства, реже
  `"HDD"` для USB-HDD типа WD Elements). У валидатора enum только
  `{HDD, SSD}`.
* `capacity_gb` — заполняй из спеки (типичные 500/1000/2000 ГБ).

Не пропускай `storage_type` и `capacity_gb` только из-за того, что два
других поля — `null`. Это всё ещё SSD на 1 ТБ, его реально продают.

## ⚠ Защитный слой 2: U.2 / U.3 enterprise SSD

Если на оф. странице форм-фактор указан как `U.2` (или `U.3`,
`E1.S`, `E3.S`) — типичные кейсы Samsung PM1733/PM9A3,
Solidigm D7-P5520, Kioxia CD8 — валидатор примет только
`form_factor = null` с reason: «U.2/U.3 не входит в текущий enum
валидатора (2.5"/3.5"/M.2/mSATA), техдолг расширения».

`storage_type`, `interface`, `capacity_gb` — заполняй нормально:
NVMe-over-U.2 → `interface = "NVMe"`, `storage_type = "SSD"`.

## ⚠ Защитный слой 3: M.2 SATA vs M.2 NVMe

У M.2-SSD интерфейс может быть и SATA (M.2 SATA, например Kingston
KC600M, Transcend MTS800S), и NVMe (Samsung 980 PRO, Crucial P310). На
оф. сайте всегда явно указано, какой именно. **Не путай слот с
интерфейсом.**

Подсказка по `raw_name`:
* `M.2 SATA`, `SATA-III M.2` → `interface = "SATA"`, `form_factor = "M.2"`.
* `M.2 NVMe`, `M.2 PCIe`, `PCI-E 4.0 x4`, `PCIe 3.0 x4` → `interface = "NVMe"`,
  `form_factor = "M.2"`.

Валидатор для `interface`: `PCIe`/`PCI-E`/`PCI Express` без `SATA` →
нормализуется в `"NVMe"` (см. `_v_storage_interface`).

## ⚠ Защитный слой 4: manufacturer = "unknown"

Если `manufacturer == "unknown"` (топ-NULL: 45 позиций), сначала
попробуй извлечь бренд из `raw_names` или `model`:

* «CBR SSD-128GB-2.5-BS24b …» → бренд `CBR` (но cbr.ru НЕ в whitelist —
  honest-null). Однако `form_factor = "2.5\""` и `interface = "SATA"`
  явно указаны в `raw_name` — их можно записать без оф. источника?
  **Нет**: каждое непустое значение должно сопровождаться `source_url`
  с whitelist-домена. Если домен бренда вне whitelist — оставляй `null`
  с пояснением «бренд CBR, оф. сайт cbr.ru вне whitelist».

* «Apacer …» / «Silicon Power …» / «Patriot …» — даже если
  manufacturer пустой/unknown, бренд извлекается из имени → ищи на
  соответствующем оф. сайте (`apacer.com`, `silicon-power.com`,
  `patriotmemory.com` — все в whitelist).

Если бренд из `raw_name` **не извлекается** (нет узнаваемого
слова-бренда) — все поля `null` с reason «бренд не определён, поиск
невозможен».

## ⚠ Защитный слой 5: не-storage ошибочно в категории

После 11.6.2.6.0b non-storage детектор должен был спрятать корпуса/
кулеры/мат.платы из категории storages. Но если в батче всё-таки
проскочит позиция, у которой `model` начинается со слов «Корпус …»,
«Кулер …», «Материнская плата …», «Видеокарта …», «Блок питания …» —
вернуть все 4 поля как `null` с reason «Не storage, категоризация
ошибочна».

## Где искать

**Whitelist оф. доменов накопителей** (только эти URL пройдут
валидатор; вне списка — `null` + reason). Список синхронизирован с
`app/services/enrichment/claude_code/schema.py::OFFICIAL_DOMAINS`
(storage-секция, расширенная на 11.6.2.6.0b +10 доменов:
`crucial.com`, `samsung.com`, `transcend-info.com`, `adata.com`,
`solidigm.com`, `silicon-power.com`, `patriotmemory.com`,
`sandisk.com`, `synology.com`, `kioxia.com`).

**Прямые домены (15):**
`kingston.com`, `westerndigital.com`, `seagate.com`, `netac.com`,
`apacer.com`, `crucial.com`, `samsung.com`, `transcend-info.com`,
`adata.com`, `solidigm.com`, `silicon-power.com`, `patriotmemory.com`,
`sandisk.com`, `synology.com`, `kioxia.com`.

**Кросс-категорийные вендоры** (тоже выпускают SSD):
`exegate.ru` (ExeGate Next/NextPro/NextPro+), `xpg.com` (XPG SX/Atom).

Магазины (DNS, Citilink, Ozon, Wildberries, Amazon, Newegg) и
агрегаторы/обзорщики (3DNews, Tom's Hardware, TechPowerUp, iXBT) —
**не источники**. См. также пункт 2 в `_общие_правила.md`.

### Подсказки по конкретным брендам

* **Kingston** — `kingston.com/.../ssd/...` или datasheet PDF на
  поддомене. Серии A400, NV2, KC600/KC600M (mSATA).
* **Western Digital / WD** — `westerndigital.com/.../<model>` (внутри
  Specs). HDD: Blue/Red/Purple/Gold; SSD: Blue SA510, Black SN770/SN850X.
* **Seagate** — `seagate.com/internal-hard-drives/...`. IronWolf,
  BarraCuda, FireCuda. Для SSD — FireCuda 530/540, IronWolf 125.
* **Samsung** — `semiconductor.samsung.com/consumer-storage/...` или
  `samsung.com`. Серии 870 EVO (SATA 2.5"), 980/990 PRO (M.2 NVMe),
  T7/T9 (External USB), PM1733/PM9A3 (enterprise U.2).
* **Crucial** — `crucial.com/ssd/<series>/<model>`. MX500 (SATA 2.5"),
  P3/P5 Plus/T700 (M.2 NVMe), E100/P310 (новейшие).
* **Transcend** — `transcend-info.com/products/...`. SSD220S/MTE220S,
  MSA452T (mSATA), ESD310/ESD380 (External).
* **A-DATA** — `adata.com/<region>/feature/<series>/`. Серии SU650,
  SU750, SU800, Legend 800/960 (M.2 NVMe), SC740/SC750/SD620
  (External USB-SSD).
* **XPG (ADATA gaming)** — `xpg.com/<region>/ssd/<series>`. SX, Atom,
  Gammix, Storm.
* **Solidigm** (бывшая Intel SSD) — `solidigm.com/products/<series>`.
  P41 Plus (consumer), D7/D5 (enterprise U.2).
* **Silicon Power** — `silicon-power.com/web/product-<model>`. A55/A58/
  A60 (SATA 2.5"), UD/PA (M.2 NVMe), PX10/PC60 (External).
* **Patriot** — `patriotmemory.com/products/...`. P210 (SATA 2.5"),
  P310/P320/P400 (M.2 NVMe), Viper VP4300/VP4530.
* **SanDisk** — `sandisk.com/.../products/...`. Plus/Ultra (SATA),
  Extreme Portable / Pro Portable (External USB).
* **Synology** — `synology.com/.../products/<model>`. Enterprise SSD:
  SAT5210/SAT5221 (SATA 2.5"), SNV3410/SNV3510 (M.2 NVMe).
* **KIOXIA** (бывш. Toshiba Memory) — `kioxia.com/.../products/...`.
  EXCERIA, EXCERIA G2, EXCERIA PLUS G3 (NVMe). Старая линейка Toshiba
  → искать новый эквивалент в KIOXIA.
* **Netac** — `netac.com/.../products/...`. N600S, N5M, Z9, NV7000.
* **Apacer** — `apacer.com/en/product/ssd/...`. AS340, AS350,
  AS2280P4U.
* **ExeGate** — `exegate.ru/products/ssd/`. Серии Next, NextPro,
  NextPro+. В `mpn` (`EX276687RUS`) часто закодирована линейка, но
  **подтверждай по спецстранице** (форм-фактор обязательно — у Next
  бывает и 2.5", и M.2 2280). 22 ExeGate в NULL → высокий приоритет.

## Нормализация значений

* **`interface`** — валидатор приводит к каноническому регистру:
  * `SATA-III`, `SATA 6 Gb/s`, `SATA 3.0`, `SATA II`, `SATA 6Gbps`
    → `"SATA"`.
  * `NVMe`, `NVMe PCIe 4.0 x4`, `PCIe 4.0 x4 NVMe`, `M.2 NVMe` → `"NVMe"`.
  * `PCIe 3.0 x4`, `PCI-E 4x4` (без явного «SATA») → `"NVMe"`
    (валидатор интерпретирует PCIe-only как NVMe — см.
    `_v_storage_interface`).
  * `SAS 12Gb/s`, `SAS-3` → `"SAS"`.
  * `USB 3.2 Gen 2`, `USB-C`, `Thunderbolt 4` → **`null`** + reason
    (защитный слой 1).
* **`form_factor`** — точные строки с кавычками:
  * `2.5-inch`, `2.5"`, `2.5'`, `2.5”`, `2.5″` → `"2.5\""`.
  * `3.5-inch`, `3.5"` → `"3.5\""`.
  * `M.2 2280`, `M.2 2230`, `M.2 2242`, `M.2 22110` — все
    нормализуются в `"M.2"` (длина в БД не хранится).
  * `mSATA` → `"mSATA"`.
  * `U.2`, `U.3`, `E1.S`, `E3.S`, `External`, `Half-Slim`, `mini-PCIe`
    → **`null`** + reason (защитный слой 2 / 1).
* **`storage_type`**:
  * SSD на любом интерфейсе (SATA, NVMe, SAS, USB) → `"SSD"`.
  * HDD (вращающиеся) → `"HDD"`.
  * NVMe как отдельный тип в БД не хранится — нормализуется в `"SSD"`.
* **`capacity_gb`** — целое в ГБ (десятичная маркетинговая нотация,
  как у вендоров):
  * 1 ТБ = 1000, 2 ТБ = 2000, 4 ТБ = 4000, 8 ТБ = 8000, 18 ТБ = 18000.
  * Маленькие SSD (32, 64, 120, 128, 240, 256, 480, 512, 960, 1000)
    — записываем как есть.
  * Enterprise серверные диски: 1.92 ТБ → 1920, 3.84 ТБ → 3840,
    7.68 ТБ → 7680, 15.36 ТБ → 15360.

## Honest-null

Если на доменах из whitelist карточка по `mpn`/`gtin`/`manufacturer +
model` **не найдена** — `null` с пояснением («Карта не найдена на оф.
сайте бренда»). **Ничего не выдумывать**, не «достраивать по аналогии»
с другой моделью серии. Не использовать сторонние обзоры даже если на
них сводка spec.

## Формат ответа

Стандартный из `_общие_правила.md`. Каждое непустое значение
сопровождается `source_url` (HTTPS, домен из whitelist выше). В корне
ответа — массив `sources_used` со списком всех URL, действительно
использованных для заполнения батча.

```json
{
  "category":     "storage",
  "batch_id":     "<тот же>",
  "filled_at":    "2026-05-05T13:45:00Z",
  "sources_used": [
    "https://exegate.ru/products/ssd/next/ex276687rus/",
    "https://www.crucial.com/ssd/p310/CT4000P310SSD8"
  ],
  "items": [
    {
      "id": <тот же int>,
      "fields": {
        "form_factor": {
          "value": "2.5\"",
          "source_url": "https://exegate.ru/products/ssd/next/ex276687rus/"
        }
      }
    }
  ]
}
```

## Куда сохранить

`enrichment/done/storage/<имя_входного_файла>` (то же имя, что у
входного batch-файла, чтобы автоматический импорт его подцепил).

## Примеры (input → output)

### Пример 1. Внутренний M.2 NVMe SSD (Samsung 980 PRO)

**Input:**
```json
{
  "id": 821,
  "manufacturer": "Samsung",
  "model":        "Твердотельный диск 1TB Samsung 980 PRO, M.2, PCI-E 4.0 x4, TLC 3D NAND [R/W - 7000/5000 MB/s] with Heatsink",
  "mpn":          "MZ-V8P1T0CW",
  "gtin":         "",
  "raw_names": [
    "Твердотельный диск 1TB Samsung 980 PRO, M.2, PCI-E 4.0 x4, TLC 3D NAND [R/W - 7000/5000 MB/s] with Heatsink"
  ],
  "current":  {"storage_type": "SSD", "form_factor": "M.2", "capacity_gb": 1000},
  "to_fill":  ["interface"]
}
```

**Output:**
```json
{
  "id": 821,
  "fields": {
    "interface": {
      "value": "NVMe",
      "source_url": "https://semiconductor.samsung.com/consumer-storage/internal-ssd/980pro/MZ-V8P1T0CW/"
    }
  }
}
```

> M.2 + PCIe 4.0 x4 = NVMe, ничего из `current` не дублируем.

### Пример 2. ExeGate Next 2.5" SATA SSD (типичный кластер NULL.form_factor)

**Input:**
```json
{
  "id": 887,
  "manufacturer": "ExeGate",
  "model":        "ExeGate SSD 120GB Next Series EX276687RUS {SATA3.0}",
  "mpn":          "EX276687RUS",
  "raw_names":    ["ExeGate SSD 120GB Next Series EX276687RUS {SATA3.0}"],
  "current":  {"storage_type": "SSD", "interface": "SATA", "capacity_gb": 120},
  "to_fill":  ["form_factor"]
}
```

**Output:**
```json
{
  "id": 887,
  "fields": {
    "form_factor": {
      "value": "2.5\"",
      "source_url": "https://exegate.ru/products/ssd/next/ex276687rus/"
    }
  }
}
```

> Серия Next (без «M.2» в имени) у ExeGate — 2.5" SATA. Серия Next M.2
> идёт под отдельным префиксом MPN. Подтверждай по спецстранице.

### Пример 3. A-DATA SC740 External USB-SSD (защитный слой 1)

**Input:**
```json
{
  "id": 817,
  "manufacturer": "A-DATA",
  "model":        "Твердотельный диск 1TB A-DATA SC740, External, USB 3.2 Gen 2 Type-C, [R/W -1050/1000 MB/s] синий",
  "mpn":          "SC740-1000G-CBU",
  "raw_names":    ["Твердотельный диск 1TB A-DATA SC740, External, USB 3.2 Gen 2 Type-C, [R/W -1050/1000 MB/s] синий"],
  "current":  {"storage_type": "SSD", "capacity_gb": 1000},
  "to_fill":  ["form_factor", "interface"]
}
```

**Output:**
```json
{
  "id": 817,
  "fields": {
    "form_factor": {
      "value": null, "source_url": null,
      "reason": "External — не входит в текущий enum валидатора (2.5\"/3.5\"/M.2/mSATA), техдолг расширения"
    },
    "interface": {
      "value": null, "source_url": null,
      "reason": "USB 3.2 Gen 2 — не входит в текущий enum валидатора (SATA/NVMe/SAS), техдолг расширения"
    }
  }
}
```

> `storage_type` и `capacity_gb` уже в `current`, в `fields` их не
> возвращаем. Поиск на adata.com здесь не нужен — оба `to_fill`
> блокируются защитным слоем 1.

## Чек-лист самопроверки перед сохранением

- [ ] Каждое непустое `value` имеет `source_url` с https:// и доменом
      из whitelist выше (15 storage-доменов + exegate.ru/xpg.com).
- [ ] `storage_type` ∈ {`"SSD"`, `"HDD"`} (не «NVMe», не «External»).
- [ ] `form_factor` ∈ {`"2.5\""`, `"3.5\""`, `"M.2"`, `"mSATA"`}
      (длина M.2 — 2280/2230/2242/22110 — отбрасываем; U.2/External/
      mini-PCIe → `null` + reason).
- [ ] `interface` ∈ {`"SATA"`, `"NVMe"`, `"SAS"`} (USB → `null` + reason).
- [ ] `capacity_gb` — целое число в ГБ (1..256000), не строка «1TB».
- [ ] M.2 SATA vs M.2 NVMe — взято с оф. сайта, не «по аналогии».
- [ ] Если `raw_name` содержит «External» + «USB» — взведён защитный
      слой 1 (form_factor=null, interface=null, storage_type/capacity
      заполняются нормально).
- [ ] Если manufacturer = `unknown` — сначала попытка извлечь бренд из
      `raw_name`; если бренд (CBR и т.п.) вне whitelist — все поля
      `null` с конкретной reason.
- [ ] Ни одно поле из `current` не задублировано в `fields`.
- [ ] В корне ответа заполнен массив `sources_used`.
