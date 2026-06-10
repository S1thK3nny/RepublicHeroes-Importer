# Star Wars The Clone Wars: Republic Heroes - file formats

Reverse-engineered documentation of the PC asset formats (Krome Studios'
**Merkury engine**, version 3). Nothing here is official; everything was
derived from hex analysis of the game's data files, validated statistically
and visually with the importer in this repo. All values little-endian.

A recurring trait: structures are written to disk with uninitialized struct
padding intact (`0xCD` bytes - MSVC debug heap fill), and many "offset"
fields are *self-relative pointers*: the stored value is relative to the
field's own struct, patched into real pointers at load time.

## File ecosystem

| Extension | Contents |
| --- | --- |
| `.mkmesh.mdl` | Model header: skeleton, material refs, vertex/index layout |
| `.mkmesh.mdg` | Model geometry: raw vertex + index buffers |
| `.min.bin` | Material definition (texture names) |
| `.mktx.tex` | Texture (raw DXT/BC data + footer) |
| `.ast.ads` | Animation set (skeleton bone list + N animations) |
| `.sdt/.ent/.pdt/... .bin` | Other subsystems (not reversed) |

Coordinate system: **Y-up**, matrices stored **row-major** (translation in
elements 12–14, i.e. the last row) - transpose for column-vector
conventions like Blender's.

## .mdl (model header)

```
0x00  char[4]   magic
0x04  int[19]   v0: counts.  v0[1]=materials, v0[2]=section-0 count (and
                ints per section-2 row), v0[3]=section-3 count,
                v0[4]=vertex-info count, v0[10]=bone-map count
0x50  float[8]
0x70  int[12]   v1: section offsets (0..11)
```

Sections (offset = `v1[n]`, 0 = absent):

- **v1[0]** - names/nodes (unused by importer)
- **v1[1]** - materials: `v0[1]` entries, 4 bytes each: u32 pointer to a
  zero-terminated material name -> `<name>.min.bin` next to the model.
- **v1[2]** - submesh ranges: `v0[1]` rows of `v0[2]` u32 slot pointers
  (0 = empty). Each pointed block: `int[3] v2, u16[2] v3, int[12] v4`,
  where `v4[0]=vstart, v4[1]=istart, v4[2]=icount, v4[3]=vcount`,
  `v3[1]=skin palette id`, `v2[2]` = pointer to next block (linked list).
- **v1[3]** - 16-byte entries (unknown)
- **v1[4]** - vertex layouts: `v0[4]` entries `int[8] v2` (`v2[0]`->layout
  block, `v2[1]`=vertex data offset in `.mdg`, `v2[3]`=vertex count).
  Layout block: `int[4] v3` (`v3[0]`->elements, `v3[1]`=element count,
  `v3[2]`=stride). Each element: 48 bytes = `char[32]` name (`position`,
  `uv`/`uv0`/`uv_0`/`map1`, `skinIndices`, `skinWeights`, `normal`,
  `diffuse`...) + `int[4]` (`[0]`=offset in vertex, `[2]`=type id).
- **v1[5]** - index buffer: `int[4]`: `[0]`=offset in `.mdg`, `[3]`=count
  (u16 indices).
- **v1[6]** - skeleton: `int[10] A`. Layout variants:
  - `A[1]==13`: skeleton block at `A[0]`
  - `A[1]==9`:  skeleton block at `A[4]`
  - `A[1]==16` and `A[5] in (13, 9)`: wrapper; skeleton block at `A[4]`
  Skeleton block (base `B`): `int[10] D`: `D[0]`=bone count,
  `B+D[1]`=bone entries (`int[8] C`: `C[4]`=parent index or -1,
  `B+C[6]`=name offset), `B+D[2]`=4×4 float bind matrices
  (armature space, row-major).
- **v1[9]** - bone maps (skin palettes): `v0[10]` entries, 257 bytes each:
  u8 count + u8[count] mapping skin index -> skeleton bone index.

## .mdg (geometry)

16-byte header (`char[4]` + `int[3]`), then raw buffers at the offsets
given by the `.mdl`. Positions `float[3]`, UVs `float[2]` (DirectX V -
flip with `1-v`), `skinIndices`/`skinWeights` `u8[4]` (weights /255).
Indices are triangle lists, local to each submesh's `vstart`.

## .min.bin (material)

`int[21] v` -> seek `v[6]` -> `int[29] v1`. Texture name offsets (relative
to string base `v[5]`, 0 = none): `v1[1]`=diffuse, `v1[2]`=normal,
`v1[3]`=specular. Names resolve to `<name>.mktx.tex`.

## .mktx.tex (texture)

Raw compressed pixel data, then a 48-byte footer. Footer (last 44 bytes):
`u16[22] v`: `v[0]`=width, `v[1]`=height, `v[7]`=format id.

| format id | codec | bytes per 4×4 block |
| --- | --- | --- |
| 0, 2, 4, 8 | DXT1 (or DXT5 when data size says 16 B/block) | 8 |
| 0, 16 | DXT5 | 16 |
| 1 | **ATI2/BC5** (2-channel normal maps; reconstruct Z) | 16 |

Pixel data = file minus last 48 bytes, base level first, then mips.
Block size is reliably `16 if len(data) >= w*h else 8`. Verified
pixel-exact against known-good reference conversions.

## .ast.ads (animation set)

### Header

```
0x00  u32  animation count
0x04  u32  offset of animation chunk table (always 0x70)
0x08       0xCD padding
0x64  u32  bone count
0x68  u32  offset of bone-name pointer table (u32[count] -> C strings)
0x6C  u32  offset of set name (C string, e.g. "a_battledroid_BINDPOSE")
0x70  u32[anim count]  chunk offsets
```

The bone list is the *animation* skeleton: track bone indices index into
it. It is a subset of the model skeleton (matched by name,
case-insensitive).

### ANSD chunk (one per animation)

All offsets below are relative to the chunk start unless noted.

```
+0x00  char[4] "ANSD"
+0x04  u32    chunk size (through the name strings)
+0x08  u32    0
+0x0C  u32    offset of animation name (C string)
+0x10  float  frame interval in seconds (e.g. 0.03333)
+0x14  float  frame count (duration = interval * count, seconds)
+0x18  i32    negated chunk offset (self pointer; ignore)
+0x1C  u32    0
+0x20  float  0.5 (constant; blend weight?)
+0x24..0x2C   0
+0x30  u32    track count
+0x34  u32[6] region offsets (track data start/end pairs)
+0x4C  ...    counts/sizes incl. +0x58 = offset to set tail
+0x60  track entries, 16 bytes each
```

### Track entry (16 bytes, at chunk+0x60 + 16*i)

```
+0x00  u16  flag: 0 = position track, 1 = rotation track
+0x02  u16  bone index (into the set's bone list)
+0x04  u32  key count
+0x08  u32  off1: key times
+0x0C  u32  off2: key values
```

**Offsets are relative to `chunk + 0x64 + 16*i`** - i.e. relative to the
entry's own position (self-relative pointer quirk).

- **Times**: `u16[key count]`, normalized 0..0xFFFF over the clip
  duration. Monotonic (verified over ~80k keys).
- **Rotation values** (flag 1): 6 bytes/key - `s16 x, y, z`; component =
  `s16/32767`; `w = sqrt(1 - x² - y² - z²)` (encoder keeps w ≥ 0).
  The quaternion is the bone's rotation **delta from the bind pose,
  expressed in armature/model space** (not the bone's local frame), in
  row-vector convention. For a column-vector engine (Blender):
  conjugate the quaternion, then conjugate it by the bone's
  armature-space bind rotation `A` to get the local pose basis:
  `basis = A⁻¹ ⊗ q* ⊗ A`. Verified: Ahsoka and battledroid
  Idle/Run/Walk render with correct motion; an armature-space vs
  local-space mixup keeps near-axis-aligned rigs (droids) looking
  almost right while bending humanoid shoulder/hip chains wrong.
  78,949 battledroid keys all satisfy |xyz| ≤ 1.
- **Position values** (flag 0): 8 bytes/key - **encoding not yet
  decoded**. Used only by a few bones per rig (root motion `z_LVE`,
  `z_Root` bob, cameras, props). Observations:
  - `z_LVE` in `Run`: key0 = `(0,0,0,0)`, end = `(0,0,-29696,23)` s16 -
    consistent with root motion along one axis; 4th u16 small (20–23)
    for `z_LVE`, large (695–24246) for cameras/root.
  - Constant tracks exist whose value should decode to the bone's
    local position (cameras) - none of: s16×scale, f16, f32 pairs,
    11-11-10 packing, normalize-4 quat, exponent-in-w matched all
    constraints. Best lead: per-key `(s16[3], u16)` with a per-bone or
    magnitude-dependent scale.

### Set tail

After the last chunk: optional per-set data, then the bone-name pointer
table and strings. Bind-pose sets (`*_BINDPOSE`) pair with
`*_bindpose.mkmesh.mdl` skeletons.

## Open questions

1. Position-track value encoding (above) - the only blocker for root
   motion / camera animation import.
2. `.mdl` sections 0/3, the second/third region offsets at chunk+0x34,
   and the exact meaning of the +0x4C tail fields.
3. Whether any textures use formats beyond DXT1/DXT5/ATI2.
