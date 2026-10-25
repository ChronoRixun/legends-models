"""Throwaway MUA1 Wolverine to XML2 v6 skin conversion probe."""
import hashlib
import json
import re
import struct
import os
from pathlib import Path

from igb_file import IgbFile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "extracted/mua1/actors/0301.igb"
TEMPLATE = Path(os.environ.get("LEGENDS_PORT", "."), "Actors/14301.IGB")  # legends-models: was a research-PC path
OUTPUT = ROOT / "prototype/14301.IGB"
SUMMARY = ROOT / "prototype/conversion.json"


def put_int(igb, obj, slot, value):
    struct.pack_into("<i", igb.buf, obj.fields[slot].offset, value)
    obj.fields[slot].value = value


def blob(igb, ref):
    offset, size = igb.block(ref)
    return igb.data[offset:offset + size]


def geometry_attribute(igb, geometry):
    for ref in igb.list_items(geometry.get(8)) or []:
        attr = igb.obj(ref)
        if attr is not None and (igb.isa(attr, "igGeometryAttr") or attr.name == "igGeometryAttr2"):
            return attr
    raise ValueError(f"no drawable attribute on {geometry.get(2)!r}")


def dxt1_to_dxt3(data):
    if len(data) % 8:
        raise ValueError("partial DXT1 block")
    out = bytearray()
    for offset in range(0, len(data), 8):
        out += b"\xff" * 8
        out += data[offset:offset + 8]
    return bytes(out)


def convert():
    source_data = SOURCE.read_bytes()
    template_data = TEMPLATE.read_bytes()
    source = IgbFile(source_data)
    target = IgbFile(template_data)
    if source.header[11] != 0xF0000008 or target.header[11] != 0xF0000006:
        raise ValueError("unexpected IGB versions")

    source_geometry = next(o for o in source.objects_of("igGeometry") if o.get(2) == "0301")
    source_attr = geometry_attribute(source, source_geometry)
    source_va = source.obj(source_attr.get(4))
    vertex_data = {}
    for ref in source.list_items(source_va.get(3)) or []:
        item = source.obj(ref)
        vertex_data[item.get(4)] = source.obj(item.get(3))
    positions = blob(source, vertex_data[1].get(4))
    normals = blob(source, vertex_data[3].get(4))
    uvs = blob(source, vertex_data[4].get(4))
    weights = blob(source, vertex_data[5].get(4))
    source_indices = blob(source, vertex_data[6].get(4))
    vertex_count = vertex_data[1].get(2)
    if (len(positions), len(normals), len(uvs)) != (12 * vertex_count, 12 * vertex_count, 8 * vertex_count):
        raise ValueError("unexpected source vertex stream sizes")
    if len(weights) != 16 * vertex_count or len(source_indices) != 4 * vertex_count:
        raise ValueError("source mesh is not four-weight skinned")

    source_skeleton = source.objects_of("igSkeleton")[0]
    target_skeleton = target.objects_of("igSkeleton")[0]
    source_bones = [source.obj(r) for r in source.list_items(source_skeleton.get(4))]
    target_bones = [target.obj(r) for r in target.list_items(target_skeleton.get(4))]
    source_by_name = {b.get(2): (i, b) for i, b in enumerate(source_bones)}
    target_by_name = {b.get(2): (i, b) for i, b in enumerate(target_bones)}
    source_to_target = {"Unnamed bone": 0}
    for i, bone in enumerate(target_bones):
        source_name = "Unnamed bone" if i == 0 else bone.get(2)
        if source_name not in source_by_name:
            raise ValueError(f"target bone {source_name!r} absent from MUA")
        source_to_target[source_name] = i

    original_palette = target.list_items(target.objects_of("igBlendMatrixSelect")[0].get(10))
    target_matrix_by_name = {b.get(2): b.get(4) for b in target_bones if b.get(4) >= 0}
    bip_matrix = max(target_matrix_by_name.values()) + 1
    target_matrix_by_name["Bip01"] = bip_matrix
    target_palette = original_palette + [bip_matrix]
    target_palette_position = {matrix: i for i, matrix in enumerate(target_palette)}
    if len(target_palette) != 33 or set(target_palette) != set(range(33)):
        raise ValueError("unexpected target blend palette")

    source_local = blob(source, source_skeleton.get(3))
    target_local = bytearray(blob(target, target_skeleton.get(3)))
    source_matrices = blob(source, source_skeleton.get(5))
    target_matrices = bytearray(33 * 64)
    for i, bone in enumerate(target_bones):
        source_name = "Unnamed bone" if i == 0 else bone.get(2)
        source_index, source_bone = source_by_name[source_name]
        source_parent = source_bone.get(3)
        if source_parent >= 0:
            parent_name = source_bones[source_parent].get(2)
            parent = source_to_target[parent_name]
        else:
            parent = -1
        put_int(target, bone, 3, parent)
        if bone.get(2) == "Bip01":
            put_int(target, bone, 4, bip_matrix)
        if i:
            target_local[i * 12:i * 12 + 12] = source_local[source_index * 12:source_index * 12 + 12]
        matrix = target_matrix_by_name.get(bone.get(2), -1)
        if matrix >= 0:
            source_matrix = source_bone.get(4)
            target_matrices[matrix * 64:matrix * 64 + 64] = source_matrices[source_matrix * 64:source_matrix * 64 + 64]
    put_int(target, target_skeleton, 6, 33)

    source_bms = source.objects_of("igBlendMatrixSelect")[0]
    source_palette = source.list_items(source_bms.get(10))
    source_name_by_matrix = {b.get(4): b.get(2) for b in source_bones if b.get(4) >= 0}
    remap = {"fx01": "Bip01 L Hand", "fx02": "Bip01 R Hand"}
    remapped_indices = bytearray()
    used_names = set()
    for palette_index in source_indices:
        source_matrix = source_palette[palette_index]
        source_name = source_name_by_matrix[source_matrix]
        target_name = remap.get(source_name, source_name)
        if target_name not in target_matrix_by_name:
            raise ValueError(f"no target blend matrix for {source_name!r}")
        used_names.add(target_name)
        remapped_indices.append(target_palette_position[target_matrix_by_name[target_name]])

    target_geometry = next(o for o in target.objects_of("igGeometry") if o.get(2) == "14301")
    target_attr = geometry_attribute(target, target_geometry)
    target_va = target.obj(target_attr.get(4))
    indirect = struct.unpack("<20i", blob(target, target_va.get(2)))
    if min(indirect[0], indirect[1], indirect[11]) < 0:
        raise ValueError("target vertex streams are incomplete")
    put_int(target, target_va, 3, vertex_count)
    put_int(target, target_va, 6, 0x10443)

    source_index_array = source.obj(source_attr.get(5))
    index_data = blob(source, source_index_array.get(2))
    indices = struct.unpack(f"<{len(index_data) // 2}H", index_data)
    if not indices or max(indices) >= vertex_count or len(indices) != 3 * source_attr.get(8):
        raise ValueError("unexpected source triangle list")
    target_index_array = target.obj(target_attr.get(5))
    put_int(target, target_attr, 6, 3)
    put_int(target, target_attr, 7, source_attr.get(8))
    put_int(target, target_attr, 13, -1)
    put_int(target, target_index_array, 3, len(indices))

    target_bms = target.objects_of("igBlendMatrixSelect")[0]
    target_palette_list = target.obj(target_bms.get(10))
    put_int(target, target_palette_list, 2, len(target_palette))
    put_int(target, target_palette_list, 3, len(target_palette))

    outline = next(o for o in target.objects_of("igGeometry") if o.get(2) == "14301_outline")
    detached = 0
    for obj in target.objects_of("igNodeList"):
        if outline.ref in (target.list_items(obj.ref) or []):
            put_int(target, obj, 2, 0)
            put_int(target, obj, 3, 0)
            put_int(target, obj, 4, -1)
            detached += 1
    if detached != 1:
        raise ValueError("could not detach the old outline")

    source_images = {}
    for image in source.objects_of("igImage"):
        if re.search(r"_d(?:-\d+)?\.png$", image.get(22).lower()):
            key = (image.get(2), image.get(3))
            if key in source_images:
                raise ValueError(f"duplicate diffuse mip {key}")
            source_images[key] = image
    if len(source_images) != 10:
        raise ValueError("incomplete MUA diffuse mip chain")
    texture_sizes = {}
    for image in target.objects_of("igImage"):
        key = (image.get(2), image.get(3))
        source_image = source_images[key]
        if source_image.get(11) != 14:
            raise ValueError("source diffuse image is not DXT1")
        pixels = dxt1_to_dxt3(blob(source, source_image.get(13)))
        expected = ((image.get(2) + 3) // 4) * ((image.get(3) + 3) // 4) * 16
        if len(pixels) != expected:
            raise ValueError(f"bad mip size for {key}")
        put_int(target, image, 11, 15)
        put_int(target, image, 12, len(pixels))
        put_int(target, image, 19, 4 * image.get(2))
        texture_sizes[image.get(13)] = pixels

    coordinates = struct.unpack(f"<{3 * vertex_count}f", positions)
    minimum = [min(coordinates[c::3]) for c in range(3)]
    maximum = [max(coordinates[c::3]) for c in range(3)]
    boxes = target.objects_of("igAABox")
    if len(boxes) != 1:
        raise ValueError("unexpected target bounding-box count")
    target.set_floats(boxes[0], 2, minimum)
    target.set_floats(boxes[0], 3, maximum)

    blocks = {
        indirect[0]: positions,
        indirect[1]: normals,
        indirect[11]: uvs,
        target_va.get(7): weights,
        target_va.get(8): bytes(remapped_indices),
        target_index_array.get(2): index_data,
        target_skeleton.get(3): bytes(target_local),
        target_skeleton.get(5): bytes(target_matrices),
        target_palette_list.get(4): struct.pack(f"<{len(target_palette)}i", *target_palette),
    }
    blocks.update(texture_sizes)
    result = target.rebuilt(blocks)
    check = IgbFile(result)
    if check.header[11] != 0xF0000006:
        raise ValueError("output version changed")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_bytes(result)
    summary = {
        "prototype": "throwaway",
        "source": str(SOURCE.relative_to(ROOT)),
        "source_sha256": hashlib.sha256(source_data).hexdigest(),
        "template": "port Actors/14301.IGB",
        "template_sha256": hashlib.sha256(template_data).hexdigest(),
        "output": str(OUTPUT.relative_to(ROOT)),
        "output_sha256": hashlib.sha256(result).hexdigest(),
        "source_igb_version": "0xf0000008",
        "output_igb_version": "0xf0000006",
        "vertices": vertex_count,
        "triangles": len(indices) // 3,
        "weights_per_vertex": 4,
        "blend_palette_entries": len(target_palette),
        "source_bones": len(source_bones),
        "target_bones": len(target_bones),
        "weighted_bones_after_fx_approximation": sorted(used_names),
        "fx_approximation": remap,
        "animation_database_retained": "port Actors/x1_03_wolverine.IGB",
        "diffuse_mips_converted": len(texture_sizes),
        "diffuse_source_max": [512, 512],
        "diffuse_output_max": [128, 128],
        "normal_specular_tangent_streams": "omitted from this probe",
        "outline": "old port outline detached; no MUA outline generated",
        "bounds_min": minimum,
        "bounds_max": maximum,
        "output_bytes": len(result),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ["output", "vertices", "triangles", "blend_palette_entries", "diffuse_mips_converted", "output_bytes"]}, indent=2))


if __name__ == "__main__":
    convert()
