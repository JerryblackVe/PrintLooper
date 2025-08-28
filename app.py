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
.small { opacity:.85; font-size:.9rem; }
.kpi { font-weight:600; margin-top:.35rem; margin-bottom:.35rem; }
.slots { display:flex; gap:18px; align-items:flex-start; }
.slot  { width:95px; text-align:center; }
.dot   { width:18px; height:18px; border-radius:50%; display:inline-block; border:1px solid #00000033; }
.footer { opacity:.7; font-size:.85rem; padding-top:1.2rem; border-top:1px dashed #2a2f3a; }
</style>
""", unsafe_allow_html=True)

# ===== Helpers =====
PLATE_NUM_RE = re.compile(r"plate_(\d+)\.gcode$", re.IGNORECASE)
HOTEND_RE = re.compile(r"^\s*M10(?:4|9)\b.*?\bS(?P<t>\d+(?:\.\d+)?)", re.IGNORECASE | re.MULTILINE)
BED_RE    = re.compile(r"^\s*M1(?:40|90)\b.*?\bS(?P<t>\d+(?:\.\d+)?)", re.IGNORECASE | re.MULTILINE)

def extract_first_temp(gcode_text: str):
    txt = gcode_text or ""
    m_h = HOTEND_RE.search(txt); m_b = BED_RE.search(txt)
    return (float(m_h.group("t")) if m_h else None,
            float(m_b.group("t")) if m_b else None)

def apply_temp_overrides(gcode_text: str, hotend: float|None, bed: float|None) -> str:
    text = gcode_text or ""
    def repl_first(patt, newS):
        m = patt.search(text)
        if not m: return None
        s,e = m.span()
        return text[:s] + re.sub(r"S\\d+(\\.\\d+)?", newS, text[s:e]) + text[e:]
    if hotend is not None:
        r = repl_first(HOTEND_RE, f"S{int(hotend)}")
        text = r if r is not None else f"; PrintLooper override\\nM104 S{int(hotend)}\\n{text}"
    if bed is not None:
        r = repl_first(BED_RE, f"S{int(bed)}")
        text = r if r is not None else f"; PrintLooper override\\nM140 S{int(bed)}\\n{text}"
    return text

def parse_time(full_gcode:str) -> str|None:
    t = full_gcode or ""
    m = re.search(r"^\\s*;TIME:(\\d+)\\s*$", t, re.MULTILINE)  # Orca/Prusa
    if m:
        sec = int(m.group(1)); h=sec//3600; mnt=(sec%3600)//60; s=sec%60
        return f"{h}h {mnt:02d}m {s:02d}s" if h else f"{mnt}m {s:02d}s"
    m = re.search(r"estimated printing time.*?=\\s*([0-9hms :]+)", t, re.IGNORECASE)
    if m: return m.group(1).strip().replace("  "," ")
    return None

def parse_filament_usage(full_gcode:str):
    """
    Lee stats desde el G-code COMPLETO (cabecera):
      ; filament used [g] = 25.59, 9.58, 9.41
      ; filament used [m] = 8.44, 3.16, 3.11
      ; filament_color = #000000;#FFFFFF;#FFFF00  (o 'colour', separadores ',' o ';')
    """
    t = full_gcode or ""
    nums_g = re.search(r"filament\\s+used\\s*\\[\\s*g\\s*\\]\\s*=\\s*([0-9.,;\\s]+)", t, re.IGNORECASE)
    nums_m = re.search(r"filament\\s+used\\s*\\[\\s*m\\s*\\]\\s*=\\s*([0-9.,;\\s]+)", t, re.IGNORECASE)
    colors = re.search(r"filament[_ ]colou?r\\s*=\\s*([#0-9a-fA-F;\\s,]+)", t, re.IGNORECASE)

    gs = [float(x.replace(',', '.')) for x in re.findall(r"[0-9]+(?:[\\.,][0-9]+)?", nums_g.group(1))] if nums_g else []
    ms = [float(x.replace(',', '.')) for x in re.findall(r"[0-9]+(?:[\\.,][0-9]+)?", nums_m.group(1))] if nums_m else []
    cs = []
    if colors:
        cs = [c.strip() for c in re.split(r"[;,]", colors.group(1)) if c.strip()]

    n = max(len(gs), len(ms), len(cs))
    slots = []
    for i in range(n):
        slots.append({
            "g": gs[i] if i < len(gs) else None,
            "m": ms[i] if i < len(ms) else None,
            "color": cs[i] if i < len(cs) else "#999999"
        })
    return slots

def select_preview_from_files(files: dict, plate_name: str) -> bytes|None:
    if not plate_name: return None
    m = PLATE_NUM_RE.search(plate_name)
    if not m: return None
    n = m.group(1)
    lower_map = {k.lower(): k for k in files.keys()}
    for cand in [f"metadata/plate_{n}.png", f"metadata/top_{n}.png", f"metadata/plate_{n}_small.png"]:
        if cand in lower_map: return files[lower_map[cand]]
    for lk, ok in lower_map.items():
        if lk.startswith("metadata/thumbnail_") and lk.endswith(".png"):
            return files[ok]
    return None

def slots_grid(slots:list[dict]):
    if not slots: 
        st.markdown("<div class='small'>Sin info de filamento.</div>", unsafe_allow_html=True)
        return
    html = ["<div class='slots'>"]
    for idx, s in enumerate(slots, start=1):
        g = "-" if s.get("g") is None else f"{s['g']:.2f} g"
        m = "-" if s.get("m") is None else f"{s['m']:.2f} m"
        c = s.get("color","#999999")
        html.append(
            f"<div class='slot'><span class='dot' style='background:{c}'></span>"
            f"<div class='small'>Slot {idx}<br>PLA<br>{g}<br>{m}</div></div>"
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)

# ===== Header =====
c1, c2 = st.columns([0.22, 0.78])
with c1:
    try: st.image(LOGO_PATH, width=LOGO_SIZE)
    except Exception: st.write("🖨️")
with c2:
    st.markdown("## PrintLooper")
    st.caption("Duplica y encadena placas con cambios automáticos para tu granja de impresión.")

# ===== Sidebar =====
with st.sidebar:
    st.markdown("### Parámetros globales")
    cycles  = st.number_input("Ciclos Z (por cambio)", min_value=0, value=5, step=1)
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

    # *** USAR EL G-CODE COMPLETO para stats (no 'core') ***
    full_gcode = (meta["files"].get(meta["plate_name"], b"")).decode("utf-8", errors="ignore")

    hot, bed = extract_first_temp(full_gcode)
    est_time = parse_time(full_gcode) or "—"
    slots    = parse_filament_usage(full_gcode)

    with cols[i]:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown(f"**{up.name}**  \n<span class='small'>/{meta['plate_name'].split('/')[-1].split('.')[0]}</span>", unsafe_allow_html=True)

        preview = select_preview_from_files(meta["files"], meta["plate_name"])
        if preview: st.image(preview, use_container_width=True)
        else:       st.image("https://via.placeholder.com/320x200?text=No+preview", use_container_width=True)

        st.markdown(f"<div class='kpi'>{est_time}</div>", unsafe_allow_html=True)

        reps = st.number_input("Repeticiones", min_value=1, value=1, step=1, key=f"reps_{i}")

        slots_grid(slots)

        st.markdown(
            f"<div class='small'>Hotend: <b>{'-' if hot is None else int(hot)}°C</b> — "
            f"Cama: <b>{'-' if bed is None else int(bed)}°C</b></div>",
            unsafe_allow_html=True
        )
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
        "core": meta["core"],              # para componer
        "full": full_gcode,                # stats/temps/colores
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
        # Aplicar overrides antes de componer (sobre 'core')
        seq_items = []
        for m in models:
            core = m["core"]
            if m["override_hot"] is not None or m["override_bed"] is not None:
                # aplicar también al core (no a full)
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
        st.code("\\n".join(lines), language="text")

        st.markdown('<div class="footer">Hecho con ❤️ por PrintLooper</div>', unsafe_allow_html=True)

    except Exception as e:
        st.error(f"Error: {e}")
