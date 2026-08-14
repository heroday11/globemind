import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const vueRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const financialRoot = path.resolve(vueRoot, '..', 'financial-terminal')
const outputRoot = path.resolve(
  process.env.GLOBEMIND_FRONTEND_OUT_DIR || path.join(vueRoot, 'dist'),
)
const forbiddenOutputs = [path.join(vueRoot, 'src'), path.join(vueRoot, 'public')]

for (const forbidden of forbiddenOutputs) {
  if (outputRoot === forbidden || outputRoot.startsWith(`${forbidden}${path.sep}`)) {
    throw new Error(`refusing to build into frontend source directory: ${outputRoot}`)
  }
}

function run(command, args, cwd, extraEnv = {}) {
  const result = spawnSync(command, args, {
    cwd,
    env: { ...process.env, ...extraEnv },
    stdio: 'inherit',
  })
  if (result.error) throw result.error
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed with exit code ${result.status}`)
  }
}

fs.mkdirSync(path.dirname(outputRoot), { recursive: true })
const temporaryRoot = fs.mkdtempSync(path.join(path.dirname(outputRoot), '.frontend-build.'))
const financialOutput = path.join(temporaryRoot, 'financial-terminal')

try {
  run('npm', ['run', 'build'], financialRoot, {
    FINANCIAL_TERMINAL_OUT_DIR: financialOutput,
  })
  run('npm', ['exec', '--', 'vite', 'build'], vueRoot, {
    VITE_BUILD_ID: process.env.VITE_BUILD_ID || 'local',
    VITE_OUT_DIR: outputRoot,
  })

  const financialTarget = path.join(outputRoot, 'fin-terminal')
  fs.rmSync(financialTarget, { recursive: true, force: true })
  fs.cpSync(financialOutput, financialTarget, { recursive: true })

  for (const required of [
    path.join(outputRoot, 'index.html'),
    path.join(financialTarget, 'index.html'),
  ]) {
    if (!fs.existsSync(required)) throw new Error(`missing frontend entry: ${required}`)
  }
  console.log(`[build-release] frontend output: ${outputRoot}`)
} finally {
  fs.rmSync(temporaryRoot, { recursive: true, force: true })
}
