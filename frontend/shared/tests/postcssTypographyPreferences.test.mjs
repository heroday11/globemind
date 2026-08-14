import assert from 'node:assert/strict'
import test from 'node:test'
import postcss from 'postcss'

import { createTypographyPreferencesPlugin } from '../postcssTypographyPreferences.js'

test('converts fixed typography declarations to preference-aware values', async () => {
  const result = await postcss([createTypographyPreferencesPlugin()]).process(
    '.card { font-size: 16px; line-height: 1.5; font-family: Inter, sans-serif; }',
    { from: undefined },
  )

  assert.match(result.css, /font-size: 1rem/)
  assert.match(result.css, /line-height: calc\(1\.5 \+ var\(--gm-leading\)\)/)
  assert.match(result.css, /font-family: var\(--gm-font-sans\)/)
})
