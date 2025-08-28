# app.py
import io, zipfile, re
import streamlit as st
from core.gcode_loop import rebuild_cycles, DEFAULT_CHANGE_TEMPLATE
from core.queue_builder import read_3mf, compose_sequence, build_final_3mf

APP_NAME  = "PrintLooper — Auto Swap for 3MF"
LOGO_PATH = "assets/PrintLooper.png"
LOGO_SIZE = 180  # ajustá a gusto

st.set_page_config(page_title=APP_NAME, page_icon="🖨️", layout="wide")

# ===== CSS =====
st.markdown("""
<style>
.main .block-container {max-width: 1200px; padding-top: 1.2rem;}
h1, h2, h3 { background: linear-gradient(90deg,#e6e6e6,#8AE234);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.stButton>button, .stDownloadButton>button {
  border-radius: 14px; padding: 0.6rem 1.1rem; font-weight: 700; }
.card { border:1px solid #2a2f3a; border-radius:16px; padding:12px; background:#141821; }
.small { opacity:.8; font-size:.9rem; }
.footer { opacity:.7; font-size:.85rem; padding-top:1.2rem; border-top:1px dashed #2a2f3a; }
ul { margin-top: .25rem; }
</style>
""", unsafe_allow_html=True)

# ===== Helpers =====
PLATE_NUM_RE = re.compile(r"plate_(\d+)\.gcode$", re.IGNORECASE)

HOTEND_RE = re.compile(r"^\s*M10(?:4|9)\b.*?\bS(?P<t>\d+(?:\.\d+)?)", re.IGNORECASE | re.MULTILINE)
BED_RE    = re.compile(r"^\s*M1(?:40|90)\b.*?\bS(?P<t>\d+(?:\.\d+)?)", re.IGNORECASE | re.MULTILINE)

COLOR_PATTERNS = [
    re.compile(r"^\s*M600\b", re.IGNORECASE | re.MULTILINE),            # pausa/cambio filamento
    re.compile(r"^\s*T(\d+)\s*(?:;.*)?$", re.IGNORECASE | re.MULTILINE), # cambio de herramienta/slot
    re.compile(r"COLOR[_\s-]*CHANGE", re.IGNORECASE),                    # comentarios de color
]

def extract_first_temp(gcode_text: str) -> tuple[float|None, float|None]:
    """Devuelve (hotend, bed) si aparecen en el G-code (primer match)."""
    m_h = HOTEND_RE.search(gcode_text)
    m_b = BED_RE.search(gcode_text)
    hot = float(m_h.group("t")) if m_h else None
    bed = float(m_b.group("t")) if m_b else None
    return hot, bed

def apply_temp_overrides(gcode_text: str, hotend: float|None, bed: float|None) -> str:
    """Reemplaza la PRIMERA ocurrencia de M104/M109 y M140/M190. Si no existen, inserta al inicio."""
    text = gcode_text

    def _replace_first(pattern, repl, txt):
        # reemplazo de solo la primera coincidencia
        return pattern.sub(repl, txt, count=1)

    # Hotend
    if hotend is not None:
        if HOTEND_RE.search(text):
            text = _replace_first(HOTEND_RE, lambda m: re.sub(r"S\d+(\.\d+)?", f"S{int(hotend)}", m.group(0)), text)
        else:
            text = f"; PrintLooper override\nM104 S{int(hotend)}\n" + text

    # Bed
    if bed is not None:
        if BED_RE.search(text):
            text = _replace_first(BED_RE, lambda m: re.sub(r"S\d+(\.\d+)?", f"S{int(bed)}", m.group(0)), text)
        else:
            text = f"; PrintLooper override\nM140 S{int(bed)}\n" + text

    return text

def detect_color_events(gcode_text: str) -> dict:
    """Cuenta eventos típicos de cambio de color/herramienta."""
    counts = {"M600": 0, "ToolChanges": 0, "Comments": 0, "ToolsUsed": set()}
    # M600
    counts["M600"] = len(COLOR_PATTERNS[0].finditer(gcode_text))
    # Tool changes Tn
    tools = re.findall(r"^\s*T(\d+)\s*(?:;.*)?$", gcode_text, flags=re.IGNORECASE | re.MULTILINE)
    counts["ToolChanges"] = len(tools)
    counts["ToolsUsed"] = set(tools)
    # Comments COLOR_CHANGE
    counts["Comments"] = len(COLOR_PATTERNS[2].finditer(gcode_text))
    return counts

def select_preview_from_files(files: dict, plate_name: str) -> bytes | None:
    """
    Imagen correspondiente al MISMO número de plate que el G-code activo.
    Prioridad: metadata/plate_{N}.png, luego top_{N}.png, luego plate_{N}_small.png, luego cualquier thumbnail.
    """
    if not plate_name:
        return None
    m = PLATE_NUM_RE.search(plate_name)
    if not m:
        return None
    n = m.group(1)

    lower_map = {k.lower(): k for k in files.keys()}
    for cand in [f"metadata/plate_{n}.png", f"metadata/top_{n}.png", f"metadata/plate_{n}_small.png"]:
        if cand in lower_map:
            return files[lower_map[cand]]

    for lk, ok in lower_map.items():
        if lk.startswith("metadata/thumbnail_") and lk.endswith(".png"):
            return files[ok]
    return None

# ===== Header =====
c1, c2 = st.columns([0.22, 0.78])
with c1:
    try:
        st.image(LOGO_PATH, width=LOGO_SIZE)
    except Exception:
        st.write("🖨️")
with c2:
    st.markdown("## PrintLooper")
    st.caption("Duplica y encadena placas con cambios automáticos para tu granja de impresión.")

# ===== Sidebar =====
with st.sidebar:
    st.markdown("### Parámetros globales")
    cycles  = st.number_input("Ciclos Z (por cambio)", min_value=0, value=5, step=1)  # default 5
    down_mm = st.number_input("Descenso Z (mm)", min_value=1.0, value=20.0, step=0.5, format="%.1f")
    up_mm   = st.number_input("Ascenso Z (mm)",   min_value=1.0, value=75.0, step=0.5, format="%.1f")
    mode    = st.radio("Orden de impresión", ["serial","interleaved"],
                       format_func=lambda x: "Serie" if x=="serial" else "Intercalado")
    use_tpl = st.checkbox("Usar plantilla custom", value=True)

with st.expander("Plantilla de 'change plates'"):
    tpl = st.text_area("Plantilla {{CYCLES}}", value=DEFAULT_CHANGE_TEMPLATE, height=220)

uploads = st.file_uploader("Subí uno o más .3mf", type=["3mf"], accept_multiple_files=True)
if not uploads:
    st.stop()

# ===== Modelos =====
models = []
cols = st.columns(len(uploads)) if len(uploads) else [st]

for i, up in enumerate(uploads):
    data = up.read()
    meta = read_3mf(data)  # {files, plate_name, core, shutdown}

    # Extraer temperaturas + color
    hot, bed = extract_first_temp(meta["core"])
    color_info = detect_color_events(meta["core"])

    with cols[i]:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown(f"**{up.name}**")
        preview = select_preview_from_files(meta["files"], meta["plate_name"])
        if preview:
            st.image(preview, use_container_width=True)
        else:
            st.image("https://via.placeholder.com/320x200?text=No+preview+for+current+plate",
                     use_container_width=True)

        st.markdown("**Repeticiones**")
        reps = st.number_input("", min_value=1, value=1, step=1, key=f"reps_{i}")

        # Resumen de configuración detectada
        st.markdown("<div class='small'>**Detectado:**</div>", unsafe_allow_html=True)
        st.markdown(
            f"<div class='small'>Hotend: <b>{('-' if hot is None else int(hot))}°C</b> — "
            f"Cama: <b>{('-' if bed is None else int(bed))}°C</b><br>"
            f"Cambios de color: T={color_info['ToolChanges']} "
            f"(tools: {', '.join(sorted(color_info['ToolsUsed'])) or '—'}), "
            f"M600={color_info['M600']}, "
            f"Comentarios={color_info['Comments']}</div>",
            unsafe_allow_html=True
        )

        # Overrides de temperatura
        mod_temps = st.checkbox("Modificar temperaturas", key=f"modt_{i}", value=False)
        new_hot = new_bed = None
        if mod_temps:
            new_hot = st.number_input("Hotend (°C)", min_value=0, max_value=400,
                                      value=int(hot) if hot is not None else 210, step=1, key=f"nh_{i}")
            new_bed = st.number_input("Cama (°C)", min_value=0, max_value=150,
                                      value=int(bed) if bed is not None else 60, step=1, key=f"nb_{i}")

        st.markdown('</div>', unsafe_allow_html=True)

    models.append({
        "name": up.name,
        "raw": data,
        "repeats": int(reps),
        "plate_name": meta["plate_name"],
        "core": meta["core"],
        "shutdown": meta["shutdown"],
        "files": meta["files"],
        "override_hot": new_hot if mod_temps else None,
        "override_bed": new_bed if mod_temps else None,
    })

# ===== Cambio de placa =====
cycle_block = rebuild_cycles(cycles, down_mm, up_mm, None, None)
change_block = (tpl if use_tpl else DEFAULT_CHANGE_TEMPLATE).replace("{{CYCLES}}", cycle_block)

if st.button("Generar 3MF compuesto"):
    try:
        # Aplicar overrides por modelo ANTES de componer
        seq_items = []
        for m in models:
            core = m["core"]
            if m["override_hot"] is not None or m["override_bed"] is not None:
                core = apply_temp_overrides(core, m["override_hot"], m["override_bed"])
            seq_items.append({"name": m["name"], "core": core, "shutdown": m["shutdown"], "repeats": m["repeats"]})

        composite_gcode = compose_sequence(seq_items, change_block, mode)

        base = models[0]
        final_3mf = build_final_3mf(base["files"], base["plate_name"], composite_gcode)

        st.success("✅ Cola compuesta generada.")
        st.balloons()
        st.download_button(
            "⬇️ Descargar 3MF compuesto",
            data=final_3mf,
            file_name=f"queue_{models[0]['name'].rsplit('.',1)[0]}.3mf",
            mime="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
        )
        # Log
        lines = [f"Orden: {'Serie' if mode=='serial' else 'Intercalado'}"]
        for m in models:
            oh = "-" if m["override_hot"] is None else m["override_hot"]
            ob = "-" if m["override_bed"] is None else m["override_bed"]
            lines.append(f"- {m['name']}: x{m['repeats']} | hotend={oh} | cama={ob}")
        st.code("\n".join(lines), language="text")

        st.markdown('<div class="footer">Hecho con ❤️ por PrintLooper</div>', unsafe_allow_html=True)

    except Exception as e:
        st.error(f"Error: {e}")
