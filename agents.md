# GvZ PZZ Importer — Project Notes

## Purpose

This project is a standalone Blender add-on that imports models, materials, and
textures directly from `.pzz` archives used by *Mobile Suit Gundam: Gundam vs.
Zeta Gundam (USA)*. It is separate from the older single-file `.bin` importer in
`/home/kress/Projects/pzz_importer.py`.

The add-on is content-driven: it detects model entries and texture banks from
their bytes, not from filenames. If an archive contains only texture banks, UI
data, or other non-model data, the importer stops with an error explaining why
instead of creating an empty scene.

## Project layout

```
/home/kress/Projects/gvz_pzz_importer/
  __init__.py                       Blender add-on entry point
  core.py                           Blender-free PZZ/model/TIM2 parser
  agents.md                         This document
  tools/package_addon.py            Rebuilds the installable zip
  dist/gvz_pzz_importer-0.1.0.zip   Single-file add-on for distribution
  artifacts/GvZ_st02_000_import.blend  Saved stage import from development
```

The project folder is the source of truth. The copy installed for Blender lives
at:

```
/home/kress/.config/blender/5.2/scripts/addons/gvz_pzz_importer/
```

That installed copy is only a deployment. After changing the project, rebuild
the zip or copy `__init__.py` and `core.py` to the installed folder.

Screenshots are intentionally not part of this folder. The nine delivered
screenshots are in `/home/kress/Pictures/GvZ_pzz_*.png`.

## Current status

- Add-on name: `GvZ PZZ Importer`
- Version: `0.1.0`
- Author: `demiurge`
- Texture layout: per-TIM2 `+0x0F` flag selects linear vs GS swizzle
- Rig: parented chunk hierarchy builds an armature with one rigid bone per chunk
- Minimum Blender version in `bl_info`: `4.0.0`
- License: `GPL-3.0`
- Packaging format: legacy Blender add-on zip, not a Blender extension package
- Install path in Blender: `Edit > Preferences > Add-ons > Install from Disk`
- Menu entry: `File > Import > GvZ PZZ (.pzz)`

The add-on has been installed and enabled in Blender 5.2.3 LTS and tested with
the installed copy, not only from the source tree.

## Build and package

Rebuild the distribution zip with:

```sh
cd /home/kress/Projects/gvz_pzz_importer
python3 tools/package_addon.py
```

The script reads the version tuple from `__init__.py` and writes:

```
dist/gvz_pzz_importer-<version>.zip
```

The zip contains exactly:

```
gvz_pzz_importer/__init__.py
gvz_pzz_importer/core.py
```

The zip has a top-level `gvz_pzz_importer/` folder so Blender installs it as a
normal add-on package. No external Python packages are required.

## Architecture

`core.py` is pure Python and has no Blender imports. It owns the binary format
work and can be tested from a normal Python interpreter.

`__init__.py` owns Blender integration:

- `import_pzz(path, import_textures=True)` reads and classifies an archive.
- One Blender collection is created per archive.
- One mesh object is created per model chunk.
- Materials are created per model material, with packed TIM2 images.
- `setup_preview()` frames imported objects and configures a Workbench render.
- `GvzPzzImporter` is the operator exposed through `File > Import`.
- `NotAModelError` is the user-facing "this is not a model archive" error.

## PZZ container format

- Header is fixed at `0x800` bytes.
- `u32` entry count at `0x00`.
- Entry descriptors begin at `0x04`.
- Descriptor high byte is the compression flag:
  - `0x40` = compressed
  - `0x00` = plain
  - unknown flags are read as plain bytes; content classification then decides
    whether the entry is a model, a texture bank, or ignored
- Descriptor low 24 bits are the stored size in `0x800` blocks.
- Payload starts at `0x800`; entries are concatenated in block-aligned order.
- Zero-size descriptors become empty entries.

Compression is a 16-bit LZ-style scheme:

- Control words and tokens are little-endian.
- Control bits are consumed MSB to LSB.
- Flag `1`: `offset = (word & 0x7FF) * 2`,
  `count = (word >> 11) * 2`.
- If `count == 0`, the next little-endian `u16` supplies `count * 2`.
- `offset == 0` terminates the stream.
- Flag `0`: two literal bytes.

## Model entry format

A model entry starts with `u32 1` at `0x00` and a chunk count at `0x04`.
Each chunk header is `0x100` bytes:

- Matrix: 16 floats at `+0x00`. Translation is in elements `12..14`;
  `transform_vertex()` applies `x' = m0*x + m4*y + m8*z + m12`.
- Name: 16 bytes at `+0x50`.
- `chunk_dat`: `i32` at `+0x60`.
- Parent chunk index: `i32` at `+0x6C` (`-1` = root).
- Next sibling index: `i32` at `+0x70`.
- `chunk_size`: `i32` at `+0xF0`.
- `data_bool`: `i32` at `+0xF4`.
- Base material: `u32` at `+0x138`.

Dummy chunks (`chunk_dat != 0` or `chunk_size <= 0`) advance by `0x100` bytes
and contain no mesh data.

For a real chunk:

- Submesh data starts at `chunk_start + 0x1A0`.
- Submesh data ends at `start + chunk_size - 0xB0`.
- The next chunk starts at `end + 0x10`.
- Submesh parsing only runs when `data_bool != 0`.

After the last chunk, the material and texture tables are stored:

- Material table header: `(msize, mcount)`, both `u32`.
- `msize == mcount * 0x50`.
- Each material is `0x50` bytes:
  - name at `+0x00`
  - texture-table index at `+0x10`
- Texture table header: `(tsize, tcount)`, both `u32`.
- `tsize == tcount * 0x40`.
- Each texture reference is `0x40` bytes:
  - name at `+0x00`
  - TIM2 filename at `+0x10`
  - width at `+0x20`
  - height at `+0x24`

## Submesh and vertex format

Each submesh begins with a `0x40`-byte header:

- `pack` count at `+0x30`.
- `pack == 0xFF` terminates submesh parsing for the chunk.
- `u32` at `+0x20` equal to `0xFFFFFFFF` marks a material-switch packet.
- For a material-switch packet, `+0x28` is the new material index, interpreted
  as an absolute index into the model material table, not relative to the
  chunk's base material at `+0x138`.
- A material-switch packet with `pack == 1` advances an extra `0x10` bytes.
- `pack == 0` advances the block without emitting geometry.
- Normal block size is `0x40 + pack * 0x40`.

Each vertex is 64 bytes:

- Position: 3 floats at `+0x00`.
- Strip flag: 4 bytes at `+0x0C`. `00 80 00 00` starts a triangle strip.
- UV: 2 floats at `+0x30`. The importer stores the raw `(u, v)` — do NOT flip
  V. Confirmed against the sky dome and the orange jet: flipping V renders the
  skydome upside down and smears the jet atlas.
- The texture-ref rectangle `(u_max, v_max, u_min, v_min)` is metadata only.
  It must NOT be applied as a UV window remap; applying it stretched and mixed
  textures.

When a strip flag is found, the previous vertex is reused as the first vertex
of the next strip. Adjacent strips can therefore contribute one bridging face.
This matches the observed source data; changing it would alter existing models.

PS2 coordinates are Y-up. The importer converts to Blender Z-up with
`(x, -z, y)`.

## Chunk hierarchy and rigging

Each chunk is a rigid part in a hierarchy, not a skinned vertex group:

- `parent` at `+0x6C`; `-1` marks a root.
- `next_sibling` at `+0x70`; used for the hierarchy order.
- The chunk matrix at `+0x00` is local to its parent.

GvZ matrices compose in row-vector order:

```
world_chunk = local_chunk @ world_parent
```

The importer builds one armature per model entry when any chunk has a parent.
It creates one bone per chunk (including empty chunks), parents the bones by
`+0x6C`, sets each bone's rest pose to the composed world matrix, and binds each
mesh chunk with a weight-1 vertex group plus an Armature modifier. This keeps
the assembled pose in rest and lets the chunks animate as rigid parts.

`Build Armature` in the import operator is on by default. Turning it off imports
the same composed world-space meshes without an armature. For archives whose
chunks are all roots (stages, UI), no armature is created and the matrices are
used directly.

The importer still imports every chunk and every model entry. Extra parts
(`t*`, `u*`, `s*`) and alternate model entries are often hidden or swapped at
runtime, so they can overlap the base body even though the rig itself is
correct. Keep `m*`/`o*` for the body and hide the extras for a clean unit.

## Texture banks and TIM2

A texture bank is either:

- a raw TIM2 file beginning at offset `0`, or
- a bundle of TIM2 blocks at `0x800` boundaries.

GVZ uses two TIM2 picture-header layouts:

- Standard layout: picture header at `+0x10`.
- Extended layout: picture header at `+0x80`.

Both layouts store the bitmap before the palette in this game:

- `img = tim2_offset + picture_at + header_size`
- `clut = img + image_size`

Picture-header fields:

- total size: `u32` at `+0x00`
- CLUT size: `u32` at `+0x04`
- image size: `u32` at `+0x08`
- header size: `u16` at `+0x0C`
- CLUT colors: `u16` at `+0x0E`
- image format: `u8` at `+0x10`
- mipmap count: `u8` at `+0x11`
- CLUT type: `u8` at `+0x12`
- bpp type: `u8` at `+0x13`
- width: `u16` at `+0x14`
- height: `u16` at `+0x16`

Supported bpp types:

- `5` = 8-bit indexed PSMT8
- `4` = 4-bit indexed PSMT4
- `3` = 32-bit RGBA
- `1` = 16-bit RGBA5551
- `2` = 24-bit RGB

GvZ stores the layout in the last reserved byte of the TIM2 file header
(`tim2_offset + 0x0F`):

- `0` = plain linear scanlines
- `1` = Climax/PS2 GS swizzle (PSMT8, and PSMT4 when present)

This flag is per TIM2 file; it is not implied by the picture header, `GsTex`
TBW, `clut_type`, or the archive. Some banks mix both layouts.

`parse_tim2()` passes the flag to `_decode_psmt8()` / `_decode_psmt4()`. When
the flag is `0`, indices are read linearly; when `1`, the GS swizzle is applied.

CSM1 palettes (`GsTex` bit 55 = 0) are deshuffled by swapping the middle eight
colors of each 32-color block. CSM2 palettes (bit 55 = 1) are left in linear
order. Palette alpha `0x80` is treated as opaque with `min(a * 2, 255)`.

Texture banks can contain malformed trailing blocks. The parser keeps earlier
valid textures and ignores unreadable blocks.

## Model-to-bank pairing

Models and banks are paired without filenames. Candidate banks are scored in
this priority order:

1. Exact match: texture count and dimension multiset both equal.
2. Bank not already assigned to another model.
3. Number of matching texture dimensions.
4. Equal texture count.

A bank must have at least one matching dimension to be assigned. Ties keep the
first bank in archive order because comparison is strict. Exact matches may be
reused when no unused bank scores higher, because some archives have multiple
models sharing one texture bank.

Texture slot `k` in the selected bank is assumed to correspond to unique model
texture `k`. There is no per-texture identity check, so a wrong bank with the
same dimensions can attach textures to the wrong slots without a warning.

The importer does not fail when a model has no matching bank. It imports the
geometry and reports a warning naming the untextured model entry.

## Classification and errors

Classification is content-based:

- `looks_like_texture_bank()` checks raw TIM2 or TIM2 at `0x800`.
- `looks_like_model()` validates the chunk table and material/texture tables.
- Everything else is ignored.

`NotAModelError` is raised when an archive has no model entries. The message
includes the actual reason, for example:

- texture-only archive: number of banks and decodable textures
- empty archive: number of empty or unrecognized entries
- malformed model entry: entry index and parser error

`PzzError` is raised for:

- files too small to be PZZ
- invalid entry counts
- entry data beyond the file end
- decompression failures, with the entry index
- model data with no renderable geometry

The Blender operator reports errors through `self.report({"ERROR"}, ...)`.
Partially readable archives import the valid models and report warnings for
skipped entries, empty models, or missing texture banks.

The command-line entry point prints `NOT_A_MODEL` or `IMPORT_FAILED` and exits
with status 2 on failure.

## Validation performed

Full corpus under:

```
/home/kress/Downloads/Gundam/Mobile Suit Gundam - Gundam vs. Zeta Gundam (USA)/output/AFS_DATA/
```

Results:

- 1596 `.pzz` archives parsed
- 0 parse errors
- 924 archives contain models, now moved to `AFS_DATA/pzz_models/`
- 672 archives are non-model (texture banks, UI, other data), left in `AFS_DATA/`
- 0 unpaired models
- 0 zero-face models
- 0 empty texture banks

Additional checks:

- 924 model archives parsed with 0 errors after the texture-layout fix.
- 2,830 TIM2 textures decoded; 2,818 PSMT8 textures split into 1,469 linear
  (`flag 0`) and 1,349 swizzled (`flag 1`).
- Nine final model screenshots rendered at 1600x1600:
  `pl18/01670`, `pl06/01518`, `pl29/01820`, `pl351/01912`, `pl05/01510`,
  `pl12/01608`, `pl23/01769`, `pl28/01812`, `pl25/01785`.
- Checkerboard regression: `02005`, `01985`, `01983`, `01989`, `02443`,
  `01407`, and `03329` decode linearly; `01670`, `01518`, `02388`, `02391`,
  and `02384` decode swizzled. Mixed bank `02393` bank 5 decodes correctly
  per texture.
- 231 model entries contain parented chunks and build armatures; stages and UI
  archives have flat roots and no armature.
- Rig rest pose verified against the composed chunk hierarchy on `01670`,
  `01518`, `01769`, `01812`, `01912`, and `01985`.
- Pose test: rotating `m04` on `01670` moves the arm independently while the
  rest of the body stays intact.

## Observed content ranges

These are informal observations from the USA archive set, not a filename
contract. The importer never uses them for detection.

- `00660`-`00664`: opening/demo models
- `01375`-`01408`: stage/diorama models
- `01458`-`02100`: player/mobile-suit models
- `02380`-`02604`: stage archives
- `02869`-`03043`: UI/menu screens
- `03293`-`03342`: UI/result screens
- `03345`-`03696`: battle-result/mission screens
- `02916`-`02986`: character portrait texture banks

`st02` is archive `02382`; its sky dome is entry `001`.

## st02 camera views

`tools/render_st02_views.py` renders 8 reproducible views of stage `st02`
(archive `02382`); outputs land in `/home/kress/Pictures/GvZ_pzz_st02_*.png`.

- `top` — orthographic, scale 7600, 1600x1600.
- `aerial_se` — direction (1, -1, 0.65), ortho, scale 8600, 1600x900.
- `aerial_nw` — direction (-1, 1, 0.65), ortho, scale 8600, 1600x900.
- `aerial_e` — direction (1, 0.2, 0.55), ortho, scale 8600, 1600x900.
- `waterfront_s` — camera (2600, -800, 30), target (0, 0, 250), lens 35, 1600x1000.
- `waterfront_n` — camera (2200, 1200, 40), target (0, 0, 250), lens 35, 1600x1000.
- `jet_close` — camera (700, -900, 850), target (0, 0, 680), lens 50, 1600x1000.
- `city_close` — camera (700, -1000, 450), target (0, 0, 80), lens 50, 1600x1000.

The jet is lifted +650 on Z and faces with material 45 deleted. For the sky
views, sky dome and city are composited.

### st02 building geometry

The s00-s22 building chunks are closed on the four facades and roof. Tower
bases (for example `s05`) have no bottom face because the buildings sit on the
city ground plane. With backface culling on and a low side camera, that open
underside appears as a small notch at the base; it is not a missing facade and
not dropped geometry.

The earlier "missing -X/-Y sides" result was an analysis artifact: the global
chunk bbox extends past the wall planes because of skirts and props, so
counting only faces on the extreme bbox plane missed the actual walls.
`transform_vertex` also converts Y-up source data to Z-up with `(x, -z, y)`,
which makes axis mixing easy. Do not change `strip_faces` winding for this.

## Known caveats

- Some models contain separate weapon, shield, booster, or alternate-pose
  chunks (`t*`, `u*`, `s*`). They are rigged and imported like any other chunk,
  but the game hides or swaps them at runtime, so they can overlap the body.
- Sky domes and environment shells are hidden only by the screenshot harness.
  The add-on itself imports every model.
- Comments and docstrings were intentionally stripped from the add-on source.
  Maintainability was explicitly not a priority for this project.
- The old single-file `.bin` importer is a separate project and is not part of
  this add-on.
- Tracked in git; `README.md` is the public entry point and this file is the format/engineering reference.
- The PZZ naming/content Markdown document was intentionally not produced; the
  short range list above is only a quick reference.

## Useful paths

- Model-bearing game archives:
  `/home/kress/Downloads/Gundam/Mobile Suit Gundam - Gundam vs. Zeta Gundam (USA)/output/AFS_DATA/pzz_models/`
- Non-model archives and extracted companions:
  `/home/kress/Downloads/Gundam/Mobile Suit Gundam - Gundam vs. Zeta Gundam (USA)/output/AFS_DATA/`
- Move manifest:
  `AFS_DATA/pzz_models/MOVE_MANIFEST.tsv`
- Extraction notes:
  `/home/kress/Downloads/Gundam/Mobile Suit Gundam - Gundam vs. Zeta Gundam (USA)/output/EXTRACTION_SUMMARY.md`
- Screenshots:
  `/home/kress/Pictures/GvZ_pzz_*.png`
- Installed add-on:
  `/home/kress/.config/blender/5.2/scripts/addons/gvz_pzz_importer/`
