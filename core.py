from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple


class PzzError(ValueError):
    pass


@dataclass
class PzzEntry:
    index: int
    data: bytes
    compressed: bool
    stored_size: int


def _decompress_entry(src: bytes) -> bytes:
    out = bytearray()
    size = len(src) // 2 * 2
    control = 0
    control_bit = -1
    i = 0
    while i < size:
        if control_bit < 0:
            control = src[i] | (src[i + 1] << 8)
            control_bit = 15
            i += 2
            continue
        if control & (1 << control_bit):
            word = src[i] | (src[i + 1] << 8)
            offset = (word & 0x7FF) * 2
            if offset == 0:
                break
            count = (word >> 11) * 2
            if count == 0:
                i += 2
                if i + 1 >= len(src):
                    raise PzzError("truncated long match length")
                count = (src[i] | (src[i + 1] << 8)) * 2
            index = len(out) - offset
            if index < 0:
                raise PzzError("invalid back-reference before output start")
            for j in range(count):
                out.append(out[index + j])
        else:
            if i + 1 >= len(src):
                break
            out.extend(src[i:i + 2])
        i += 2
        control_bit -= 1
    return bytes(out)


def read_pzz(path: str) -> List[PzzEntry]:
    with open(path, "rb") as f:
        archive = f.read()
    if len(archive) < 0x800:
        raise PzzError("file too small to be a PZZ archive")

    count = struct.unpack_from("<I", archive, 0)[0]
    if count > 4096:
        raise PzzError("invalid PZZ entry count %d" % count)
    if 4 + count * 4 > 0x800:
        raise PzzError("PZZ entry table overlaps payload")

    entries: List[PzzEntry] = []
    offset = 0x800
    for i in range(count):
        word = struct.unpack_from("<I", archive, 4 + i * 4)[0]
        flag = word >> 24
        stored = (word & 0xFFFFFF) * 0x800
        if stored == 0:
            entries.append(PzzEntry(i, b"", False, 0))
            continue
        if offset + stored > len(archive):
            raise PzzError("PZZ entry %d exceeds file size" % i)
        raw = archive[offset:offset + stored]
        offset += stored
        if flag == 0x40:
            try:
                data = _decompress_entry(raw)
            except PzzError as exc:
                raise PzzError("PZZ entry %d: %s" % (i, exc)) from exc
            entries.append(PzzEntry(i, data, True, stored))
        elif flag == 0x00:
            entries.append(PzzEntry(i, raw, False, stored))
        else:
            entries.append(PzzEntry(i, raw, False, stored))
    return entries


@dataclass
class Submesh:
    vertices: List[Tuple[float, float, float]]
    uvs: List[Tuple[float, float]]
    flags: List[bytes]
    material: int


@dataclass
class Chunk:
    name: str
    matrix: Tuple[float, ...]
    material: int
    submeshes: List[Submesh]
    parent: int = -1


@dataclass
class Material:
    name: str
    texture_index: int


@dataclass
class TextureRef:
    name: str
    filename: str
    width: int
    height: int


@dataclass
class Model:
    chunks: List[Chunk]
    materials: List[Material]
    textures: List[TextureRef]
    tail_offset: int

    def unique_textures(self) -> List[TextureRef]:
        seen = set()
        result = []
        for tex in self.textures:
            key = tex.filename.lower()
            if key and key not in seen:
                seen.add(key)
                result.append(tex)
        return result

    def texture_slots(self) -> Dict[str, int]:
        return {tex.filename.lower(): i for i, tex in enumerate(self.unique_textures())}


STRIP_FLAG = b"\x00\x80\x00\x00"


def _parse_submeshes(data: bytes, start: int, end: int,
                     initial_material: int) -> List[Submesh]:
    submeshes: List[Submesh] = []
    pos = start
    current_material = initial_material

    while pos + 0x40 <= end:
        pack = data[pos + 0x30]
        if pack == 0xFF:
            break
        f20 = struct.unpack_from("<I", data, pos + 0x20)[0]
        f28 = struct.unpack_from("<I", data, pos + 0x28)[0]

        is_dummy = (f20 == 0xFFFFFFFF)
        block = 0x40 + pack * 0x40
        if is_dummy and pack == 1:
            block += 0x10
        if pos + block > end:
            raise PzzError(
                "submesh at %#x (pack=%d) exceeds chunk end %#x" % (pos, pack, end)
            )

        if is_dummy:
            current_material = f28
            pos += block
            continue

        if pack == 0:
            pos += block
            continue

        verts: List[Tuple[float, float, float]] = []
        uvs: List[Tuple[float, float]] = []
        flags: List[bytes] = []
        for j in range(pack):
            vp = pos + 0x40 + j * 64
            x, y, z = struct.unpack_from("<3f", data, vp)
            u, v = struct.unpack_from("<2f", data, vp + 48)
            verts.append((x, y, z))
            uvs.append((u, v))
            flags.append(data[vp + 12:vp + 16])

        submeshes.append(Submesh(verts, uvs, flags, current_material))
        pos += block

    return submeshes


def _valid_name(raw: bytes) -> str:
    text = raw.split(b"\x00")[0].decode("ascii", "replace")
    return "".join(ch for ch in text if 32 <= ord(ch) < 127)


def looks_like_model(data: bytes) -> bool:
    if len(data) < 0x400:
        return False
    if struct.unpack_from("<I", data, 0)[0] != 1:
        return False
    nchunks = struct.unpack_from("<I", data, 4)[0]
    if not (1 <= nchunks <= 4096):
        return False
    pos = 0x10
    for _ in range(nchunks):
        if pos + 0x100 > len(data):
            return False
        chunk_dat = struct.unpack_from("<i", data, pos + 0x60)[0]
        chunk_size = struct.unpack_from("<i", data, pos + 0xF0)[0]
        if chunk_dat != 0 or chunk_size <= 0:
            pos += 0x100
            continue
        if chunk_size < 0xB0:
            return False
        end = pos + 0x1A0 + chunk_size - 0xB0
        if end > len(data):
            return False
        pos = end + 0x10
    if pos + 0x20 > len(data):
        return False
    msize, mcount = struct.unpack_from("<2I", data, pos)
    if msize != mcount * 0x50 or pos + 0x10 + msize + 0x20 > len(data):
        return False
    tpos = pos + 0x10 + msize
    tsize, tcount = struct.unpack_from("<2I", data, tpos)
    return (tsize == tcount * 0x40
            and tpos + 0x10 + tsize <= len(data))


def parse_model(data: bytes) -> Model:
    if not looks_like_model(data):
        raise PzzError("not a GvZ model entry")
    nchunks = struct.unpack_from("<I", data, 4)[0]
    chunks: List[Chunk] = []
    pos = 0x10

    for _ in range(nchunks):
        matrix = struct.unpack_from("<16f", data, pos)
        name = _valid_name(data[pos + 0x50:pos + 0x60])
        chunk_dat = struct.unpack_from("<i", data, pos + 0x60)[0]
        chunk_size = struct.unpack_from("<i", data, pos + 0xF0)[0]
        data_bool = struct.unpack_from("<i", data, pos + 0xF4)[0]
        chunk_material = struct.unpack_from("<I", data, pos + 0x138)[0]
        parent = struct.unpack_from("<i", data, pos + 0x6C)[0]

        if chunk_dat != 0 or chunk_size <= 0:
            chunks.append(Chunk(name, matrix, chunk_material, [], parent))
            pos += 0x100
            continue

        start = pos + 0x1A0
        end = start + chunk_size - 0xB0
        if end > len(data) or end < start:
            raise PzzError("chunk %r exceeds model size" % name)
        submeshes = (_parse_submeshes(data, start, end, chunk_material)
                     if data_bool != 0 else [])
        chunks.append(Chunk(name, matrix, chunk_material, submeshes, parent))
        pos = end + 0x10

    tail = pos
    msize, mcount = struct.unpack_from("<2I", data, tail)
    materials: List[Material] = []
    mbase = tail + 0x10
    for i in range(mcount):
        o = mbase + i * 0x50
        name = _valid_name(data[o:o + 0x10])
        texture_index = struct.unpack_from("<I", data, o + 0x10)[0]
        materials.append(Material(name, texture_index))

    tpos = mbase + msize
    tsize, tcount = struct.unpack_from("<2I", data, tpos)
    textures: List[TextureRef] = []
    tbase = tpos + 0x10
    for i in range(tcount):
        o = tbase + i * 0x40
        name = _valid_name(data[o:o + 0x10])
        filename = _valid_name(data[o + 0x10:o + 0x20])
        width, height = struct.unpack_from("<2I", data, o + 0x20)
        textures.append(TextureRef(name, filename, width, height))

    return Model(chunks, materials, textures, tail)


@dataclass
class TextureImage:
    width: int
    height: int
    rgba: bytes
    source_offset: int = 0


def _palette_rgba8(data: bytes, offset: int, count: int) -> List[Tuple[int, int, int, int]]:
    pal = []
    for i in range(count):
        r, g, b, a = data[offset + i * 4:offset + i * 4 + 4]
        pal.append((r, g, b, min(a * 2, 255)))
    return pal


def _palette_rgba5551(data: bytes, offset: int, count: int) -> List[Tuple[int, int, int, int]]:
    pal = []
    for i in range(count):
        v = struct.unpack_from("<H", data, offset + i * 2)[0]
        r = (v & 0x1F) * 255 // 31
        g = ((v >> 5) & 0x1F) * 255 // 31
        b = ((v >> 10) & 0x1F) * 255 // 31
        a = 255 if (v & 0x8000) else 0
        pal.append((r, g, b, a))
    return pal


def _deshuffle_palette(palette: Sequence[Tuple[int, int, int, int]]):
    out: List[Tuple[int, int, int, int]] = []
    for start in range(0, len(palette) - 31, 32):
        block = palette[start:start + 32]
        out.extend(block[0:8])
        out.extend(block[16:24])
        out.extend(block[8:16])
        out.extend(block[24:32])
    out.extend(palette[len(out):])
    return out


def _decode_psmt8(data: bytes, img: int, width: int, height: int,
                  palette: Sequence[Tuple[int, int, int, int]],
                  image_size: int, swizzled: bool = True) -> bytes:
    out = bytearray(width * height * 4)
    for y in range(height):
        row = y * width * 4
        for x in range(width):
            if swizzled:
                block_loc = (y & ~0xF) * width + (x & ~0xF) * 2
                swap_sel = (((y + 2) >> 2) & 1) * 4
                pos_y = (((y & ~3) >> 1) + (y & 1)) & 7
                col_loc = pos_y * width * 2 + ((x + swap_sel) & 7) * 4
                byte_num = ((y >> 1) & 1) + ((x >> 2) & 2)
                off = block_loc + col_loc + byte_num
            else:
                off = y * width + x
            idx = data[img + off] if off < image_size else 0
            r, g, b, a = palette[idx] if idx < len(palette) else (0, 0, 0, 255)
            p = row + x * 4
            out[p:p + 4] = bytes((r, g, b, a))
    return bytes(out)


def _decode_psmt4(data: bytes, img: int, width: int, height: int,
                  palette: Sequence[Tuple[int, int, int, int]],
                  image_size: int, swizzled: bool = True) -> bytes:
    out = bytearray(width * height * 4)
    padded_w = max(width, 32)
    logw = max(0, (padded_w - 1).bit_length())
    mask = (1 << (logw + 2)) - 1
    for y in range(height):
        row = y * width * 4
        for x in range(width):
            if swizzled:
                xx = x ^ (((((y >> 1) & 1) ^ ((y >> 2) & 1)) << 2))
                nx = (xx & 7) | ((xx >> 1) & ~7)
                ny = (y & 1) | ((y >> 1) & ~1)
                n = ((y >> 1) & 1) | (((xx >> 3) & 1) << 1)
                s = (n | (nx << 2) | (ny << (logw + 1))) & mask
                byte_index = (y * padded_w // 2) + (s >> 1)
                nibble = s & 1
            else:
                pixel = y * width + x
                byte_index = pixel >> 1
                nibble = pixel & 1
            if byte_index >= image_size:
                idx = 0
            elif nibble:
                idx = (data[img + byte_index] >> 4) & 0xF
            else:
                idx = data[img + byte_index] & 0xF
            r, g, b, a = palette[idx] if idx < len(palette) else (0, 0, 0, 255)
            p = row + x * 4
            out[p:p + 4] = bytes((r, g, b, a))
    return bytes(out)


def _tim2_header(data: bytes, offset: int):
    if data[offset:offset + 4] != b"TIM2":
        raise PzzError("missing TIM2 magic at %#x" % offset)

    for picture_at in (0x10, 0x80):
        if offset + picture_at + 0x30 > len(data):
            continue
        total, clut_size, image_size = struct.unpack_from(
            "<3I", data, offset + picture_at)
        header_size = struct.unpack_from("<H", data, offset + picture_at + 0x0C)[0]
        clut_colors = struct.unpack_from("<H", data, offset + picture_at + 0x0E)[0]
        image_format, mipmaps, clut_type, bpp = struct.unpack_from(
            "<4B", data, offset + picture_at + 0x10)
        width, height = struct.unpack_from(
            "<2H", data, offset + picture_at + 0x14)
        if width <= 0 or height <= 0 or width > 4096 or height > 4096:
            continue
        if bpp not in (1, 2, 3, 4, 5):
            continue
        if header_size < 0x30 or header_size > 0x400:
            continue
        if image_format != 0 or mipmaps < 1:
            continue

        expected = {
            5: width * height,
            4: (width * height + 1) // 2,
            3: width * height * 4,
            2: width * height * 3,
            1: width * height * 2,
        }[bpp]
        if image_size < expected:
            continue

        img_off = offset + picture_at + header_size
        clut_off = img_off + image_size

        if img_off + image_size > len(data):
            continue
        if clut_colors and clut_off + clut_size > len(data):
            continue
        if total and offset + total > len(data) + 0x10:
            continue

        # GvZ stores a per-image layout flag in the last reserved byte of the
        # TIM2 file header. 1 = PSMT8/PSMT4 data in GS swizzled layout,
        # 0 = plain linear scanlines.
        swizzled = data[offset + 0x0F] != 0
        tex0 = struct.unpack_from("<Q", data, offset + picture_at + 0x18)[0]
        return (total, clut_size, image_size, header_size, clut_colors,
                image_format, mipmaps, clut_type, bpp, width, height,
                img_off, clut_off, swizzled, tex0)

    raise PzzError("invalid or truncated TIM2 header at %#x" % offset)


def parse_tim2(data: bytes, offset: int) -> TextureImage:
    (total, clut_size, image_size, header_size, clut_colors,
     image_format, mipmaps, clut_type, bpp, width, height,
     img, clut, swizzled, tex0) = _tim2_header(data, offset)

    if bpp in (1, 2, 3):
        out = bytearray(width * height * 4)
        if bpp == 3:
            for i in range(width * height):
                r, g, b, a = data[img + i * 4:img + i * 4 + 4]
                out[i * 4:i * 4 + 4] = bytes((r, g, b, min(a * 2, 255)))
        elif bpp == 1:
            for i in range(width * height):
                v = struct.unpack_from("<H", data, img + i * 2)[0]
                r = (v & 0x1F) * 255 // 31
                g = ((v >> 5) & 0x1F) * 255 // 31
                b = ((v >> 10) & 0x1F) * 255 // 31
                a = 255 if (v & 0x8000) else 0
                out[i * 4:i * 4 + 4] = bytes((r, g, b, a))
        else:
            for i in range(width * height):
                r, g, b = data[img + i * 3:img + i * 3 + 3]
                out[i * 4:i * 4 + 4] = bytes((r, g, b, 255))
        return TextureImage(width, height, bytes(out), offset)

    if clut_colors <= 0 or clut_size <= 0:
        raise PzzError("indexed TIM2 without palette at %#x" % offset)

    color_size = clut_size // clut_colors
    if color_size == 4:
        palette = _palette_rgba8(data, clut, clut_colors)
    elif color_size == 2:
        palette = _palette_rgba5551(data, clut, clut_colors)
    else:
        raise PzzError("unsupported TIM2 palette entry size %d" % color_size)

    # CSM (GsTex bit 55): 0 = CSM1 swizzled CLUT, 1 = CSM2 linear CLUT.
    if ((tex0 >> 55) & 1) == 0 and clut_colors >= 32:
        palette = _deshuffle_palette(palette)

    if bpp == 5:
        rgba = _decode_psmt8(data, img, width, height, palette, image_size,
                             swizzled)
    else:
        rgba = _decode_psmt4(data, img, width, height, palette, image_size,
                             swizzled)
    return TextureImage(width, height, rgba, offset)


def _tim2_offsets(data: bytes) -> List[int]:
    if data[:4] == b"TIM2":
        return [0]
    offsets = []
    for o in range(0x800, max(0x800, len(data) - 3), 0x800):
        if data[o:o + 4] != b"TIM2":
            continue
        try:
            _tim2_header(data, o)
        except PzzError:
            continue
        offsets.append(o)
    return offsets


def parse_texture_bank(data: bytes) -> List[TextureImage]:
    textures = []
    for o in _tim2_offsets(data):
        try:
            textures.append(parse_tim2(data, o))
        except PzzError:
            continue
    return textures


def looks_like_texture_bank(data: bytes) -> bool:
    if len(data) < 4:
        return False
    if data[:4] == b"TIM2":
        return True
    if len(data) < 0x808:
        return False
    return data[0x800:0x804] == b"TIM2"


@dataclass
class ModelEntry:
    index: int
    model: Model


@dataclass
class BankEntry:
    index: int
    textures: List[TextureImage]


@dataclass
class Archive:
    models: List[ModelEntry] = field(default_factory=list)
    banks: List[BankEntry] = field(default_factory=list)
    ignored: List[int] = field(default_factory=list)
    errors: List[Tuple[int, str]] = field(default_factory=list)


def classify_archive(entries: Sequence[PzzEntry]) -> Archive:
    archive = Archive()
    for entry in entries:
        data = entry.data
        if not data:
            archive.ignored.append(entry.index)
            continue
        try:
            if looks_like_texture_bank(data):
                textures = parse_texture_bank(data)
                archive.banks.append(BankEntry(entry.index, textures))
                if not textures:
                    archive.errors.append(
                        (entry.index, "texture bank has no decodable TIM2 images"))
                continue
            if looks_like_model(data):
                archive.models.append(
                    ModelEntry(entry.index, parse_model(data)))
                continue
        except Exception as exc:
            message = (str(exc) if isinstance(exc, PzzError)
                       else "%s: %s" % (type(exc).__name__, exc))
            archive.errors.append((entry.index, message))
            archive.ignored.append(entry.index)
            continue
        archive.ignored.append(entry.index)
    return archive


def _dimension_counts(textures: Sequence[TextureRef]) -> Dict[Tuple[int, int], int]:
    counts: Dict[Tuple[int, int], int] = {}
    for tex in textures:
        key = (tex.width, tex.height)
        counts[key] = counts.get(key, 0) + 1
    return counts


def pair_models_and_banks(archive: Archive) -> Dict[int, BankEntry]:
    assignments: Dict[int, BankEntry] = {}
    used = set()
    for mentry in archive.models:
        unique = mentry.model.unique_textures()
        want_dims = _dimension_counts(unique)
        best = None
        best_score = None
        for bank in archive.banks:
            have_dims = _dimension_counts(
                [TextureRef("", "", t.width, t.height) for t in bank.textures]
            )
            dim_score = sum(min(n, have_dims.get(dim, 0)) for dim, n in want_dims.items())
            exact = (len(unique) == len(bank.textures)
                     and dim_score == len(unique))
            score = (2 if exact else 0,
                     0 if bank.index in used else 1,
                     dim_score,
                     4 if len(unique) == len(bank.textures) else 0)
            if best is None or score > best_score:
                best, best_score = bank, score
        if best is not None and best_score[2] > 0:
            assignments[mentry.index] = best
            used.add(best.index)
    return assignments


def strip_faces(flags: Sequence[bytes]) -> List[Tuple[int, int, int]]:
    faces: List[Tuple[int, int, int]] = []

    def emit(strip: List[int]) -> None:
        for j in range(2, len(strip)):
            if j % 2 == 0:
                faces.append((strip[j - 2], strip[j - 1], strip[j]))
            else:
                faces.append((strip[j - 1], strip[j - 2], strip[j]))

    strip: List[int] = []
    for i, flag in enumerate(flags):
        if flag == STRIP_FLAG:
            if len(strip) >= 3:
                emit(strip)
            strip = [i - 1, i] if i > 0 else [i]
        else:
            strip.append(i)
    if len(strip) >= 3:
        emit(strip)
    return faces


def transform_vertex(matrix: Sequence[float], x: float, y: float, z: float):
    tx = matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12]
    ty = matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13]
    tz = matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14]
    tw = matrix[3] * x + matrix[7] * y + matrix[11] * z + matrix[15]
    if tw not in (0.0, 1.0):
        tx, ty, tz = tx / tw, ty / tw, tz / tw
    return (tx, -tz, ty)


IDENTITY_MATRIX = (1.0, 0.0, 0.0, 0.0,
                   0.0, 1.0, 0.0, 0.0,
                   0.0, 0.0, 1.0, 0.0,
                   0.0, 0.0, 0.0, 1.0)


def mat_mul(a: Sequence[float], b: Sequence[float]) -> Tuple[float, ...]:
    return tuple(
        sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4))
        for r in range(4) for c in range(4)
    )


def chunk_world_matrices(model: Model) -> List[Tuple[float, ...]]:
    """Compose each chunk's local matrix through its parent chain.

    The chunk matrix at +0x00 is local to the parent named at +0x6C.
    A parent of -1 marks a root. Cycles and forward references are guarded.
    """
    world: List[Optional[Tuple[float, ...]]] = [None] * len(model.chunks)

    def resolve(index: int, stack: set) -> Tuple[float, ...]:
        cached = world[index]
        if cached is not None:
            return cached
        chunk = model.chunks[index]
        parent = chunk.parent
        if (parent < 0 or parent >= len(model.chunks)
                or parent == index or parent in stack):
            result = tuple(chunk.matrix)
        else:
            # GvZ matrices use row-vector composition: local then parent.
            result = mat_mul(chunk.matrix, resolve(parent, stack | {index}))
        world[index] = result
        return result

    for i in range(len(model.chunks)):
        resolve(i, set())
    return [matrix for matrix in world if matrix is not None]
