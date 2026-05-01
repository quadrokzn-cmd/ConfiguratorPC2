# Обогащение характеристик КОРПУСОВ через Claude Code

> Перед началом обязательно прочти `_общие_правила.md` — там общие
> требования к источникам, формат ответа и описание полей входного JSON
> (`mpn`, `gtin`, `raw_names`, `current`, `to_fill`).

## Целевые поля и их валидация

| Поле | Тип | Допустимые значения / диапазон |
|---|---|---|
| `supported_form_factors` | list[str] | непустой массив форм-факторов из набора `{"E-ATX","ATX","MATX","ITX","XL-ATX","SSI-EEB","SSI-CEB"}` |
| `has_psu_included` | bool | `true`, если в комплекте идёт встроенный БП; иначе `false` |
| `included_psu_watts` | int | 100..2000 (только если `has_psu_included = true`; иначе `null` + reason) |

> Все три поля связаны: если `has_psu_included = false`, то
> `included_psu_watts` обязан быть `null` (с пояснением «БП не входит
> в комплект»). Не возвращай число там, где has_psu_included=false.

## ⚠ Защитный слой: SBC и одноплатные компьютеры

Если `raw_name` (или `model`) содержит хотя бы один из маркеров —
**Raspberry Pi**, **Orange Pi**, **Rock Pi**, **Pico**, **Arduino**,
**SBC** — это корпус для одноплатного компьютера, **не PC-корпус**.
Возвращай ВСЕ три целевых поля как:

```json
{"value": null, "source_url": null, "reason": "Корпус для одноплатного компьютера (SBC), не подходит для PC-сборки"}
```

Не пытайся искать у них ATX-форм-факторы или БП — у Raspberry Pi случай
просто физически другой класс изделия.

## Где искать

**Whitelist оф. доменов корпусов** (только эти URL пройдут валидатор;
любой другой домен — `null` + reason):

`jonsbo.com`, `fractal-design.com`, `lian-li.com`, `nzxt.com`,
`phanteks.com`, `thermaltake.com`, `chenbro.com`, `aerocool.io`,
`montechpc.com`, `azza.com.tw`, `aicipc.com`, `ocypus.com`,
`in-win.com`, `hpe.com`, `gamemax.com`, `raijintek.com`, `xpg.com`,
`powerman-pc.ru`, `digma.ru`, `hiper.ru`, `silverstonetek.com`,
`formula-pc.ru`, `fox-line.ru`, `accord-pc.ru`, `kingprice.ru`,
`acd-group.com`.

> Список синхронизирован с
> `app/services/enrichment/claude_code/schema.py::OFFICIAL_DOMAINS`
> (case-секция, включая 6 доменов, добавленных на этапе 11.6.2.4.0:
> `gamemax.com`, `raijintek.com`, `xpg.com`, `powerman-pc.ru`,
> `digma.ru`, `hiper.ru`).

Магазины (DNS, Citilink, Ozon, Wildberries, Amazon, Newegg) и
агрегаторы/обзорщики (3DNews, Tom's Hardware, TechPowerUp, iXBT) —
**не источники**. См. также пункт 2 в `_общие_правила.md`.

### Подсказки по конкретным доменам

- **JONSBO** — `jonsbo.com/en/products/<series>` → Specification.
- **Fractal Design** — `fractal-design.com/products/cases/<series>/<model>` →
  Specifications → "Motherboard compatibility" + "Power supply".
- **Lian Li** — `lian-li.com/product/<series>/<model>`.
- **NZXT** — `nzxt.com/product/<model>` → Tech Specs.
- **Phanteks** — `phanteks.com/<series>` → Specifications.
- **Thermaltake** — `thermaltake.com/products/<series>` → Specifications.
- **AeroCool** — `aerocool.io/product/<series>/<model>`.
- **Montech** — `montechpc.com/product/<series>`.
- **Azza** — `azza.com.tw/product/<series>/<model>`.
- **Ocypus** — `ocypus.com/product/<series>` (часто корпус + БП в одной
  spec).
- **InWin** — `in-win.com/en/case/<series>` (модели IW-RS436 и др.).
- **Chenbro / AICIPC** — серверные/индустриальные шасси, форм-факторы
  обычно `SSI-EEB` / `SSI-CEB` / `E-ATX`.
- **GameMax** — `gamemax.com/product/<series>` (Asgard, Vega, Diamond и др.).
- **RAIJINTEK** — `raijintek.com/products/<series>` (Ophion / Asterion / Thetis).
- **XPG (ADATA gaming)** — `xpg.com/<region>/case/<series>` (INVADER X и др.).
- **POWERMAN** — `powerman-pc.ru/catalog/korpusa/<model>`.
- **DIGMA / HIPER** — `digma.ru/catalog/<model>`, `hiper.ru/catalog/<model>`.
- **Российские ОЕМ-сборщики** — `formula-pc.ru`, `accord-pc.ru`,
  `kingprice.ru`, `acd-group.com`, `fox-line.ru` (часто корпуса
  собственного бренда у этих сборщиков).
- **HPE** — `hpe.com` для серверных шасси ProLiant / Apollo.

## Нормализация форм-факторов

Возвращай каноничные значения **строго из whitelist валидатора**:
`E-ATX`, `ATX`, `MATX`, `ITX`, `XL-ATX`, `SSI-EEB`, `SSI-CEB`.

Маппинг частых вариантов написания → каноничное:

| Источник пишет | Возвращай |
|---|---|
| `Mini-ITX`, `MiniITX`, `mITX`, `M-ITX` | `ITX` |
| `Micro-ATX`, `mATX`, `MicroATX`, `µATX`, `M-ATX`, `uATX` | `MATX` |
| `Extended ATX`, `EATX` | `E-ATX` |
| `ATX` | `ATX` (без изменений) |
| `XL-ATX` | `XL-ATX` |
| `SSI-EEB`, `SSI EEB` | `SSI-EEB` |
| `SSI-CEB`, `SSI CEB` | `SSI-CEB` |

> **mini-DTX, FlexATX, Pico-ITX, Nano-ITX и пр.** — валидатор не
> принимает. Если корпус **только** под такие форм-факторы (не
> совместим ни с одним из 7 каноничных) — возвращай `supported_form_factors`
> как `null` с пояснением «не совместим ни с одним из принятых
> форм-факторов». Если же в спеке кроме них указаны и совместимые
> (часто mini-DTX заявлен **дополнительно** к ITX) — возвращай только
> совместимые.

Иногда производитель пишет одной строкой: «Mid Tower ATX/mATX/ITX».
Это означает массив `["ATX", "MATX", "ITX"]` (без дубликатов, в любом
порядке).

> Важно: возвращай **реальную совместимость** по spec-листу, а не
> «физический размер корпуса». E-ATX-корпус, у которого Motherboard
> Support перечисляет «E-ATX, ATX, mATX, ITX» — это массив из 4
> элементов; не нужно сводить к одному «E-ATX».

## Правило `has_psu_included`

- `true` — если в spec-листе явно указан встроенный БП с конкретной
  мощностью (например, «Pre-installed PSU 450W», «Power Supply: 700W
  bronze», «БП в комплекте 600 Вт»).
- `false` — если «Power Supply: Not Included», «БП не входит в
  комплект», или ни в одном разделе spec-листа БП не упомянут.
- Если spec-страница нашлась, но раздел БП в ней отсутствует и
  допускает обе трактовки — вернуть `null` с пояснением «PSU section
  отсутствует в spec, неоднозначно».

## Правило `included_psu_watts`

- Берём номинальную мощность встроенного БП в Вт **строго так, как
  указано на оф. странице вендора**. Не округлять, не «оценивать на
  глаз».
- Если `has_psu_included = false` → `included_psu_watts` обязан быть
  `null` (`reason: "БП не входит в комплект"`).
- Если корпус **очень малой мощности (< 100 Вт)** или мощность не
  указана в спеке — `null` с пояснением.
- Если на странице корпус продаётся в нескольких комплектациях с
  разными БП (например, «версии 500W / 600W / 700W») — выбирай
  **минимальную** заявленную мощность с пояснением в `reason`, либо
  `null` если непонятно, какая комплектация в нашей БД.

## Honest-null

Если на доменах из whitelist карточка по `mpn`/`gtin`/`manufacturer + model`
**не найдена** — все три целевых поля верни как `null` с пояснением
(«Карта не найдена на оф. сайте бренда»). **Ничего не выдумывать**, не
угадывать «по аналогии» с другим корпусом той же серии.

## Формат ответа

Стандартный формат из `_общие_правила.md`. Каждое непустое значение
сопровождается `source_url` (HTTPS, домен из whitelist выше). В корне
ответа — массив `sources_used` со списком всех URL, которые
действительно использовались для заполнения батча (это упростит
ручную выборочную проверку).

```json
{
  "category":     "case",
  "batch_id":     "<тот же>",
  "filled_at":    "2026-05-01T13:45:00Z",
  "sources_used": [
    "https://www.fractal-design.com/products/cases/define/define-7/black/",
    "https://gamemax.com/product/asgard-300-bk"
  ],
  "items": [
    {
      "id": <тот же int>,
      "fields": {
        "supported_form_factors": {
          "value": ["E-ATX", "ATX", "MATX", "ITX"],
          "source_url": "https://..."
        },
        "has_psu_included":       {"value": false, "source_url": "https://..."},
        "included_psu_watts":     {"value": null,  "source_url": null,
                                   "reason": "БП не входит в комплект"}
      }
    }
  ]
}
```

## Куда сохранить

`enrichment/done/case/<имя_входного_файла>` (то же имя, что у входного
batch-файла, чтобы автоматический импорт его подцепил).

## Примеры (input → output)

### Пример 1. Стандартный mid-tower без БП

**Input:**
```json
{
  "id": 12345,
  "manufacturer": "Fractal Design",
  "model":        "Define 7 Black",
  "mpn":          "FD-C-DEF7A-01",
  "gtin":         "",
  "raw_names": [
    "Корпус Fractal Design Define 7 Black FD-C-DEF7A-01 без БП ATX"
  ],
  "current":  {},
  "to_fill":  ["supported_form_factors", "has_psu_included"]
}
```

**Output (для этого item):**
```json
{
  "id": 12345,
  "fields": {
    "supported_form_factors": {
      "value": ["E-ATX", "ATX", "MATX", "ITX", "SSI-CEB", "SSI-EEB"],
      "source_url": "https://www.fractal-design.com/products/cases/define/define-7/black/"
    },
    "has_psu_included": {
      "value": false,
      "source_url": "https://www.fractal-design.com/products/cases/define/define-7/black/"
    }
  }
}
```

### Пример 2. Корпус с встроенным БП (POWERMAN)

**Input:**
```json
{
  "id": 23456,
  "manufacturer": "POWERMAN",
  "model":        "ST-2202 450W",
  "mpn":          "6189606",
  "raw_names": [
    "Корпус mATX POWERMAN ST-2202 (450W, 1xUSB 3.0, 2xUSB 2.0) черный"
  ],
  "current":  {},
  "to_fill":  ["supported_form_factors", "has_psu_included", "included_psu_watts"]
}
```

**Output:**
```json
{
  "id": 23456,
  "fields": {
    "supported_form_factors": {
      "value": ["MATX", "ITX"],
      "source_url": "https://powerman-pc.ru/catalog/korpusa/st-2202/"
    },
    "has_psu_included":   {"value": true, "source_url": "https://powerman-pc.ru/catalog/korpusa/st-2202/"},
    "included_psu_watts": {"value": 450,  "source_url": "https://powerman-pc.ru/catalog/korpusa/st-2202/"}
  }
}
```

### Пример 3. Корпус для Raspberry Pi (защитный слой)

**Input:**
```json
{
  "id": 34567,
  "manufacturer": "Raspberry Pi",
  "model":        "Case for Pi 5 Red/White",
  "raw_names":    ["Корпус для Raspberry Pi 5 Official Case Red/White"],
  "current":  {},
  "to_fill":  ["supported_form_factors", "has_psu_included", "included_psu_watts"]
}
```

**Output:**
```json
{
  "id": 34567,
  "fields": {
    "supported_form_factors": {
      "value": null, "source_url": null,
      "reason": "Корпус для одноплатного компьютера (SBC), не подходит для PC-сборки"
    },
    "has_psu_included": {
      "value": null, "source_url": null,
      "reason": "Корпус для одноплатного компьютера (SBC), не подходит для PC-сборки"
    },
    "included_psu_watts": {
      "value": null, "source_url": null,
      "reason": "Корпус для одноплатного компьютера (SBC), не подходит для PC-сборки"
    }
  }
}
```

## Чек-лист самопроверки перед сохранением

- [ ] Каждое непустое `value` имеет `source_url` с https:// и доменом
      из whitelist выше.
- [ ] Все form-factor значения — строго из набора
      `{"E-ATX","ATX","MATX","ITX","XL-ATX","SSI-EEB","SSI-CEB"}`,
      без дубликатов.
- [ ] Если `has_psu_included = false`, то `included_psu_watts = null`
      с пояснением.
- [ ] Если в `raw_names` встретился маркер SBC (Raspberry/Orange/Rock
      Pi, Pico, Arduino, SBC) — все три поля `null` с одинаковым
      reason.
- [ ] Ни одно поле из `current` не задублировано в `fields`.
- [ ] В корне ответа заполнен массив `sources_used`.
