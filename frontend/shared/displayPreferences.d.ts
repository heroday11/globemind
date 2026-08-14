export interface DisplayPreferences {
  version: number;
  fontFamily: "system" | "sans" | "serif";
  fontSizeOffset: number;
  lineHeight: "compact" | "standard" | "relaxed";
}

export const DISPLAY_PREFERENCES_VERSION: number;
export const DISPLAY_PREFERENCES_STORAGE_PREFIX: string;
export const DISPLAY_PREFERENCES_EVENT: string;
export const FONT_SIZE_OFFSET_MIN: number;
export const FONT_SIZE_OFFSET_MAX: number;
export const DEFAULT_DISPLAY_PREFERENCES: Readonly<DisplayPreferences>;
export const FONT_FAMILY_OPTIONS: ReadonlyArray<
  Readonly<{
    value: DisplayPreferences["fontFamily"];
    label: string;
    description: string;
    sample: string;
  }>
>;
export const LINE_HEIGHT_OPTIONS: ReadonlyArray<
  Readonly<{
    value: DisplayPreferences["lineHeight"];
    label: string;
    description: string;
  }>
>;

export function resolveDisplayPreferencesStorageKey(
  storage?: Storage | null,
): string;
export function normalizeDisplayPreferences(
  value?: Partial<DisplayPreferences> | unknown,
): DisplayPreferences;
export function loadDisplayPreferences(
  storage?: Storage | null,
  storageKey?: string,
): DisplayPreferences;
export function saveDisplayPreferences(
  value: Partial<DisplayPreferences>,
  storage?: Storage | null,
  storageKey?: string,
): DisplayPreferences;
export function applyDisplayPreferences(
  value: Partial<DisplayPreferences>,
  root?: HTMLElement,
): DisplayPreferences;
export function displayPreferencesEqual(
  left: Partial<DisplayPreferences>,
  right: Partial<DisplayPreferences>,
): boolean;
