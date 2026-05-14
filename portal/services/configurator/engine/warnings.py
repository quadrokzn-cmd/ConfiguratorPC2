# Генерация предупреждений к собранной конфигурации.
#
# Warning-и появляются, когда сборка формально валидна, но какое-то её
# свойство требует проверки менеджером (например, нет данных о длине GPU
# или количестве слотов памяти на плате). Каждое предупреждение — готовая
# строка на русском, которую CLI/UI отдаст пользователю.

from __future__ import annotations


W_MB_SLOTS_UNKNOWN = (
    "Количество слотов памяти на плате не подтверждено, "
    "требуется проверка менеджером"
)
W_GPU_CASE_LENGTH_UNKNOWN = (
    "Совместимость GPU и корпуса по длине не подтверждена, "
    "требуется проверка менеджером"
)
W_PSU_POWER = (
    "Требуется проверка достаточности мощности БП менеджером"
)


def psu_watts_shortage(requested: int, actual: int) -> str:
    """Warning, когда подобранный БП слабее запрошенной пользователем мощности.

    Используется, когда в запросе явно указано «БП 550W», а в наличии нет
    блоков ≥550W и selector подобрал ближайший доступный (например, 450W).
    Сборка при этом не отменяется — выдаётся как warning, чтобы менеджер
    мог принять решение (заказать БП мощнее или согласовать с клиентом).
    """
    return f"Подобран БП {actual}W при запрошенных {requested}W — недостаточно мощности"
W_GPU_EXTRA_POWER = (
    "Требуется проверка наличия необходимых разъёмов питания в БП"
)
W_TRANSIT_INCLUDED = (
    "Сборка включает позиции в транзите, ожидаемая поставка до 7 дней"
)


def collect_warnings(
    *,
    cpu: dict,
    motherboard: dict,
    gpu: dict | None,
    case: dict,
    used_transit: bool,
    ram_modules_count: int = 1,
    extra_warnings: list[str] | None = None,
) -> list[str]:
    """Собирает список предупреждений к сборке.

    ram_modules_count — количество модулей памяти в итоговой сборке.
    При modules_count == 1 предупреждение про количество слотов подавляется:
    один слот на любой плате гарантированно есть, проверять нечего.

    extra_warnings — предупреждения, пришедшие из compatibility.check_build
    (например, «длина GPU не подтверждена»). Они объединяются с теми, что
    вычисляются здесь, с устранением дубликатов при сохранении порядка.
    """
    out: list[str] = []

    # 1) memory_slots у MB не заполнен → считаем 4 слота, предупреждаем.
    # Но только если планок больше одной: для одной планки слот точно есть.
    if motherboard.get("memory_slots") is None and ram_modules_count > 1:
        out.append(W_MB_SLOTS_UNKNOWN)

    # 2) Есть дискретная GPU — проверяем связанные предупреждения
    if gpu is not None:
        # длина GPU/корпуса — если хоть одно из полей NULL
        if gpu.get("length_mm") is None or case.get("max_gpu_length_mm") is None:
            out.append(W_GPU_CASE_LENGTH_UNKNOWN)

        # общее предупреждение про БП
        out.append(W_PSU_POWER)

        # дополнительные разъёмы питания GPU
        if gpu.get("needs_extra_power") is True:
            out.append(W_GPU_EXTRA_POWER)

    # 3) Сборка из транзита
    if used_transit:
        out.append(W_TRANSIT_INCLUDED)

    # 4) Добавляем предупреждения от правил совместимости
    if extra_warnings:
        for w in extra_warnings:
            if w and w not in out:
                out.append(w)

    # Убираем дубликаты с сохранением порядка
    seen: set[str] = set()
    result: list[str] = []
    for w in out:
        if w not in seen:
            seen.add(w)
            result.append(w)
    return result
