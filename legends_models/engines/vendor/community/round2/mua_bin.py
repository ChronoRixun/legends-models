#!/usr/bin/env python3
"""List/extract MUA1/MUA2 PC .bin archives (version 1, flags 0x11).
Paths include the archive stem: textures.bin -> textures/...
Only the Python standard library is required. Existing files are not overwritten.
"""
import argparse
import fnmatch
import struct
import sys
import zlib
from pathlib import Path


def require(ok, message):
    if not ok:
        raise ValueError(message)


def decrypt(data):
    out = bytearray(data)
    signed = ((len(out) + 0x55) & 0xffff)
    if signed >= 0x8000:
        signed -= 0x10000
    state = (((signed << 16) | signed) ^ 0x03b9a1d9) & 0x0fffffff
    for pos in range(0, len(out) - 3, 4):
        state = (state * 0x3fd + 1) & 0x0fffffff
        word = struct.unpack_from("<I", out, pos)[0]
        struct.pack_into("<I", out, pos, word ^ state)
    return out


def inflate(data, expected):
    stream = zlib.decompressobj()
    out = stream.decompress(decrypt(data), expected + 1)
    require(len(out) == expected, "Decompressed size mismatch")
    require(stream.eof and not stream.unused_data
            and not stream.unconsumed_tail, "Incomplete/trailing zlib stream")
    return out


def read_exact(handle, size):
    data = handle.read(size)
    require(len(data) == size, "Truncated archive")
    return data


def safe_parts(name):
    parts = name.replace("\\", "/").split("/")
    reserved = {"CON", "PRN", "AUX", "NUL"}
    reserved.update("COM" + str(i) for i in range(1, 10))
    reserved.update("LPT" + str(i) for i in range(1, 10))
    require(all(p and p not in (".", "..") and not p.endswith((" ", "."))
                and not any(ord(c) < 32 or c in '<>:"|?*' for c in p)
                and p.split(".")[0].upper() not in reserved
                for p in parts), "Unsafe path: " + repr(name))
    return parts


class Archive:
    def __init__(self, filename):
        self.path = Path(filename)
        total = self.path.stat().st_size
        with self.path.open("rb") as f:
            magic, version, flags, count, tc, nu, nc, maximum = (
                struct.unpack("<IHHIIIII", read_exact(f, 28)))
            require(magic == 0xb19aca6e, "Wrong archive magic")
            require((version, flags) == (1, 0x11),
                    "Unsupported version/flags; expected 1/0x11")
            self.base = 28 + tc + nc
            require(self.base <= total, "Metadata outside archive")
            toc = inflate(read_exact(f, tc), count * 40)
            names = inflate(read_exact(f, nc), nu)
        self.entries = []
        seen = set()
        for ignored, nameoff, offset, packed, unpacked in struct.iter_unpack("<5Q", toc):
            require(nameoff < len(names), "Filename offset outside table")
            end = names.find(b"\0", nameoff)
            require(end >= 0, "Unterminated filename")
            name = names[nameoff:end].decode("utf-8")
            name = "/".join(safe_parts(self.path.stem) + safe_parts(name))
            require(name.casefold() not in seen, "Duplicate output path: " + name)
            seen.add(name.casefold())
            absolute = self.base + offset
            require(absolute + packed <= total, "Payload outside archive: " + name)
            self.entries.append((name, absolute, packed, unpacked))
        require(max((e[3] for e in self.entries), default=0) == maximum,
                "Maximum file size mismatch")

    def read(self, entry):
        name, offset, packed, unpacked = entry
        with self.path.open("rb") as f:
            f.seek(offset)
            return inflate(read_exact(f, packed), unpacked)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list")
    listing.add_argument("archive")
    extract = sub.add_parser("extract")
    extract.add_argument("archive")
    extract.add_argument("out_dir")
    extract.add_argument("--filter", default="*",
                         help="Case-sensitive glob over full or archive-relative path")
    args = parser.parse_args(argv)
    archive = Archive(args.archive)
    if args.command == "list":
        print("OFFSET\tPACKED\tSIZE\tPATH")
        for name, offset, packed, size in archive.entries:
            print(f"0x{offset:x}\t{packed}\t{size}\t{name}")
        return
    root = Path(args.out_dir).resolve()
    jobs = []
    for entry in archive.entries:
        name = entry[0]
        relative = name.split("/", 1)[1]
        if not (fnmatch.fnmatchcase(name, args.filter)
                or fnmatch.fnmatchcase(relative, args.filter)):
            continue
        target = root.joinpath(*safe_parts(name))
        require(target.resolve().is_relative_to(root),
                "Output escapes destination: " + name)
        require(not target.exists() and not target.is_symlink(),
                "Output already exists: " + str(target))
        jobs.append((entry, target))
    for entry, target in jobs:
        data = archive.read(entry)  # Verify size and Adler-32 before opening output.
        target.parent.mkdir(parents=True, exist_ok=True)
        require(target.resolve().is_relative_to(root),
                "Output escapes destination: " + entry[0])
        with target.open("xb") as f:
            f.write(data)
        print(entry[0])


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, struct.error, zlib.error) as exc:
        sys.exit(f"mua_bin: {exc}")
