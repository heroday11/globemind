#!/usr/bin/env node

import { createHash } from 'node:crypto'
import { lstat, readFile, readdir, realpath, writeFile } from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'

const BUDGET_FIELDS = new Set([
  'assets_path',
  'index_path',
  'entry_prefix',
  'max_asset_files',
  'max_total_asset_bytes',
  'max_total_js_bytes',
  'max_total_css_bytes',
  'max_single_asset_bytes',
  'max_entry_js_bytes',
])

function fail(message) {
  throw new Error(message)
}

function parseArgs(argv) {
  const values = {}
  for (let index = 0; index < argv.length; index += 2) {
    const flag = argv[index]
    const value = argv[index + 1]
    if (!['--dist', '--config', '--output'].includes(flag) || !value) {
      fail('usage: check_frontend_budgets.mjs --dist DIR --config FILE --output FILE')
    }
    values[flag.slice(2)] = value
  }
  if (!values.dist || !values.config || !values.output) fail('missing required argument')
  return values
}

function safeRelative(value, label) {
  if (typeof value !== 'string' || !value || value.includes('\n') || value.includes('\r')) {
    fail(`${label} must be a non-empty relative path`)
  }
  if (path.isAbsolute(value) || value.split('/').includes('..')) fail(`${label} is unsafe`)
  return value
}

function validateBudget(name, value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) fail(`${name} budget is invalid`)
  if (Object.keys(value).length !== BUDGET_FIELDS.size) fail(`${name} budget field set is invalid`)
  for (const key of Object.keys(value)) if (!BUDGET_FIELDS.has(key)) fail(`${name} budget field is unknown: ${key}`)
  safeRelative(value.assets_path, `${name}.assets_path`)
  safeRelative(value.index_path, `${name}.index_path`)
  if (typeof value.entry_prefix !== 'string' || !value.entry_prefix.startsWith('/')) {
    fail(`${name}.entry_prefix is invalid`)
  }
  for (const [key, limit] of Object.entries(value)) {
    if (key.startsWith('max_') && (!Number.isSafeInteger(limit) || limit <= 0)) {
      fail(`${name}.${key} must be a positive integer`)
    }
  }
}

async function listFiles(root, relative = '') {
  const directory = path.join(root, relative)
  const entries = await readdir(directory, { withFileTypes: true })
  const files = []
  for (const entry of entries.sort((left, right) => left.name.localeCompare(right.name))) {
    const child = path.posix.join(relative.replaceAll('\\', '/'), entry.name)
    const absolute = path.join(root, child)
    const metadata = await lstat(absolute)
    if (metadata.isSymbolicLink()) fail(`asset tree contains symlink: ${child}`)
    if (metadata.isDirectory()) files.push(...(await listFiles(root, child)))
    else if (metadata.isFile()) files.push({ path: child, bytes: metadata.size })
  }
  return files
}

function entryAsset(indexHtml, prefix) {
  const scripts = [...indexHtml.matchAll(/<script\b([^>]*)>/gi)]
  const candidates = scripts
    .filter((match) => /\btype=["']module["']/i.test(match[1]))
    .map((match) => match[1].match(/\bsrc=["']([^"']+)["']/i)?.[1])
    .filter(Boolean)
    .map((value) => value.split(/[?#]/, 1)[0])
    .filter((value) => value.startsWith(prefix) && value.endsWith('.js'))
  if (candidates.length !== 1) fail(`expected exactly one module entry below ${prefix}`)
  return candidates[0].slice(1)
}

function overLimit(failures, name, metric, actual, maximum) {
  if (actual > maximum) failures.push({ surface: name, metric, actual, maximum })
}

async function inspectSurface(dist, name, budget) {
  validateBudget(name, budget)
  const assetsRoot = path.join(dist, budget.assets_path)
  const indexFile = path.join(dist, budget.index_path)
  const [files, indexHtml] = await Promise.all([
    listFiles(assetsRoot),
    readFile(indexFile, 'utf8'),
  ])
  const entryPath = entryAsset(indexHtml, budget.entry_prefix)
  const entry = files.find((item) => path.posix.join(budget.assets_path, item.path) === entryPath)
  if (!entry) fail(`${name} entry asset is absent from its asset tree: ${entryPath}`)

  // Legacy chunks are selected only by nomodule browsers. Keep the existing
  // budgets as modern-client payload guards and report the compatibility
  // footprint separately instead of weakening those limits.
  const legacyJs = files.filter((item) => /-legacy-[^/]+\.js$/.test(item.path))
  const budgetFiles = files.filter((item) => !legacyJs.includes(item))
  const js = budgetFiles.filter((item) => item.path.endsWith('.js'))
  const css = budgetFiles.filter((item) => item.path.endsWith('.css'))
  const sum = (items) => items.reduce((total, item) => total + item.bytes, 0)
  const largest = budgetFiles.reduce((current, item) => (item.bytes > current.bytes ? item : current), { path: '', bytes: 0 })
  const metrics = {
    asset_files: budgetFiles.length,
    total_asset_bytes: sum(budgetFiles),
    total_js_bytes: sum(js),
    total_css_bytes: sum(css),
    largest_asset: largest,
    entry_js: { path: entryPath, bytes: entry.bytes },
    legacy_js: {
      asset_files: legacyJs.length,
      total_bytes: sum(legacyJs),
    },
  }
  const failures = []
  overLimit(failures, name, 'asset_files', metrics.asset_files, budget.max_asset_files)
  overLimit(failures, name, 'total_asset_bytes', metrics.total_asset_bytes, budget.max_total_asset_bytes)
  overLimit(failures, name, 'total_js_bytes', metrics.total_js_bytes, budget.max_total_js_bytes)
  overLimit(failures, name, 'total_css_bytes', metrics.total_css_bytes, budget.max_total_css_bytes)
  overLimit(failures, name, 'single_asset_bytes', largest.bytes, budget.max_single_asset_bytes)
  overLimit(failures, name, 'entry_js_bytes', entry.bytes, budget.max_entry_js_bytes)
  return { metrics, limits: budget, failures }
}

async function main() {
  const args = parseArgs(process.argv.slice(2))
  const dist = await realpath(args.dist)
  const configPath = await realpath(args.config)
  const configBytes = await readFile(configPath)
  const config = JSON.parse(configBytes.toString('utf8'))
  if (!config || typeof config !== 'object' || Array.isArray(config)) fail('budget config is invalid')
  if (Object.keys(config).sort().join(',') !== 'financial,main,schema_version' || config.schema_version !== 1) {
    fail('budget config has an unsupported schema')
  }
  const surfaces = {}
  for (const name of ['main', 'financial']) surfaces[name] = await inspectSurface(dist, name, config[name])
  const failures = Object.values(surfaces).flatMap((surface) => surface.failures)
  const payload = {
    schema_version: 1,
    status: failures.length ? 'failed' : 'passed',
    config_sha256: createHash('sha256').update(configBytes).digest('hex'),
    surfaces,
    failures,
  }
  await writeFile(args.output, `${JSON.stringify(payload, null, 2)}\n`, { encoding: 'utf8', flag: 'wx' })
  process.stdout.write(`${JSON.stringify(payload)}\n`)
  if (failures.length) process.exitCode = 1
}

main().catch((error) => {
  process.stderr.write(`frontend budget error: ${error.message}\n`)
  process.exitCode = 2
})
