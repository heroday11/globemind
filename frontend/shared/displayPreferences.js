export const DISPLAY_PREFERENCES_VERSION = 1;
export const DISPLAY_PREFERENCES_STORAGE_PREFIX =
  "globemind_display_preferences_v1";
export const DISPLAY_PREFERENCES_EVENT = "globemind:display-preferences";

export const FONT_SIZE_OFFSET_MIN = -2;
export const FONT_SIZE_OFFSET_MAX = 3;

const SYSTEM_SANS =
  'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif';
const CJK_SANS =
  '"Noto Sans CJK SC", "Source Han Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif';
const CJK_SERIF =
  '"Noto Serif CJK SC", "Source Han Serif SC", "Songti SC", SimSun, Georgia, serif';
const SYSTEM_MONO =
  'ui-monospace, "SFMono-Regular", Menlo, Monaco, Consolas, "Liberation Mono", monospace';

export const FONT_FAMILY_OPTIONS = Object.freeze([
  Object.freeze({
    value: "system",
    label: "系统字体",
    description: "跟随当前设备，适合日常使用",
    sample: "Aa 数据研判",
  }),
  Object.freeze({
    value: "sans",
    label: "中文黑体",
    description: "优先使用思源或苹方，结构清晰",
    sample: "Aa 数据研判",
  }),
  Object.freeze({
    value: "serif",
    label: "阅读宋体",
    description: "长文与报告阅读更有书面感",
    sample: "Aa 数据研判",
  }),
]);

export const LINE_HEIGHT_OPTIONS = Object.freeze([
  Object.freeze({
    value: "compact",
    label: "紧凑",
    description: "同屏显示更多信息",
  }),
  Object.freeze({
    value: "standard",
    label: "标准",
    description: "平衡信息量与阅读节奏",
  }),
  Object.freeze({
    value: "relaxed",
    label: "宽松",
    description: "段落更舒展，适合长时间阅读",
  }),
]);

const FONT_STACKS = Object.freeze({
  system: Object.freeze({
    sans: SYSTEM_SANS,
    serif: CJK_SERIF,
    mono: SYSTEM_MONO,
  }),
  sans: Object.freeze({ sans: CJK_SANS, serif: CJK_SERIF, mono: SYSTEM_MONO }),
  serif: Object.freeze({
    sans: CJK_SERIF,
    serif: CJK_SERIF,
    mono: SYSTEM_MONO,
  }),
});

const LINE_HEIGHT_OFFSETS = Object.freeze({
  compact: Object.freeze({ unitless: -0.08, px: -1 }),
  standard: Object.freeze({ unitless: 0, px: 0 }),
  relaxed: Object.freeze({ unitless: 0.12, px: 2 }),
});

const ROOT_FONT_SIZES = Object.freeze({
  "-2": 14.5,
  "-1": 15.25,
  0: 16,
  1: 16.5,
  2: 17.25,
  3: 18,
});

export const DEFAULT_DISPLAY_PREFERENCES = Object.freeze({
  version: DISPLAY_PREFERENCES_VERSION,
  fontFamily: "system",
  fontSizeOffset: 0,
  lineHeight: "standard",
});

function safeStorage(storage) {
  return storage && typeof storage.getItem === "function" ? storage : null;
}

function safeAccountIdentity(storage) {
  const target = safeStorage(storage);
  if (!target) return "device";
  try {
    const currentUser = JSON.parse(target.getItem("current_user") || "null");
    const identity =
      currentUser?.id ?? currentUser?.user_id ?? currentUser?.username;
    if (
      identity === undefined ||
      identity === null ||
      String(identity).trim() === ""
    )
      return "device";
    return encodeURIComponent(String(identity).trim()).slice(0, 160);
  } catch {
    return "device";
  }
}

export function resolveDisplayPreferencesStorageKey(
  storage = globalThis.localStorage,
) {
  return `${DISPLAY_PREFERENCES_STORAGE_PREFIX}:${safeAccountIdentity(storage)}`;
}

export function normalizeDisplayPreferences(value = {}) {
  const source =
    value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const fontFamily = Object.prototype.hasOwnProperty.call(
    FONT_STACKS,
    source.fontFamily,
  )
    ? source.fontFamily
    : DEFAULT_DISPLAY_PREFERENCES.fontFamily;
  const lineHeight = Object.prototype.hasOwnProperty.call(
    LINE_HEIGHT_OFFSETS,
    source.lineHeight,
  )
    ? source.lineHeight
    : DEFAULT_DISPLAY_PREFERENCES.lineHeight;
  const rawOffset = Number(source.fontSizeOffset);
  const fontSizeOffset = Number.isFinite(rawOffset)
    ? Math.min(
        FONT_SIZE_OFFSET_MAX,
        Math.max(FONT_SIZE_OFFSET_MIN, Math.round(rawOffset)),
      )
    : DEFAULT_DISPLAY_PREFERENCES.fontSizeOffset;

  return {
    version: DISPLAY_PREFERENCES_VERSION,
    fontFamily,
    fontSizeOffset,
    lineHeight,
  };
}

export function loadDisplayPreferences(
  storage = globalThis.localStorage,
  storageKey = resolveDisplayPreferencesStorageKey(storage),
) {
  const target = safeStorage(storage);
  if (!target) return { ...DEFAULT_DISPLAY_PREFERENCES };
  try {
    return normalizeDisplayPreferences(
      JSON.parse(target.getItem(storageKey) || "null"),
    );
  } catch {
    return { ...DEFAULT_DISPLAY_PREFERENCES };
  }
}

export function saveDisplayPreferences(
  value,
  storage = globalThis.localStorage,
  storageKey = resolveDisplayPreferencesStorageKey(storage),
) {
  const normalized = normalizeDisplayPreferences(value);
  const target = safeStorage(storage);
  if (!target || typeof target.setItem !== "function") return normalized;
  try {
    target.setItem(storageKey, JSON.stringify(normalized));
  } catch {
    // A blocked or full localStorage must not prevent the live preference from applying.
  }
  return normalized;
}

export function applyDisplayPreferences(
  value,
  root = globalThis.document?.documentElement,
) {
  const normalized = normalizeDisplayPreferences(value);
  if (!root?.style || typeof root.style.setProperty !== "function")
    return normalized;

  const stacks = FONT_STACKS[normalized.fontFamily];
  const lineHeight = LINE_HEIGHT_OFFSETS[normalized.lineHeight];
  root.style.setProperty("--gm-font-sans", stacks.sans);
  root.style.setProperty("--gm-font-serif", stacks.serif);
  root.style.setProperty("--gm-font-mono", stacks.mono);
  root.style.setProperty(
    "--gm-root-font-size",
    `${ROOT_FONT_SIZES[normalized.fontSizeOffset]}px`,
  );
  root.style.setProperty("--gm-leading", String(lineHeight.unitless));
  root.style.setProperty("--gm-leading-px", `${lineHeight.px}px`);

  if (root.dataset) {
    root.dataset.gmFontFamily = normalized.fontFamily;
    root.dataset.gmFontSize = String(normalized.fontSizeOffset);
    root.dataset.gmLineHeight = normalized.lineHeight;
  }

  return normalized;
}

export function displayPreferencesEqual(left, right) {
  const a = normalizeDisplayPreferences(left);
  const b = normalizeDisplayPreferences(right);
  return (
    a.fontFamily === b.fontFamily &&
    a.fontSizeOffset === b.fontSizeOffset &&
    a.lineHeight === b.lineHeight
  );
}
