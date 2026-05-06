# IMAP-канал автозагрузки прайса Merlion (этап 12.1).
#
# Письма от matveeva.y@merlion.ru (приходят через Gmail-forward — реальный
# отправитель сохраняется в From / Reply-To / X-Forwarded-For), Subject
# «Прайс-лист MERLION», вложение — ZIP ~5 МБ, внутри XLSX.
#
# Поток:
#   1. Записываем bytes во временный .zip;
#   2. Распаковываем в /tmp/merlion_<uuid>/ (на Windows — %TEMP%/...);
#   3. Рекурсивно ищем все .xlsx в распакованном содержимом;
#   4. Берём самый большой по размеру файл (на случай если в архиве
#      окажется несколько листов или сопровождающие документы);
#   5. Прогоняем через MerlionLoader — тот же, что и /admin/price-uploads;
#   6. Чистим временные файлы (try/finally).

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import zipfile

from app.services.auto_price.base import register_fetcher
from app.services.auto_price.fetchers.base_imap import BaseImapFetcher
from app.services.price_loaders.merlion import MerlionLoader
from app.services.price_loaders.models import PriceRow


logger = logging.getLogger(__name__)


@register_fetcher
class MerlionImapFetcher(BaseImapFetcher):
    supplier_slug = "merlion"
    supplier_display_name = "Merlion"  # совпадает с suppliers.name

    # Сохраняется через Gmail-forward (см. разведку); регекс ловит и
    # текущего менеджера matveeva.y@, и любого другого с домена.
    # (?![\w.]) защищает от поддомена-подделки вида «scam@merlion.ru.fake».
    sender_pattern = r"@merlion\.ru(?![\w.])"
    subject_pattern = r"^\s*Прайс-лист\s+MERLION"
    attachment_extensions = (".zip",)
    max_attachment_size_mb = 50

    def parse_attachment(self, data: bytes, filename: str) -> list[PriceRow]:
        tmp_root = tempfile.mkdtemp(prefix="auto_merlion_imap_")
        try:
            zip_path = os.path.join(tmp_root, filename or "merlion.zip")
            with open(zip_path, "wb") as f:
                f.write(data)

            extract_dir = os.path.join(tmp_root, "unpacked")
            os.makedirs(extract_dir, exist_ok=True)
            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extractall(extract_dir)
            except zipfile.BadZipFile as exc:
                raise RuntimeError(
                    f"Merlion IMAP: вложение «{filename}» не распознано "
                    f"как ZIP-архив: {exc}"
                ) from exc

            xlsx_paths: list[tuple[int, str]] = []  # (size, path)
            for root, _dirs, files in os.walk(extract_dir):
                for name in files:
                    if name.lower().endswith(".xlsx"):
                        full = os.path.join(root, name)
                        try:
                            size = os.path.getsize(full)
                        except OSError:
                            size = 0
                        xlsx_paths.append((size, full))

            if not xlsx_paths:
                raise RuntimeError(
                    f"Merlion IMAP: ZIP «{filename}» не содержит ни одного "
                    "файла .xlsx — формат рассылки изменился."
                )

            # Самый большой файл — это и есть основной прайс. Сопровождающие
            # документы (лицензии, инструкции) если и попадаются, то сильно
            # меньше по размеру.
            xlsx_paths.sort(key=lambda x: x[0], reverse=True)
            xlsx_path = xlsx_paths[0][1]

            loader = MerlionLoader()
            rows = list(loader.iter_rows(xlsx_path))
            logger.info(
                "Merlion IMAP: распарсено %d PriceRow из %s (zip «%s», %d байт)",
                len(rows), os.path.basename(xlsx_path), filename, len(data),
            )
            return rows
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)
