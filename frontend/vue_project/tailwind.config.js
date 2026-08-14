/** 主站 Tailwind 仅扫描活跃 Vue 源码；归档占位单元不参与生产样式构建。 */
/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  /** 知识图谱嵌入 + Orbis 展示页根节点均需带 `data-tw-important` */
  important: '[data-tw-important]',
  content: [
    './index.html',
    './src/**/*.{vue,js}',
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#ecfeff',
          100: '#cffafe',
          600: '#0891b2',
          700: '#0e7490',
        },
        /** Orbis 展示页（ShowcaseOrbis） */
        background: '#010828',
        cream: '#EFF4FF',
        neon: '#6FFF00',
      },
      fontFamily: {
        grotesk: ['Anton', 'sans-serif'],
        condiment: ['Condiment', 'cursive'],
        mono: [
          'ui-monospace',
          'SFMono-Regular',
          'Menlo',
          'Monaco',
          'Consolas',
          'Liberation Mono',
          'Courier New',
          'monospace',
        ],
      },
      boxShadow: {
        soft: '0 1px 2px rgba(15, 23, 42, 0.06)',
      },
    },
  },
  plugins: [],
}
