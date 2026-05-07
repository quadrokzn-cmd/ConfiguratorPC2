# IMAP-канал автозагрузки прайса Merlion (этап 12.1, fix-3 12.1).
#
# Письма от matveeva.y@merlion.ru (приходят через Gmail-forward — реальный
# отправитель сохраняется в From / Reply-To / X-Forwarded-For), Subject
# «Прайс-лист MERLION», вложение — ZIP ~5 МБ, внутри один XLSX или XLSM.
#
# Поток:
#   1. Записываем bytes во временный .zip;
#   2. Идём по infolist() ZIP-а: для записей без UTF-8 EFS-флага
#      (bit 11) перекодируем filename cp437→cp1251 — Merlion-архив
#      пишется именно так. Это важно для имени файла на диске;
#   3. Распаковываем .xlsx/.xlsm в /tmp/merlion_<uuid>/ через
#      _extract_zip_member_raw — РУЧНУЮ распаковку по offset через
#      struct + zlib. Это нужно потому что в реальных Merlion-архивах
#      имя файла в central directory и в local file header физически
#      различаются (отклонение от ZIP-спеки на стороне их
#      архиватора), и стандартный zipfile.ZipFile.open() законно
#      бросает «File name in directory ... and header ... differ».
#      Наша ручная распаковка эту проверку не делает — она просто
#      seek-ает по info.header_offset и достаёт сжатые байты;
#   4. Берём самый большой .xlsx/.xlsm — это и есть основной прайс;
#   5. Прогоняем через MerlionLoader — тот же, что и /admin/price-uploads;
#   6. Чистим временные файлы (try/finally).

from __future__ import annotations

import logging
import os
import re
import shutil
import struct
import tempfile
import zipfile
import zlib

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
                    try:
                        self._extract_zip_member_raw(zf, info, out_path)
                    except RuntimeError as exc:
                        raise RuntimeError(
                            f"Merlion IMAP: ошибка распаковки «{real_name}» "
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

    # Local file header — фиксированная часть, 30 байт.
    # Формат от ZIP-спеки и точно совпадает с zipfile.structFileHeader:
    #   <4s          PK\x03\x04
    #    2B          version_needed (major, minor)
    #    4H          flag_bits, compress_method, mod_time, mod_date
    #    L           crc32
    #    2L          compressed_size, uncompressed_size
    #    2H          filename_length, extra_field_length
    _LFH_STRUCT = struct.Struct("<4s2B4HL2L2H")
    _LFH_SIGNATURE = b"PK\x03\x04"

    @staticmethod
    def _extract_zip_member_raw(
        zf: zipfile.ZipFile, info: zipfile.ZipInfo, dst_path: str,
    ) -> None:
        """Распаковывает запись ZIP-а вручную, минуя zipfile.ZipFile.open().

        Зачем: реальный Merlion-архиватор пишет байты имени файла в
        central directory и в local file header в РАЗНЫХ кодировках
        (cp437-decode даёт другую строку, чем cp1251-байты в LFH). По
        ZIP-спеке эти записи должны совпадать; стандартный
        ZipFile.open() это законно проверяет и валится с
        BadZipFile «File name in directory ... and header ... differ».
        Мы не валидируем имя — просто берём compressed-байты по
        info.header_offset и распаковываем zlib.

        Поддерживает только STORED (0) и DEFLATED (8) — больше Merlion
        и не использует. compress_size берём из central (info), а не
        из local: при streaming-флаге (bit 3 = 0x008) local LFH хранит
        нули, и только central + data descriptor содержат настоящий
        размер.
        """
        zf.fp.seek(info.header_offset)
        raw = zf.fp.read(MerlionImapFetcher._LFH_STRUCT.size)
        if len(raw) != MerlionImapFetcher._LFH_STRUCT.size:
            raise RuntimeError(
                "обрезанный local file header "
                f"(прочитано {len(raw)}, ожидалось {MerlionImapFetcher._LFH_STRUCT.size} байт)"
            )
        unpacked = MerlionImapFetcher._LFH_STRUCT.unpack(raw)
        signature = unpacked[0]
        # Индексы соответствуют zipfile._FH_*; нам нужны только
        # filename_length и extra_field_length (последние два H).
        fname_len = unpacked[10]
        extra_len = unpacked[11]
        if signature != MerlionImapFetcher._LFH_SIGNATURE:
            raise RuntimeError(
                f"неверная сигнатура local file header: {signature!r}"
            )
        # Пропускаем filename + extra field, переходя к compressed body.
        zf.fp.seek(fname_len + extra_len, 1)

        compressed = zf.fp.read(info.compress_size)
        if len(compressed) != info.compress_size:
            raise RuntimeError(
                f"обрезанные данные: прочитано {len(compressed)} из "
                f"{info.compress_size} compressed-байт"
            )

        if info.compress_type == zipfile.ZIP_STORED:
            payload = compressed
        elif info.compress_type == zipfile.ZIP_DEFLATED:
            try:
                payload = zlib.decompress(compressed, -zlib.MAX_WBITS)
            except zlib.error as exc:
                raise RuntimeError(f"zlib decompress error: {exc}") from exc
        else:
            raise RuntimeError(
                f"неподдерживаемый compress_type={info.compress_type} "
                "(поддерживаются только STORED=0 и DEFLATED=8)"
            )

        # info.file_size — это «uncompressed size» из central. Если
        # архиватор записал его неверно, это не повод падать (xlsx
        # всё равно сам проверит свою целостность). Просто warn.
        if info.file_size and len(payload) != info.file_size:
            logger.warning(
                "Merlion IMAP: распакованный размер %d не совпал с "
                "info.file_size=%d для %r — продолжаем (xlsx-валидатор "
                "поймает реальную битость, если есть).",
                len(payload), info.file_size, info.filename,
            )

        with open(dst_path, "wb") as dst:
            dst.write(payload)
