# Обогащение характеристик ВИДЕОКАРТ через Claude Code

Сначала прочти `_общие_правила.md` — там общие требования к источникам и
формату ответа.

## Целевые поля и тип
| Поле | Тип | Допустимый диапазон |
|---|---|---|
| `tdp_watts` | int | 10..600 |
| `needs_extra_power` | bool | true / false |
| `video_outputs` | str | строка вида `"HDMI 2.1 x1, DisplayPort 1.4a x3"` |
| `core_clock_mhz` | int | 100..4000 |
| `memory_clock_mhz` | int | 500..40000 (эффективная) |
| `vram_gb` | int | 1..128 |
| `vram_type` | enum | `GDDR5`, `GDDR5X`, `GDDR6`, `GDDR6X`, `GDDR7`, `HBM2`, `HBM3`, `DDR3`/`DDR4`/`DDR5` |

## Где искать на оф. сайтах
- **NVIDIA-референсы (RTX/GTX без бренда)** — `nvidia.com/.../graphics-cards/.../`,
  раздел "Specifications". TDP = «Total Graphics Power» (TGP).
- **AMD-референсы (Radeon RX без бренда)** — `amd.com/.../graphics/...`.
  TDP = «Total Board Power» (TBP).
- **Партнёрские (ASUS/MSI/Gigabyte/Palit/Zotac/PNY/Biostar/AFOX)**:
  ищи по SKU на сайте бренда. Структура URL обычно:
  - `gigabyte.com/Graphics-Card/<SKU>` → раздел Specification.
  - `msi.com/Graphics-Card/<series>/Specification`.
  - `asus.com/.../graphics-cards/.../<series>/spec/`.
  - `palit.com/palit/vgapro.php?id=...` → Specification.
- **Boost/Game Clock** записывай в `core_clock_mhz` (МГц). Если есть и Base,
  и Boost — бери **Boost (Game)**, как более информативный.
- **Memory Speed**: на сайте обычно указано в Gbps (например, 17 Gbps) или
  в МГц эффективной частоты. В нашу схему пишется **в МГц эффективной**
  (17 Gbps = 17000 МГц). Для GDDR6X: 21 Gbps = 21000 МГц.
- **video_outputs**: формат `"HDMI 2.1 x1, DisplayPort 1.4a x3"`. Точное
  количество и версии портов из spec-листа.
- **needs_extra_power**: `true`, если на карте есть разъём
  6-pin/8-pin/12VHPWR. `false` — для карт с питанием только из PCIe-слота
  (обычно low-profile, GT 1030, RX 6400 и т. п.).

## Особенности по производителям
- **Matrox**: спецификации лежат на `matrox.com/en/video/products/graphics-cards/`.
- **AFOX**: используем `afox.eu` (англ.) или `afox.ru`. Часто данные есть
  только на странице конкретной модели; если нет — `null`.
- **Biostar**: `biostar.com.tw/app/en/vga/` — у бюджетных GPU часто нет
  core_clock_mhz, и это норма.
- **Серверные/проф. карты NVIDIA Quadro / RTX A** — раздел
  `nvidia.com/en-us/design-visualization/.../`.

## Правила формирования ответа
- Возвращай только поля из `to_fill`. Поля, лежащие в `current`, не трогай.
- Если карта на сайте не найдена ни по SKU, ни по модели — все нужные поля
  верни как `{"value": null, "source_url": null, "reason": "Карта не найдена на оф. сайте бренда"}`.
- Если найдена страница, но конкретного поля на ней нет — `null` с пояснением.

## Куда сохранить
`enrichment/done/gpu/batch_NNN.json` (тот же `batch_id`, что во входе).
