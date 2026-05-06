# IMAP-канал автозагрузки прайса Merlion (этап 12.1, fix-2 12.1).
#
# Письма от matveeva.y@merlion.ru (приходят через Gmail-forward — реальный
# отправитель сохраняется в From / Reply-To / X-Forwarded-For), Subject
# «Прайс-лист MERLION», вложение — ZIP ~5 МБ, внутри один XLSX или XLSM.
#
# Поток:
#   1. Записываем bytes во временный .zip;
#   2. Идём по infolist() ZIP-а: для записей без UTF-8 EFS-флага
#      (bit 11) перекодируем filename cp437→cp1251 — Merlion-архив
#      пишется именно так. Иначе zf.extractall() падает с mismatch
#      имени между central directory и local header;
#   3. По объекту ZipInfo (а НЕ строке имени) распаковываем .xlsx/.xlsm
#      файлы в /tmp/merlion_<uuid>/ через shutil.copyfileobj;
#   4. Берём самый большой .xlsx/.xlsm — это и есть основной прайс;
#   5. Прогоняем через MerlionLoader — тот же, что и /admin/price-uploads;
#   6. Чистим временные файлы (try/finally).

from __future__ import annotations

import logging
import os
import re
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

            # Реальный Merlion-ZIP пишется с кириллическими именами в
            # cp1251 БЕЗ выставленного UTF-8 EFS-флага (bit 11). По
            # спецификации zipfile в этом случае декодирует имена как
            # cp437 — и extractall() позже падает с «File name in
            # directory ... and header ... differ», т.к. центральная
            # директория и локальный заголовок дают разный «мусор».
            # Чиним вручную: для каждой записи без EFS-флага
            # перекодируем filename cp437→cp1251 и читаем по объекту
            # ZipInfo (zf.open(info)), а не по строке имени.
            try:
                zf = zipfile.ZipFile(zip_path, "r")
            except zipfile.BadZipFile as exc:
                raise RuntimeError(
                    f"Merlion IMAP: вложение «{filename}» не распознано "
                    f"как ZIP-архив: {exc}"
                ) from exc

            xlsx_paths: list[tuple[int, str]] = []  # (size, path_on_disk)
            try:
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    real_name = self._decode_zip_member_name(info)
                    # Берём только лист Excel (xlsx или xlsm — Merlion
                    # последний раз перешёл на .xlsm с макросами).
                    low = real_name.lower()
                    if not (low.endswith(".xlsx") or low.endswith(".xlsm")):
                        continue
                    # Раскладка extract_dir: имя файла нормализуем для
                    # FS — убираем разделители и небезопасные символы.
                    safe_name = re.sub(r"[\\/:*?\"<>|]+", "_", os.path.basename(real_name))
                    if not safe_name:
                        # Пустое имя после нормализации — пропустим.
                        continue
                    out_path = os.path.join(extract_dir, safe_name)
                    # Читаем по ZipInfo-объекту: zf.open(info) находит
                    # запись по offset, не по filename — поэтому
                    # внутренний mismatch имени cp437/cp1251 ему не
                    # мешает.
                    try:
                        with zf.open(info) as src, open(out_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                    except (zipfile.BadZipFile, RuntimeError) as exc:
                        # RuntimeError может возникнуть на запароленных
                        # архивах. Merlion их не шлёт, но защитимся.
                        raise RuntimeError(
                            f"Merlion IMAP: ошибка чтения «{real_name}» "
                            f"из ZIP «{filename}»: {exc}"
                        ) from exc
                    try:
                        size = os.path.getsize(out_path)
                    except OSError:
                        size = 0
                    xlsx_paths.append((size, out_path))
            finally:
                zf.close()

            if not xlsx_paths:
                raise RuntimeError(
                    f"Merlion IMAP: ZIP «{filename}» не содержит ни одного "
                    "файла .xlsx или .xlsm — формат рассылки изменился."
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

    @staticmethod
    def _decode_zip_member_name(info: zipfile.ZipInfo) -> str:
        """Возвращает «настоящее» имя файла внутри ZIP с учётом cp1251.

        Если EFS-флаг (bit 11) выставлен — архив пишет UTF-8, ничего
        перекодировать не нужно. Иначе zipfile.ZipFile уже декодировал
        байты как cp437 (по спецификации) — мы их перекодируем обратно
        в cp437→cp1251. На неудаче возвращаем как есть.
        """
        if info.flag_bits & 0x800:
            return info.filename
        try:
            return info.filename.encode("cp437").decode("cp1251")
        except UnicodeError:
            return info.filename
