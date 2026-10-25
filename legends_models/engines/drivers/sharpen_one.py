# SPDX-License-Identifier: MIT
"""Driver: texture-only sharpen of one port actor with models-xml2 upscale.rewrite (unchanged)."""
import argparse, json, os, sys
sys.path.insert(0, os.environ['LEGENDS_ENGINE_TOOLS'])
from pathlib import Path
import upscale  # noqa: E402  (vendor/xml2/upscale.py)

ap = argparse.ArgumentParser()
ap.add_argument('--src', type=Path, required=True)
ap.add_argument('--dest', type=Path, required=True)
ap.add_argument('--report', type=Path, required=True)
ap.add_argument('--ai-model', type=Path)
a = ap.parse_args()
transform = upscale.AI(a.ai_model) if a.ai_model else upscale.baseline
info = upscale.rewrite(a.src, a.dest, transform)
info['method'] = 'Real-ESRGAN x4 (ONNX, optional)' if a.ai_model else '4x Lanczos + unsharp mask (default)'
a.report.write_text(json.dumps(info, indent=2))
print(json.dumps({k: v for k, v in info.items() if k != 'textures'}))
