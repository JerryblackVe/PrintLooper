import io, re, zipfile, hashlib
from datetime import datetime
from typing import List, Tuple, Optional

SECTION_RE = re.compile(
    r"(;=+\s*Starting\s+to\s+change\s+plates[^\n]*\n)(.*?)(;=+\s*Finish\s+to\s+change\s+plates[^\n]*\n)",
    re.IGNORECASE | re.DOTALL
)

ZDOWN_RE = re.compile(r"^\s*G380\s+S3\s+Z-?\s*(?P<down>\d+(?:\.\d+)?)\s+F[\d.]+\s*(?:;.*)?\s*$", re.IGNORECASE | re.MULTILINE)
ZUP_RE   = re.compile(r"^\s*G380\s+S2\s+Z\s*(?P<up>\d+(?:\.\d+)?)\s+F[\d.]+\s*(?:;.*)?\s*$",  re.IGNORECASE | re.MULTILINE)

SHUTDOWN_RE = re.compile(
    r"^\s*(?:M104\s+S0\b|M140\s+S0\b|M106\s+S0\b|M107\b|M84\b|M18\b)\s*.*$",
    re.IGNORECASE | re.MULTILINE
)

END_OF_PRINT_RE = re.compile(r"^\s*;.*END_OF_PRINT.*$", re.IGNORECASE | re.MULTILINE)

DEFAULT_CHANGE_TEMPLATE = """;========Starting to change plates =================
G91;
{{CYCLES}}
G1 Z5 F1200
G90;
G28 Y;
G91;
G380 S2 Z30 F1200
G90;
M211 Y0 Z0;
G91;
G90;
G1 Y266 F2000;
G1 Y35 F1000
G1 Y0 F2500
G91;
G380 S3 Z-20 F1200
G90;
G1 Y266 F2000
G1 Y53 F2000
G1 Y266 F2000
G1 Y250 F8000
G1 Y266 F8000
G1 Y100 F2000
G1 Y266 F2000
G1 Y250 F8000
G1 Y266 F8000
G1 Y0 F1000
G1 Y150 F1000
G28 Y;
;========Finish to change plates =================
"""

def md5_bytes(b: bytes) -> str:
    h = hashlib.md5(); h.update(b); return h.hexdigest()

def _extract_F(line: Optional[str], default=" F1200") -> str:
    if not line:
        return default
    m = re.search(r"\sF([\d.]+)", line, re.IGNORECASE)
    return f" F{m.group(1)}" if m else default

def find_cycles(lines: List[str]) -> Tuple[Optional[int], Optional[int], List[Tuple[float,float]]]:
    """
    Tolera comentarios/vacíos entre down/up (hasta 1 línea intermedia).
    Devuelve (start_index, end_index_exclusive, [(down, up), ...]).
    """
    i = 0
    n = len(lines)
    while i < n:
        if ZDOWN_RE.match(lines[i]):
            break
        i += 1
    start = i
    cycles = []
    while i < n:
        m_down = ZDOWN_RE.match(lines[i])
        if not m_down:
            break
        # permitir una línea intermedia (comentario/vacía)
        j = i + 1
        if j < n and (lines[j].strip().startswith(";") or lines[j].strip() == ""):
            j += 1
        if j >= n or not ZUP_RE.match(lines[j]):
            break
        m_up = ZUP_RE.match(lines[j])
        down = float(m_down.group("down"))
        up   = float(m_up.group("up"))
        cycles.append((down, up))
        i = j + 1
    end = i
    if cycles:
        return start, end, cycles
    return None, None, []

def rebuild_cycles(desired_cycles:int, down_mm:float, up_mm:float,
                   example_down_line:Optional[str]=None, example_up_line:Optional[str]=None) -> str:
    f_down = _extract_F(example_down_line)
    f_up   = _extract_F(example_up_line)
    comment_down = ""
    if example_down_line:
        mcd = re.search(r"(;.*)$", example_down_line)
        if mcd: comment_down = " " + mcd.group(1).lstrip()
    comment_up = ""
    if example_up_line:
        mcu = re.search(r"(;.*)$", example_up_line)
        if mcu: comment_up = " " + mcu.group(1).lstrip()
    out = []
    for _ in range(desired_cycles):
        out.append(f"G380 S3 Z-{down_mm}{f_down}{(' ' + comment_down) if comment_down and not comment_down.startswith(';') else comment_down}".rstrip() + "\n")
        out.append(f"G380 S2 Z{up_mm}{f_up}{(' ' + comment_up) if comment_up and not comment_up.startswith(';') else comment_up}".rstrip() + "\n")
    return "".join(out)

def normalize_existing_change_sections(text:str, cycles:int, down_mm:float, up_mm:float, report:list):
    first_section_text = None
    def _replace(m):
        nonlocal first_section_text
        head, body, tail = m.group(1), m.group(2), m.group(3)
        lines = body.splitlines(keepends=False)
        s, e, found = find_cycles(lines)
        if found:
            example_down = lines[s]
            example_up   = lines[s+1] if s+1 < len(lines) else None
            new_cycle_lines = rebuild_cycles(cycles, down_mm, up_mm, example_down, example_up)
            new_body = "\n".join(lines[:s]) + ("\n" if s>0 else "") + new_cycle_lines + ("\n" if e < len(lines) else "") + "\n".join(lines[e:])
            res = head + new_body + tail
            if first_section_text is None:
                first_section_text = res
            report.append(f"[change plates] ciclos normalizados → {cycles} (down={down_mm}, up={up_mm})")
            return res
        else:
            if first_section_text is None:
                first_section_text = head + body + tail
            report.append("[change plates] sección sin ciclos; no cambios.")
            return head + body + tail

    new_text, n = SECTION_RE.subn(_replace, text)
    if n == 0:
        report.append("No se encontraron secciones 'change plates'.")
        return text, None, False
    return new_text, first_section_text, True

def build_change_block_from_template(cycles:int, down_mm:float, up_mm:float, template:str) -> str:
    cycle_lines = rebuild_cycles(cycles, down_mm, up_mm, None, None)
    if "{{CYCLES}}" in template:
        return template.replace("{{CYCLES}}", cycle_lines)
    parts = template.splitlines(keepends=True)
    inject_at = min(2, len(parts))
    return "".join(parts[:inject_at]) + cycle_lines + "\n" + "".join(parts[inject_at:])

def split_core_and_shutdown(text:str) -> Tuple[str,str]:
    # priorizar ;END_OF_PRINT si existe
    m_end = list(END_OF_PRINT_RE.finditer(text))
    if m_end:
        idx = m_end[-1].start()
        return text[:idx], text[idx:]

    m = list(SHUTDOWN_RE.finditer(text))
    if not m:
        return text, ""
    idx = m[-1].start()
    return text[:idx], text[idx:]

def duplicate_with_change_blocks(gcode_text:str, repeats:int, change_block:str, report:list) -> str:
    core, shutdown = split_core_and_shutdown(gcode_text)
    if repeats <= 1:
        return core + shutdown
    parts = [core]
    for _ in range(repeats - 1):
        parts += ["\n", change_block, "\n", core]
    parts.append(shutdown)
    report.append(f"Duplicación: {repeats} repeticiones; bloque insertado {repeats-1} veces.")
    return "".join(parts)

def process_one_gcode(gcode_bytes:bytes, repeats:int, cycles:int, down_mm:float, up_mm:float,
                      user_tpl:str, use_existing_tpl:bool, report:list) -> bytes:
    text = gcode_bytes.decode("utf-8", errors="ignore")
    norm_text, existing_block, _ = normalize_existing_change_sections(text, cycles, down_mm, up_mm, report)

    if use_existing_tpl and existing_block:
        change_block = existing_block
        report.append("Plantilla: se usó primera sección existente (normalizada).")
    else:
        change_block = build_change_block_from_template(cycles, down_mm, up_mm, user_tpl or DEFAULT_CHANGE_TEMPLATE)
        report.append("Plantilla: se usó plantilla definida/por defecto.")

    duplicated = duplicate_with_change_blocks(norm_text, repeats, change_block, report)
    return duplicated.encode("utf-8")

def process_3mf(src_bytes: bytes, repeats:int, cycles:int, down_mm:float, up_mm:float,
                user_tpl:str, use_existing_tpl:bool):
    """
    - Modifica todos los *.gcode (prioriza Metadata/plate_*.gcode si existen).
    - Recalcula *.gcode.md5 si corresponde.
    - Devuelve bytes del .3mf, cantidad modificada y reporte.
    """
    report = []
    zin = zipfile.ZipFile(io.BytesIO(src_bytes), "r", allowZip64=True)

    # Leer todo a dict (evitar doble compresión encadenada)
    files = {info.filename: zin.read(info.filename) for info in zin.infolist()}
    zin.close()

    # Elegir candidatos
    all_gcodes = [n for n in files if n.lower().endswith(".gcode")]
    plate_gcodes = [n for n in all_gcodes if "/plate_" in n.lower()]
    targets = plate_gcodes or all_gcodes  # fallback si no son plate_*.gcode

    modified = 0
    for name in targets:
        files[name] = process_one_gcode(files[name], repeats, cycles, down_mm, up_mm, user_tpl, use_existing_tpl, report)
        modified += 1

    # Recalcular MD5 vecinos
    for name in list(files.keys()):
        low = name.lower()
        if low.endswith(".gcode.md5"):
            gname = name[:-4]
            if gname in files:
                files[name] = (md5_bytes(files[gname]) + "\n").encode("ascii")

    # Escribir salida
    out_mem = io.BytesIO()
    with zipfile.ZipFile(out_mem, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zout:
        for n, b in files.items():
            zout.writestr(n, b)
        ts = datetime.utcnow().isoformat() + "Z"
        rpt = [f"# Reporte ({ts})",
               f"- GCODEs procesados: {modified}",
               f"- Repeticiones: {repeats}",
               f"- Ciclos: {cycles} | down={down_mm} | up={up_mm}"]
        rpt.extend([f"- {r}" for r in report])
        zout.writestr("Metadata/change_plates_report.txt", ("\n".join(rpt) + "\n").encode("utf-8"))
    out_mem.seek(0)
    return out_mem.getvalue(), modified, report
