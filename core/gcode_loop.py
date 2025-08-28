import io, re, zipfile, hashlib
from datetime import datetime
from typing import List, Tuple, Optional, Dict

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

def split_core_and_shutdown(text:str):
    m_end = list(END_OF_PRINT_RE.finditer(text))
    if m_end:
        idx = m_end[-1].start()
        return text[:idx], text[idx:]
    m = list(SHUTDOWN_RE.finditer(text))
    if not m:
        return text, ""
    idx = m[-1].start()
    return text[:idx], text[idx:]
