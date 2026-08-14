const FONT_SIZE_KEYWORDS = new Set([
  "inherit",
  "initial",
  "unset",
  "revert",
  "revert-layer",
  "xx-small",
  "x-small",
  "small",
  "medium",
  "large",
  "x-large",
  "xx-large",
  "xxx-large",
  "smaller",
  "larger",
  "math",
]);

const GLOBAL_LINE_HEIGHT_TOKEN = "--gm-leading";
const GLOBAL_LINE_HEIGHT_PX_TOKEN = "--gm-leading-px";
const PX_VALUE_PATTERN = /(-?(?:\d+|\d*\.\d+))px\b/gi;

function isFontFaceDeclaration(declaration) {
  return (
    declaration.parent?.type === "atrule" &&
    declaration.parent.name?.toLowerCase() === "font-face"
  );
}

function transformFontSize(value) {
  const normalized = value.trim();
  if (
    !normalized ||
    normalized === "0" ||
    FONT_SIZE_KEYWORDS.has(normalized.toLowerCase())
  ) {
    return value;
  }

  return normalized.replace(PX_VALUE_PATTERN, (_, rawValue) => {
    const pixels = Number(rawValue);
    if (!Number.isFinite(pixels) || pixels === 0) return "0";
    const rem = Math.round((pixels / 16) * 10000) / 10000;
    return `${String(rem).replace(/^0\./, ".").replace(/^-0\./, "-.")}rem`;
  });
}

function transformLineHeight(value) {
  const normalized = value.trim();
  if (
    !normalized ||
    normalized === "0" ||
    normalized.includes(GLOBAL_LINE_HEIGHT_TOKEN)
  )
    return value;
  if (/^(?:\d+|\d*\.\d+)$/.test(normalized)) {
    return `calc(${normalized} + var(${GLOBAL_LINE_HEIGHT_TOKEN}))`;
  }
  if (/^(?:\d+|\d*\.\d+)px$/i.test(normalized)) {
    return `calc(${normalized} + var(${GLOBAL_LINE_HEIGHT_PX_TOKEN}))`;
  }
  return value;
}

function classifyFontFamily(value) {
  const normalized = value.trim();
  const lower = normalized.toLowerCase();
  if (
    !normalized ||
    lower === "inherit" ||
    lower === "initial" ||
    lower === "unset" ||
    lower === "revert" ||
    lower.includes("--gm-font-")
  ) {
    return value;
  }

  if (
    /mono|consolas|courier|menlo|monaco|cascadia|fira code|jetbrains|sfmono|liberation mono/.test(
      lower,
    )
  ) {
    return "var(--gm-font-mono)";
  }

  if (
    lower.includes("--serif") ||
    (!lower.includes("sans-serif") &&
      /serif|songti|simsun|fangsong|georgia|times new roman/.test(lower))
  ) {
    return "var(--gm-font-serif)";
  }

  return "var(--gm-font-sans)";
}

function ruleContext(rule) {
  const context = [];
  let parent = rule.parent;
  while (parent && parent.type !== "root") {
    if (parent.type === "atrule")
      context.unshift(`@${parent.name} ${parent.params}`);
    else if (parent.type === "rule") context.unshift(parent.selector);
    parent = parent.parent;
  }
  return context.join("\u0000");
}

function removeOverriddenDeclarations(root) {
  const rules = [];
  root.walkRules((rule) => rules.push(rule));
  const seenBySelector = new Map();

  for (const rule of rules.reverse()) {
    const key = `${ruleContext(rule)}\u0001${rule.selector}`;
    const seen = seenBySelector.get(key) ?? new Map();
    seenBySelector.set(key, seen);

    for (const node of [...rule.nodes].reverse()) {
      if (node.type !== "decl") continue;
      const laterImportance = seen.get(node.prop);
      const isImportant = Boolean(node.important);
      if (
        laterImportance === true ||
        (laterImportance === false && !isImportant)
      ) {
        node.remove();
        continue;
      }
      if (laterImportance === undefined || isImportant)
        seen.set(node.prop, isImportant);
    }

    if (rule.nodes.length === 0) rule.remove();
  }
}

export function createTypographyPreferencesPlugin() {
  return {
    postcssPlugin: "globemind-typography-preferences",
    Declaration(declaration) {
      if (isFontFaceDeclaration(declaration)) return;
      const property = declaration.prop.toLowerCase();
      if (property === "font-size") {
        declaration.value = transformFontSize(declaration.value);
      } else if (property === "line-height") {
        declaration.value = transformLineHeight(declaration.value);
      } else if (property === "font-family") {
        declaration.value = classifyFontFamily(declaration.value);
      }
    },
    OnceExit(root) {
      removeOverriddenDeclarations(root);
    },
  };
}

export const typographyPreferencesPlugin = createTypographyPreferencesPlugin();
