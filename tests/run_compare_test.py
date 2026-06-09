"""Compare our .mktx.tex -> DDS conversion against the 2014 reference files.

Run with:
  blender --background --factory-startup --python tests/run_compare_test.py

For each sample that has a reference (.mktxout.tga / .mktxout.dds), convert
the .mktx.tex with the addon, load both in Blender, and report per-channel
mean absolute difference. Also dumps PNGs for visual inspection.
"""
import glob
import importlib
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(REPO, "tests", "samples")
TMP = os.path.join(REPO, "tests", "_tmp_cmp")

sys.path.insert(0, REPO)
addon = importlib.import_module("io_import_republicheroes_mdl")

import bpy  # noqa: E402
import numpy as np  # noqa: E402

os.makedirs(TMP, exist_ok=True)


def load_pixels(path):
    img = bpy.data.images.load(path)
    w, h = img.size
    px = np.array(img.pixels[:], dtype=np.float32).reshape(h, w, img.channels)
    return img, px


def save_png(img, name):
    img.filepath_raw = os.path.join(TMP, name + ".png")
    img.file_format = "PNG"
    img.save()


def mad(a, b):
    """Mean absolute difference of RGB, trying vertical flip too."""
    a, b = a[..., :3], b[..., :3]
    direct = float(np.abs(a - b).mean())
    flipped = float(np.abs(a[::-1] - b).mean())
    return min(direct, flipped), ("flipped" if flipped < direct else "direct")


refs = []
for tga in glob.glob(os.path.join(glob.escape(SAMPLES), "*.mktxout.tga")):
    tex = tga.replace(".mktxout.tga", ".mktx.tex")
    if os.path.exists(tex):
        refs.append((tex, tga))

assert refs, "no reference pairs found"

for tex_path, ref_path in sorted(refs):
    name = os.path.basename(tex_path).replace(".mktx.tex", "")
    dds_path = os.path.join(TMP, name + ".dds")
    assert addon.convert_mktx(tex_path, dds_path), name

    mine_img, mine = load_pixels(dds_path)
    ref_img, ref = load_pixels(ref_path)

    if addon._dds_fourcc(dds_path) == b"ATI2":
        # BC5: reconstruct Z exactly as the material node chain does.
        x = mine[..., 0] * 2.0 - 1.0
        y = mine[..., 1] * 2.0 - 1.0
        z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, 1.0))
        mine = mine.copy()
        mine[..., 2] = (z + 1.0) * 0.5

    if mine.shape[:2] != ref.shape[:2]:
        print("SKIP {:36s} size mismatch: mine {} ref {}".format(
            name, mine.shape[:2], ref.shape[:2]))
        continue

    diff, orient = mad(mine, ref)
    print("{} {:36s} MAD={:.4f} ({}) [0..1 scale, 8-bit step={:.4f}]".format(
        "OK  " if diff < 0.02 else "DIFF", name, diff, orient, 1 / 255))

    save_png(mine_img, name + ".mine")
    save_png(ref_img, name + ".ref")
    bpy.data.images.remove(mine_img)
    bpy.data.images.remove(ref_img)

print("COMPARE DONE")
