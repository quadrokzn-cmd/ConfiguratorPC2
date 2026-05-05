# Базовый интерфейс автозагрузчика прайса (этап 12.3).
#
# Каждый канал автозагрузки (REST API, IMAP, URL) — это наследник
# BaseAutoFetcher, который реализует fetch_and_save() и регистрируется
# декоратором @register_fetcher. Имя поставщика (supplier_slug) совпадает
# с ключом в auto_price_loads.supplier_slug и в LOADERS из
# app/services/price_loaders/__init__.py.
#
# fetch_and_save() возвращает price_upload_id (id записи в price_uploads),
# чтобы runner мог положить его в auto_price_loads.last_price_upload_id и
# в auto_price_load_runs.price_upload_id. На любой ошибке — бросает
# исключение, runner его поймает и запишет в last_error_message.

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseAutoFetcher(ABC):
    # Slug поставщика. Должен совпадать с auto_price_loads.supplier_slug
    # и с ключом из LOADERS (price_loaders/__init__.py).
    supplier_slug: str = ""

    @abstractmethod
    def fetch_and_save(self) -> int:
        """Полный цикл: достать данные → распарсить → записать в БД.

        Возвращает:
            int — id новой записи в price_uploads.

        Бросает:
            Любое исключение — сетевые ошибки, неверные креды, неверная
            схема ответа. runner.run_auto_load перехватит и сохранит
            краткое описание в auto_price_loads.last_error_message.
        """


# ---- Регистр fetcher'ов ----------------------------------------------
#
# {slug: класс}. Заполняется декоратором @register_fetcher на этапе
# импорта fetchers/*. runner.run_auto_load(slug) ищет класс здесь.
# В подэтапах 12.1 / 12.2 / 12.4 сюда же приедут новые поставщики.

_REGISTRY: dict[str, type[BaseAutoFetcher]] = {}


def register_fetcher(cls: type[BaseAutoFetcher]) -> type[BaseAutoFetcher]:
    """Декоратор: регистрирует fetcher по supplier_slug.

    Падает на этапе импорта, если slug пустой или дублируется — это
    проще ловить, чем silent override уже зарегистрированного класса.
    """
    slug = (cls.supplier_slug or "").strip()
    if not slug:
        raise RuntimeError(
            f"@register_fetcher: у класса {cls.__name__} пустой supplier_slug."
        )
    if slug in _REGISTRY and _REGISTRY[slug] is not cls:
        raise RuntimeError(
            f"@register_fetcher: slug «{slug}» уже зарегистрирован за "
            f"{_REGISTRY[slug].__name__}, нельзя перезаписать через {cls.__name__}."
        )
    _REGISTRY[slug] = cls
    return cls


def get_fetcher_class(slug: str) -> type[BaseAutoFetcher] | None:
    """Возвращает класс fetcher'а по slug либо None, если такого нет.

    Используется и runner'ом (run_auto_load), и UI (toggle: блокирует
    enabled=TRUE для slug без зарегистрированного fetcher'а)."""
    return _REGISTRY.get((slug or "").strip())


def list_registered_slugs() -> list[str]:
    """Список slug'ов, для которых есть fetcher. Нужен для UI и тестов."""
    return sorted(_REGISTRY.keys())
