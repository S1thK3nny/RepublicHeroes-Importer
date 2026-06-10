"""Headless smoke test for the Republic Heroes MDL importer.

Run with:
  blender --background --factory-startup --python tests/run_blender_test.py

Builds a minimal synthetic .mdl/.mdg/.min.bin set (one triangle, one bone,
one material referencing the real sample texture), imports it through the
addon operator, and asserts on the resulting scene.
"""
import importlib
import os
import shutil
import struct
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SAMPLE_TEX = os.path.join(REPO, "tests", "samples",
                          "w_proximitymine_c.mktx.tex")
TMP = os.path.join(REPO, "tests", "_tmp")

if not os.path.exists(SAMPLE_TEX):
    print("SKIP: sample game asset not present (tests/samples/ holds "
          "copyrighted textures that are not redistributed). Drop the "
          "game's .mktx.tex files there to run this test.")
    sys.exit(0)

sys.path.insert(0, REPO)
addon = importlib.import_module("io_import_republicheroes_mdl")
addon.register()

import bpy  # noqa: E402


def ints(*values):
    return struct.pack("<{}i".format(len(values)), *values)


def build_synthetic_mdl():
    buf = bytearray()
    buf += b"MDL\x00"                                    # 0: magic
    v0 = [0] * 19
    v0[1] = 1   # material count
    v0[2] = 1   # ints per section-2 row
    v0[4] = 1   # vertex info count
    v0[10] = 1  # bone map count
    buf += ints(*v0)                                     # 4
    buf += struct.pack("<8f", *([0.0] * 8))              # 80
    v1 = [0] * 12
    v1[1] = 160  # section 1: materials
    v1[2] = 172  # section 2: index ranges
    v1[4] = 240  # section 4: vertex layout
    v1[5] = 384  # section 5: index buffer info
    v1[6] = 400  # section 6: skeleton
    v1[9] = 616  # section 9: bone maps
    buf += ints(*v1)                                     # 112
    assert len(buf) == 160

    buf += ints(164)                                     # 160: ptr to mat name
    buf += b"testmat\x00"                                # 164
    assert len(buf) == 172

    buf += ints(176)                                     # 172: section-2 row
    # 176: indices block: i(3) chain(next=0), H(2) skinID=0, i(12) ranges
    buf += ints(0, 0, 0)
    buf += struct.pack("<2H", 0, 0)
    v4 = [0] * 12
    v4[0] = 0  # vstart
    v4[1] = 0  # istart
    v4[2] = 3  # icount
    v4[3] = 3  # vcount
    buf += ints(*v4)
    assert len(buf) == 240

    # 240: vertex info entry: i(8)
    v2 = [0] * 8
    v2[0] = 272  # offset of layout block
    v2[1] = 16   # vertex data offset in .mdg
    v2[3] = 3    # vertex count
    buf += ints(*v2)
    buf += ints(288, 2, 20, 0)                           # 272: layout block
    buf += b"position".ljust(32, b"\x00") + ints(0, 0, 0, 0)   # 288
    buf += b"uv".ljust(32, b"\x00") + ints(12, 0, 0, 0)        # 336
    assert len(buf) == 384

    buf += ints(76, 0, 0, 3)                             # 384: indices info
    assert len(buf) == 400

    # 400: skeleton: A=i(10) with A[1]=13, base at A[0]
    a = [0] * 10
    a[0] = 440
    a[1] = 13
    buf += ints(*a)
    d = [0] * 10
    d[0] = 1    # bone count
    d[1] = 40   # bone entries offset (rel. base)
    d[2] = 112  # matrices offset (rel. base)
    buf += ints(*d)                                      # 440 (= base)
    c = [0] * 8
    c[4] = -1   # parent
    c[6] = 72   # name offset (rel. base) -> 512
    buf += ints(*c)                                      # 480
    buf += b"root\x00".ljust(40, b"\x00")                # 512..552
    identity = (1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                0.0, 0.0, 0.0, 1.0)
    buf += struct.pack("<16f", *identity)                # 552
    assert len(buf) == 616

    buf += struct.pack("<B", 1) + b"\x00"                # 616: bone map
    buf += b"\x00" * 255                                 # pad entry to 257
    return bytes(buf)


def build_synthetic_mdg():
    buf = bytearray()
    buf += b"MDG\x00" + ints(0, 0, 0)                    # 16-byte header
    vdata = [
        (0.0, 0.0, 0.0, 0.0, 0.0),
        (1.0, 0.0, 0.0, 1.0, 0.0),
        (0.0, 1.0, 0.0, 0.0, 1.0),
    ]
    for x, y, z, u, v in vdata:                          # stride 20
        buf += struct.pack("<5f", x, y, z, u, v)
    assert len(buf) == 76
    buf += struct.pack("<3H", 0, 1, 2)                   # indices
    return bytes(buf)


def build_synthetic_min_bin():
    buf = bytearray()
    v = [0] * 21
    v[5] = 200  # string base
    v[6] = 84   # offset of v1 block
    buf += ints(*v)                                      # 0..84
    v1 = [0] * 29
    v1[1] = 8   # diffuse name at v[5]+8 = 208
    buf += ints(*v1)                                     # 84..200
    buf += b"\x00" * 8                                   # 200..208
    buf += b"w_proximitymine_c\x00"
    return bytes(buf)


def main():
    if os.path.isdir(TMP):
        shutil.rmtree(TMP)
    os.makedirs(TMP)

    with open(os.path.join(TMP, "synth.mdl"), "wb") as f:
        f.write(build_synthetic_mdl())
    with open(os.path.join(TMP, "synth.mdg"), "wb") as f:
        f.write(build_synthetic_mdg())
    with open(os.path.join(TMP, "testmat.min.bin"), "wb") as f:
        f.write(build_synthetic_min_bin())
    shutil.copy(SAMPLE_TEX, os.path.join(TMP, "w_proximitymine_c.mktx.tex"))

    result = bpy.ops.import_scene.republic_heroes_mdl(
        filepath=os.path.join(TMP, "synth.mdl"))
    assert result == {"FINISHED"}, result

    # --- mesh ---
    obj = bpy.data.objects.get("synth-0")
    assert obj is not None, "mesh object missing"
    mesh = obj.data
    assert len(mesh.vertices) == 3, len(mesh.vertices)
    assert len(mesh.polygons) == 1, len(mesh.polygons)

    # --- UVs (flipped: uv (0,0) -> (0,1)) ---
    uv_layer = mesh.uv_layers.active
    assert uv_layer is not None, "no uv layer"
    for loop in mesh.loops:
        if loop.vertex_index == 0:
            uv = tuple(uv_layer.data[loop.index].uv)
            assert abs(uv[0]) < 1e-6 and abs(uv[1] - 1.0) < 1e-6, uv

    # --- skeleton ---
    arm_obj = bpy.data.objects.get("synth-armature")
    assert arm_obj is not None, "armature missing"
    assert "root" in arm_obj.data.bones, list(arm_obj.data.bones.keys())
    assert obj.parent == arm_obj
    assert any(m.type == "ARMATURE" and m.object == arm_obj
               for m in obj.modifiers)

    # --- material + texture conversion ---
    assert len(mesh.materials) == 1
    mat = mesh.materials[0]
    assert mat.name == "testmat", mat.name
    teximg_nodes = [n for n in mat.node_tree.nodes
                    if n.type == "TEX_IMAGE" and n.image]
    assert teximg_nodes, "no image texture node"
    img = teximg_nodes[0].image

    dds_path = os.path.join(TMP, "w_proximitymine_c.mktx.dds")
    assert os.path.exists(dds_path), "dds not written"
    # Expected dimensions from the .tex footer.
    with open(SAMPLE_TEX, "rb") as f:
        tex = f.read()
    w, h = struct.unpack_from("<2H", tex, len(tex) - 44)
    assert tuple(img.size) == (w, h), (tuple(img.size), (w, h))

    print("TEST OK: mesh/uv/skeleton/material/texture all verified "
          "({}x{} {})".format(w, h, os.path.basename(dds_path)))


main()
