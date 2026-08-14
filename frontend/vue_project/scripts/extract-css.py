"""Extract CSS from DataAssistant.vue to external file and update the component import."""
import re

vue_path = "/root/data/globemind/frontend/vue_project/src/views/DataAssistant.vue"
css_path = "/root/data/globemind/frontend/vue_project/src/views/DataAssistant/style.css"

with open(vue_path, "r", encoding="utf-8") as f:
    content = f.read()

# Extract the CSS block
m = re.search(r'(<style scoped>)(.*?)(</style>)', content, re.DOTALL)
if not m:
    print("ERROR: Could not find <style scoped> block")
    exit(1)

css_content = m.group(2).strip() + "\n"

# Write CSS to external file
import os
os.makedirs(os.path.dirname(css_path), exist_ok=True)
with open(css_path, "w", encoding="utf-8") as f:
    f.write(css_content)

# Replace inline CSS with import
new_content = content.replace(
    m.group(0),
    '<style src="./DataAssistant/style.css" scoped></style>'
)

with open(vue_path, "w", encoding="utf-8") as f:
    f.write(new_content)

old_lines = content.count("\n")
new_lines = new_content.count("\n")
print(f"Extracted CSS: {len(css_content.splitlines())} lines")
print(f"DataAssistant.vue: {old_lines} → {new_lines} lines (removed {old_lines - new_lines})")
print(f"Style saved to: {css_path}")
