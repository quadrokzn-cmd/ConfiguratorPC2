# IMAP-канал автозагрузки прайса OCS (этап 12.1).
#
# Письма от egarifullina@ocs.ru (домен ocs.ru) с Subject «B2B OCS …»
# и единственным XLSX-вложением (~15 МБ). MIME у вложения —
# application/octet-stream (а не application/vnd.openxmlformats-…),
# поэтому фильтруем по расширению .xlsx, а не по MIME — это и
# подтвердила разведка scripts/_diag_imap_inbox.py.

from __future__ import annotations

import logging
import os
import tempfile

from portal.services.configurator.auto_price.base import register_fetcher
from portal.services.configurator.auto_price.fetchers.base_imap import BaseImapFetcher
from portal.services.configurator.price_loaders.models import PriceRow
from portal.services.configurator.price_loaders.ocs import OcsLoader


logger = logging.getLogger(__name__)


@register_fetcher
class OCSImapFetcher(BaseImapFetcher):
    supplier_slug = "ocs"
    supplier_display_name = "OCS"  # совпадает с suppliers.name

    # Любой адрес домена ocs.ru — на случай смены менеджера.
    # (?![\w.]) защищает от поддомена-подделки вида «test@ocs.ru.fake».
    sender_pattern = r"@ocs\.ru(?![\w.])"
    # Прайс-лист OCS приходит с Subject вида
    #   «B2B OCS - Состояние склада и цены DD.MM.YYYY»
    # — фиксируем по началу строки, остальное вариативно.
    subject_pattern = r"^\s*B2B\s+OCS\s*-\s*Состояние\s+склада\s+и\s+цены"
    attachment_extensions = (".xlsx", ".xls")

    def parse_attachment(self, data: bytes, filename: str) -> list[PriceRow]:
        # OcsLoader.iter_rows читает путь к файлу через openpyxl
        # (load_workbook(filepath)), поэтому записываем bytes во временный
        # файл и удаляем его в finally. tempfile в /tmp на linux и в
        # %TEMP% на Windows — ОС сама ротирует.
        suffix = ".xlsx" if filename.lower().endswith(".xlsx") else ".xls"
        # delete=False, чтобы можно было закрыть handle и openpyxl мог
        # переоткрыть файл сам (на Windows это обязательно).
        fd, path = tempfile.mkstemp(prefix="auto_ocs_imap_", suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            loader = OcsLoader()
            rows = list(loader.iter_rows(path))
            logger.info(
                "OCS IMAP: распарсено %d PriceRow из вложения «%s» (%d байт)",
                len(rows), filename, len(data),
            )
            return rows
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
