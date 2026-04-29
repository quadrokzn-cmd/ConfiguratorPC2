# Системные фильтры классификации компонентов (этап 9Г.1).
#
# Используются загрузчиками прайсов, чтобы при создании скелета компонента
# заранее пометить «спорные» позиции is_hidden=True. Раньше такие позиции
# чистились разовыми скриптами (scripts/hide_case_fans.py,
# scripts/hide_external_netac_ssd.py), но при следующей загрузке свежего
# прайса они опять появлялись как видимые.
#
# Если правило ложит компоненты, которые быть скрытыми НЕ должны —
# расширять исключения в этом модуле, а не плодить новые ad-hoc-скрипты.
# См. docs/enrichment_techdebt.md, секции 9 и 2.

from __future__ import annotations

import re


# Признаки «корпусного» вентилятора в названии: явные слова про корпус
# и chassis-варианты, плюс типовые модельные шаблоны (AF120/SP140/PWM 120).
_CASE_FAN_KEYWORDS = re.compile(
    r"(корпусн[ыоая]|case[\s\-]?fan|chassis[\s\-]?fan|"
    r"вентилятор\s+для\s+корпуса)",
    flags=re.IGNORECASE,
)

# Модельные паттерны корпусных вентиляторов на 120/140 мм без радиатора.
_CASE_FAN_MODELS = re.compile(
    r"\b(?:AF|SP|PWM|ARGB|RGB)[\-\s]*1[24]0\b",
    flags=re.IGNORECASE,
)

# Просто слово «вентилятор» / «fan» — слабый сигнал (отсекается, если есть
# CPU-маркеры).
_GENERIC_FAN = re.compile(r"вентилятор|\bfan\b", flags=re.IGNORECASE)

# Маркеры CPU-кулера: если они есть в имени, позицию НЕ помечаем как
# корпусную, даже если в имени есть слово «вентилятор».
_CPU_COOLER_HINTS = re.compile(
    r"(процессор|cpu[\s\-]?cooler|башенн|tower|радиатор|heat[\s\-]?sink|"
    r"liquid|aio|жидкост|охлад\.\s*проц|water\s*cool)",
    flags=re.IGNORECASE,
)


def is_likely_case_fan(
    name: str | None,
    manufacturer: str | None = None,
    category_hint: str | None = None,
) -> bool:
    """Эвристика: похожа ли позиция на корпусный вентилятор.

    name           — наименование компонента из прайса.
    manufacturer   — бренд (используется как доп.строка для regex).
    category_hint  — наша категория, если уже известна (например, 'cooler').
                     Сейчас не влияет на результат, оставлен для расширения.

    Возвращает True, если name + manufacturer содержат явные признаки
    корпусного вентилятора и при этом нет маркеров CPU-кулера.
    Защитное поведение: при пустых/None входах возвращает False —
    скрытие должно требовать положительной находки, а не отсутствия данных.
    """
    if not name:
        return False

    full = name
    if manufacturer:
        full = f"{full} {manufacturer}"

    # Любой явный CPU-маркер блокирует пометку: даже у Noctua / Arctic
    # бывают вентиляторы 120/140 мм, которые поставляются как часть
    # CPU-кулера и не должны исчезать из конфигуратора.
    if _CPU_COOLER_HINTS.search(full):
        return False

    if _CASE_FAN_KEYWORDS.search(full):
        return True
    if _CASE_FAN_MODELS.search(full):
        return True
    if _GENERIC_FAN.search(full):
        return True

    return False


def is_likely_external_storage(
    name: str | None,
    manufacturer: str | None = None,
) -> bool:
    """Заглушка под будущий фильтр внешних накопителей (USB-C SSD и т.п.).

    В этапе 9Г.1 закрыта только разовая чистка 4 Netac USB-C SSD скриптом
    scripts/hide_external_netac_ssd.py — этого хватает: схема storages
    для внешних накопителей и так не применима. Если внешних SSD будет
    появляться больше при следующих загрузках прайсов, реализовать здесь
    проверку (USB-C / external / portable) и подключить в orchestrator
    тем же способом, что и is_likely_case_fan для коулеров.
    """
    return False
