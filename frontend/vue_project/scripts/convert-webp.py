"""Convert large PNG/JPG assets to WebP in public/ (in-place replacement)."""
import os
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    import sys
    print("Pillow not installed. Run: pip install Pillow", file=sys.stderr)
    sys.exit(1)

PUBLIC_DIR = Path(__file__).resolve().parent.parent / "public"
MIN_SIZE = 50 * 1024  # 50 KB threshold

converted = []
skipped = []

for ext in ("*.png", "*.jpg", "*.jpeg", "*.PNG", "*.JPG", "*.JPEG"):
    for fp in sorted(PUBLIC_DIR.rglob(ext)):
        if fp.stat().st_size < MIN_SIZE:
            skipped.append(fp)
            continue
        webp_path = fp.with_suffix(".webp")
        if webp_path.exists():
            # Already converted; skip but note it
            if webp_path.stat().st_mtime >= fp.stat().st_mtime:
                skipped.append(fp)
                continue
        try:
            img = Image.open(fp).convert("RGB")
            img.save(webp_path, "WEBP", quality=82, method=6)
            # Preserve original mtime so build tools can compare
            stat = fp.stat()
            os.utime(webp_path, (stat.st_atime, stat.st_mtime))
            old_size = fp.stat().st_size
            new_size = webp_path.stat().st_size
            saving = (1 - new_size / old_size) * 100
            converted.append((fp, old_size, new_size, saving))
        except Exception as e:
            print(f"[ERROR] {fp}: {e}")

# Report
total_old = sum(c[1] for c in converted)
total_new = sum(c[2] for c in converted)
print(f"Converted: {len(converted)} images")
for fp, old, new, pct in converted:
    rel = fp.relative_to(PUBLIC_DIR.parent)
    print(f"  {rel}  {old//1024}K → {new//1024}K  ({pct:.0f}% saved)")
if converted:
    print(f"Total: {total_old//1024}K → {total_new//1024}K ({total_old//1024 - total_new//1024}K saved, {(1-total_new/total_old)*100:.0f}%)")
print(f"Skipped (small or up-to-date): {len(skipped)} files")
