"""Reusable helpers copied from the round-2 fitter; historical CLI omitted."""
import struct

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
