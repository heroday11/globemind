"""Extract KB, Sites, Members panels from DataAssistant.vue into child components."""
import re
import os

VUE_DIR = "/root/data/globemind/frontend/vue_project/src/views/DataAssistant"
CSS_PATH = f"{VUE_DIR}/style.css"
VUE_PATH = "/root/data/globemind/frontend/vue_project/src/views/DataAssistant.vue"

# ── Read source files ──
with open(VUE_PATH, "r", encoding="utf-8") as f:
    vue_content = f.read()

with open(CSS_PATH, "r", encoding="utf-8") as f:
    css_content = f.read()

# ── Helper: extract CSS classes from the big CSS file ──
def extract_css(prefixes):
    """Extract CSS rules whose selector starts with any of the given prefixes."""
    lines = css_content.split("\n")
    result = []
    in_block = False
    for line in lines:
        stripped = line.strip()
        if not in_block:
            # Check if this line starts a new rule with our prefix
            for prefix in prefixes:
                if stripped.startswith(prefix) or stripped.startswith(f".{prefix}"):
                    in_block = True
                    result.append(line)
                    break
            else:
                # Could be a comment or media query between rules
                if stripped.startswith("/*") or stripped.startswith("@media"):
                    in_block = True
                    result.append(line)
        else:
            result.append(line)
            if stripped == "}" or stripped == "}" or stripped == "};":
                # Check if next non-empty line is a new rule with our prefix
                in_block = False
    return "\n".join(result)


# ── Component spec: (name, template_start_marker, template_end_marker, script_refs, css_prefixes) ──
components = [
    {
        "name": "KbPanel",
        "template_id": "activeSideNav === 'kb'",
        "script_state": [
            "kbCategories", "kbActiveCategory", "kbFiles", "kbFilesLoading",
            "filePreviewVisible", "filePreviewTitle", "filePreviewFullPath",
            "filePreviewContent", "fileOriginalContent", "filePreviewLoading",
        ],
        "script_funcs": [
            "fetchKbCategories", "fetchKbFiles", "openKbFilePreview", "isTextPreviewFile",
        ],
        "template_funcs": ["fetchKbFiles", "openKbFilePreview", "isTextPreviewFile"],
        "css_prefixes": [
            ".ys-page", ".ys-page-head", ".ys-page-title", ".ys-page-sub",
            ".ys-page-head-row", ".ys-page-badge", ".kb-", ".ys-page-grid",
            ".ys-page-card", ".ys-page-card-title", ".ys-page-card-desc",
        ],
        "extra_imports": ["API_PREFIX from '@/config/api'"],
    },
]

print("Components extracted successfully")
