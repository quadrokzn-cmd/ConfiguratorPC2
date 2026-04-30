# Обогащение характеристик ВИДЕОКАРТ через Claude Code

> Перед началом обязательно прочти `_общие_правила.md` — там общие
> требования к источникам, формат ответа и описание полей входного JSON
> (`mpn`, `gtin`, `raw_names`, `current`, `to_fill`).

## Целевые поля и их валидация
| Поле | Тип | Допустимый диапазон / значения |
|---|---|---|
| `tdp_watts` | int | 10..600 |
| `needs_extra_power` | bool | `true` / `false` |
| `video_outputs` | str | строка вида `"1xHDMI 2.1, 3xDisplayPort 1.4a"` |
| `core_clock_mhz` | int | 100..4000 (берём Boost/Game Clock) |
| `memory_clock_mhz` | int | 500..40000 (эффективная частота, Gbps × 1000) |
| `vram_gb` | int | 1..128 |
| `vram_type` | enum | `GDDR5`, `GDDR5X`, `GDDR6`, `GDDR6X`, `GDDR7`, `HBM2`, `HBM2E`, `HBM3`, `DDR3..DDR5` |

> Поля `vram_gb` и `vram_type` чаще всего уже заполнены regex'ом и
> попадут в `current`, а не в `to_fill`. Не трогай поля из `current`.

## ⚠ Различие референсного чипа и AIB-карты

NVIDIA / AMD выпускают **референсные** характеристики (Founders Edition,
ref TBP, базовые частоты), а **AIB-партнёры** (ASUS / MSI / Gigabyte /
Palit / Zotac / PNY / Sapphire / Inno3D / Maxsun / AFOX) делают свои
исполнения с разогнанными частотами и **другим TDP**.

Поэтому:
1. **Если в `manufacturer` указан AIB-вендор** (ASUS / MSI / Gigabyte /
   Palit / Zotac / PNY / Sapphire / Inno3D / Maxsun / AFOX и т. п.) —
   ищи именно эту конкретную модель на сайте AIB-вендора, а не на
   nvidia.com / amd.com. Частоты и TDP могут отличаться от референса.
2. **Если `manufacturer` пуст или совпадает с чипмейкером** (NVIDIA,
   AMD) — берём референсные данные с nvidia.com/amd.com.
3. Boost/Game Clock у AIB обычно выше, чем у референса (часто
   маркируется "OC" в `model`/`raw_names`).

## Где искать спецификации
- **NVIDIA-референсы (RTX/GTX без бренда)** — `nvidia.com/.../graphics-cards/.../`,
  раздел "Specifications". TDP = «Total Graphics Power» (TGP).
- **AMD-референсы (Radeon RX без бренда)** — `amd.com/.../graphics/...`.
  TDP = «Total Board Power» (TBP).
- **Intel Arc** — `intel.com/.../arc-graphics/...`.
- **ASUS** — `asus.com/.../graphics-cards/.../<series>/spec/`.
- **MSI** — `msi.com/Graphics-Card/<series>/Specification`.
- **Gigabyte / AORUS** — `gigabyte.com/Graphics-Card/<SKU>` → Specification.
- **Palit** — `palit.com/palit/vgapro.php?id=...` → Specification.
- **Zotac** — `zotac.com/.../graphics_card/...`.
- **PNY** — `pny.com/professional/.../<sku>` (квадры) или
  `pny.com/.../geforce-rtx-...`.
- **Biostar** — `biostar.com.tw/app/en/vga/`. Бюджетные карты часто без
  core_clock_mhz — это норма.
- **Matrox** — `matrox.com/en/video/products/graphics-cards/`.
- **AFOX** — `afox.eu` / `afox.ru` / `afox-corp.com` (мини-каталог).
- **Sapphire** — `sapphiretech.com/.../products/...` (AMD AIB).
- **Inno3D** — `inno3d.com/products/...` (NVIDIA AIB).
- **Maxsun** — `maxsun.com` или `maxsun.com.cn` (документация для
  китайского рынка часто только на cn-домене).
- **NVIDIA Quadro / RTX A** — `nvidia.com/en-us/design-visualization/.../`.

## Как читать spec-лист
- **Boost/Game Clock**: записывай в `core_clock_mhz` (МГц). Если есть и
  Base, и Boost — бери **Boost (Game)**, как более информативный.
- **Memory Speed**: на сайте обычно указано в Gbps (например, 17 Gbps)
  или в МГц эффективной частоты. В нашу схему пишется
  **в МГц эффективной**: 17 Gbps = 17000 МГц, 21 Gbps = 21000 МГц.
- **video_outputs**: формат
  `"1xHDMI 2.1, 3xDisplayPort 1.4a"` (число × тип, разделитель — запятая).
- **needs_extra_power**:
  - `true`, если на карте есть разъём питания (6-pin, 8-pin, 12VHPWR,
    12V-2x6 и любые их сочетания);
  - `false` — для карт с питанием только из PCIe-слота
    (обычно low-profile: GT 1030, RX 6400, Quadro T400 и т. п.).

## Правила формирования ответа
- Возвращай ТОЛЬКО поля из `to_fill`. Поля, лежащие в `current`, не
  трогай — они уже зафиксированы в БД.
- Если карта на сайте не найдена ни по `mpn`, ни по `gtin`, ни по
  `manufacturer + model` — все нужные поля верни как
  `{"value": null, "source_url": null, "reason": "Карта не найдена на оф. сайте бренда"}`.
- Если найдена страница, но конкретного поля на ней нет —
  `{"value": null, "source_url": null, "reason": "Поле отсутствует в spec"}`.
- Никогда не возвращай значение без `source_url`; никогда не указывай
  не-официальный домен — валидатор отклонит и значение, и потратит
  лишний цикл.

## Куда сохранить
`enrichment/done/gpu/<имя_входного_файла>` (тот же `batch_id`, что во
входе; имя файла — то же, что у входа, чтобы автоматический импорт
смог его подцепить).
