/**
 * 并行启动主站 Vite（dev:main）与知识图谱子项目（dev:knowledge-graph）。
 * 带标签的输出流 + 启动完成后打印清晰入口。
 */
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import path from 'node:path'
import { createInterface } from 'node:readline'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

function prefixStream(stream, label, colorCode) {
  const rl = createInterface({ input: stream, crlfDelay: Infinity })
  rl.on('line', (line) => {
    console.log(`\x1b[${colorCode}m[${label}]\x1b[0m ${line}`)
  })
}

function spawnLabeled(cmd, args, label, colorCode) {
  const child = spawn(
    // shell:true 时拼接为单字符串，避免 DEP0190 参数未转义警告
    [cmd, ...args].join(' '),
    [],
    { cwd: root, shell: true, stdio: ['inherit', 'pipe', 'pipe'], env: process.env },
  )
  prefixStream(child.stdout, label, colorCode)
  prefixStream(child.stderr, label, colorCode)
  return child
}

const CYAN = '36'
const MAGENTA = '35'
const GREEN = '32'
const YELLOW = '33'

console.log('')
console.log(`\x1b[${GREEN}m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\x1b[0m`)
console.log(`\x1b[${GREEN}m  DataSearch Frontend — 正在启动...\x1b[0m`)
console.log(`\x1b[${GREEN}m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\x1b[0m`)
console.log('')

const main = spawnLabeled('npm', ['run', 'dev:main'], 'main', CYAN)
const kg = spawnLabeled('npm', ['run', 'dev:knowledge-graph'], 'kg', MAGENTA)

let exiting = false

function shutdown() {
  if (exiting) return
  exiting = true
  try { main.kill() } catch { /* ignore */ }
  try { kg.kill() } catch { /* ignore */ }
  process.exit(0)
}

process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)

function onChildExit() {
  if (!exiting) shutdown()
}

main.on('exit', onChildExit)
kg.on('exit', onChildExit)

// 启动完成后打印入口提示
setTimeout(() => {
  if (exiting) return
  console.log('')
  console.log(`\x1b[${GREEN}m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\x1b[0m`)
  console.log(`\x1b[${GREEN}m  Dev servers ready\x1b[0m`)
  console.log(`\x1b[${GREEN}m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\x1b[0m`)
  console.log('')
  console.log(`  \x1b[${YELLOW}m🏠  首页 / 主站      →  \x1b[1mhttp://localhost:5173/\x1b[0m`)
  console.log(`  \x1b[${CYAN}m🔭  星图 / 知识图谱  →  http://localhost:5176/\x1b[0m`)
  console.log('')
}, 3000)
