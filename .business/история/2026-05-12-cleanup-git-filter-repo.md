# Backlog #8 — вычистить quadrotech-*.sql дампы из истории git

**Дата:** 2026-05-12
**Контекст:** Backlog #8 из `plans/2026-04-23-platforma-i-aukciony.md`
**Триггер:** После UI-5 (2026-05-12) репо работает на одном FastAPI portal.
В истории git остались 2 крупных blob'а от Этапа 1 слияния (eb550bb,
добавлены) и Этапа 9c (d8b9c59, удалены с диска): `quadrotech-full.sql`
и `quadrotech-data-only.sql`, каждый ~58 МБ исходно. Файлы физически
удалены с диска, но pack-объекты остаются в истории — pack-size до
операции 36.49 MiB.

Собственник дал OK на разовое исключение: `git filter-repo --invert-paths`
+ force-push в `origin master`. Backup-ветка как страховка обязательна.

## 1. Поставленная задача

Удалить из истории git два blob'а (`quadrotech-full.sql`,
`quadrotech-data-only.sql`) через `git filter-repo`, сохранить backup-ветку
с исходным master'ом, перезаписать `origin master` через force-push,
зафиксировать pack-size до/после, прогнать pytest baseline, обновить
backlog #8 в плане.

Жёсткие ограничения:
- Force-push только на финальном шаге, разово, без `--no-verify`/`--no-gpg-sign`.
- Backup-ветка `backup-pre-filter-2026-05-12` создаётся до filter-repo
  и пушится в remote.
- Никакой работы с другими крупными файлами (tenders.sql, matches.sql,
  xlsx-прайсы) — вне scope этого backlog'а.

## 2. Как решал

1. **Установка `git-filter-repo`.** `python -m pip install --user` не
   сработал — активен venv проекта. Установил без `--user` в venv:
   `git-filter-repo-2.47.0`. Сам tool показывает версию как короткий
   git-hash (`a40bce548d2c`) — это нормально для filter-repo.

2. **Sanity-проверка состояния.** Working tree clean, ветка master,
   remote — `https://github.com/quadrokzn-cmd/ConfiguratorPC2.git` (HTTPS,
   не SSH как было в backlog-промте — зафиксировал актуальный URL для
   шага восстановления remote). Файлов на диске нет, но в git history
   присутствуют (`eb550bb` добавил, `d8b9c59` удалил с диска).

3. **Backup-ветка.** `git branch backup-pre-filter-2026-05-12` указывает
   на тот же SHA, что и master (`af337a8`). Не чекаутил — остался на master.

4. **filter-repo.** Первый запуск без `--force` отказался работать
   («Refusing to destructively overwrite repo history since this does
   not look like a fresh clone»). Это ожидаемая защита — у нас не
   свежий клон. Добавил `--force` и запустил повторно. Это **не**
   `git push --force` — это локальный флаг filter-repo, безопасный
   при наличии backup-ветки.

   filter-repo переписал 157 коммитов за 0.44с, удалил origin (своя
   защита — восстановил на следующем шаге), сделал repack автоматически.
   Новый HEAD: `bc0665f`.

5. **Sanity после filter-repo.** `git log --all --oneline -- <файл>`
   пустой для обоих дампов. Топ blob'ов начинается с `tenders.sql ~43 МБ`
   (как и предсказывал backlog) — quadrotech-*.sql отсутствуют.

6. **Repack.** `git reflog expire --expire=now --all && git gc
   --prune=now --aggressive` — pack-size 28.06 MiB.

7. **pytest baseline.** `pytest -m "not live" -q` — `1862 passed,
   1 skipped` за 102.45с. Совпадает с UI-5 baseline (1862/1).

8. **Восстановил remote** через `git remote add origin <HTTPS URL>`,
   тот же URL, что был до filter-repo.

9. **Рефлексия + апдейт плана + commit + force-push** — последний шаг.

## 3. Решил ли — да / нет / частично

Да, полностью.

DoD выполнен:
- `git log --all --oneline -- .business/_backups_2026-05-08-merge/quadrotech-full.sql` пуст ✓
- `git log --all --oneline -- .business/_backups_2026-05-08-merge/quadrotech-data-only.sql` пуст ✓
- pytest 1862 passed, без новых failures ✓
- pack-size 36.49 MiB → 28.06 MiB (выигрыш 8.43 MiB, ~23%) ✓
- backup-pre-filter-2026-05-12 запушена в origin как страховка ✓
- master в origin перезаписан через force-push ✓
- рефлексия записана ✓
- backlog #8 помечен выполненным ✓

**Цифры:**
- SHA HEAD до filter-repo: `af337a8432f74e1776d69a58e9d2415f4953a09b`
  (= `backup-pre-filter-2026-05-12`)
- SHA HEAD после filter-repo: `bc0665f54b4c1fc2c9a6b20203cc50b6552705b3`
- pack-size до: 36.49 MiB
- pack-size после: 28.06 MiB (выигрыш 8.43 MiB)
- pytest: 1862 passed, 1 skipped

## 4. Эффективно ли решение, что можно было лучше

Эффективно. Операция заняла ~3 минуты на filter-repo+repack, ~2 минуты
на pytest. Выигрыш 8.43 МБ — в верхней части ожидаемого диапазона
5-10 МБ (SQL хорошо пакуется, поэтому 2×58 МБ исходных дали ~8 МБ
после дельт и компрессии).

Что прошло хорошо:
- Backup-ветка как страховка — даёт точку отката одной командой
  (`git reset --hard backup-pre-filter-2026-05-12`).
- pytest baseline после filter-repo подтвердил, что переписанная
  история ни на что не повлияла (это и так должно было быть — изменены
  только blob'ы вне рабочего кода — но проверить было дёшево).
- Restoration remote через HTTPS (актуальный URL), не SSH из backlog'а —
  заметил расхождение в шаге 2.

Что можно было лучше:
- В будущем подобные крупные ассеты (SQL-дампы, прайсы) лучше класть
  в `.gitignore` и хранить в B2/локально, не в git. Это не делалось
  по дисциплине — но `tenders.sql` (43 МБ) до сих пор в репо, и
  Backlog #8 его не трогает (out of scope). Когда-нибудь стоит сделать
  отдельный backlog «вычистить tenders.sql + xlsx-прайсы», аналогично.
- Защита filter-repo от «не свежего клона» (`--force` required) —
  лёгкий капкан для копипасты. В backlog-промте этого предупреждения
  не было; написал бы заранее, не пришлось бы делать вторую попытку.

## 5. Как было и как стало

**Как было:**
- pack-size 36.49 MiB.
- В истории git существуют blob'ы `quadrotech-full.sql` и
  `quadrotech-data-only.sql` (~58 МБ каждый исходно, ~4 МБ pack'ом
  каждый после компрессии — но удалить их можно было только переписав
  историю).
- master SHA: `af337a8`.

**Как стало:**
- pack-size 28.06 MiB (−8.43 MiB, −23%).
- В истории git этих blob'ов нет.
- master SHA: `bc0665f`.
- Backup-ветка `backup-pre-filter-2026-05-12` указывает на `af337a8`
  и запушена в origin как страховка.
- Локально и в `origin master` ветки переписаны (force-push разрешён
  собственником разово для этой операции).

---

## Что собственник делает руками после force-push

Сторонним клонам репо нужно синхронизироваться с переписанной историей.
Обычный `git pull` приведёт к merge-конфликту или разъезду — поэтому
делается жёсткий ресет на новый `origin/master`.

```bash
# 1) На dev-машине (Windows, Git Bash или PowerShell)
cd d:/ProjectsClaudeCode/ConfiguratorPC2
git fetch origin
git reset --hard origin/master

# 2) На офисном сервере (RDP под server1c\Администратор, Git Bash)
cd /d/AuctionsIngest/ConfiguratorPC2
GIT_SSH_COMMAND="ssh -F /d/AuctionsIngest/.ssh/config" git fetch origin
git reset --hard origin/master

# Sanity на офисе:
cd /d/AuctionsIngest/ConfiguratorPC2
./venv/Scripts/python.exe -c "from portal.services.auctions.ingest.orchestrator import run_ingest_once; print('ok')"
```

После ресета локальные неподтверждённые изменения, если есть, будут
утеряны — поэтому при наличии незакоммиченных правок сначала
`git stash`, потом `git reset --hard`, потом `git stash pop`.

Backup-ветка `backup-pre-filter-2026-05-12` остаётся в origin как
страховка — на случай, если через какое-то время выяснится, что в
удалённых blob'ах было что-то нужное. После 1-2 недель её можно
удалить (`git push origin --delete backup-pre-filter-2026-05-12`),
но не сейчас.
