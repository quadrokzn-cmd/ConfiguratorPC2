# Обогащение характеристик БЛОКОВ ПИТАНИЯ через Claude Code

> Перед началом обязательно прочти `_общие_правила.md` — там общие
> требования к источникам, формат ответа и описание полей входного JSON
> (`mpn`, `gtin`, `raw_names`, `current`, `to_fill`).

Этап 11.6.2.5.1 закрывает 144 видимых PSU без `power_watts` (после
аудита 11.6.2.5.0a/b/c, на проде осталось 1415 видимых psus). Топ-NULL
по брендам: ExeGate 30, unknown 23, Ginzzu 22, Aerocool 16, Deepcool 15,
CHIEFTEC 9, Thermaltake 7, Zalman 6, PcCooler 5, POWERMAN 4, Crown 2,
XPG 2.

## Целевое поле

| Поле          | Тип | Диапазон |
|---------------|-----|----------|
| `power_watts` | int | 100..3000 (валидатор принимает 5..3000, но всё что < 100 практически всегда адаптер — см. защитный слой ниже) |

> Поля `form_factor`, `efficiency_rating`, `modularity`, `has_12vhpwr` в
> БД 100% NULL. На этом этапе они **не входят** в `to_fill`. Если найдёшь
> их попутно на оф. странице — игнорируй: схема валидации их сейчас не
> примет (отдельный этап 11.6.2.5.1b+).

## ⚠ Защитный слой 1: PSU-адаптеры и зарядные

Если в `raw_name` встретилось хотя бы одно (case-insensitive):

* «адаптер», «adapter», «переходник», «зарядное», «charger», «POE»,
  «PoE инжектор», «USB-PD», «powerbank», «dock-station»,
  «блок питания для ноутбука» — **это адаптер**, не системный PSU.
* Бренд-серии: `Gembird NPA-AC*`, `KS-is`, `BURO BUM-*` / `BU-PA-*`,
  `ORIENT PU-C*` / `USB-*` / `SAP-*` / `PA-*`, `GOPOWER`, `WAVLINK`,
  `FSP FSP040`, `Ubiquiti POE-*`, «Бастион РАПАН» — **адаптер**.

Возвращай:
```json
{"value": null, "source_url": null,
 "reason": "PSU-адаптер/зарядное, не системный блок питания"}
```

Защита от ложных срабатываний — если в имени есть `ATX`/`SFX`/`TFX`/`EPS`,
`80+`/`80 PLUS`/`Bronze`/`Gold`/`Platinum`, явная мощность ≥200W, или
серия настоящего PSU (CBR ATX, Exegate UN/PPH/PPX, Ginzzu CB/PC, XPG
KYBER/PROBE/PYMCORE, Zalman ZM, Aerocool Mirage/Cylon/KCAS, Powerman
PM, 1STPLAYER NGDP, Thermaltake Smart, Formula VX/KCAS) — это **PSU**,
не адаптер.

## ⚠ Защитный слой 2: «не-PSU» в категории psus

Если `model` начинается со слов «Корпус …», «Кулер …», «Вентилятор …»,
«Устройство охлаждения …», «MasterBox …», «Mid-tower», «Система
водяного охлаждения …» — позиция категоризирована как PSU ошибочно
(orchestrator на 11.6.2.5.0c должен такое прятать, но старые скелеты
могли остаться).

Возвращай `null` с пояснением «Не PSU (корпус/кулер), категоризация
ошибочна» — power_watts у них не определён.

## ⚠ Защитный слой 3: Ginzzu

Сайт `ginzzu.com` **офлайн**, Ginzzu PSU (серия SA: SA400, SA450,
SA500 и т. п.) на whitelist-доменах не публикуется. Все 22 Ginzzu →
honest-null **без обращения к WebSearch/WebFetch**. Сэкономь тулколлы:

```json
{"value": null, "source_url": null,
 "reason": "Ginzzu PSU: оф. сайт ginzzu.com офлайн, datasheet недоступен"}
```

## ⚠ Защитный слой 4: manufacturer = "unknown"

Если `manufacturer == "unknown"`, сначала попробуй извлечь бренд из
`raw_names` или `model`:

* «Gembird Блок питания …» → бренд `Gembird` → проверь защитный слой 1
  (NPA-AC* — адаптер).
* «Ubiquiti POE-…» → адаптер.
* «FSP FSP040-…» → адаптер.

Если бренд из `raw_name` извлекается → ищи по нему. Если **не**
извлекается (нет узнаваемого слова-бренда) — `null` с пояснением «бренд
не определён, поиск невозможен».

## Где искать

**Whitelist оф. доменов PSU** (только эти URL пройдут валидатор; вне
списка — `null` + reason). Список синхронизирован с
`app/services/enrichment/claude_code/schema.py::OFFICIAL_DOMAINS`
(PSU-секция, расширенная на 11.6.2.5.0c +5 доменов:
`exegate.ru`, `crown-micro.com`, `gamemaxpc.com`, `formulav-line.com`,
`super-flower.com.tw`).

**Прямые PSU-домены:**
`seasonic.com`, `zalman.com`, `chieftec.com`, `chieftec.eu`,
`fsp-group.com`, `fsplifestyle.com`, `gamerstorm.com`, `exegate.ru`,
`crown-micro.com`, `gamemaxpc.com`, `formulav-line.com`,
`super-flower.com.tw`, `ubnt.com`, `ui.com`, `ubiquiti.com`,
`cisco.com`.

**Кросс-категорийные вендоры (тоже выпускают PSU):**
`thermaltake.com`, `corsair.com`, `deepcool.com`, `coolermaster.com`,
`cooler-master.com`, `aerocool.io`, `aerocool.com`, `evga.com`,
`silverstonetek.com`, `bequiet.com`, `be-quiet.net`, `xpg.com`,
`raijintek.com`, `gamemax.com`, `pccooler.com.cn`, `lian-li.com`,
`powerman-pc.ru`, `formula-pc.ru`, `accord-pc.ru`, `kingprice.ru`.

Магазины (DNS, Citilink, Ozon, Wildberries, Amazon, Newegg) и
агрегаторы/обзорщики (3DNews, Tom's Hardware, TechPowerUp, iXBT) —
**не источники**. См. также пункт 2 в `_общие_правила.md`.

### Подсказки по конкретным брендам

* **ExeGate** — `exegate.ru/products/power_supplies/`. Серии PPH/PPX/UN/
  XP/AAA/AA. В `mpn` (`EX282046RUS-OEM`) часто закодирована модель
  (`650PPH-LT-OEM` = 650W), но **подтверждай по спецстранице**.
* **Aerocool / Formula KCAS** — `aerocool.io/product/<series>` или
  `formulav-line.com`. Внимание: серия «Formula KCAS» с пометкой
  «(ex Aerocool)» — это перевыпуск Aerocool под брендом Formula V Line,
  ищи на `formulav-line.com`.
* **Deepcool / GamerStorm** — `deepcool.com/products/<series>` для
  свежих, `gamerstorm.com` для legacy-серий PN-D/PN-M (PF450L/PF550L и
  др.).
* **CHIEFTEC** — `chieftec.com` или `chieftec.eu`. Серии SteelPower
  (BDK-*), Polaris (PPS-*), Smart (GPS-*).
* **Thermaltake** — `thermaltake.com/<region>/products/<series>`. Серия
  Smart (BM2/BM3, BX1/BX2), Toughpower (GF/GX/PF), iRGB.
* **Zalman** — `zalman.com/global/product/<series>`. Серии ZM-XE
  (Wattbit), TX, GVII, MegaMax, GigaMax.
* **PcCooler** — `pccooler.com.cn/products/`. Серии KF (KF550, YS1200),
  P5 (P5-YK850, P5-YS850).
* **POWERMAN** — `powerman-pc.ru/catalog/bloki_pitaniya/<model>`.
  Серии PM, PMP. В imya обычно есть мощность (`PMP-450ATX` → 450W).
* **Crown / Crown Micro** — `crown-micro.com/catalog/power-supplies/`.
  Серия CM-PS (CM-PS400W, CM-PS450W smart).
* **XPG (ADATA gaming)** — `xpg.com/<region>/psu/<series>`. PROBE,
  PYMCORE, KYBER, CYBERCORE.
* **Ubiquiti PoE-инжекторы** — `ui.com/store/poe-injectors` или
  `ubnt.com`. POE-15-12W → 12W out, POE-24-30W → 30W, POE-48-60W → 60W.
  Это **валидные PSU** с малой выходной мощностью; проверь страницу.
* **Cisco PWR-…** — `cisco.com/c/en/us/products/...` или datasheet.
  Например, PWR-C5-1KWAC = 1000W.
* **Российские OEM-сборщики** (Formula PC, Accord PC, KingPrice) —
  `formula-pc.ru`, `accord-pc.ru`, `kingprice.ru`.

## Нюансы и валидация power_watts

* Тип `int`, диапазон валидатора 5..3000, но **на практике все валидные
  системные PSU** — от 100W (mini-PSU) до 2000W (HEDT/серверные).
  Значение < 100W почти всегда означает адаптер/PoE-инжектор —
  возвращай `null` с пояснением, кроме явных PoE-инжекторов от
  Ubiquiti/Cisco.
* Серверные БП 750/1100/1600/2000W — нормально, не выдавай null без
  необходимости.
* Если в `raw_name` уже есть число (`650PPH-LT-OEM`, `KCAS-700`,
  `XP800`, `PROBE600B`) — **всё равно открой страницу продукта и
  подтверди**, прежде чем писать значение и URL. Не писать значение
  только из имени.
* Если на оф. сайте мощность не указана — `null` с пояснением «не
  опубликовано вендором». Не оценивать «по аналогии» с другой моделью
  серии.

## Honest-null

Если на доменах из whitelist карточка по `mpn`/`gtin`/`manufacturer +
model` **не найдена** — `null` с пояснением («Карта не найдена на оф.
сайте бренда»). **Ничего не выдумывать.**

## Формат ответа

Стандартный из `_общие_правила.md`. Каждое непустое значение
сопровождается `source_url` (HTTPS, домен из whitelist выше). В корне
ответа — массив `sources_used` со списком всех URL, действительно
использованных для заполнения батча.

```json
{
  "category":     "psu",
  "batch_id":     "<тот же>",
  "filled_at":    "2026-05-05T13:45:00Z",
  "sources_used": [
    "https://exegate.ru/products/power_supplies/pph/ex282046rus/",
    "https://www.formulav-line.com/product/kcas-700-plus/"
  ],
  "items": [
    {
      "id": <тот же int>,
      "fields": {
        "power_watts": {
          "value": 650,
          "source_url": "https://exegate.ru/products/power_supplies/pph/ex282046rus/"
        }
      }
    }
  ]
}
```

## Куда сохранить

`enrichment/done/psu/<имя_входного_файла>`.

## Примеры (input → output)

### Пример 1. ExeGate PPH с мощностью в mpn (типичный кейс)

**Input:**
```json
{
  "id": 852,
  "manufacturer": "ExeGate",
  "model":        "ExeGate EX282046RUS-OEM 80 PLUS® 650PPH-LT-OEM (ATX, APFC, КПД 82%, 12cm fan, 24pin, (4+4)pin, PCIe, 5xSATA, 3xIDE, black, RTL)",
  "mpn":          "EX282046RUS-OEM",
  "gtin":         "",
  "raw_names": [
    "ExeGate EX282046RUS-OEM 80 PLUS® 650PPH-LT-OEM (ATX, APFC, КПД 82% (80 PLUS), 12cm fan, 24pin, (4+4)pin, PCIe, 5xSATA, 3xIDE, black, RTL)"
  ],
  "current":  {},
  "to_fill":  ["power_watts"]
}
```

**Output:**
```json
{
  "id": 852,
  "fields": {
    "power_watts": {
      "value": 650,
      "source_url": "https://exegate.ru/products/power_supplies/pph/ex282046rus/"
    }
  }
}
```

### Пример 2. Ginzzu (защитный слой 3)

**Input:**
```json
{
  "id": 1261,
  "manufacturer": "Ginzzu",
  "model":        "Ginzzu SA400 OEM",
  "mpn":          "SA400",
  "raw_names":    ["Ginzzu SA400 OEM"],
  "current":  {},
  "to_fill":  ["power_watts"]
}
```

**Output:**
```json
{
  "id": 1261,
  "fields": {
    "power_watts": {
      "value": null,
      "source_url": null,
      "reason": "Ginzzu PSU: оф. сайт ginzzu.com офлайн, datasheet недоступен"
    }
  }
}
```

> Ginzzu обрабатываем без WebSearch/WebFetch — сэкономь тулколлы.

### Пример 3. Gembird NPA-AC* в категории «unknown» (защитный слой 1+4)

**Input:**
```json
{
  "id": 639,
  "manufacturer": "unknown",
  "model":        "Gembird Блок питания универсальный 3В-12В, 12Вт, 8 штекеров (NPA-AC4)",
  "mpn":          "NPA-AC4",
  "raw_names":    ["Gembird Блок питания универсальный 3В-12В, 12Вт, 8 штекеров (NPA-AC4)"],
  "current":  {},
  "to_fill":  ["power_watts"]
}
```

**Output:**
```json
{
  "id": 639,
  "fields": {
    "power_watts": {
      "value": null,
      "source_url": null,
      "reason": "PSU-адаптер/зарядное (Gembird NPA-AC*), не системный блок питания"
    }
  }
}
```

> Здесь сработали два защитных слоя: бренд `Gembird` извлечён из
> `raw_name` (manufacturer был "unknown"), и серия `NPA-AC*` —
> адаптер из защитного слоя 1.

## Чек-лист самопроверки перед сохранением

- [ ] Каждое непустое `value` имеет `source_url` с https:// и доменом
      из whitelist выше.
- [ ] `power_watts` — целое число (не строка «650W», не Decimal).
- [ ] Если `value < 100` и это **не** PoE-инжектор Ubiquiti/Cisco —
      вернуть `null` с пояснением (адаптер).
- [ ] Если бренд = Ginzzu — вернуть `null` без обращения к WebSearch.
- [ ] Если в `raw_name` слово «адаптер» / `Gembird NPA-AC*` /
      `KS-is` / `BURO BUM-*` / `ORIENT PU-C*` / `Ubiquiti POE-` —
      вернуть `null` с reason.
- [ ] Если manufacturer = `unknown`, сначала попробовать извлечь бренд
      из `raw_name`; если нет — `null` с reason «бренд не определён».
- [ ] Ни одно поле из `current` не задублировано в `fields`.
- [ ] В корне ответа заполнен массив `sources_used`.
