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
- `app/templates/**/*.html` — все шаблоны и макросы
- `static/js/**/*.js` — на случай классов, формируемых строкой в JS
  (`project.js` так делает для строк спецификации)

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

## Что осталось на 9А.2/3

- Перевести админку (`dashboard.html`, `users.html`, `mapping_*`,
  `budget.html`, `all_queries.html`) и страницы
  `projects_list.html`, `history.html`, `result.html`,
  `project_new_query.html` на новые токены.
- Завести роуты `/admin/suppliers` и `/admin/components` (сейчас
  пункты сайдбара ведут на дашборд-плейсхолдер).
- Заменить временный SVG-плейсхолдер логотипа на присланный
  заказчиком исходник (`brand_mark` в `_macros/icons.html`).
