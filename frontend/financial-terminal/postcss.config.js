import autoprefixer from 'autoprefixer'
import tailwindcss from 'tailwindcss'
import { createTypographyPreferencesPlugin } from '../shared/postcssTypographyPreferences.js'

export default {
  plugins: [tailwindcss(), createTypographyPreferencesPlugin(), autoprefixer()],
}
