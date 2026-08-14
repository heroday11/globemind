/** 知识图谱（knowledge_graph_backup）使用 Tailwind；主站其它页面为 Element Plus，用 important 限定作用域，减少互相干扰 */
/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  /** 知识图谱嵌入 + Orbis 展示页根节点均需带 `data-tw-important` */
  important: '[data-tw-important]',
  content: [
    './index.html',
    './src/**/*.{vue,js}',
    '../knowledge_graph_backup/src/**/*.{vue,js,ts,jsx,tsx}',
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
