# app.py
import re
import streamlit as st
from core.gcode_loop import rebuild_cycles, DEFAULT_CHANGE_TEMPLATE
from core.queue_builder import read_3mf, compose_sequence, build_final_3mf

APP_NAME  = "PrintLooper — Auto Swap for 3MF"
LOGO_PATH = "assets/PrintLooper.png"

st.set_page_config(page_title=APP_NAME, page_icon="🖨️", layout="wide")

# ========= THEME TOGGLE (Claro / Oscuro con CSS variables) =========
if "theme" not in st.session_state:
    st.session_state.theme = "dark"  # default

PALETTE = {
    "dark": dict(
        accent="#8AE234", text="#E6E6E6", text_muted="#9AA4B2",
        bg="#0E1117", panel="#141821", card="#0F1420",
        card_hover="#151B28", border="#1F2430",
        shadow="0 10px 30px rgba(0,0,0,.25)"
    ),
    "light": dict(
        accent="#6CC644", text="#0E1117", text_muted="#5B6472",
        bg="#F7F9FB", panel="#EEF1F5", card="#FFFFFF",
        card_hover="#F6FAF3", border="#DDE3EA",
        shadow="0 8px 20px rgba(0,0,0,.08)"
    ),
}

def inject_theme_css(theme: str):
    p = PALETTE[theme]
    st.markdown(f"""
    <style>
    :root {{
      --accent:{p['accent']};
      --text:{p['text']};
      --muted:{p['text_muted']};
      --bg:{p['bg']};
      --panel:{p['panel']};
      --card:{p['card']};
      --card-hover:{p['card_hover']};
      --border:{p['border']};
      --shadow:{p['shadow']};
      --radius:16px;
    }}
    html, body, .main, .stApp {{ background: var(--bg) !important; color: var(--text) !important; }}

    .main .block-container{{max-width:1280px;padding-top:1rem;padding-bottom:2rem;}}
    section[data-testid="stSidebar"]{{ border-right:1px solid var(--border); background:var(--panel)!important; }}

    /* Header */
    .app-header{{ display:flex; gap:18px; align-items:center; margin:.2rem 0 1rem 0; }}
    .app-title{{
      font-size:34px; font-weight:800; line-height:1.1;
      background:linear-gradient(90deg,var(--text),var(--accent));
      -webkit-background-clip:text; -webkit-text-fill-color:transparent;
    }}
    .app-sub{{ color:var(--muted); font-size:14px; margin-top:2px; }}

    /* Cards */
    .card{{
      background:var(--card); border:1px solid var(--border); border-radius:var(--radius);
      padding:16px 16px 14px 16px; transition:all .18s ease; box-shadow: var(--shadow);
    }}
    .card:hover{{ background:var(--card-hover); transform:translateY(-2px); }}

    /* Grid */
    .grid{{ display:grid; grid-template-columns: repeat(12, 1fr); gap:16px; }}
    .col-6{{ grid-column: span 6; }}
    .col-4{{ grid-column: span 4; }}
    .col-3{{ grid-column: span 3; }}

    /* Buttons */
    .stButton>button, .stDownloadButton>button {{
      border-radius:12px; padding:.65rem 1.05rem; font-weight:700;
      border:1px solid color-mix(in srgb, var(--accent) 30%, transparent);
      box-shadow:0 6px 16px color-mix(in srgb, var(--accent) 20%, transparent);
      background: var(--accent) !important; color: black !important;
    }}
    .stButton>button:hover, .stDownloadButton>button:hover{{ filter:brightness(1.05); transform:translateY(-1px); }}

    /* Inputs / radios / uploader */
    .stNumberInput, .stRadio, .stSelectbox, .stCheckbox, .stTextArea, .stFileUploader{{ background:var(--panel)!important; border-radius:12px!important; }}
    [data-baseweb="input"]>div{{ background:transparent!important; color:var(--text)!important; }}
    .streamlit-expanderHeader{{ font-weight:700; color:var(--text); border-radius:12px!important; border:1px solid var(--border); background:var(--panel); }}
    .streamlit-expanderContent{{ background:var(--panel); }}
    [data-testid="stFileUploaderDropzone"]{{ border:1px dashed #2a3342!important; background:var(--card)!important; border-radius:14px!important; }}

    .footer{{ opacity:.8; font-size:.9rem; margin-top:12px; color:var(--muted); }}
    </style>
    """, unsafe_allow_html=True)

# Inyectar CSS del tema actual
inject_theme_css(st.session_state.theme)

# ========= HELPERS =========
PLATE_NUM_RE = re.compile(r"plate_(\d+)\.gcode$", re.IGNORECASE)
def select_preview_from_files(files: dict, plate_name: str):
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

def minimal_3mf_skeleton() -> dict[str, bytes]:
    content_types = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Default Extension="gcode" ContentType="text/plain"/>
  <Default Extension="md5" ContentType="text/plain"/>
</Types>"""
    rels = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>"""
    model = b"""<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources/><build/>
</model>"""
    return {
        "[Content_Types].xml": content_types,
        "_rels/.rels": rels,
        "3D/3dmodel.model": model,
        "Metadata/plate_1.gcode": b"; PrintLooper minimal placeholder\n",
        "Metadata/plate_1.gcode.md5": b"0\n",
    }

# ========= HEADER =========
c1, c2 = st.columns([0.18, 0.82])
with c1:
    try: st.image(LOGO_PATH, use_container_width=True)
    except Exception: st.write("🖨️")
with c2:
    st.markdown('<div class="app-header"><div><div class="app-title">PrintLooper</div>'
                '<div class="app-sub">Duplica y encadena placas con cambios automáticos (MOD Bambu Lab A1 — cama PEI).</div>'
                '</div></div>', unsafe_allow_html=True)

# ========= SIDEBAR =========
with st.sidebar:
    # Toggle de tema
    st.markdown("### Apariencia")
    theme_choice = st.toggle("Tema claro", value=(st.session_state.theme=="light"),
                             help="Activa el modo claro. Apaga para modo oscuro.")
    st.session_state.theme = "light" if theme_choice else "dark"
    inject_theme_css(st.session_state.theme)  # reinyectar si cambia

    st.markdown("### Parámetros globales")
    cycles  = st.number_input("Ciclos Z (por cambio)", 0, 200, 5, 1,
                              help="Número de pares bajar/subir Z que ejecuta el ciclo de expulsión.")
    down_mm = st.number_input("Descenso Z (mm)", 1.0, 500.0, 20.0, 0.5, format="%.1f",
                              help="Cuánto baja Z durante el ciclo de expulsión.")
    up_mm   = st.number_input("Ascenso Z (mm)", 1.0, 500.0, 75.0, 0.5, format="%.1f",
                              help="Cuánto sube Z para despejar y evitar colisiones.")
    mode    = st.radio("Orden de impresión", ["serial","interleaved"],
                       format_func=lambda x: "Serie" if x=="serial" else "Intercalado",
                       help="Serie: completa un modelo y sigue con el siguiente. Intercalado: alterna por turnos.")
    use_tpl = st.checkbox("Usar plantilla custom", True,
                          help="Si está activo, se usa la plantilla editable de cambio. Si no, se usará la por defecto.")

    st.markdown("---")
    st.markdown("### Espera antes del cambio de placa")
    wait_enabled = st.checkbox("Activar espera", False,
                               help="Espera antes del cambio (por tiempo o temperatura).")
    wait_mode = st.radio("Modo de espera", ["time", "temp"],
                         format_func=lambda v: "Por tiempo (min)" if v=="time" else "Por temperatura (cama ≤ °C)",
                         horizontal=True, disabled=not wait_enabled,
                         help="Tiempo: G4 (segundos). Temperatura: M140 S0 + M190 R<temp>.")
    wait_minutes = st.number_input("Minutos de espera", 0.0, 120.0, 2.0, 0.5, format="%.1f",
                                   disabled=(not wait_enabled or wait_mode!="time"),
                                   help="Pausa fija antes del cambio. La cama se apaga.")
    target_bed = st.number_input("Temperatura objetivo de cama (°C)", 0, 120, 35, 1,
                                 disabled=(not wait_enabled or wait_mode!="temp"),
                                 help="Espera hasta enfriar la cama a este valor (M190 R).")

with st.expander("Plantilla de 'change plates'"):
    tpl = st.text_area("Plantilla {{CYCLES}}", value=DEFAULT_CHANGE_TEMPLATE, height=220,
                       help="Podés usar {{CYCLES}} para inyectar los ciclos Z. Si no lo usás, se insertan tras la 2ª línea.")

uploads = st.file_uploader("Subí uno o más .3mf", type=["3mf"], accept_multiple_files=True,
                           help="Podés subir varios; a cada uno le asignás repeticiones.")

# ========= LOAD MODELS & GRID CARDS =========
models = []
if uploads:
    st.markdown('<div class="grid">', unsafe_allow_html=True)
    for i, up in enumerate(uploads):
        data = up.read()
        meta = read_3mf(data)
        st.markdown('<div class="col-6"><div class="card">', unsafe_allow_html=True)
        st.markdown(f"**{up.name}**  \n<span class='small'>/{meta['plate_name'].split('/')[-1].split('.')[0]}</span>",
                    unsafe_allow_html=True)
        preview = select_preview_from_files(meta["files"], meta["plate_name"])
        st.image(preview if preview else "https://via.placeholder.com/640x360?text=Preview",
                 use_container_width=True)
        reps = st.number_input("Repeticiones", min_value=1, value=1, step=1, key=f"reps_{i}",
                               help="Veces que se imprimirá este modelo en la cola.")
        st.markdown('</div></div>', unsafe_allow_html=True)

        models.append({
            "name": up.name, "raw": data, "repeats": int(reps),
            "plate_name": meta["plate_name"], "core": meta["core"],
            "shutdown": meta["shutdown"], "files": meta["files"],
        })
    st.markdown("</div>", unsafe_allow_html=True)

# ========= CHANGE BLOCK (con espera opcional) =========
cycle_block = rebuild_cycles(cycles, down_mm, up_mm, None, None)
change_block = (tpl if use_tpl else DEFAULT_CHANGE_TEMPLATE).replace("{{CYCLES}}", cycle_block)
pre_wait_block = ""
if wait_enabled:
    if wait_mode == "time" and wait_minutes > 0:
        seconds = int(wait_minutes * 60)
        pre_wait_block = ("; PrintLooper: esperar por tiempo antes del cambio\n"
                          "M140 S0\n" f"G4 S{seconds}\n")
    elif wait_mode == "temp":
        pre_wait_block = ("; PrintLooper: enfriar cama a temperatura objetivo\n"
                          "M140 S0\n" f"M190 R{int(target_bed)}\n")
change_block_final = pre_wait_block + change_block

# ========= GENERATE NORMAL QUEUE =========
if uploads and st.button("Generar 3MF compuesto",
                         help="Genera un único .3mf con los modelos y sus repeticiones, intercalando el bloque de cambio."):
    try:
        seq_items = [{"name": m["name"], "core": m["core"], "shutdown": m["shutdown"], "repeats": m["repeats"]} for m in models]
        composite_gcode = compose_sequence(seq_items, change_block_final, mode)
        base = models[0]
        final_3mf = build_final_3mf(base["files"], base["plate_name"], composite_gcode)

        st.success("✅ Cola compuesta generada.")
        st.download_button("⬇️ Descargar 3MF compuesto", data=final_3mf,
                           file_name=f"queue_{models[0]['name'].rsplit('.',1)[0]}.3mf",
                           mime="application/vnd.ms-package.3dmanufacturing-3dmodel+xml")
    except Exception as e:
        st.error(f"Error: {e}")

# ========= TEST MODE (Solo movimientos) =========
def build_test_core(safety_z: float, xy_speed: int) -> str:
    return f"""\
; ===== PrintLooper TEST CORE (no imprime) =====
G90
M104 S0
M106 S0
G28
G1 Z{safety_z:.2f} F1200
G1 X20 Y20 F{xy_speed}
G1 X220 Y20 F{xy_speed}
G1 X220 Y220 F{xy_speed}
G1 X20 Y220 F{xy_speed}
G1 X120 Y120 F{xy_speed}
G4 S2
"""

def build_test_shutdown() -> str:
    return "M104 S0\nM140 S0\nM106 S0\nM84\n"

with st.sidebar:
    st.markdown("---")
    st.markdown("### Modo prueba (solo movimientos)")
    test_repeats = st.number_input("Repeticiones de prueba", 1, 999, 3, 1,
                                   help="Cuántas veces repetir el ciclo de test.")
    test_safety_z = st.number_input("Altura segura Z (mm)", 1.0, 500.0, 10.0, 1.0, format="%.1f",
                                    help="Altura a la que se mueve Z para evitar colisiones.")
    test_xy_speed = st.number_input("Velocidad XY (mm/min)", 100, 50000, 6000, 100,
                                    help="Velocidad de los movimientos XY del test.")

st.markdown("---")
if st.button("🧪 Generar 3MF de prueba (solo movimientos)",
             help="Crea un .3mf de test sin extrusión para validar espera y rutina de cambio."):
    try:
        core_test = build_test_core(test_safety_z, int(test_xy_speed))
        shutdown_test = build_test_shutdown()
        seq_test = [{"name": "TEST", "core": core_test, "shutdown": shutdown_test, "repeats": int(test_repeats)}]
        composite_gcode = compose_sequence(seq_test, change_block_final, mode)

        if uploads:
            base_files = models[0]["files"]
            plate_name = models[0]["plate_name"]
        else:
            base_files = minimal_3mf_skeleton()
            plate_name = "Metadata/plate_1.gcode"

        final_3mf = build_final_3mf(base_files, plate_name, composite_gcode)

        st.success("✅ 3MF de prueba generado.")
        st.download_button("⬇️ Descargar 3MF de prueba", data=final_3mf,
                           file_name="printlooper_test_moves.3mf",
                           mime="application/vnd.ms-package.3dmanufacturing-3dmodel+xml")
    except Exception as e:
        st.error(f"Error: {e}")
