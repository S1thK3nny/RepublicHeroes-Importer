"""Convert the real sample .mktx.tex textures and verify Blender loads them.

Run with:
  blender --background --factory-startup --python tests/run_texture_test.py
"""
import glob
import importlib
import os
import struct
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLES = os.path.join(REPO, "tests", "samples")
TMP = os.path.join(REPO, "tests", "_tmp_tex")

sys.path.insert(0, REPO)
addon = importlib.import_module("io_import_republicheroes_mdl")

import bpy  # noqa: E402

tex_files = glob.glob(os.path.join(glob.escape(SAMPLES), "*.mktx.tex"))
if not tex_files:
    print("SKIP: no sample textures in tests/samples/ (copyrighted game "
          "assets are not redistributed). Drop the game's .mktx.tex files "
          "there to run this test.")
    sys.exit(0)

os.makedirs(TMP, exist_ok=True)

ok = 0
for tex_path in sorted(tex_files):
    name = os.path.basename(tex_path).replace(".mktx.tex", "")
    with open(tex_path, "rb") as f:
        data = f.read()
    w, h, = struct.unpack_from("<2H", data, len(data) - 44)
    fmt_id = struct.unpack_from("<22H", data, len(data) - 44)[7]
    dds_path = os.path.join(TMP, name + ".dds")
    result = addon.convert_mktx(tex_path, dds_path)
    assert result, "conversion failed: " + name
    img = bpy.data.images.load(dds_path)
    assert tuple(img.size) == (w, h), (name, tuple(img.size), (w, h))
    fourcc = addon._dds_fourcc(dds_path).decode("ascii", "replace")
    print("OK {:36s} {:4d}x{:<4d} fmt_id={:<2d} -> {}".format(
        name, w, h, fmt_id, fourcc))
    bpy.data.images.remove(img)
    ok += 1

print("TEST OK: {} textures converted and loaded".format(ok))
