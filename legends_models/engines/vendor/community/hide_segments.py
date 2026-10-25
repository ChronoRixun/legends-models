"""Hide named stock sub-skin geometries (zero primitives) in a converted actor: used when the community skin already
contains that part (Juggernaut head + helmet) and the stock part would sample the replaced texture with stock UVs."""
import hashlib, json, struct, sys
from pathlib import Path
sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / 'round2'))
import community_convert  # noqa: F401  (installs the community model reader)
from community_igb import IgbFile
from community_model import geometry_attribute

def hide(data, names):
    f = IgbFile(data)
    done = []
    for g in f.objects_of('igGeometry'):
        if g.get(2) in names:
            ga = geometry_attribute(f, g)
            before = ga.get(7)
            struct.pack_into('<i', f.buf, ga.fields[7].offset, 0)
            done.append((g.get(2), before))
    out = f.edited()
    chk = IgbFile(out)
    assert all(o.decoded for o in chk.objects)
    return out, done

if __name__ == '__main__':
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    out, done = hide(src.read_bytes(), set(sys.argv[3:]))
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
    print(json.dumps({'hidden': done, 'sha256': hashlib.sha256(out).hexdigest()}))
