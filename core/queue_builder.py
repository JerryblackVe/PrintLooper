import io, re, zipfile, hashlib
from typing import List, Tuple, Optional, Dict
from datetime import datetime
from .gcode_loop import (
    process_one_gcode, rebuild_cycles, DEFAULT_CHANGE_TEMPLATE,
    split_core_and_shutdown, md5_bytes
)

PNG_THUMB_RE = re.compile(r"^metadata/thumbnail_.*\.png$", re.IGNORECASE)

def read_3mf(info_bytes: bytes) -> Dict:
    """Extrae plate gcode principal, shutdown, thumbnails y un dict de archivos."""
    z = zipfile.ZipFile(io.BytesIO(info_bytes), "r", allowZip64=True)
    files = {i.filename: z.read(i.filename) for i in z.infolist()}
    z.close()

    # Preferir plate_1.gcode (o cualquier plate_*.gcode)
    gcodes = [n for n in files if n.lower().endswith(".gcode")]
    plate = None
    for n in gcodes:
        if "/plate_1.gcode" in n.lower():
            plate = n; break
    if plate is None and gcodes:
        # fallback: primero que aparezca
        plate = gcodes[0]

    gcode_text = files[plate].decode("utf-8", errors="ignore") if plate else ""
    core, shutdown = split_core_and_shutdown(gcode_text)

    thumbs = [n for n in files if PNG_THUMB_RE.match(n.lower())]
    return {
        "files": files,
        "plate_name": plate,
        "core": core,
        "shutdown": shutdown,
        "thumbs": thumbs
    }

def compose_sequence(
    items: List[Dict],               # [{core:str, shutdown:str, name:str, repeats:int}, ...]
    change_block: str,
    mode: str                        # "serial" | "interleaved"
) -> str:
    """Devuelve G-code compuesto con cambios de placa entre segmentos y apagado final único."""
    parts = []
    if mode == "serial":
        for it in items:
            for r in range(it["repeats"]):
                if parts: parts.append("\n" + change_block + "\n")
                parts.append(it["core"])
    else:  # interleaved
        # ciclo por rondas hasta agotar repeticiones
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
    # apagar con el shutdown del PRIMER ítem que lo tenga, si no vacío
    first_shutdown = next((it["shutdown"] for it in items if it["shutdown"]), "")
    parts.append(first_shutdown)
    return "".join(parts)

def build_final_3mf(
    skeleton_files: Dict[str, bytes], plate_name: str,
    composite_gcode: str
) -> bytes:
    """Reemplaza plate gcode + .md5 en un esqueleto y devuelve .3mf."""
    files = dict(skeleton_files)  # copy
    if plate_name not in files:
        # fallback: buscar cualquier .gcode
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
            zout.writestr(n, b)
        ts = datetime.utcnow().isoformat() + "Z"
        rpt = [
            f"# Queue report ({ts})",
            "- Modo: cola compuesta",
        ]
        zout.writestr("Metadata/queue_report.txt", ("\n".join(rpt) + "\n").encode("utf-8"))
    out.seek(0)
    return out.getvalue()
