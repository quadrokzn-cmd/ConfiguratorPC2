# Дизайн-система и frontend-сборка

Этап 9А.1 ввёл локальную сборку Tailwind вместо CDN, локальный шрифт
Inter и единый набор токенов/компонентов. Все цвета, размеры и стили
кнопок/полей живут в `tailwind.config.js` (extend) и
`static/src/main.css` (`@layer components`).

## Workflow

1. **Один раз:** `npm install` (поднимет tailwindcss, postcss, autoprefixer
   как dev-зависимости — `node_modules/` в `.gitignore`).
2. **Перед коммитом любых изменений в стилях/шаблонах:**
   ```bash
   npm run build:css
   git add static/dist/main.css
   ```
   Собранный `static/dist/main.css` коммитится в репозиторий — на проде
   (Railway) Node.js не нужен.
3. **При локальной разработке:** `npm run watch:css` — пересобирает CSS
   при изменении шаблонов и `main.css`.

Tailwind сканирует:
- `app/templates/**/*.html` — все шаблоны и макросы конфигуратора
- `portal/templates/**/*.html` — все шаблоны портала (этап 9Б.2)
- `static/js/**/*.js` — на случай классов, формируемых строкой в JS
  (`project.js` так делает для строк спецификации)

## Две палитры в одном CSS (этап 9Б.2)

Конфигуратор и портал делят **один** скомпилированный
`static/dist/main.css`, но рендерятся в разных палитрах. Переключение —
через класс на `<body>`:

| Шаблон                  | Класс body         | Палитра                                    |
|-------------------------|--------------------|--------------------------------------------|
| `app/templates/base.html`    | `app-theme`    | Конфигуратор: тёмный графит (+1 ступень светлее, чем 9А.1) |
| `portal/templates/base.html` | `portal-theme` | Портал: «графит со светом» (+3 ступени светлее) |
| `portal/templates/login.html`| `portal-theme` | Логин-страница тоже в портал-палитре       |

Под капотом — **CSS custom properties** (`--surface-base`, `--ink-primary`,
`--line-default` …) на двух body-классах в `@layer base`. Tailwind
tokens (`bg-surface-1`, `text-ink-primary`) собираются через хелпер
`themed(...)` в `tailwind.config.js`, который возвращает
`rgb(var(--surface-1) / <alpha-value>)`. Это даёт два эффекта:

- классы Tailwind продолжают работать (`bg-surface-1`, `bg-surface-1/40`,
  `border-line-default` и т.п.) — без выбора темы в шаблонах;
- на body меняется один класс — каскадно меняются все 5 уровней
  surface, 4 уровня текста и 3 уровня линий.

Brand (`#2052E8` и оттенки), семантика (success/warning/danger/info)
и полупрозрачные `line.soft/softer` — **константы**, общие для обеих тем.

### Базовые hex'ы

**`.app-theme` (конфигуратор):**

| Токен          | Hex      | Назначение                                  |
|----------------|----------|---------------------------------------------|
| surface.base   | `#0E121C`| Фон страницы                                |
| surface.1      | `#131826`| Карточки                                     |
| surface.2      | `#1B2231`| Инпуты, плашки                              |
| surface.3      | `#252D40`| Hover, выделенный пункт сайдбара            |
| surface.4      | `#303A52`| Самый верх стека (модалки, поповеры)        |
| ink.primary    | `#E7EAF3`| Основной текст                              |
| ink.secondary  | `#9AA3B6`| Подписи                                     |
| ink.muted      | `#6A7286`| Меты                                        |
| line.subtle    | `#1F2638`| Тонкие разделители                          |
| line.default   | `#2C354B`| Стандартные границы                         |
| line.strong    | `#404A65`| Сильные границы                             |

**`.portal-theme` (портал):**

| Токен          | Hex      | Назначение                                  |
|----------------|----------|---------------------------------------------|
| surface.base   | `#181E2C`| Фон страницы                                |
| surface.1      | `#1F2638`| Карточки/виджеты                            |
| surface.2      | `#2A3247`| Плашки, hover карточек                      |
| surface.3      | `#353F58`| Hover активных элементов                    |
| surface.4      | `#424E6B`| Самый верх стека                            |
| ink.primary    | `#E7EAF3`| Основной текст (тот же)                     |
| ink.secondary  | `#A8B0C2`| Подписи (немного светлее, чтобы не сливалось)|
| ink.muted      | `#7A839A`| Меты                                        |
| line.subtle    | `#2A3247`| Тонкие разделители                          |
| line.default   | `#3A435E`| Стандартные границы                         |
| line.strong    | `#525E7C`| Сильные границы                             |

Контрасты ink.primary/secondary на surface.base — выше WCAG AA (4.5+)
для основного текста и подписей в обеих темах. ink.muted (3+) —
используется только на caption-микроразмерах и timestamps.

### Когда менять hex'ы

- Не правьте hex прямо в `tailwind.config.js` — там цвета объявлены
  через `themed('--...')`, реальные значения только в `@layer base`
  внутри `static/src/main.css`.
- Меняете значение → пересобираете CSS (`npm run build:css`) →
  визуально проходитесь по 3-4 страницам обоих сервисов
  (главная, проект, /admin/components, дашборд портала).
- Если меняете базу (surface.base) больше чем на 10-15% по светлоте —
  пересчитайте контрасты ink.* для WCAG AA.

## Дизайн-токены

Файл: `tailwind.config.js`

| Группа     | Токены                                            |
| ---------- | ------------------------------------------------- |
| Поверхности| `surface.base/1/2/3/4` — 5 уровней глубины        |
| Текст      | `ink.primary/secondary/muted/inverse`             |
| Линии      | `line.subtle/default/strong`                      |
| Бренд      | `brand.50…900`, основной — `brand.500` (#2F6FF1)  |
| Семантика  | `success/warning/danger/info` — каждая с `bg`     |
| Шрифт      | Inter — по `@font-face` из `static/fonts/inter/`  |

## Компонентные классы

Файл: `static/src/main.css` → `@layer components`

| Класс                                                | Назначение                              |
| ---------------------------------------------------- | --------------------------------------- |
| `.btn` + `.btn-primary/secondary/ghost/danger/success` | Кнопки (4 семантики)                   |
| `.btn-sm/md/lg`                                      | Размеры                                 |
| `.input`, `.select`, `.textarea`                     | Поля ввода                              |
| `.label`, `.help-text`, `.error-text`                | Подписи/подсказки                       |
| `.check`, `.radio`                                   | Кастомные чекбокс/радио                 |
| `.card` + `.card-pad/sm/lg` + `.card-elev`           | Карточки                                |
| `.badge` + `.badge-neutral/brand/success/warning/danger/info` | Бейджи                          |
| `.kt-table`                                          | Таблица «список»                        |
| `.nav-item`, `.nav-item-active`, `.nav-section-label`| Сайдбар                                 |
| `.breadcrumbs` + `.crumb-sep`, `.crumb-current`      | Хлебные крошки                          |
| `.alert` + `.alert-danger/warning/info/success`      | Алерты                                  |
| `.modal-overlay`, `.modal-container`, `.modal-header/body/footer` | Модалки                |
| `.kt-spinner`                                        | Спиннер «обрабатываем»                  |

## Раскладка приложения

Файл: `app/templates/base.html`

```
┌─────────┬────────────────────────────────────┐
│         │ topbar (хлебные крошки + extra)    │
│ aside   ├────────────────────────────────────┤
│ sidebar │ main (контент страницы)            │
│ 248px   │                                    │
└─────────┴────────────────────────────────────┘
```

Каждая страница переопределяет:
- `{% block breadcrumbs %}` — путь в крошках (а-ля `Проекты / Имя`)
- `{% block topbar_extra %}` — кнопки/контролы справа от крошек
- `{% block content %}` — основной контент
- `{% block scripts %}` — постзагружаемые скрипты страницы

## Иконки

`app/templates/_macros/icons.html` — макрос `{{ icon('plus', class='...') }}`,
inline-SVG в стиле lucide (stroke 1.75, line-cap round). Цвет
наследуется через `currentColor`. Список доступных имён см. в
файле макроса.

## Дашборд портала (этап 9Б.2)

Главная страница портала (`portal/templates/home.html`) — компактный
дашборд из 5 виджетов и одной плитки модуля:

- **Активные проекты** — `COUNT(*) FROM projects`.
- **Менеджеры** — `COUNT(*) FROM users WHERE role='manager' AND is_active`.
- **Курс доллара ЦБ** — последняя запись из `exchange_rates`. Там же
  крутится APScheduler конфигуратора, который кладёт сюда курсы
  5 раз в день.
- **Свежесть прайсов** — `MAX(uploaded_at) FROM price_uploads` по
  трём поставщикам OCS / Merlion / Treolan, бейдж «свежий»/«устарел»
  по порогу 14 дней.
- **Компоненты в БД** — суммарный `COUNT(*)` по 8 категориям
  (`is_hidden = FALSE`) + миниатюрный bar-chart по категориям без
  внешних библиотек графиков.

Источник данных — `portal/services/dashboard.py:get_dashboard_data(db)`.
Контракт ключей: `active_projects, managers, exchange_rate,
suppliers_freshness, components_breakdown`. На пустой БД сервис не
падает — отдаёт нули и `None`, шаблон показывает «—» / «нет данных».

Виджеты — общие метрики компании, доступны всем авторизованным.
Плитка модуля «Конфигуратор ПК» отрисовывается только если у
пользователя есть `permissions["configurator"]` или роль admin —
иначе показывается пустой стейт «Доступных модулей нет».

CSS компонентов дашборда — `.portal-widget`, `.portal-grid`,
`.portal-module-tile` и др. в `@layer components` основного
`static/src/main.css`. Squircle-радиусы 18-20px, big numbers 56-64px,
breathing layout (24px padding, 20px gap).

## Что осталось на 9А.2/3

- Перевести админку (`dashboard.html`, `users.html`, `mapping_*`,
  `budget.html`, `all_queries.html`) и страницы
  `projects_list.html`, `history.html`, `result.html`,
  `project_new_query.html` на новые токены.
- Завести роуты `/admin/suppliers` и `/admin/components` (сейчас
  пункты сайдбара ведут на дашборд-плейсхолдер).
- Заменить временный SVG-плейсхолдер логотипа на присланный
  заказчиком исходник (`brand_mark` в `_macros/icons.html`).
