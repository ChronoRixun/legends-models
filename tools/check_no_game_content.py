#!/usr/bin/env python3
"""check_no_game_content.py - keep game content, personal data and big binaries out of this repository.

The Legends Classic repository's guard, used unchanged by legends-models (the CI workflow
.github/workflows/content-guard.yml runs it). Standard library only, Python 3.8+,
Windows / Linux / macOS.

This project is bring-your-own-copy: the repository holds our code and our notes, never anything from the games.
The guard fails when a file looks like a game file, a decoded game file, game text, a disassembly or decompiler
listing, personal data or a secret, or is simply too big. It is meant to run in three places:

  pre-commit      python tools/check_no_game_content.py --staged
                  (checks the staged content - exactly what the commit will contain)
  CI, every push  python tools/check_no_game_content.py --all
  CI, PRs         python tools/check_no_game_content.py --range origin/main..HEAD
                  (every blob any commit in the range adds or changes, so "add then delete" is caught too:
                  git history is forever)
  once, before    python tools/check_no_game_content.py --history
  the first push  (every blob reachable from any ref)

  also:           python tools/check_no_game_content.py PATH [PATH ...]     files or folders on disk
                  python tools/check_no_game_content.py --self-test

Allowlist: `.content-guard-allow` at the repository root (override with --allow). One entry per line:

    RULE  GLOB  # reason (required)

RULE is a rule id below or `*`; GLOB is matched against the repository-relative path with forward slashes
(fnmatch: `*` also crosses `/`). An entry without a reason is refused. Entries that match nothing are reported as
warnings so the list stays honest.

Local deny patterns (never committed): regular expressions, one per line, in `.git/info/content-guard-deny` and/or
the file named by the CONTENT_GUARD_DENY environment variable. Put your own name, host name, LAN prefix, drive
layout there; a match is a PERSONAL_PATTERN error. Keeping them out of the repo keeps them out of public view.

Rules (error unless marked warn):
  GAME_EXT         a game-file, disc-image, disassembly-database or binary-executable extension
  GAME_MAGIC       content with a known game-format signature, whatever the extension
  MEDIA            images / audio / video (screenshots and textures are game imagery; original art must be allowlisted)
  BINARY           any other binary content
  SIZE             larger than --max-size (default 1 MiB)
  DECODED_XML      lines of decoded Raven XML (zones, packages, stats, missions, conversations, menus, subtitles)
  GAME_TEXT_ATTR   an XML attribute that carries prose (text=, descname=, description=, ...) of 4+ words
  GAME_TEXT_JSON   a JSON key that carries prose (descname, description, text, bio, ...) of 4+ words
  DISASM           instruction listings (5+ lines) or decompiler output
  RESEARCH_TXT     a .txt file under research/ (research outputs are regenerated, not committed) - allowlist the
                   reports that are ours
  PERSONAL_PATH    a user-profile path with a real user name (C:\\Users\\<name>, /home/<name>, ...)
  PRIVATE_IP       a private, CGNAT or link-local IPv4 address (documentation ranges and loopback are fine)
  EMAIL            an e-mail address (noreply / example domains are fine)
  PRIVATE_LINK     a link to a private chat / artifact page
  SECRET           an API token or private key
  PERSONAL_PATTERN a local deny pattern matched
  LONG_ID (warn)   a 17-20 digit number (Discord / platform ids are public, but personal ones belong elsewhere)

Exit codes: 0 clean (warnings allowed), 1 violations, 2 usage or git error.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Tuple

MAX_SIZE_DEFAULT = 1024 * 1024
TEXT_SCAN_LIMIT = 8 * 1024 * 1024        # never scan more than this much of one file as text
ALLOW_FILE = ".content-guard-allow"

# ------------------------------------------------------------------------------------------------ extensions
GAME_EXTENSIONS = {
    # Alchemy / Raven engine files (XML1, XML2, MUA)
    ".igb", ".igz", ".xmlb", ".engb", ".freb", ".gerb", ".itab", ".spab", ".polb", ".rusb", ".chrb", ".navb",
    ".boyb", ".pkgb", ".fb", ".zss", ".zsm", ".zsd", ".zsnd", ".bnx", ".xpr", ".xbx", ".xbe", ".xex", ".sfd",
    ".adx", ".aix", ".ahx", ".xwb", ".xsb", ".xgs", ".xma", ".bik", ".usm",
    # XML1 Xbox loose text formats (decoded they are still the game's files)
    ".eng", ".fre", ".ger", ".ita", ".spa",
    # disc images and dumps
    ".iso", ".xiso", ".cci", ".cso", ".gcm", ".rvz", ".wbfs", ".nkit", ".chd", ".cue", ".mdf", ".mds", ".nrg",
    # executables and native libraries (release artifacts come from CI, never from git)
    ".exe", ".dll", ".sys", ".pyd", ".so", ".dylib", ".msi",
    # disassembly / decompiler databases and listings
    ".asm", ".idb", ".i64", ".til", ".nam", ".id0", ".id1", ".bndb", ".gpr", ".gzf", ".rep", ".lst",
    # save files
    ".sav", ".xsv",
}
MEDIA_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tga", ".dds", ".tif", ".tiff", ".ico", ".psd",
    ".wav", ".ogg", ".mp3", ".flac", ".wma", ".m4a", ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".webm", ".mpg",
}
TEXTUAL_EXTENSIONS = {".txt", ".md", ".py", ".json", ".jsonl", ".toml", ".yml", ".yaml", ".ini", ".cfg", ".sh",
                      ".ps1", ".bat", ".cmd", ".c", ".h", ".cpp", ".hpp", ".java", ".js", ".ts", ".css", ".html",
                      ".xml", ".csv", ".tsv", ".rst", ""}


# ------------------------------------------------------------------------------------------------ signatures
def _u32(data: bytes, off: int) -> Optional[int]:
    return struct.unpack_from("<I", data, off)[0] if len(data) >= off + 4 else None


def _nul_padded_ascii(chunk: bytes) -> bool:
    """True for b'some/name.ext\\0\\0...': printable ASCII, then NULs only."""
    end = chunk.find(b"\0")
    if end <= 0:
        return False
    return all(32 <= c < 127 for c in chunk[:end]) and not chunk[end:].strip(b"\0")


def sniff_magic(data: bytes) -> Optional[str]:
    """Name of a known game / media format when `data` (the start of a file) has its signature."""
    head = data[:16]
    if head.startswith(b"XBEH"):
        return "Xbox executable (XBE)"
    if head.startswith(b"ZSNDXBOX") or head.startswith(b"ZSNDPC") or head.startswith(b"ZSND"):
        return "Raven ZSND sound bank"
    if _u32(data, 0) == 0x11B1 and _u32(data, 4) in (0, 1, 2):
        return "Raven binary XML (XMLB family: .xmlb .engb .chrb .navb .boyb .pkgb)"
    if len(data) >= 0x30 and _u32(data, 0x28) == 0xFADA:
        return "Alchemy IGB"
    if head.startswith(b"XPR0") or head.startswith(b"XPR1") or head.startswith(b"XPR2"):
        return "Xbox texture bundle (XPR)"
    if len(data) >= 0x10014 and data[0x10000:0x10014] == b"MICROSOFT*XBOX*MEDIA":
        return "Xbox disc image (XDVDFS)"
    if len(data) >= 0x8006 and data[0x8001:0x8006] == b"CD001":
        return "ISO 9660 disc image"
    if len(data) >= 0x20 and _u32(data, 0x1C) == 0x3D9F33C2:
        return "GameCube disc image"
    if head[:4] == b"\x00\x00\x01\xba" and b"SofdecStream" in data[:0x4000]:
        return "Sofdec movie (SFD)"
    if head[:2] == b"\x80\x00" and b"(c)CRI" in data[:0x200]:
        return "CRI ADX audio"
    if head.startswith(b"MZ") and len(data) >= 0x40:
        pe = _u32(data, 0x3C)
        if pe is not None and data[pe:pe + 4] == b"PE\0\0":
            return "Windows executable / DLL (PE)"
    if head.startswith(b"\x7fELF"):
        return "ELF executable"
    if head.startswith(b"CISO") or head.startswith(b"ZISO"):
        return "compressed disc image (CSO/ZSO)"
    # XML1 .fb bundle record: name[0x80] (NUL padded path), type[0x40], u32 size, data
    if len(data) >= 0xC4 and _nul_padded_ascii(data[:0x80]) and _nul_padded_ascii(data[0x80:0xC0]):
        name = data[:0x80].split(b"\0", 1)[0]
        size = _u32(data, 0xC0) or 0
        if (b"/" in name or b"." in name) and 0 < size < (1 << 31):
            return "XML1 .fb bundle"
    return None


def sniff_media(data: bytes) -> Optional[str]:
    head = data[:16]
    if head.startswith(b"\x89PNG"):
        return "PNG image"
    if head[:3] == b"\xff\xd8\xff":
        return "JPEG image"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "GIF image"
    if head[:4] == b"DDS ":
        return "DDS texture"
    if head[:2] == b"BM" and len(data) > 26 and _u32(data, 2) == len(data):
        return "BMP image"
    if head[:4] == b"RIFF" and head[8:12] in (b"WAVE", b"AVI ", b"WEBP"):
        return {b"WAVE": "WAV audio", b"AVI ": "AVI video", b"WEBP": "WebP image"}[head[8:12]]
    if head[:4] == b"OggS":
        return "Ogg audio"
    if head[:3] == b"ID3" or head[:2] == b"\xff\xfb":
        return "MP3 audio"
    if head[4:8] == b"ftyp":
        return "MP4 / MOV video"
    return None


def is_binary(data: bytes) -> bool:
    """git's own heuristic: a NUL byte in the first 8000 bytes."""
    return b"\0" in data[:8000]


# ------------------------------------------------------------------------------------------------ text rules
RAVEN_ELEMENTS = (
    "world|entity|precache|zoneinfo|zone|loadpointlist|loadpoint|SubTitles|conversation|startCondition|"
    "participant|line|response|MENU|animtext|mark|packagedef|actorskin|actoranimdb|xml_resident|combat_is|"
    "bigconvmap|characters|stats|talent|talentvalues|talentvalue|Race|MISSIONS|MISSION|OBJECTIVE|dialog|"
    "SOUNDTABLE|sounds|powerstyle|fightstyle|boltonactoranims|inst|motionpath|monster_spawner|codex|trivia|"
    "credits|herostat|npcstat|items|item_type|shared_talents|combat_events"
)
DECODED_XML_LINE = re.compile(r"^\s*<(?:%s)\b[^>\n]*(?:=\"|/?>)" % RAVEN_ELEMENTS, re.I | re.M)
DECODED_XML_MIN = {".md": 8}            # design notes may show a short schema excerpt; everything else: 3
DECODED_XML_MIN_DEFAULT = 3

PROSE_ATTRS = r"text|descname|description|descshort|desctext\d*|bio|subtitle|objectivetext|hint|tooltip"
TEXT_ATTR = re.compile(r"\b(?:%s)\s*=\s*\"([^\"\n]{12,})\"" % PROSE_ATTRS, re.I)
TEXT_JSON = re.compile(r"\"(?:%s|objective|dialogue|line_text)\"\s*:\s*\"([^\"\n]{12,})\"" % PROSE_ATTRS, re.I)
PLACEHOLDER = re.compile(r"^\s*(?:\.\.\.|…|<[^>]*>|[@$%{]|\^)")
WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")

MNEMONICS = (
    "mov|movzx|movsx|movss|movsd|movaps|movups|lea|push|pushad|pop|popad|call|jmp|je|jne|jz|jnz|ja|jae|jb|jbe|jg|"
    "jge|jl|jle|js|jns|cmp|test|add|adc|sub|sbb|xor|and|or|not|neg|inc|dec|shl|shr|sar|rol|ror|imul|idiv|mul|div|"
    "ret|retn|leave|nop|int3|fld|fst|fstp|fild|fistp|fadd|faddp|fsub|fsubp|fmul|fmulp|fdiv|fdivp|fcomp|fnstsw|"
    "sete|setne|cdq|rep|stosd|movsb|cvttss2si|cvtsi2ss|addss|subss|mulss|divss|comiss|ucomiss|xorps"
)
# an address with at least one digit (so prose like "faded and ..." never counts), optional opcode bytes, a mnemonic
ASM_ADDRESSED = re.compile(r"^\s*(?:0x)?(?=[0-9A-Fa-f]*\d)[0-9A-Fa-f]{5,16}:?\s+(?:[0-9A-Fa-f]{2}\s+){0,15}"
                           r"(?:%s)\b" % MNEMONICS, re.M)
# no address: an indented mnemonic whose first operand is a memory operand, a hex number or an x86 register
# (so Python continuation lines such as "    and sum(...)" never count)
X86_OPERAND = (r"(?:(?:byte|word|dword|qword|tbyte)\s+ptr\b|\[|0x[0-9A-Fa-f]+\b|"
               r"(?:e?[abcd]x|e?[sd]i|e?[sb]p|[abcd][lh]|st\(?\d?\)?|xmm\d)\b)")
ASM_BARE = re.compile(r"^\s+(?:%s)\s+%s" % (MNEMONICS, X86_OPERAND), re.M)
ASM_MIN = 5
DECOMP_TYPES = re.compile(r"\bundefined[1248]?\b")
DECOMP_SYMS = re.compile(r"\b(?:FUN|DAT|PTR|LAB)_[0-9A-Fa-f]{8}\b")

USER_PATH = re.compile(
    r"(?i)(?:\b[A-Z]:[\\/]{1,2}Users[\\/]{1,2}|(?<![\w.])/(?:c/)?Users/|(?<![\w.])/home/)"
    r"(?!(?:<[^>]*>|%[^%]*%|\$\w+|\{[^}]*\}|you|your[-_ ]?name|user(?:name)?|me|name|someone|example|public|"
    r"default|all users|runner(?:admin)?|vsts|appveyor|travis|circleci|builder|docker|root|ubuntu)(?:[\\/\s\"'`]|$))"
    r"[A-Za-z0-9._-]+"
)
IPV4 = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d.]*\d)")
EMAIL = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})\b")
EMAIL_OK_DOMAINS = ("users.noreply.github.com", "noreply.github.com", "example.com", "example.org", "example.net",
                    "anthropic.com")   # the Co-Authored-By trailer uses noreply@anthropic.com
EMAIL_OK_LOCAL = re.compile(r"^(?:noreply|no-reply|git|user|you|name)$", re.I)
PRIVATE_LINK = re.compile(r"(?i)\bclaude\.ai/(?:artifact|code/artifact|chat|share|project)s?/|chatgpt\.com/(?:c|share)/")
SECRETS = [
    ("GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{50,}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("AWS key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("Discord bot token", re.compile(r"\b[MNO][A-Za-z\d_-]{23,27}\.[A-Za-z\d_-]{6}\.[A-Za-z\d_-]{27,40}\b")),
    ("Anthropic / OpenAI key", re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{32,}\b")),
]
LONG_ID = re.compile(r"(?<![\w.#-])\d{17,20}(?![\w.])")


def _private_ip(ip: str) -> bool:
    try:
        o = [int(x) for x in ip.split(".")]
    except ValueError:
        return False
    if any(x > 255 for x in o):
        return False
    a, b = o[0], o[1]
    return (a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168)
            or (a == 100 and 64 <= b <= 127) or (a == 169 and b == 254))


def _prose(value: str) -> bool:
    """True for 4+ real words that aren't a placeholder, a path or a format string."""
    if PLACEHOLDER.match(value) or "/" in value or "\\" in value or "_" in value:
        return False
    return len(WORD.findall(value)) >= 4


# ------------------------------------------------------------------------------------------------ findings
@dataclass
class Finding:
    path: str
    rule: str
    message: str
    line: int = 0
    severity: str = "error"


@dataclass
class Allow:
    rule: str
    glob: str
    reason: str
    lineno: int
    used: bool = False


@dataclass
class Guard:
    max_size: int = MAX_SIZE_DEFAULT
    allows: List[Allow] = field(default_factory=list)
    deny: List[re.Pattern] = field(default_factory=list)

    def allowed(self, path: str, rule: str) -> bool:
        for a in self.allows:
            if (a.rule == rule or a.rule == "*") and fnmatch.fnmatchcase(path, a.glob):
                a.used = True
                return True
        return False

    # -------------------------------------------------------------------------------------------- one file
    def check(self, path: str, data: bytes, size: Optional[int] = None) -> List[Finding]:
        size = len(data) if size is None else size
        path = path.replace("\\", "/")
        name = path.rsplit("/", 1)[-1].lower()
        ext = os.path.splitext(name)[1]
        out: List[Finding] = []

        def add(rule, msg, line=0, severity="error"):
            if not self.allowed(path, rule):
                out.append(Finding(path, rule, msg, line, severity))

        if ext in GAME_EXTENSIONS or name.startswith("decomp") and ext in (".c", ".cpp", ".txt"):
            add("GAME_EXT", f"'{ext or name}' files are game data, disc images, executables or disassembly - "
                            "never committed (build artifacts come from CI)")
        if ext in MEDIA_EXTENSIONS:
            add("MEDIA", f"'{ext}' media file: screenshots and textures are game imagery; original art needs an "
                         f"allowlist entry saying so")
        if size > self.max_size:
            add("SIZE", f"{size:,} bytes, over the {self.max_size:,}-byte limit")

        magic = sniff_magic(data)
        if magic:
            add("GAME_MAGIC", f"content is a {magic}")
        media = sniff_media(data)
        if media and ext not in MEDIA_EXTENSIONS:
            add("MEDIA", f"content is a {media}")
        if is_binary(data):
            if not magic and not media and ext not in GAME_EXTENSIONS:
                add("BINARY", "binary content: this repository holds text only (fixtures are generated by tests)")
            return out

        text = data[:TEXT_SCAN_LIMIT].decode("utf-8", errors="replace")
        lines = text.splitlines()
        out += self._text_rules(path, ext, text, lines, add)
        return out

    def _text_rules(self, path, ext, text, lines, add) -> List[Finding]:
        def first_line(rx):
            m = rx.search(text)
            return text.count("\n", 0, m.start()) + 1 if m else 0

        if path.startswith("research/") and ext == ".txt":
            add("RESEARCH_TXT", "research output as .txt: regenerate it instead, or allowlist it as our own report")

        n = len(DECODED_XML_LINE.findall(text))
        if n >= DECODED_XML_MIN.get(ext, DECODED_XML_MIN_DEFAULT):
            add("DECODED_XML", f"{n} lines of decoded game XML (zone / package / stats / mission / conversation / "
                               f"menu elements)", first_line(DECODED_XML_LINE))

        for i, line in enumerate(lines, 1):
            for m in TEXT_ATTR.finditer(line):
                if _prose(m.group(1)):
                    add("GAME_TEXT_ATTR", "an XML text attribute holding prose (game text?) - use a placeholder", i)
                    break
        if ext in (".json", ".jsonl"):
            for i, line in enumerate(lines, 1):
                for m in TEXT_JSON.finditer(line):
                    if _prose(m.group(1)):
                        add("GAME_TEXT_JSON", "a JSON text field holding prose (game text?) - strip the field", i)
                        break

        n = len(ASM_ADDRESSED.findall(text)) + len(ASM_BARE.findall(text))
        if n >= ASM_MIN:
            add("DISASM", f"{n} lines that look like an instruction listing - describe the code in prose with "
                          f"addresses instead", first_line(ASM_ADDRESSED) or first_line(ASM_BARE))
        if (len(DECOMP_TYPES.findall(text)) >= 3 and len(DECOMP_SYMS.findall(text)) >= 3
                and sum(1 for l in lines if l.rstrip().endswith(";")) >= 5):
            add("DISASM", "looks like decompiler output (undefinedN types, FUN_/DAT_ symbols, C statements)",
                first_line(DECOMP_TYPES))

        for i, line in enumerate(lines, 1):
            m = USER_PATH.search(line)
            if m:
                add("PERSONAL_PATH", f"user-profile path '{m.group(0)}' - use %USERPROFILE%, ~ or a placeholder", i)
            for ip in IPV4.findall(line):
                if _private_ip(ip):
                    add("PRIVATE_IP", f"private network address {ip} - use a documentation address "
                                      f"(192.0.2.x, 198.51.100.x, 203.0.113.x)", i)
            for m in EMAIL.finditer(line):
                local = m.group(0).split("@", 1)[0]
                dom = m.group(1).lower()
                if not (any(dom == d or dom.endswith("." + d) for d in EMAIL_OK_DOMAINS)
                        or EMAIL_OK_LOCAL.match(local)):
                    add("EMAIL", f"e-mail address {m.group(0)}", i)
            if PRIVATE_LINK.search(line):
                add("PRIVATE_LINK", "link to a private chat / artifact page", i)
            for what, rx in SECRETS:
                if rx.search(line):
                    add("SECRET", f"looks like a {what}", i)
            for rx in self.deny:
                if rx.search(line):
                    add("PERSONAL_PATTERN", f"matches local deny pattern /{rx.pattern}/", i)
            if ext not in (".json", ".jsonl", ".csv", ".tsv") and LONG_ID.search(line):
                add("LONG_ID", "17-20 digit id (Discord / platform id?) - fine if public, move it if personal",
                    i, severity="warn")
        return []


# ------------------------------------------------------------------------------------------------ inputs
def git(repo: Path, *args: str, input_bytes: Optional[bytes] = None) -> bytes:
    p = subprocess.run(["git", "-C", str(repo), *args], input=input_bytes, capture_output=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr.decode(errors='replace').strip()}")
    return p.stdout


def repo_root(start: Path) -> Optional[Path]:
    try:
        return Path(git(start, "rev-parse", "--show-toplevel").decode().strip())
    except (RuntimeError, FileNotFoundError):
        return None


def read_blobs(repo: Path, items: List[Tuple[str, str]]) -> Iterator[Tuple[str, bytes, int]]:
    """(path, sha) -> (path, first TEXT_SCAN_LIMIT bytes, full size) through one `git cat-file --batch`."""
    if not items:
        return
    proc = subprocess.Popen(["git", "-C", str(repo), "cat-file", "--batch"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE)
    try:
        for path, sha in items:
            proc.stdin.write(sha.encode() + b"\n")
            proc.stdin.flush()
            header = proc.stdout.readline().split()
            if len(header) < 3 or header[1] != b"blob":
                continue
            size = int(header[2])
            keep = proc.stdout.read(min(size, TEXT_SCAN_LIMIT))
            rest = size - len(keep)
            while rest > 0:
                rest -= len(proc.stdout.read(min(rest, 1 << 20)))
            proc.stdout.read(1)     # the LF after the content
            yield path, keep, size
    finally:
        proc.stdin.close()
        proc.wait()


def index_items(repo: Path, staged_only: bool) -> List[Tuple[str, str]]:
    entries = {}
    for rec in git(repo, "ls-files", "-s", "-z").split(b"\0"):
        if not rec:
            continue
        meta, path = rec.split(b"\t", 1)
        mode, sha, _stage = meta.split()
        if mode == b"160000":           # submodule
            continue
        entries[path.decode()] = sha.decode()
    if staged_only:
        names = [n.decode() for n in git(repo, "diff", "--cached", "--name-only", "--diff-filter=ACMRT", "-z")
                 .split(b"\0") if n]
        return [(n, entries[n]) for n in names if n in entries]
    return sorted(entries.items())


def range_items(repo: Path, rev_range: str) -> List[Tuple[str, str]]:
    seen, items = set(), []
    for commit in git(repo, "rev-list", "--reverse", rev_range).split():
        raw = git(repo, "diff-tree", "-r", "-z", "--root", "--no-commit-id", "--diff-filter=ACMRT",
                  "--no-renames", commit.decode()).split(b"\0")
        for meta, path in zip(raw[0::2], raw[1::2]):
            if not meta:
                continue
            f = meta.split()
            sha, mode = f[3].decode(), f[1].decode()
            if mode == "160000" or sha in seen:
                continue
            seen.add(sha)
            items.append((path.decode(), sha))
    return items


def history_items(repo: Path) -> List[Tuple[str, str]]:
    objs = git(repo, "rev-list", "--objects", "--all").decode(errors="replace").splitlines()
    pairs = [(l.split(" ", 1)[1] if " " in l else "", l.split(" ", 1)[0]) for l in objs]
    check = git(repo, "cat-file", "--batch-check=%(objectname) %(objecttype)",
                input_bytes="\n".join(s for _, s in pairs).encode()).decode().splitlines()
    blobs = {l.split()[0] for l in check if l.endswith(" blob")}
    seen, items = set(), []
    for path, sha in pairs:
        if sha in blobs and sha not in seen:
            seen.add(sha)
            items.append((path or f"<blob {sha[:12]}>", sha))
    return items


def disk_items(paths: Iterable[str], base: Path) -> Iterator[Tuple[str, bytes, int]]:
    for p in paths:
        p = Path(p)
        files = [p] if p.is_file() else [f for f in sorted(p.rglob("*")) if f.is_file() and ".git" not in f.parts]
        for f in files:
            try:
                rel = f.resolve().relative_to(base.resolve()).as_posix()
            except ValueError:
                rel = f.as_posix()
            size = f.stat().st_size
            with open(f, "rb") as fh:
                yield rel, fh.read(TEXT_SCAN_LIMIT), size


def load_allowlist(path: Path) -> List[Allow]:
    allows = []
    if not path.is_file():
        return allows
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        body, _, reason = line.partition("#")
        parts = body.split()
        if len(parts) != 2 or not reason.strip():
            raise ValueError(f"{path.name}:{i}: expected 'RULE GLOB # reason', got: {raw!r}")
        allows.append(Allow(parts[0], parts[1], reason.strip(), i))
    return allows


def load_deny(repo: Optional[Path]) -> List[re.Pattern]:
    files = []
    if repo is not None:
        try:
            gitdir = Path(git(repo, "rev-parse", "--absolute-git-dir").decode().strip())
            files.append(gitdir / "info" / "content-guard-deny")
        except RuntimeError:
            pass
    if os.environ.get("CONTENT_GUARD_DENY"):
        files.append(Path(os.environ["CONTENT_GUARD_DENY"]))
    pats = []
    for f in files:
        if f.is_file():
            for raw in f.read_text(encoding="utf-8").splitlines():
                if raw.strip() and not raw.lstrip().startswith("#"):
                    pats.append(re.compile(raw.strip()))
    return pats


# ------------------------------------------------------------------------------------------------ report
def report(findings: List[Finding], guard: Guard, fmt: str, checked: int, warn_unused: bool) -> int:
    errors = [f for f in findings if f.severity == "error"]
    warns = [f for f in findings if f.severity != "error"]
    for f in findings:
        loc = f"{f.path}:{f.line}" if f.line else f.path
        if fmt == "github":
            kind = "error" if f.severity == "error" else "warning"
            line = f",line={f.line}" if f.line else ""
            print(f"::{kind} file={f.path}{line},title={f.rule}::{f.message}")
        else:
            print(f"{loc}: {f.severity.upper()} {f.rule}: {f.message}")
    if warn_unused:
        for a in guard.allows:
            if not a.used:
                msg = f"allowlist line {a.lineno} ({a.rule} {a.glob}) matched nothing"
                print(f"::warning title=ALLOWLIST::{msg}" if fmt == "github" else f"{ALLOW_FILE}: WARN {msg}")
    status = "FAILED" if errors else "ok"
    sys.stdout.flush()
    print(f"content guard: {checked} file(s) checked, {len(errors)} error(s), {len(warns)} warning(s) - {status}",
          file=sys.stderr)
    if errors:
        print("This repository never holds game files, decoded game data, game text, disassembly, personal data or "
              "secrets. Remove the file (or the lines), or - if it is really ours and fine - add an entry with a "
              f"reason to {ALLOW_FILE}.", file=sys.stderr)
    return 1 if errors else 0


# ------------------------------------------------------------------------------------------------ self-test
def self_test() -> int:
    """Synthetic samples only - nothing here comes from a game. Strings are assembled at run time so the guard's
    own source never matches its own rules (the last check runs the guard over this file)."""
    lt, q = "<", '"'
    lorem = "Lorem ipsum dolor sit amet consectetur"
    xml_dump = "\n".join(f'{lt}{tag} name={q}n{i}{q} value={q}{i}{q} />'
                         for i, tag in enumerate(["world", "entity", "entity", "precache", "entity"]))
    asm = "\n".join(f"0040{i:04x}  {op}    eax, dword ptr [ecx + 0x{i:x}]" for i, op in
                    enumerate(["mov", "lea", "mov", "add", "cmp", "mov"]))
    fb = b"maps/test/zone.igb".ljust(0x80, b"\0") + b"igb".ljust(0x40, b"\0") + struct.pack("<I", 16) + b"\1" * 16
    igb = struct.pack("<12I", 64, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0xFADA, 0xF0000006) + b"\0" * 32
    xmlb = struct.pack("<II", 0x11B1, 1) + b"\0" * 24
    cases = [
        # (path, bytes, rules expected - empty set = must pass)
        ("tools/ok.py", b"import os\n\ndef f(x):\n    return x + 1  # add one\n", set()),
        ("research/notes.md", ("# Notes\nThe table at 0x6e9800 has 20 slots of 12 bytes.\n"
                               f"Example: {lt}talent descname={q}<power name>{q} description={q}...{q}/>\n").encode(),
         set()),
        ("research/names.json", b'{"animdbs": {"01_hero": "x1_01_hero"}, "count": 3}\n', set()),
        ("research/sweep/graph.json", b'{"missions": {"m1": {"descname": "Short"}}}\n', set()),
        ("docs/example.md", b"Set LocalIP=192.0.2.10 or 203.0.113.5; loopback 127.0.0.1 is fine.\n", set()),
        ("maps/zone.igb", b"not really", {"GAME_EXT"}),
        ("data/level.bin", igb, {"GAME_MAGIC"}),
        ("data/table.dat", xmlb, {"GAME_MAGIC"}),
        ("data/bank.bin", b"ZSNDPC  " + b"\0" * 56, {"GAME_MAGIC"}),
        ("data/default.bin", b"XBEH" + b"\0" * 60, {"GAME_MAGIC"}),
        ("data/bundle.bin", fb, {"GAME_MAGIC"}),
        ("docs/shot.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 32, {"MEDIA"}),
        ("tools/blob.bin", b"\0\1\2\3 random", {"BINARY"}),
        ("research/dump.xml", xml_dump.encode(), {"DECODED_XML"}),
        ("research/zone.md", xml_dump.encode(), set()),                       # 5 lines in a .md: under 8
        ("research/talk.xml", f"{lt}line text={q}{lorem}.{q} />\n".encode(), {"GAME_TEXT_ATTR"}),
        ("research/plan.json", f'{{"description": "{lorem}."}}\n'.encode(), {"GAME_TEXT_JSON"}),
        ("research/listing.md", asm.encode(), {"DISASM"}),
        ("research/out.txt", b"counts: 3\n", {"RESEARCH_TXT"}),
        ("tools/run.sh", ("cd " + "C:" + "\\Users\\" + "jdoe" + "\\work\n").encode(), {"PERSONAL_PATH"}),
        ("tools/doc.md", ("see " + "C:" + "\\Users\\" + "<you>" + "\\Documents\n").encode(), set()),
        ("tools/net.md", ("host " + ".".join(["192", "168", "7", "20"]) + "\n").encode(), {"PRIVATE_IP"}),
        ("tools/ver.md", b"Windows 10.0.26200 build\n", set()),
        ("tools/mail.md", ("mail " + "jdoe" + "@" + "mail.test.org" + "\n").encode(), {"EMAIL"}),
        ("tools/trailer.md", b"Co-Authored-By: Bot <noreply@anthropic.com>\n", set()),
        ("tools/token.md", ("t = " + "gh" + "p_" + "A" * 36 + "\n").encode(), {"SECRET"}),
        ("big.txt", b"x" * (MAX_SIZE_DEFAULT + 1), {"SIZE"}),
    ]
    failures = 0
    g = Guard()
    for path, data, expected in cases:
        got = {f.rule for f in g.check(path, data) if f.severity == "error"}
        if got != expected:
            failures += 1
            print(f"SELF-TEST FAIL {path}: expected {sorted(expected)}, got {sorted(got)}")
    # allowlist: an entry lifts exactly its rule
    g2 = Guard(allows=[Allow("RESEARCH_TXT", "research/out.txt", "our report", 1)])
    if g2.check("research/out.txt", b"counts: 3\n"):
        failures += 1
        print("SELF-TEST FAIL allowlist did not lift RESEARCH_TXT")
    # local deny pattern
    g3 = Guard(deny=[re.compile(r"(?i)\bmy-pc-name\b")])
    if {f.rule for f in g3.check("a.md", b"built on MY-PC-NAME\n")} != {"PERSONAL_PATTERN"}:
        failures += 1
        print("SELF-TEST FAIL deny pattern")
    # the guard's own source passes the guard
    me = Path(__file__).read_bytes()
    own = [f for f in Guard().check("tools/check_no_game_content.py", me) if f.severity == "error"]
    if own:
        failures += 1
        print("SELF-TEST FAIL the guard flags its own source:", *[f"{f.rule}:{f.line} {f.message}" for f in own],
              sep="\n  ")
    # git plumbing on a throw-away repo: --staged sees staged content, --range sees an added-then-deleted file.
    # The repo is isolated from the user's global / system git config (no hooks, signing or templates apply).
    tmp = tempfile.mkdtemp(prefix="guard-selftest-")
    try:
        r = Path(tmp)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com",
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        run = lambda *a: subprocess.run(["git", "-C", tmp, *a], check=True, capture_output=True, env=env)
        run("init", "-q")
        (r / "ok.py").write_text("print('hi')\n")
        run("add", "ok.py")
        run("commit", "-q", "-m", "one")
        base = git(r, "rev-parse", "HEAD").decode().strip()
        (r / "zone.igb").write_bytes(b"x")
        run("add", "zone.igb")
        staged = index_items(r, staged_only=True)
        if [p for p, _ in staged] != ["zone.igb"]:
            failures += 1
            print("SELF-TEST FAIL --staged listing:", staged)
        run("commit", "-q", "-m", "two")
        run("rm", "-q", "zone.igb")
        run("commit", "-q", "-m", "three")
        rng = [p for p, _ in range_items(r, f"{base}..HEAD")]
        if rng != ["zone.igb"]:
            failures += 1
            print("SELF-TEST FAIL --range listing:", rng)
        hist = sorted(p for p, _ in history_items(r))
        if hist != ["ok.py", "zone.igb"]:
            failures += 1
            print("SELF-TEST FAIL --history listing:", hist)
    except (OSError, subprocess.CalledProcessError, RuntimeError) as e:
        print(f"SELF-TEST SKIP git plumbing ({e})")
    finally:
        def _force(func, p, _exc):       # git marks its objects read-only; Windows refuses to delete those
            os.chmod(p, 0o700)
            func(p)
        if sys.version_info >= (3, 12):
            shutil.rmtree(tmp, onexc=_force)
        else:
            shutil.rmtree(tmp, onerror=_force)
    total = len(cases) + 4
    print(f"self-test: {total - failures}/{total} passed")
    return 1 if failures else 0


# ------------------------------------------------------------------------------------------------ main
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="check the staged files (pre-commit)")
    mode.add_argument("--all", action="store_true", help="check every file in the index (CI)")
    mode.add_argument("--range", metavar="A..B", help="check every blob added or changed by commits in A..B")
    mode.add_argument("--history", action="store_true", help="check every blob reachable from any ref")
    mode.add_argument("--self-test", action="store_true", help="run the built-in synthetic tests")
    ap.add_argument("paths", nargs="*", help="files or folders on disk (instead of a git mode)")
    ap.add_argument("--repo", default=".", help="repository (default: the current directory)")
    ap.add_argument("--allow", help=f"allowlist file (default: {ALLOW_FILE} at the repository root)")
    ap.add_argument("--max-size", type=int, default=MAX_SIZE_DEFAULT, help="size limit in bytes (default 1 MiB)")
    ap.add_argument("--format", choices=("text", "github"), default="text", help="github: workflow annotations")
    a = ap.parse_args(argv)

    if a.self_test:
        return self_test()
    git_mode = a.staged or a.all or a.range or a.history
    if git_mode and a.paths:
        ap.error("give either a git mode or paths, not both")
    if not git_mode and not a.paths:
        ap.error("nothing to check: use --staged, --all, --range, --history or give paths")

    root = repo_root(Path(a.repo)) if git_mode or not a.allow else None
    if git_mode and root is None:
        print(f"not a git repository: {a.repo}", file=sys.stderr)
        return 2
    base = root or Path(a.repo)
    try:
        allows = load_allowlist(Path(a.allow) if a.allow else base / ALLOW_FILE)
        deny = load_deny(root)
    except (ValueError, re.error) as e:
        print(e, file=sys.stderr)
        return 2
    guard = Guard(max_size=a.max_size, allows=allows, deny=deny)

    try:
        if a.staged:
            stream = read_blobs(root, index_items(root, staged_only=True))
        elif a.all:
            stream = read_blobs(root, index_items(root, staged_only=False))
        elif a.range:
            stream = read_blobs(root, range_items(root, a.range))
        elif a.history:
            stream = read_blobs(root, history_items(root))
        else:
            stream = disk_items(a.paths, base)
        findings, checked = [], 0
        for path, data, size in stream:
            checked += 1
            findings += guard.check(path, data, size)
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 2
    # unused allowlist entries only mean something when the whole tree was checked
    return report(findings, guard, a.format, checked, warn_unused=bool(a.all or a.history))


if __name__ == "__main__":
    sys.exit(main())
