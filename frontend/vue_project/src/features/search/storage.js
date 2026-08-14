export const SEARCH_THEME_NAME_KEY = 'data_search_theme_name_v1'
export const SEARCH_THEME_LIST_KEY = 'data_search_themes_list_v1'
export const DEFAULT_SEARCH_FOLDER_NAME = '默认工作夹'
export const LEGACY_DEFAULT_THEME_NAME = '新闻分析主题'

export function normalizeSearchFolderName(name) {
  return String(name || '').trim() || DEFAULT_SEARCH_FOLDER_NAME
}

export function displaySearchFolderName(name) {
  const normalized = normalizeSearchFolderName(name)
  return normalized === LEGACY_DEFAULT_THEME_NAME ? DEFAULT_SEARCH_FOLDER_NAME : normalized
}

export function normalizeSearchThemeList(list, currentTheme) {
  const normalized = [
    ...new Set((Array.isArray(list) ? list : []).map((item) => String(item).trim()).filter(Boolean)),
  ]
  const current = normalizeSearchFolderName(currentTheme)
  if (!normalized.includes(current)) normalized.push(current)
  return normalized
}

export function createSearchThemeName(themeNames, now = new Date()) {
  const base = `工作文件夹 ${now.getMonth() + 1}-${now.getDate()}`
  let name = base
  let suffix = 1
  while (themeNames.includes(name)) {
    name = `${base} (${suffix++})`
  }
  return name
}

export function renameSearchThemeList(themeNames, previousName, nextName) {
  const previous = normalizeSearchFolderName(previousName)
  const next = normalizeSearchFolderName(nextName)
  const index = themeNames.indexOf(previous)
  if (index >= 0) {
    const copy = [...themeNames]
    copy[index] = next
    return [...new Set(copy.map((item) => String(item).trim()).filter(Boolean))]
  }
  return themeNames.includes(next) ? [...themeNames] : [...themeNames, next]
}

export function createSearchThemeStorage(
  storage = globalThis.localStorage,
  {
    nameKey = SEARCH_THEME_NAME_KEY,
    listKey = SEARCH_THEME_LIST_KEY,
  } = {},
) {
  return Object.freeze({
    readCurrent() {
      const current = storage.getItem(nameKey)
      return current && current.trim() ? current.trim() : DEFAULT_SEARCH_FOLDER_NAME
    },

    saveCurrent(name) {
      storage.setItem(nameKey, name)
    },

    saveThemes(themeNames) {
      storage.setItem(listKey, JSON.stringify(themeNames))
    },

    loadThemes(currentTheme) {
      let list = []
      try {
        const raw = storage.getItem(listKey)
        if (raw) list = JSON.parse(raw)
      } catch {
        list = []
      }
      if (!Array.isArray(list) || list.length === 0) {
        const storedCurrent = (storage.getItem(nameKey) || DEFAULT_SEARCH_FOLDER_NAME).trim()
        list = [storedCurrent || DEFAULT_SEARCH_FOLDER_NAME]
      }
      const normalized = normalizeSearchThemeList(list, currentTheme)
      this.saveThemes(normalized)
      return normalized
    },
  })
}
