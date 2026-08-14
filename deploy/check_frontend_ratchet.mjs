#!/usr/bin/env node
import { spawnSync } from 'node:child_process'
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const baselinePath = path.join(projectRoot, 'quality', 'frontend-ratchet.json')
const baselineBytes = fs.readFileSync(baselinePath)
const baseline = JSON.parse(baselineBytes.toString('utf8'))
const outputIndex = process.argv.indexOf('--output')
const outputPath = outputIndex >= 0 ? process.argv[outputIndex + 1] : null

if (baseline.schema_version !== 1) throw new Error('unsupported frontend ratchet schema')
if (outputIndex >= 0 && !outputPath) throw new Error('--output requires a path')

function run(command, args, cwd) {
  const result = spawnSync(command, args, {
    cwd,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  })
  if (result.error) throw result.error
  return result
}

const vueRoot = path.join(projectRoot, 'frontend', 'vue_project')
const eslintBinary = path.join(vueRoot, 'node_modules', '.bin', 'eslint')
const eslint = run(
  eslintBinary,
  [...baseline.vue_eslint.paths, '--format', 'json'],
  vueRoot,
)
let eslintRows
try {
  eslintRows = JSON.parse(eslint.stdout)
} catch (error) {
  throw new Error(`cannot parse ESLint output (exit ${eslint.status}): ${eslint.stderr}`, {
    cause: error,
  })
}
const eslintActual = {
  errors: eslintRows.reduce((total, row) => total + row.errorCount, 0),
  warnings: eslintRows.reduce((total, row) => total + row.warningCount, 0),
  fatal_errors: eslintRows.reduce((total, row) => total + row.fatalErrorCount, 0),
}
const eslintLimits = {
  errors: baseline.vue_eslint.max_errors,
  warnings: baseline.vue_eslint.max_warnings,
  fatal_errors: baseline.vue_eslint.max_fatal_errors,
}
const eslintPassed =
  eslint.status <= 1 &&
  Object.entries(eslintActual).every(([name, count]) => count <= eslintLimits[name])

const financialRoot = path.join(projectRoot, 'frontend', 'financial-terminal')
const tscBinary = path.join(financialRoot, 'node_modules', '.bin', 'tsc')
const typescript = run(tscBinary, ['--noEmit', '--pretty', 'false'], financialRoot)
const typescriptOutput = `${typescript.stdout}\n${typescript.stderr}`
const typescriptErrors = (typescriptOutput.match(/error TS\d+:/g) || []).length
const typescriptPassed =
  (typescript.status === 0 || typescriptErrors > 0) &&
  typescriptErrors <= baseline.financial_typescript.max_errors

const payload = {
  schema_version: 1,
  status: eslintPassed && typescriptPassed ? 'passed' : 'failed',
  baseline_sha256: crypto.createHash('sha256').update(baselineBytes).digest('hex'),
  vue_eslint: {
    status: eslintPassed ? 'passed' : 'failed',
    actual: eslintActual,
    maximum: eslintLimits,
    baseline_update_recommended:
      eslintActual.errors < eslintLimits.errors || eslintActual.warnings < eslintLimits.warnings,
  },
  financial_typescript: {
    status: typescriptPassed ? 'passed' : 'failed',
    actual_errors: typescriptErrors,
    maximum_errors: baseline.financial_typescript.max_errors,
    baseline_update_recommended: typescriptErrors < baseline.financial_typescript.max_errors,
  },
}

const serialized = `${JSON.stringify(payload, null, 2)}\n`
if (outputPath) {
  fs.mkdirSync(path.dirname(path.resolve(outputPath)), { recursive: true })
  fs.writeFileSync(outputPath, serialized)
}
process.stdout.write(serialized)
process.exit(payload.status === 'passed' ? 0 : 1)
