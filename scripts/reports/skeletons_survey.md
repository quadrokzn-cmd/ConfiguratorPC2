# Разведка по скелетам Merlion/Treolan

**Дата:** 2026-04-24  
**Задача микроэтапа:** regex-обогащение ~2076 компонентов, созданных
при загрузке прайсов Merlion (supplier_id=5) и Treolan (supplier_id=6).

## 1. ID поставщиков

| id | name    |
|----|---------|
| 4  | OCS     |
| 5  | Merlion |
| 6  | Treolan |

## 2. Ключевые поля по категориям

Определение «скелета» — компонент, у которого хотя бы одно из ниже
перечисленных полей = NULL.

| Категория     | Ключевые поля                                                                                                         |
|---------------|-----------------------------------------------------------------------------------------------------------------------|
| cpu           | socket, cores, tdp_watts, memory_type                                                                                 |
| motherboard   | socket, chipset, form_factor, memory_type, has_m2_slot                                                                |
| ram           | memory_type, module_size_gb, frequency_mhz, form_factor                                                               |
| gpu           | vram_gb, vram_type, tdp_watts, needs_extra_power, video_outputs, core_clock_mhz, memory_clock_mhz                     |
| storage       | storage_type, form_factor, interface, capacity_gb                                                                     |
| case          | supported_form_factors, has_psu_included, included_psu_watts                                                          |
| psu           | power_watts                                                                                                           |
| cooler        | supported_sockets, max_tdp_watts                                                                                      |

## 3. Распределение скелетов по категориям

| cat         | total | skeletons | skel_from_merlion_treolan |
|-------------|-------|-----------|---------------------------|
| case        |   896 |       839 |                       478 |
| cooler      |   716 |       561 |                       473 |
| cpu         |   159 |         0 |                         0 |
| gpu         |   495 |       255 |                       204 |
| motherboard |   658 |         2 |                         0 |
| psu         |   635 |       307 |                       303 |
| ram         |   744 |         0 |                         0 |
| storage     |   813 |       243 |                       233 |
| **Итого**   |  5116 |      2207 |                      1691 |

Гипотеза брифинга подтверждена: основная масса скелетов — Merlion/Treolan.

## 4. Dry-run существующих regex (покрытие по полям)

| cat         | обработано | >=1 поле | field              | covered | pct    |
|-------------|------------|----------|--------------------|---------|--------|
| cpu         | 0          | 0        | —                  | —       | —      |
| psu         | 307        | 302      | power_watts        | 302/307 | 98.4%  |
| ram         | 0          | 0        | —                  | —       | —      |
| storage     | 243        | 232      | storage_type       | 232/243 | 95.5%  |
|             |            |          | form_factor        | 217/243 | 89.3%  |
|             |            |          | interface          | 232/243 | 95.5%  |
|             |            |          | capacity_gb        | 229/243 | 94.2%  |
| cooler      | 561        | 389      | supported_sockets  | 389/561 | 69.3%  |
|             |            |          | max_tdp_watts      | 261/561 | 46.5%  |
| gpu         | 255        | 175      | vram_gb            | 131/255 | 51.4%  |
|             |            |          | vram_type          | 142/255 | 55.7%  |
|             |            |          | остальные 5 полей  | 0       | 0.0%   |
| motherboard | 2          | 0        | —                  | —       | —      |
| case        | 839        | 379      | supported_ff       | 377/839 | 44.9%  |
|             |            |          | has_psu_included   | 232/839 | 27.7%  |
|             |            |          | included_psu_watts | 14/839  | 1.7%   |

**Итог:** высокое покрытие (≥60%) — у psu, storage, cooler.supported_sockets.  
Требуют доработки паттернов: cooler.max_tdp_watts, gpu.*, case.*.

## 5. Примеры скелетов (15–20 на категорию)

### PSU
```
Formula V FV-400LT, 400W, APFC, 12cm Fan
Bloody ATX 500W BD-PS500W 80 PLUS WHITE
Seasonic ATX 1200W Vertex GX-1200 80+ gold
PCCooler KF550, 550W, APFC, 80+
Deepcool GAMERSTORM PN850M (ATX 3.1, 850W, 80+ GOLD, Gen5 PCIe)
```
Покрытие 98.4% — шаблон универсальный.

### STORAGE
```
Toshiba SATA-III 10TB MG10ADA10TE (7200rpm) 512Mb 3.5"
WD Green SSD 2,5" SATA 1Tb, WDS100T5G0A
PC Pet PCIe 4.0 x4 4TB PCPS004T4 M.2 2280
Samsung 9100 PRO SSD M.2 NVMe 2.0 Gen 5.0 x4 8Tb
WD SATA-III 22TB 0F48155 Ultrastar DC HC570 3.5"
Netac N600S 128GB 2.5 SATAIII 3D NAND
GS Nanotech SSD GS027 512Гб PCIe 3 x4, M.2 2280   <-- «Гб» русское
```
Пробелы: «Гб/Тб» не ловится, форм-фактор `2,5"` (с запятой) не ловится.

### COOLER
```
Deepcool LM420 ARGB Soc-AM5/AM4/1200/1700/1851 4-pin 25.2dB Al LCD 2176gr Ret
Lian-Li Galahad II LCD 360 ARGB Soc-AM5/AM4/... 30dB Al+Cu LCD Ret
Thermalright Peerless Assassin 120 Black (4-pin PWM, 157mm, ...) — S: 1700, 1200, 20XX, 115X, AM5, AM4
ID-COOLING SE-214-XT V2 BLACK LGA1851/1700/1200/115X/AM5/AM4 (TDP 200W, PWM)
Система водяного охлаждения Deepcool LE240 Pro ARGB 280W
```
Пробелы: Merlion-формат AIO (Deepcool/Lian-Li/Thermalright) без указания TDP
Ватт — нужен derived по размеру радиатора (240/280/360/420 мм).

### GPU
```
MSI RTX 5060 Ti 16G INSPIRE 2X OC//RTX 5060Ti, HDMI, DP*3, 16G , D7
ASUS PRIME-RTX5060TI-O16G//RTX5060TI HDMI DP*3 16G D7
Gigabyte PCI-E 2.0 GV-N710D3-2GL NVIDIA GT 710 2Gb 64bit DDR3
Afox AF1050TI-4096D5H7-V9 NVIDIA GTX 1050TI 4Gb 128bit GDDR5
MAXSUN MS-GTX1650 TR 4GD6//GTX1650 HDMI, 4G, D6
Afox AF210-512D3L3-V2 NVIDIA G210 512Mb 64bit DDR3   <-- 512Mb
INNO3D RTX 5060 Twin X2//RTX5060, HDMI, DP*3, 8G,D7
```
Пробелы: сокращение `D6`/`D7` для типа VRAM, `4Gb` (маленькая b) для объёма,
редкий случай `512Mb`.

### CASE
```
Bloody CC-121 белый без БП mATX 7x120mm 1xUSB2.0 1xUSB3.0 audio     <-- без БП работает
InWin ENR022 Black 500W PM-500ATX U3.0*2+A(HD) mATX                 <-- с БП работает
Formula V Line CS-110-S mATX USB3.0x1/USB2.0x1/audio (ex Aerocool)  <-- БП не упомянут
Ocypus Gamma C50 BK, MATX, USB3.0*1+USB2.0*2                        <-- БП не упомянут
Zalman T8, ATX, BLACK, 1x5.25", 2x3.5", 2x2.5", 2xUSB2.0, REAR 1x120mm
```
Пробелы: современные корпусы DIY почти всегда идут «без БП», но явного
маркера в имени может не быть.

## 6. Выводы для микроэтапа

1. cpu/ram/motherboard пропускаем — скелетов нет.
2. psu/storage — текущее покрытие хорошее, добавить только точечные мелкие паттерны (Гб, 2,5").
3. cooler — доработать max_tdp_watts: derived по размеру радиатора AIO.
4. gpu — доработать регекс vram_gb/vram_type (D6/D7, Gb с малой «b»).
5. case — derived has_psu_included=False при известном form_factor и
   отсутствии признаков БП (с невысокой confidence).
6. Поля gpu.tdp_watts / needs_extra_power / video_outputs / core_clock_mhz /
   memory_clock_mhz — системно отсутствуют в именах прайса, оставляем для
   этапа 2.5Б (AI).
