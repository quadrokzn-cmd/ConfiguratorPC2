/** @type {import('tailwindcss').Config} */

// 9Б.2: палитры конфигуратора и портала живут как CSS-переменные на
// body.app-theme / body.portal-theme. Tailwind генерирует классы вроде
// bg-surface-1, text-ink-primary через эту функцию-замыкание: на выходе
// получается rgb(var(--surface-1) / <alpha-value>), что подхватывается
// каскадом из @layer base в static/src/main.css. Brand и семантические
// цвета НЕ меняются между темами и остаются константами ниже.
const themed = (varName) => ({ opacityValue }) => {
  if (opacityValue !== undefined) {
    return `rgb(var(${varName}) / ${opacityValue})`;
  }
  return `rgb(var(${varName}))`;
};

module.exports = {
  content: [
    "./app/templates/**/*.html",
    "./portal/templates/**/*.html",
    // UI-1: общие партиалы (sidebar, fx_widget) живут в shared/.
    "./shared/templates/**/*.html",
    "./static/js/**/*.js",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          'Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI',
          'Roboto', 'Helvetica', 'Arial', 'sans-serif',
        ],
        mono: [
          'JetBrains Mono', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace',
        ],
      },
      colors: {
        // Поверхности тёмной темы — приглушённый сине-чёрный.
        // Хексы определены в @layer base в static/src/main.css:
        //   .app-theme    — конфигуратор, +1 ступень светлее (9Б.2 фикс
        //                   фидбэка «слишком тёмно»);
        //   .portal-theme — портал, +3 ступени светлее (заметно «живее»,
        //                   но всё ещё тёмная тема).
        surface: {
          base: themed('--surface-base'),  // фон страницы
          1:    themed('--surface-1'),     // карточки
          2:    themed('--surface-2'),     // инпуты, интерактивные плашки
          3:    themed('--surface-3'),     // hover, выделенный пункт сайдбара
          4:    themed('--surface-4'),     // самый верх стека (модалки, поповеры)
        },
        ink: {
          primary:   themed('--ink-primary'),
          secondary: themed('--ink-secondary'),
          muted:     themed('--ink-muted'),
          inverse:   themed('--ink-inverse'),
        },
        line: {
          // 9А.1.1: «белые границы» — полупрозрачные линии вместо плотных
          // тёмных рамок. soft/softer одинаковы для обеих тем, потому что
          // полупрозрачное белое смотрится корректно на любом тёмном фоне.
          soft:    'rgba(255,255,255,0.06)',  // карточки в покое
          softer:  'rgba(255,255,255,0.10)',  // карточки в hover
          // subtle/default/strong отличаются между темами (см. main.css):
          // на светлом портале границам нужно быть чуть ярче, чтобы
          // оставаться различимыми.
          subtle:  themed('--line-subtle'),
          default: themed('--line-default'),
          strong:  themed('--line-strong'),
        },
        // Бренд-синий КВАДРО-ТЕХ. Гайдлайн: #0000FF, но на тёмном UI
        // на крупных площадях он кислотный, поэтому slightly tamed
        // компромисс — оставляет узнаваемость, не давит на глаза.
        brand: {
          50:  '#EAF1FF',
          100: '#D0DEFF',
          200: '#9FBCFF',
          300: '#6F9AFF',
          400: '#4078FF',
          500: '#2052E8',  // primary (было #2F6FF1)
          600: '#1640C7',  // hover
          700: '#0F309E',  // active
          800: '#0C2578',
          900: '#0A1B54',
        },
        // Семантика
        success: { 500: '#10B981', 600: '#059669', bg: 'rgba(16,185,129,0.10)' },
        warning: { 500: '#F59E0B', 600: '#D97706', bg: 'rgba(245,158,11,0.12)' },
        danger:  { 500: '#EF4444', 600: '#DC2626', bg: 'rgba(239,68,68,0.12)' },
        info:    { 500: '#06B6D4', 600: '#0891B2', bg: 'rgba(6,182,212,0.12)' },
      },
      borderRadius: {
        sm:  '6px',
        md:  '8px',
        lg:  '10px',
        xl:  '14px',
        '2xl': '18px',
        '3xl': '22px',
      },
      boxShadow: {
        // Мягкие плотные тени, подходящие для тёмного фона
        'elev-1': '0 1px 0 rgba(255,255,255,0.03), 0 1px 2px rgba(0,0,0,0.30)',
        'elev-2': '0 6px 20px -8px rgba(0,0,0,0.55), 0 2px 6px rgba(0,0,0,0.35)',
        'elev-3': '0 16px 40px -12px rgba(0,0,0,0.60), 0 4px 12px rgba(0,0,0,0.45)',
        'focus':  '0 0 0 3px rgba(32,82,232,0.35)',
        // 9А.1.1: точечные светящиеся акценты «активность через свет»
        'glow-soft':   '0 4px 18px -6px rgba(255,255,255,0.05), 0 2px 6px rgba(0,0,0,0.25)',
        'glow-brand':  '0 0 0 1px rgba(64,120,255,0.35), 0 0 22px -6px rgba(32,82,232,0.55)',
        'glow-rail':   '-3px 0 14px -2px rgba(32,82,232,0.45)',
      },
      fontSize: {
        // Семантическая шкала
        'caption':  ['11px', { lineHeight: '16px', letterSpacing: '0.02em' }],
        'micro':    ['12px', { lineHeight: '18px' }],
        'small':    ['13px', { lineHeight: '20px' }],
        'body':     ['14px', { lineHeight: '22px' }],
        'body-lg':  ['15px', { lineHeight: '24px' }],
        'h3':       ['16px', { lineHeight: '24px', fontWeight: '600' }],
        'h2':       ['18px', { lineHeight: '26px', fontWeight: '600' }],
        'h1':       ['22px', { lineHeight: '30px', fontWeight: '600', letterSpacing: '-0.01em' }],
        'display':  ['28px', { lineHeight: '36px', fontWeight: '600', letterSpacing: '-0.015em' }],
      },
      spacing: {
        '18': '4.5rem',
        '88': '22rem',
      },
      transitionDuration: {
        '120': '120ms',
        '180': '180ms',
      },
    },
  },
  plugins: [],
}
