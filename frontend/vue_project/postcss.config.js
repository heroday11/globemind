import autoprefixer from 'autoprefixer'
import tailwindcss from 'tailwindcss'
import { createTypographyPreferencesPlugin } from '@globemind/shared/postcss-typography'

export default {
  plugins: [tailwindcss(), createTypographyPreferencesPlugin(), autoprefixer()],
}
