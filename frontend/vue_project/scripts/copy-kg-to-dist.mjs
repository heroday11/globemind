/**
 * 将 knowledge_graph_backup 的构建结果复制到主项目 dist/knowledge-graph/
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const src = path.join(root, 'knowledge_graph_backup', 'dist')
const dest = path.join(root, 'dist', 'knowledge-graph')

if (!fs.existsSync(src)) {
  console.error('[copy-kg] 不存在:', src, '请先执行 npm run build:knowledge-graph')
  process.exit(1)
}

fs.mkdirSync(path.dirname(dest), { recursive: true })
fs.rmSync(dest, { recursive: true, force: true })
fs.cpSync(src, dest, { recursive: true })
console.log('[copy-kg] 已复制到', dest)
