# GvZ PZZ Importer — Blender add-on for Gundam vs. Zeta Gundam `.pzz` models

A standalone Blender add-on that imports models, materials, textures and a rigid-part rig
directly from the `.pzz` archives used by the PS2 game *Mobile Suit Gundam: Gundam vs.
Zeta Gundam*.

It parses the PZZ container, its 16-bit LZ compression, the model/chunk hierarchy and the
embedded TIM2 texture banks in pure Python, then rebuilds each archive inside Blender as
collections, meshes, packed images and one armature with one rigid bone per chunk.

- Blender add-on (legacy add-on zip), tested on Blender 4.x and 5.2 LTS
- No external Python dependencies — standard library only
- `core.py` is Blender-free and usable from a plain Python interpreter
- Imported via `File > Import > GvZ PZZ (.pzz)`

## Install

1. Download `dist/gvz_pzz_importer-0.1.0.zip` (or build it, see below).
2. Blender: `Edit > Preferences > Add-ons > Install from Disk…` and pick the zip.
3. Enable **GvZ PZZ Importer** in the add-on list.

Minimum Blender version is `4.0.0` (`bl_info`). Tested and enabled on Blender 5.2.3 LTS.

## Usage

`File > Import > GvZ PZZ (.pzz)` and select a `.pzz` file.

Operator options:

| Option | Default | Effect |
| --- | --- | --- |
| Clear Scene | off | Wipe the current scene before importing |
| Import Textures | on | Decode and pack the matching TIM2 texture bank |
| Build Armature | on | Build a rigid-part armature from the chunk hierarchy |

Each archive becomes one collection; each model chunk becomes one mesh object. Materials get
packed TIM2 images, so the file is self-contained after import.

Detection is content-based, not filename-based. Texture-only archives, UI data and other
non-model entries are rejected with a message explaining what the archive actually contains
instead of producing an empty scene.

## What it handles

- **PZZ container**: `0x800`-byte header, entry descriptors with a compression flag and
  block-aligned sizes, 16-bit LZ streams.
- **Model entries**: chunk table with local matrices, parent/sibling links, per-chunk submesh
  data, material-switch packets and triangle strips, PS2 Y-up to Blender Z-up conversion.
- **Rigging**: one rigid bone per chunk, rest pose set to the composed world matrix, meshes
  bound with weight-1 vertex groups and an Armature modifier. Archives whose chunks are all
  roots (stages, UI) import flat with no armature.
- **TIM2 textures**: PSMT8 and PSMT4 indexed, 32-bit RGBA, 16-bit RGBA5551 and 24-bit RGB,
  both standard and extended picture headers, CSM1 palette deshuffling, and per-file linear
  vs. PS2 GS swizzle selection stored in the TIM2 header.
- **Model ↔ texture bank pairing** without filenames, scored by texture count and dimension
  multiset.

Format details, offsets and parser behaviour are documented in [`agents.md`](agents.md).

## Validation

Parsed against the full USA archive set:

- 1596 `.pzz` archives, 0 parse errors — 924 contain models, 672 are non-model
- 2830 TIM2 textures decoded (1469 linear, 1349 GS-swizzled, plus 12 32-bit)
- 0 unpaired models, 0 zero-face models, 0 empty texture banks
- 231 archives build armatures with parented chunks

## Build the distribution zip

```sh
python3 tools/package_addon.py
# -> dist/gvz_pzz_importer-<version>.zip
```

The zip contains only `gvz_pzz_importer/__init__.py` and `gvz_pzz_importer/core.py`.

## Known caveats

- Archives often contain alternate parts (`t*`, `u*`, `s*` names) that the game hides or swaps
  at runtime; they import rigged like any other chunk and can overlap the body.
- Texture banks are matched by dimensions, not by texture identity, so an archive with two
  same-sized banks can attach the wrong one without a warning.
- Stages and environment shells import in full; hiding sky domes is left to the user.
- This is a legacy Blender add-on zip, not a Blender extension package.

## License

GPL-3.0 — see [LICENSE](LICENSE).

## Built with

Developed with **DeepSeek Flash** (`deepseek-v4.1-flash`) as the coding model.

---

Keywords: Blender add-on, Blender 4.x, Blender 5.x, Gundam vs. Zeta Gundam, Mobile Suit
Gundam, PS2 model importer, `.pzz` archive, PZZ container, TIM2 texture decoder, PSMT8,
PSMT4, game asset extraction, model ripping, rigid rig import.
