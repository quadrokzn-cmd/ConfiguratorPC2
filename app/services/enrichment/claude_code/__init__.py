# Подмодуль обогащения характеристик через Claude Code (этап 2.5Б).
#
# Поток:
#   1) exporter.py выгружает из БД незаполненные позиции по категориям
#      в JSON-батчи в enrichment/pending/<category>/batch_NNN.json.
#   2) Claude Code открывает соответствующий промпт из enrichment/prompts/
#      и заполняет поля через web search по официальным сайтам производителей.
#      Результат сохраняется в enrichment/done/<category>/batch_NNN.json.
#   3) importer.py читает done/, валидирует значения и URL-источники,
#      пишет в БД через apply_enrichment, перекладывает обработанные
#      батчи в enrichment/archive/.
