import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const read = (relative) => readFile(new URL(relative, import.meta.url), 'utf8')

test('legacy academic-data path discloses its actual capability-market meaning', async () => {
  const [view, router, navigation] = await Promise.all([
    read('../src/views/AcademicData.vue'),
    read('../src/router/index.js'),
    read('../src/components/appNav.vue'),
  ])

  assert.match(view, /旧路径说明/)
  assert.match(view, /不是论文、书目或已接通的学术数据库/)
  assert.match(view, /to="\/sources"/)
  assert.match(router, /'\/academic-data': 'Agent 能力与连接卡 · GlobeMind'/)
  assert.match(navigation, />Agent 能力市场<\/router-link>/)
  assert.doesNotMatch(navigation, />智库信息汇聚<\/router-link>/)
})

test('public canonical metadata is route-specific and private surfaces fail closed to noindex', async () => {
  const [index, router, robots, sitemap] = await Promise.all([
    read('../index.html'),
    read('../src/router/index.js'),
    read('../public/robots.txt'),
    read('../public/sitemap.xml'),
  ])

  assert.doesNotMatch(index, /<link rel="canonical"/)
  assert.match(index, /<meta name="robots" content="noindex,nofollow">/)
  assert.match(router, /const PUBLIC_CANONICAL_ORIGIN = 'https:\/\/globemind\.top'/)
  assert.match(router, /INDEXABLE_CANONICAL_PATHS\.has\(normalized\)/)
  assert.match(router, /robots\.setAttribute\('content', indexable \? 'index,follow' : 'noindex,nofollow'\)/)
  assert.match(router, /canonical\?\.remove\(\)/)
  assert.doesNotMatch(router.match(/const INDEXABLE_CANONICAL_PATHS = new Set\(\[([\s\S]*?)\]\)/)?.[1] || '', /research-workspace|model-assurance|entity-governance|user-center|login/)
  assert.match(robots, /Sitemap: https:\/\/globemind\.top\/sitemap\.xml/)
  assert.match(sitemap, /<loc>https:\/\/globemind\.top\/sources<\/loc>/)
  assert.doesNotMatch(sitemap, /research-workspace|model-assurance|entity-governance|user-center|showcase/)
})
