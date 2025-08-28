import io, re, zipfile, hashlib
from typing import List, Dict
from datetime import datetime
from .gcode_loop import split_core_and_shutdown, md5_bytes, rebuild_cycles, DEFAULT_CHANGE_TEMPLATE

PNG_THUMB_RE = re.compile(r"^metadata/thumbnail_.*\.png$", re.IGNORECASE)

def read_3mf(info_bytes: bytes) -> Dict:
    z = zipfile.ZipFile(io.BytesIO(info_bytes), "r", allowZip64=True)
    files = {i.filename: z.read(i.filename) for i in z.infolist()}
    z.close()

    gcodes = [n for n in files if n.lower().endswith(".gcode")]
    plate = None
    for n in gcodes:
        if "/plate_1.gcode" in n.lower():
            plate = n; break
    if plate is None and gcodes:
        plate = gcodes[0]

    gcode_text = files[plate].decode("utf-8", errors="ignore") if plate else ""
    core, shutdown = split_core_and_shutdown(gcode_text)
    thumbs = [n for n in files if re.match(PNG_THUMB_RE, n.lower())]

    return {
        "files": files,
        "plate_name": plate,
        "core": core,
        "shutdown": shutdown,
        "thumbs": thumbs
    }

def compose_sequence(items: List[Dict], change_block: str, mode: str) -> str:
    parts = []
    if mode == "serial":
        for it in items:
            for _ in range(it["repeats"]):
                if parts: parts.append("\n" + change_block + "\n")
                parts.append(it["core"])
    else:  # interleaved
        remaining = True
        round_idx = 0
        while remaining:
            remaining = False
            for it in items:
                if round_idx < it["repeats"]:
                    if parts: parts.append("\n" + change_block + "\n")
                    parts.append(it["core"])
                    remaining = True
            round_idx += 1
    first_shutdown = next((it["shutdown"] for it in items if it["shutdown"]), "")
    parts.append(first_shutdown)
    return "".join(parts)

def build_final_3mf(skeleton_files: Dict[str, bytes], plate_name: str, composite_gcode: str) -> bytes:
    files = dict(skeleton_files)
    if plate_name not in files:
        candidates = [n for n in files if n.lower().endswith(".gcode")]
        if not candidates:
            raise ValueError("No se encontró .gcode base en el esqueleto 3MF.")
        plate_name = candidates[0]

    files[plate_name] = composite_gcode.encode("utf-8")
    md5_name = plate_name + ".md5"
    if md5_name in files:
        files[md5_name] = (hashlib.md5(files[plate_name]).hexdigest() + "\n").encode("ascii")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zout:
        for n, b in files.items():
            zout.wr
