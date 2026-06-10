# Republic Heroes Importer

Blender 4.0+ add-on for importing models and animations from **Star Wars
The Clone Wars: Republic Heroes** (PC).

## Installation

1. In Blender: *Edit > Preferences > Add-ons > Install...*
2. Select `io_import_republicheroes_mdl.py`.
3. Enable *"Star Wars The Clone Wars: Republic Heroes (.mdl)"*.

## Usage

### Models - *File > Import > Republic Heroes Model (.mdl)*

The importer expects the game's companion files next to the `.mdl`:

| File | Purpose |
| --- | --- |
| `<model>.mdl` | Header: skeleton, material refs, vertex/index layout |
| `<model>.mdg` | Raw geometry (vertex and index buffers) - **required** |
| `<material>.min.bin` | Material definitions (texture names) |
| `<texture>.mktx.tex` | Textures (DXT-compressed) |

### Animations - *File > Import > Republic Heroes Animations (.ads)*

1. Import the matching model first (for characters, the `*_bindpose`
   model is the animation skeleton - e.g. `a_battledroid_bindpose.mkmesh.mdl`
   pairs with `a_battledroid.ast.ads`).
2. Select the armature, then import the `.ast.ads` file.

Each animation in the set becomes a Blender **Action** named
`<set>.<animation>` (e.g. `a_battledroid.Run`) with fake-user enabled -
pick them in the Action Editor or stack them in the NLA. A name filter in
the import options lets you import a subset (e.g. only names containing
"Idle"). Rotation keys are imported exactly; the few position tracks the
format has (root motion, cameras) are not decoded yet, so characters
animate in place (see `docs/FORMAT.md`).

Meshes are built with UVs, smooth shading and Principled BSDF node materials
(base color, normal map, specular). The skeleton is imported as an armature
and meshes are bound to it with vertex groups and an Armature modifier.

Import options:

- **Convert Textures** - Use this to convert `.mktx.tex` to `.dds` files written next to
  the sources (off = reuse previously converted files). No need to use Noesis or other external tools to convert textures first.
- **Flip UVs Vertically** - game data stores DirectX-style V; leave on.
- **Import Skeleton** - import with or without the armature (why would you not want the skeleton?).

## Format notes

Full reverse-engineered format documentation lives in
[`docs/FORMAT.md`](docs/FORMAT.md). Highlights:

- `.mktx.tex` files are raw DXT data with a 48-byte footer holding the
  dimensions and a format id. Mapping (verified pixel-level against known
  reference conversions): id **1** = ATI2/BC5 (two-channel normal maps),
  other 16-bytes-per-block ids (0, 16) = DXT5, everything else = DXT1.
- BC5 normal maps only store X/Y; the importer adds a node chain that
  reconstructs Z (`sqrt(1 - x² - y²)`) before the Normal Map node.
- Bone matrices are stored row-major (translation in the last row) and are
  transposed on import for Blender's column-vector convention. Models are
  Y-up.
- Skin weights are per-vertex byte quadruplets (`/255`), with per-submesh
  bone palettes mapping skin indices to skeleton bone indices.
- `.ast.ads` animation rotations are 48-bit compressed quaternions
  (s16 x/y/z, w reconstructed), stored as deltas from the bind pose in
  **armature/model space** - validated over ~79k keys and visual renders
  of battledroid and Ahsoka Idle/Run/Walk.

The `research/` folder holds the analysis scripts used to reverse the
animation format, plus sample game files they (and the tests) run against.

## Tests

Headless smoke tests (adjust the Blender path as needed):

```powershell
& "D:\SteamLibrary\steamapps\common\Blender\blender.exe" --background --factory-startup --python tests\run_blender_test.py
& "D:\SteamLibrary\steamapps\common\Blender\blender.exe" --background --factory-startup --python tests\run_texture_test.py
& "D:\SteamLibrary\steamapps\common\Blender\blender.exe" --background --factory-startup --python tests\run_compare_test.py
```

- `run_blender_test.py` builds a synthetic `.mdl`/`.mdg`/`.min.bin` set (one
  triangle, one bone, one material referencing a real sample texture), runs
  the import operator, and asserts on the resulting mesh, UVs, armature,
  parenting, material and converted texture.
- `run_texture_test.py` converts every `.mktx.tex` sample in `tests/samples/`
  and verifies Blender loads each resulting DDS at the expected dimensions.
- `run_compare_test.py` compares each conversion pixel-by-pixel against
  known-good reference conversions (`tests/samples/*.mktxout.tga`).
- `run_anim_test.py` imports the battledroid (92 actions) and wed1577
  (13 actions) animation sets and asserts on action counts, fcurves, the
  type-16 skeleton variant, the name filter, and that the droid stays
  upright through the Run cycle.
