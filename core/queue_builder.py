# core/queue_builder.py
import io
import re
import zipfile
import hashlib
from datetime import datetime
from typing import Dict, List, Tuple

# Reutilizamos la misma lógica de partición que en gcode_loop
# (si cambiaste la firma, mantené estas importaciones)
from .gcode_loop import split_core_and_shutdown

PLATE_NUM_RE = re.compile(r"/plate_(\d+)\.gcode$", re.IGNORECASE)


def read_3mf(info_bytes: bytes) -> Dict:
    """
    Lee un .3mf en memoria y devuelve:
      - files: dict nombre->bytes (todo el ZIP)
      - plate_name: nombre del G-code principal (Metadata/plate_*.gcode)
      - core: bloque repetible (G-code sin el apagado final)
      - shutdown: bloque final de apagado
    Selecciona como plate por defecto Metadata/plate_1.gcode si existe,
    de lo contrario el primer .gcode encontrado.
    """
    with zipfile.ZipFile(io.BytesIO(info_bytes), "r", allowZip64=True) as z:
        files: Dict[str, bytes] = {i.filename: z.read(i.filename) for i in z.infolist()}

    gcodes = [n for n in files if n.lower().endswith(".gcode")]
    plate_name = None
    # Preferir plate_1.gcode si existe
    for n in gcodes:
        if "/plate_1.gcode" in n.lower():
            plate_name = n
            break
    if plate_name is None and gcodes:
        plate_name = gcodes[0]

    gcode_text = files[plate_name].decode("utf-8", errors="ignore") if plate_name else ""
    core, shutdown = split_core_and_shutdown(gcode_text)

    return {
        "files": files,
        "plate_name": plate_name,
        "core": core,
        "shutdown": shutdown,
    }


def compose_sequence(
    items: List[Dict],             # [{name, core, shutdown, repeats}, ...]
    change_block: str,
    mode: str                      # "serial" | "interleaved"
) -> str:
    """
    Compone un único G-code:
      - Inserta change_block entre segmentos.
      - En 'serial': imprime todas las repeticiones de cada item antes del siguiente.
      - En 'interleaved': alterna por rondas hasta agotar repeticiones.
      - Usa el primer 'shutdown' no-vacío al final.
    """
    parts: List[str] = []
    if mode == "serial":
        for it in items:
            for _ in range(int(it["repeats"])):
                if parts:
                    parts.append("\n" + change_block + "\n")
                parts.append(it["core"])
    else:
        # interleaved
        remaining = True
        round_idx = 0
        while remaining:
            remaining = False
            for it in items:
                if round_idx < int(it["repeats"]):
                    if parts:
                        parts.append("\n" + change_block + "\n")
                    parts.append(it["core"])
                    remaining = True
            round_idx += 1

    first_shutdown = next((it["shutdown"] for it in items if it.get("shutdown")), "")
    parts.append(first_shutdown)
    return "".join(parts)


def build_final_3mf(
    skeleton_files: Dict[str, bytes],
    plate_name: str,
    composite_gcode: str
) -> bytes:
    """
    Toma un diccionario de archivos (ZIP original), reemplaza el G-code del plate
    y su .md5 si existe, y escribe un .3mf nuevo en memoria.
    """
    files = dict(skeleton_files)  # copia

    # Verificar plate base
    if plate_name not in files:
        candidates = [n for n in files if n.lower().endswith(".gcode")]
        if not candidates:
            raise ValueError("No se encontró .gcode base en el esqueleto 3MF.")
        plate_name = candidates[0]

    # Reemplazar G-code
    files[plate_name] = composite_gcode.encode("utf-8")

    # Actualizar MD5 si está presente
    md5_name = plate_name + ".md5"
    if md5_name in files:
        digest = hashlib.md5(files[plate_name]).hexdigest() + "\n"
        files[md5_name] = digest.encode("ascii")

    # Escribir ZIP final (¡usar writestr!, no 'wr')
    out = io.BytesIO()
    with zipfile.ZipFile(out, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zout:
        for name, data in files.items():
            zout.writestr(name, data)
        ts = datetime.utcnow().isoformat() + "Z"
        report = [f"# Queue report ({ts})", "- Modo: cola compuesta"]
        zout.writestr("Metadata/queue_report.txt", ("\n".join(report) + "\n").encode("utf-8"))

    out.seek(0)
    return out.getvalue()
