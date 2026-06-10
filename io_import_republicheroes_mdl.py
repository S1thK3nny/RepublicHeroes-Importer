bl_info = {
    "name": "Star Wars The Clone Wars: Republic Heroes (.mdl)",
    "author": "RepublicHeroes-Importer project",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "File > Import > Republic Heroes Model (.mdl)",
    "description": "Import .mdl/.mdg models from Star Wars The Clone Wars: Republic Heroes (PC)",
    "category": "Import-Export",
}

import io
import math
import os
import struct

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper
from mathutils import Matrix, Quaternion, Vector


# ---------------------------------------------------------------------------
# Binary reading
# ---------------------------------------------------------------------------

class BinaryReader:
    """Little-endian binary reader over an in-memory copy of the file."""

    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = f.read()
        self.buf = io.BytesIO(self.data)
        self.path = path
        self.dirname = os.path.dirname(path)
        self.basename = os.path.basename(path).split(".")[0]

    def _unpack(self, fmt_char, size, n):
        return struct.unpack("<" + fmt_char * n, self.buf.read(size * n))

    def i(self, n):
        return self._unpack("i", 4, n)

    def I(self, n):
        return self._unpack("I", 4, n)

    def h(self, n):
        return self._unpack("h", 2, n)

    def H(self, n):
        return self._unpack("H", 2, n)

    def b(self, n):
        return self._unpack("b", 1, n)

    def B(self, n):
        return self._unpack("B", 1, n)

    def f(self, n):
        return self._unpack("f", 4, n)

    def word(self, length):
        """Read a fixed-size zero-padded string field."""
        raw = self.buf.read(length)
        return raw.split(b"\x00", 1)[0].decode("latin-1")

    def find(self, terminator=b"\x00"):
        """Read a string up to (and consuming) the terminator byte."""
        if isinstance(terminator, str):
            terminator = terminator.encode("latin-1")
        start = self.buf.tell()
        idx = self.data.find(terminator, start)
        if idx < 0:
            idx = len(self.data)
        s = self.data[start:idx]
        self.buf.seek(idx + len(terminator))
        return s.decode("latin-1")

    def read(self, count):
        return self.buf.read(count)

    def seek(self, offset, whence=0):
        self.buf.seek(offset, whence)

    def tell(self):
        return self.buf.tell()

    def fileSize(self):
        return len(self.data)


# ---------------------------------------------------------------------------
# .mktx.tex -> .dds texture conversion
# ---------------------------------------------------------------------------

def _dds_mip_count(width, height, block_size, data_len):
    count = 0
    total = 0
    w, h = width, height
    while True:
        size = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block_size
        if total + size > data_len:
            break
        total += size
        count += 1
        if w <= 1 and h <= 1:
            break
        w = max(1, w // 2)
        h = max(1, h // 2)
    return max(count, 1)


def _dds_header(width, height, fourcc, mip_count):
    header = bytearray(128)
    header[0:4] = b"DDS "
    # size, flags (CAPS|HEIGHT|WIDTH|PIXELFORMAT|MIPMAPCOUNT|LINEARSIZE),
    # height, width, pitchOrLinearSize, depth, mipMapCount
    block_size = 8 if fourcc == b"DXT1" else 16
    linear_size = max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * block_size
    struct.pack_into("<7I", header, 4, 124, 0x000A1007, height, width,
                     linear_size, 0, mip_count)
    # pixel format: size, flags (FOURCC)
    struct.pack_into("<2I", header, 76, 32, 0x4)
    header[84:88] = fourcc
    # caps: COMPLEX|MIPMAP|TEXTURE
    caps = 0x401008 if mip_count > 1 else 0x1000
    struct.pack_into("<I", header, 108, caps)
    return bytes(header)


def convert_mktx(tex_path, dds_path):
    """Convert a .mktx.tex file to a .dds file. Returns dds_path or None."""
    with open(tex_path, "rb") as f:
        data = f.read()
    if len(data) <= 48:
        return None
    v = struct.unpack_from("<22H", data, len(data) - 44)
    width, height = v[0], v[1]
    fmt_id = v[7]
    if width == 0 or height == 0:
        return None
    pixels = data[:len(data) - 48]
    # The original 2.49 script forced DXT1 for every format id, which garbled
    # 16-bytes-per-block textures. Verified against the 2014 reference
    # conversions in this repo: format id 1 is ATI2/BC5 (two-channel normal
    # maps), other 16-bytes-per-block ids (0, 16) are DXT5, the rest DXT1.
    block16 = len(pixels) >= width * height
    if block16 and fmt_id == 1:
        fourcc = b"ATI2"
    elif block16:
        fourcc = b"DXT5"
    else:
        fourcc = b"DXT1"
    block_size = 16 if block16 else 8
    mips = _dds_mip_count(width, height, block_size, len(pixels))
    try:
        with open(dds_path, "wb") as out:
            out.write(_dds_header(width, height, fourcc, mips))
            out.write(pixels)
    except OSError as e:
        print("WARNING: could not write", dds_path, e)
        return None
    return dds_path


# ---------------------------------------------------------------------------
# Parsed-data containers
# ---------------------------------------------------------------------------

class VertexInfo:
    def __init__(self):
        self.vertex_element_offset = None
        self.vertex_count = None
        self.vertex_stride = None
        self.vertex_elements = []


class IndicesInfo:
    def __init__(self):
        self.icount = None
        self.istart = None
        self.vcount = None
        self.vstart = None
        self.matID = None
        self.skinID = None


class MatInfo:
    def __init__(self):
        self.name = None
        self.diffuse = None
        self.normal = None
        self.specular = None


class BoneInfo:
    def __init__(self):
        self.name = None
        self.parentID = None
        self.matrix = None


# ---------------------------------------------------------------------------
# .min.bin material parsing
# ---------------------------------------------------------------------------

def _read_texture(p, base_offset, rel_offset, convert_textures):
    p.seek(base_offset + rel_offset)
    tex_name = p.find(b"\x00")
    tex_path = os.path.join(p.dirname, tex_name + ".mktx.tex")
    if not os.path.exists(tex_path):
        return None
    dds_path = os.path.join(p.dirname, tex_name + ".mktx.dds")
    if convert_textures or not os.path.exists(dds_path):
        return convert_mktx(tex_path, dds_path)
    return dds_path


def parse_min_bin(bin_path, mat, convert_textures):
    p = BinaryReader(bin_path)
    v = p.i(21)
    p.seek(v[6])
    v1 = p.i(29)
    if v1[1] != 0:
        mat.diffuse = _read_texture(p, v[5], v1[1], convert_textures)
    if v1[2] != 0:
        mat.normal = _read_texture(p, v[5], v1[2], convert_textures)
    if v1[3] != 0:
        mat.specular = _read_texture(p, v[5], v1[3], convert_textures)


# ---------------------------------------------------------------------------
# .mdl parsing (header, skeleton, materials, vertex/index layout)
# ---------------------------------------------------------------------------

def matrix_from_floats(data):
    # 2.49 Mathutils used row-vector convention (translation in the last row);
    # modern Blender uses column vectors, so transpose on the way in.
    return Matrix((data[0:4], data[4:8], data[8:12], data[12:16])).transposed()


def parse_skeleton(g, offset):
    bones = []
    g.seek(offset)
    A = g.i(10)
    if A[1] == 13:
        base = A[0]
    elif A[1] == 9:
        base = A[4]
    elif A[1] == 16 and A[5] in (13, 9):
        # wrapper block (e.g. a_droid_wed1577): real skeleton is the
        # second (offset, type) pair
        base = A[4]
    else:
        print("WARNING: unknown skeleton layout:", A)
        return bones
    g.seek(base)
    D = g.i(10)
    g.seek(base + D[1])
    for m in range(D[0]):
        bone = BoneInfo()
        C = g.i(8)
        t = g.tell()
        g.seek(base + C[6])
        bone.name = g.find(b"\x00")
        bone.parentID = C[4]
        g.seek(t)
        bones.append(bone)
    g.seek(base + D[2])
    for m in range(D[0]):
        bones[m].matrix = matrix_from_floats(g.f(16))
    return bones


def parse_mdl(g, convert_textures):
    g.word(4)  # magic
    v0 = g.i(19)
    g.f(8)
    v1 = g.i(12)

    bones = []
    if v1[6] != 0:
        bones = parse_skeleton(g, v1[6])

    # SECTION 1: materials (each entry points at a <name>.min.bin file)
    mat_list = []
    g.seek(v1[1])
    for m in range(v0[1]):
        t = g.tell()
        g.seek(g.i(1)[0])
        bin_name = g.find(b"\x00")
        mat = MatInfo()
        mat.name = bin_name
        bin_path = os.path.join(g.dirname, bin_name + ".min.bin")
        if os.path.exists(bin_path):
            parse_min_bin(bin_path, mat, convert_textures)
        else:
            print("WARNING: missing material file:", bin_path)
        mat_list.append(mat)
        g.seek(t + 4)

    # SECTION 2: index-buffer ranges per material (linked-list per slot)
    indices_info_list = []
    g.seek(v1[2])
    for m in range(v0[1]):
        v = g.i(v0[2])
        t = g.tell()
        sub_list = []
        for n in range(v0[2]):
            next_offset = v[n]
            while next_offset != 0:
                g.seek(next_offset)
                v2 = g.i(3)
                v3 = g.H(2)
                v4 = g.i(12)
                info = IndicesInfo()
                info.vstart = v4[0]
                info.istart = v4[1]
                info.icount = v4[2]
                info.vcount = v4[3]
                info.matID = m
                info.skinID = v3[1]
                sub_list.append(info)
                next_offset = v2[2]
        g.seek(t)
        indices_info_list.append(sub_list)

    # SECTION 4: vertex buffer layout descriptions
    vertex_info_list = []
    g.seek(v1[4])
    for m in range(v0[4]):
        info = VertexInfo()
        v2 = g.i(8)
        t0 = g.tell()
        g.seek(v2[0])
        v3 = g.i(4)
        g.seek(v3[0])
        info.vertex_element_offset = v2[1]
        info.vertex_count = v2[3]
        info.vertex_stride = v3[2]
        for n in range(v3[1]):
            t2 = g.tell()
            name = g.word(32)
            values = g.i(4)
            info.vertex_elements.append((name, values))
            g.seek(t2 + 48)
        g.seek(t0)
        vertex_info_list.append(info)

    # SECTION 5: index buffer location
    g.seek(v1[5])
    v = g.i(4)
    indices_offset = v[0]
    indices_count = v[3]

    # SECTION 9: bone maps (skin palette -> skeleton bone index)
    bone_map_list = []
    if v1[9] != 0:
        g.seek(v1[9])
        for m in range(v0[10]):
            t = g.tell()
            count = g.B(1)[0]
            bone_map_list.append(g.B(count))
            g.seek(t + 257)

    return {
        "bones": bones,
        "mat_list": mat_list,
        "indices_info_list": indices_info_list,
        "vertex_info_list": vertex_info_list,
        "indices_offset": indices_offset,
        "indices_count": indices_count,
        "bone_map_list": bone_map_list,
    }


# ---------------------------------------------------------------------------
# .mdg parsing (raw vertex / index data)
# ---------------------------------------------------------------------------

UV_ELEMENT_NAMES = {"uv", "uv0", "uv_0", "map1"}


def parse_mdg(g, mdl):
    g.word(4)
    g.i(3)

    positions = []
    uvs = []
    skin_indices = []
    skin_weights = []

    for info in mdl["vertex_info_list"]:
        stride = info.vertex_stride
        count = info.vertex_count
        base = info.vertex_element_offset

        uv_element = None
        for name, values in info.vertex_elements:
            if name in UV_ELEMENT_NAMES or (name == "diffuse" and values[2] == 5):
                uv_element = (name, values)
                break

        for name, values in info.vertex_elements:
            if name == "position":
                g.seek(base)
                for k in range(count):
                    t = g.tell()
                    g.seek(t + values[0])
                    positions.append(g.f(3))
                    g.seek(t + stride)
            elif uv_element is not None and (name, values) == uv_element:
                g.seek(base)
                for k in range(count):
                    t = g.tell()
                    g.seek(t + values[0])
                    uvs.append(g.f(2))
                    g.seek(t + stride)
            elif name == "skinIndices":
                g.seek(base)
                for k in range(count):
                    t = g.tell()
                    g.seek(t + values[0])
                    skin_indices.append(g.B(4))
                    g.seek(t + stride)
            elif name == "skinWeights":
                g.seek(base)
                for k in range(count):
                    t = g.tell()
                    g.seek(t + values[0])
                    skin_weights.append(g.B(4))
                    g.seek(t + stride)

    g.seek(mdl["indices_offset"])
    indices = g.H(mdl["indices_count"])

    return {
        "positions": positions,
        "uvs": uvs,
        "skin_indices": skin_indices,
        "skin_weights": skin_weights,
        "indices": indices,
    }


# ---------------------------------------------------------------------------
# Blender scene building
# ---------------------------------------------------------------------------

def build_skeleton(context, name, bones):
    """Create an armature object from the parsed bone list.

    Returns (armature_object, bone_names) where bone_names[i] is the actual
    Blender bone name for skeleton bone index i (Blender may deduplicate).
    """
    if not bones:
        return None, []

    if context.active_object and context.active_object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    arm = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, arm)
    context.collection.objects.link(obj)
    context.view_layer.objects.active = obj
    obj.select_set(True)

    bpy.ops.object.mode_set(mode="EDIT")
    edit_bones = []
    for i, bone in enumerate(bones):
        eb = arm.edit_bones.new(bone.name if bone.name else str(i))
        eb.head = (0.0, 0.0, 0.0)
        eb.tail = (0.0, 0.05, 0.0)
        edit_bones.append(eb)
    for i, bone in enumerate(bones):
        pid = bone.parentID
        if pid is not None and pid != -1 and 0 <= pid < len(bones) and pid != i:
            edit_bones[i].parent = edit_bones[pid]
    for i, bone in enumerate(bones):
        if bone.matrix is not None:
            try:
                edit_bones[i].matrix = bone.matrix
            except Exception as e:
                print("WARNING: bad matrix for bone", bone.name, e)
    bone_names = [eb.name for eb in edit_bones]
    bpy.ops.object.mode_set(mode="OBJECT")

    arm.display_type = "STICK"
    obj.show_in_front = True
    return obj, bone_names


def _dds_fourcc(path):
    try:
        with open(path, "rb") as f:
            header = f.read(88)
        return header[84:88]
    except OSError:
        return b""


def _load_image(path):
    if path is None or not os.path.exists(path):
        return None
    try:
        return bpy.data.images.load(path, check_existing=True)
    except RuntimeError as e:
        print("WARNING: could not load image", path, e)
        return None


def build_material(mat_info, name):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    if bsdf is None:
        return material

    x = -400
    diffuse_img = _load_image(mat_info.diffuse)
    if diffuse_img is not None:
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = diffuse_img
        tex.location = (x, 300)
        links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])

    normal_img = _load_image(mat_info.normal)
    if normal_img is not None:
        normal_img.colorspace_settings.name = "Non-Color"
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = normal_img
        tex.location = (x - 600, -100)
        nmap = nodes.new("ShaderNodeNormalMap")
        nmap.location = (x + 100, -100)
        if _dds_fourcc(mat_info.normal) == b"ATI2":
            # BC5 stores only X/Y; rebuild Z = sqrt(1 - x^2 - y^2).
            sep = nodes.new("ShaderNodeSeparateColor")
            sep.location = (x - 350, -100)
            links.new(tex.outputs["Color"], sep.inputs["Color"])

            def math_node(op, loc, a=None, b=None, c=None):
                node = nodes.new("ShaderNodeMath")
                node.operation = op
                node.location = loc
                for i, val in enumerate((a, b, c)):
                    if isinstance(val, (int, float)):
                        node.inputs[i].default_value = val
                    elif val is not None:
                        links.new(val, node.inputs[i])
                return node

            mx = math_node("MULTIPLY_ADD", (x - 200, 0), sep.outputs["Red"], 2.0, -1.0)
            my = math_node("MULTIPLY_ADD", (x - 200, -200), sep.outputs["Green"], 2.0, -1.0)
            x2 = math_node("MULTIPLY", (x - 50, 0), mx.outputs[0], mx.outputs[0])
            y2 = math_node("MULTIPLY", (x - 50, -200), my.outputs[0], my.outputs[0])
            one_minus = math_node("SUBTRACT", (x + 100, -350), 1.0, x2.outputs[0])
            sub2 = math_node("SUBTRACT", (x + 250, -350), one_minus.outputs[0], y2.outputs[0])
            z = math_node("SQRT", (x + 400, -350), sub2.outputs[0])
            z_enc = math_node("MULTIPLY_ADD", (x + 550, -350), z.outputs[0], 0.5, 0.5)

            comb = nodes.new("ShaderNodeCombineColor")
            comb.location = (x - 50, -100)
            links.new(sep.outputs["Red"], comb.inputs["Red"])
            links.new(sep.outputs["Green"], comb.inputs["Green"])
            links.new(z_enc.outputs[0], comb.inputs["Blue"])
            links.new(comb.outputs["Color"], nmap.inputs["Color"])
        else:
            links.new(tex.outputs["Color"], nmap.inputs["Color"])
        links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])

    specular_img = _load_image(mat_info.specular)
    if specular_img is not None:
        specular_img.colorspace_settings.name = "Non-Color"
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = specular_img
        tex.location = (x, -400)
        for input_name in ("Specular IOR Level", "Specular"):
            if input_name in bsdf.inputs:
                links.new(tex.outputs["Color"], bsdf.inputs[input_name])
                break

    return material


def build_mesh(context, name, sub, geo, blender_mat, bone_map,
               arm_obj, bone_names, flip_uvs):
    vstart, vcount = sub.vstart, sub.vcount
    istart, icount = sub.istart, sub.icount

    verts = geo["positions"][vstart:vstart + vcount]
    uvs = geo["uvs"][vstart:vstart + vcount]
    indices = geo["indices"][istart:istart + icount]

    faces = []
    for m in range(0, len(indices) - 2, 3):
        face = indices[m:m + 3]
        if max(face) < len(verts) and len(set(face)) == 3:
            faces.append(tuple(face))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.validate(verbose=False)

    if uvs:
        uv_layer = mesh.uv_layers.new(name="UVMap")
        for loop in mesh.loops:
            u, v = uvs[loop.vertex_index]
            uv_layer.data[loop.index].uv = (u, 1.0 - v) if flip_uvs else (u, v)

    mesh.polygons.foreach_set("use_smooth", [True] * len(mesh.polygons))
    if blender_mat is not None:
        mesh.materials.append(blender_mat)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    context.collection.objects.link(obj)

    skin_idx = geo["skin_indices"][vstart:vstart + vcount]
    skin_wts = geo["skin_weights"][vstart:vstart + vcount]
    if skin_idx and skin_wts:
        groups = {}
        for vert_id, (idxs, wts) in enumerate(zip(skin_idx, skin_wts)):
            for bone_idx, weight in zip(idxs, wts):
                if weight == 0:
                    continue
                gid = bone_map[bone_idx] if bone_map and bone_idx < len(bone_map) else bone_idx
                if gid < len(bone_names):
                    group_name = bone_names[gid]
                else:
                    group_name = str(gid)
                vg = groups.get(group_name)
                if vg is None:
                    vg = obj.vertex_groups.new(name=group_name)
                    groups[group_name] = vg
                vg.add([vert_id], weight / 255.0, "REPLACE")

    if arm_obj is not None:
        obj.parent = arm_obj
        mod = obj.modifiers.new("Armature", "ARMATURE")
        mod.object = arm_obj

    return obj


def import_mdl(context, filepath, convert_textures=True, flip_uvs=True,
               import_skeleton=True):
    g = BinaryReader(filepath)
    mdl = parse_mdl(g, convert_textures)

    mdg_path = os.path.splitext(filepath)[0] + ".mdg"
    if not os.path.exists(mdg_path):
        raise FileNotFoundError("Missing geometry file: " + mdg_path)
    geo = parse_mdg(BinaryReader(mdg_path), mdl)

    base = os.path.splitext(os.path.basename(filepath))[0]

    arm_obj, bone_names = (None, [])
    if import_skeleton:
        arm_obj, bone_names = build_skeleton(context, base + "-armature",
                                             mdl["bones"])

    blender_mats = {}
    objects = []
    mesh_id = 0
    for sub_list in mdl["indices_info_list"]:
        for sub in sub_list:
            mat_info = mdl["mat_list"][sub.matID]
            if sub.matID not in blender_mats:
                mat_name = mat_info.name or "{}-mat-{}".format(base, sub.matID)
                blender_mats[sub.matID] = build_material(mat_info, mat_name)
            bone_map = ()
            if mdl["bone_map_list"] and sub.skinID < len(mdl["bone_map_list"]):
                bone_map = mdl["bone_map_list"][sub.skinID]
            obj = build_mesh(context, "{}-{}".format(base, mesh_id), sub, geo,
                             blender_mats[sub.matID], bone_map, arm_obj,
                             bone_names, flip_uvs)
            objects.append(obj)
            mesh_id += 1

    return objects, arm_obj


# ---------------------------------------------------------------------------
# .ast.ads animation set parsing
# ---------------------------------------------------------------------------
# Format notes: see docs/FORMAT.md. Rotation keys are deltas from the bind
# pose, which is also what Blender's pose-bone basis expects.

# Native frame rate of the game's animation data: every clip duration in
# the dump is an exact multiple of 1/30 s and key times sit on that grid.
GAME_FPS = 30.0


def _cstr(data, offset):
    end = data.index(b"\x00", offset)
    return data[offset:end].decode("latin-1")


def _decode_pos_component(mant_word, exp5):
    """One channel of a position key (mirrors the game's FUN_0068fe80).

    Each channel is a custom 21-bit float: 1 sign bit + 15-bit mantissa
    (in mant_word) and a 5-bit exponent (bias 15) packed in the key's 4th
    short. exp5 == 0 means zero.
    """
    if exp5 == 0:
        return 0.0
    bits = ((exp5 + 0x70) << 23) | ((mant_word & 0x7FFF) << 8)
    if mant_word & 0x8000:
        bits |= 0x80000000
    return struct.unpack("<f", struct.pack("<I", bits & 0xFFFFFFFF))[0]


def _decode_pos_key(s0, s1, s2, s3):
    """Decode an 8-byte position key (4 u16) to a 3-vector delta."""
    return (
        _decode_pos_component(s0, (s3 >> 10) & 0x1F),
        _decode_pos_component(s1, (s3 >> 5) & 0x1F),
        _decode_pos_component(s2, s3 & 0x1F),
    )


def parse_ads(filepath):
    with open(filepath, "rb") as f:
        d = f.read()
    anim_count, table_off = struct.unpack_from("<2I", d, 0)
    bone_count, bone_table_off, set_name_off = struct.unpack_from("<3I", d, 0x64)
    set_name = _cstr(d, set_name_off)
    bone_names = [_cstr(d, struct.unpack_from("<I", d, bone_table_off + 4 * i)[0])
                  for i in range(bone_count)]

    anims = []
    for ai in range(anim_count):
        chunk = struct.unpack_from("<I", d, table_off + 4 * ai)[0]
        if d[chunk:chunk + 4] != b"ANSD":
            print("WARNING: bad ANSD chunk at", hex(chunk))
            continue
        name = _cstr(d, chunk + struct.unpack_from("<I", d, chunk + 0xC)[0])
        # +0x10 = clip duration in seconds (+0x14 is just its reciprocal)
        duration = struct.unpack_from("<f", d, chunk + 0x10)[0]
        track_count = struct.unpack_from("<I", d, chunk + 0x30)[0]

        rot_tracks = {}
        pos_tracks = {}
        for ti in range(track_count):
            entry = chunk + 0x60 + 16 * ti
            track_type, bone = struct.unpack_from("<2H", d, entry)
            key_count, off1, off2 = struct.unpack_from("<3I", d, entry + 4)
            data_base = chunk + 0x64 + 16 * ti
            times = struct.unpack_from("<{}H".format(key_count), d,
                                       data_base + off1)
            times = [t / 65535.0 for t in times]
            if bone >= len(bone_names):
                continue
            if track_type == 1:
                # rotation: 6 bytes/key, compressed quaternion delta
                quats = []
                for k in range(key_count):
                    x, y, z = struct.unpack_from(
                        "<3h", d, data_base + off2 + 6 * k)
                    qx, qy, qz = x / 32767.0, y / 32767.0, z / 32767.0
                    qw = math.sqrt(max(
                        0.0, 1.0 - (qx * qx + qy * qy + qz * qz)))
                    quats.append((qw, qx, qy, qz))
                rot_tracks[bone_names[bone]] = (times, quats)
            elif track_type == 0:
                # position: 8 bytes/key, custom-float delta from bind pose
                positions = [
                    _decode_pos_key(*struct.unpack_from(
                        "<4H", d, data_base + off2 + 8 * k))
                    for k in range(key_count)]
                pos_tracks[bone_names[bone]] = (times, positions)
            # track types 2/3/4 (absolute quat, multiplicative, hermite)
            # are not emitted by this game's exporter for these assets.

        anims.append({
            "name": name,
            "duration": duration,
            "rot_tracks": rot_tracks,
            "pos_tracks": pos_tracks,
        })
    return {"set_name": set_name, "bone_names": bone_names, "anims": anims}


def build_actions(context, arm_obj, ads, prefix, name_filter=""):
    """Create one Action per animation in the set.

    File quats are rotation deltas from the bind pose expressed in
    armature/model space (row-vector convention, hence the conjugation).
    Blender's pose basis lives in the bone's local rest frame, so the
    delta is conjugated by the bone's armature-space rest rotation.

    Position tracks are custom-float deltas from the bind pose in
    parent-bone space; Blender's pose.location lives in the bone's local
    rest frame, so the delta is rotated by the inverse local rest rotation.

    Keys are placed on the game's native 30 fps frame grid (every clip
    duration in the game data is an exact multiple of 1/30 s).
    See docs/FORMAT.md.
    """
    pose_map = {pb.name.lower(): pb.name for pb in arm_obj.pose.bones}
    rest_arm = {pb.name: pb.bone.matrix_local.to_quaternion()
                for pb in arm_obj.pose.bones}
    rest_local_rot = {}
    for pb in arm_obj.pose.bones:
        rest = pb.bone.matrix_local
        if pb.bone.parent is not None:
            rest = pb.bone.parent.matrix_local.inverted() @ rest
        rest_local_rot[pb.name] = rest.to_quaternion()

    created = []
    missing = set()
    for anim in ads["anims"]:
        if name_filter and name_filter.lower() not in anim["name"].lower():
            continue
        action = bpy.data.actions.new("{}.{}".format(prefix, anim["name"]))
        action.use_fake_user = True
        frame_span = max(anim["duration"], 1e-6) * GAME_FPS
        for bone_name, (times, quats) in anim["rot_tracks"].items():
            target = pose_map.get(bone_name.lower())
            if target is None:
                missing.add(bone_name)
                continue
            basis = []
            ra = rest_arm[target]
            ra_inv = ra.inverted()
            for qw, qx, qy, qz in quats:
                q = ra_inv @ Quaternion((qw, -qx, -qy, -qz)) @ ra
                if basis and basis[-1].dot(q) < 0:
                    q.negate()
                basis.append(q)
            data_path = 'pose.bones["{}"].rotation_quaternion'.format(target)
            for ci in range(4):
                fc = action.fcurves.new(data_path, index=ci,
                                        action_group=target)
                fc.keyframe_points.add(len(times))
                co = [0.0] * (2 * len(times))
                for k, t in enumerate(times):
                    frame = 1.0 + t * frame_span
                    # u16 time quantization: snap to the 30 fps grid
                    if abs(frame - round(frame)) < 0.05:
                        frame = float(round(frame))
                    co[2 * k] = frame
                    co[2 * k + 1] = basis[k][ci]
                fc.keyframe_points.foreach_set("co", co)
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"
                fc.update()

        for bone_name, (times, positions) in anim["pos_tracks"].items():
            target = pose_map.get(bone_name.lower())
            if target is None:
                missing.add(bone_name)
                continue
            rl_inv = rest_local_rot[target].inverted()
            locs = [rl_inv @ Vector(p) for p in positions]
            data_path = 'pose.bones["{}"].location'.format(target)
            for ci in range(3):
                fc = action.fcurves.new(data_path, index=ci,
                                        action_group=target)
                fc.keyframe_points.add(len(times))
                co = [0.0] * (2 * len(times))
                for k, t in enumerate(times):
                    frame = 1.0 + t * frame_span
                    if abs(frame - round(frame)) < 0.05:
                        frame = float(round(frame))
                    co[2 * k] = frame
                    co[2 * k + 1] = locs[k][ci]
                fc.keyframe_points.foreach_set("co", co)
                for kp in fc.keyframe_points:
                    kp.interpolation = "LINEAR"
                fc.update()

        action.use_frame_range = True
        action.frame_start = 1.0
        action.frame_end = 1.0 + round(frame_span)
        created.append(action)

    if missing:
        print("WARNING: animation bones not found in armature:",
              sorted(missing))
    if created:
        if arm_obj.animation_data is None:
            arm_obj.animation_data_create()
        if arm_obj.animation_data.action is None:
            arm_obj.animation_data.action = created[0]
    return created


# ---------------------------------------------------------------------------
# Operator / UI
# ---------------------------------------------------------------------------

class IMPORT_OT_republic_heroes_mdl(bpy.types.Operator, ImportHelper):
    """Import a Star Wars The Clone Wars: Republic Heroes model"""
    bl_idname = "import_scene.republic_heroes_mdl"
    bl_label = "Import Republic Heroes MDL"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".mdl"
    filter_glob: StringProperty(default="*.mdl", options={"HIDDEN"})

    convert_textures: BoolProperty(
        name="Convert Textures",
        description="Convert .mktx.tex textures to .dds next to the source "
                    "files (overwrites previously converted .mktx.dds files)",
        default=True,
    )
    flip_uvs: BoolProperty(
        name="Flip UVs Vertically",
        description="Use 1-V for texture coordinates (matches the game data)",
        default=True,
    )
    import_skeleton: BoolProperty(
        name="Import Skeleton",
        description="Create an armature and bind the meshes to it",
        default=True,
    )

    def execute(self, context):
        try:
            objects, arm_obj = import_mdl(
                context, self.filepath,
                convert_textures=self.convert_textures,
                flip_uvs=self.flip_uvs,
                import_skeleton=self.import_skeleton,
            )
        except FileNotFoundError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except (struct.error, IndexError) as e:
            self.report({"ERROR"},
                        "Failed to parse file (unexpected format): " + repr(e))
            return {"CANCELLED"}
        self.report({"INFO"}, "Imported {} mesh(es)".format(len(objects)))
        return {"FINISHED"}


class IMPORT_OT_republic_heroes_ads(bpy.types.Operator, ImportHelper):
    """Import a Republic Heroes animation set onto the active armature"""
    bl_idname = "import_scene.republic_heroes_ads"
    bl_label = "Import Republic Heroes Animations"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".ads"
    filter_glob: StringProperty(default="*.ads", options={"HIDDEN"})

    name_filter: StringProperty(
        name="Name Filter",
        description="Only import animations whose name contains this text "
                    "(empty = all)",
        default="",
    )
    set_scene_fps: BoolProperty(
        name="Set Scene to 30 FPS",
        description="Keyframes are placed on the game's native 30 fps grid; "
                    "set the scene frame rate to match so playback speed "
                    "is correct",
        default=True,
    )

    def execute(self, context):
        arm_obj = context.active_object
        if arm_obj is None or arm_obj.type != "ARMATURE":
            armatures = [o for o in context.scene.objects
                         if o.type == "ARMATURE"]
            if len(armatures) == 1:
                arm_obj = armatures[0]
            else:
                self.report({"ERROR"},
                            "Select the target armature first (active object "
                            "must be the armature the animations are for)")
                return {"CANCELLED"}
        try:
            ads = parse_ads(self.filepath)
        except (struct.error, IndexError, ValueError) as e:
            self.report({"ERROR"},
                        "Failed to parse file (unexpected format): " + repr(e))
            return {"CANCELLED"}
        prefix = os.path.basename(self.filepath).split(".")[0]
        if self.set_scene_fps:
            context.scene.render.fps = int(GAME_FPS)
            context.scene.render.fps_base = 1.0
        actions = build_actions(context, arm_obj, ads, prefix,
                                name_filter=self.name_filter)
        self.report({"INFO"},
                    "Imported {} action(s) from set {!r}".format(
                        len(actions), ads["set_name"]))
        return {"FINISHED"}


def menu_func_import(self, context):
    self.layout.operator(IMPORT_OT_republic_heroes_mdl.bl_idname,
                         text="Republic Heroes Model (.mdl)")
    self.layout.operator(IMPORT_OT_republic_heroes_ads.bl_idname,
                         text="Republic Heroes Animations (.ads)")


def register():
    bpy.utils.register_class(IMPORT_OT_republic_heroes_mdl)
    bpy.utils.register_class(IMPORT_OT_republic_heroes_ads)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.utils.unregister_class(IMPORT_OT_republic_heroes_ads)
    bpy.utils.unregister_class(IMPORT_OT_republic_heroes_mdl)


if __name__ == "__main__":
    register()
