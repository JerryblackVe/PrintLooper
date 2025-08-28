# app.py
import io, zipfile, re, hashlib
import streamlit as st
from core.gcode_loop import rebuild_cycles, DEFAULT_CHANGE_TEMPLATE
from core.queue_builder import read_3mf, compose_sequence, build_final_3mf

APP_NAME  = "PrintLooper — Auto Swap for 3MF"
LOGO_PATH = "assets/PrintLooper.png"
LOGO_SIZE = 180  # ajustá a gusto

st.set_page_config(page_title=APP_NAME, page_icon="🖨️", layout="wide")

# ========= Estilos =========
st.markdown("""
<style>
.main .block-container {max-width: 1200px; padding-top: 1.2rem;}
h1, h2, h3 { background: linear-gradient(90deg,#e6e6e6,#8AE234);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.stButton>button, .stDownloadButton>button { border-radius: 14px; padding: 0.6rem 1.1rem; font-weight: 700; }
.card { border:1px solid #2a2f3a; border-radius:16px; padding:12px; background:#141821; }
.small { opacity:.8; font-size:.9rem; }
.footer { opacity:.7; font-size:.85rem; padding-top:1.2rem; border-top:1px dashed #2a2f3a; }
</style>
""", unsafe_allow_html=True)

# ========= Helper: preview por plate activo =========
PLATE_NUM_RE = re.compile(r"plate_(\d+)\.gcode$", re.IGNORECASE)
def select_preview_from_files(files: dict, plate_name: str) -> bytes | None:
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

# ========= Esqueleto 3MF mínimo (para modo prueba sin uploads) =========
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
  <resources/>
  <build/>
</model>"""
    return {
        "[Content_Types].xml": content_types,
        "_rels/.rels": rels,
        "3D/3dmodel.model": model,
        "Metadata/plate_1.gcode": b"; PrintLooper minimal placeholder\n",
        "Metadata/plate_1.gcode.md5": b"0\n",
    }

# ========= Header =========
c1, c2 = st.columns([0.22, 0.78])
with c1:
    try: st.image(LOGO_PATH, width=LOGO_SIZE)
    except Exception: st.write("🖨️")
with c2:
    st.markdown("## PrintLooper")
    st.caption("Duplica y encadena placas con cambios automáticos para tu granja de impresión (MOD Bambu Lab A1 — cambio de cama PEI).")

# ========= Sidebar =========
with st.sidebar:
    st.markdown("### Parámetros globales")
    cycles  = st.number_input("Ciclos Z (por cambio)", min_value=0, value=5, step=1)
    st.caption("❓ Número de pares de movimientos Z (bajar/subir) que ejecuta el ciclo de expulsión.")

    down_mm = st.number_input("Descenso Z (mm)", min_value=1.0, value=20.0, step=0.5, format="%.1f")
    st.caption("❓ Cuánto baja el eje Z durante el ciclo de expulsión.")

    up_mm   = st.number_input("Ascenso Z (mm)",   min_value=1.0, value=75.0, step=0.5, format="%.1f")
    st.caption("❓ Cuánto sube el eje Z para despejar la pieza y evitar colisiones.")

    mode    = st.radio("Orden de impresión", ["serial","interleaved"],
                       format_func=lambda x: "Serie" if x=="serial" else "Intercalado")
    st.caption("❓ Serie = termina todas las repeticiones de un modelo y pasa al siguiente. Intercalado = alterna entre modelos por turno.")

    use_tpl = st.checkbox("Usar plantilla custom", value=True)
    st.caption("❓ Si está activo, se usa la plantilla editable de cambio de placa. Si no, se utilizará la plantilla por defecto.")

    st.markdown("---")
    st.markdown("### Espera antes del cambio de placa")
    wait_enabled = st.checkbox("Activar espera", value=False)
    st.caption("❓ Si se activa, la impresora esperará antes de iniciar el cambio de placa (por tiempo o temperatura).")

    wait_mode = st.radio(
        "Modo de espera",
        ["time", "temp"],
        format_func=lambda v: "Por tiempo (min)" if v=="time" else "Por temperatura (cama ≤ °C)",
        horizontal=True,
        disabled=not wait_enabled
    )
    st.caption("❓ Elige si la espera será por una cantidad fija de minutos o hasta que la cama alcance una temperatura objetivo (M190 R).")

    wait_minutes = st.number_input(
        "Minutos de espera",
        min_value=0.0, value=2.0, step=0.5, format="%.1f",
        disabled=(not wait_enabled or wait_mode!="time")
    )
    if wait_mode == "time":
        st.caption("❓ Duración de la pausa antes del cambio. La cama se apaga (M140 S0) y se espera G4 S<segundos>.")

    target_bed = st.number_input(
        "Temperatura objetivo de cama (°C)",
        min_value=0, max_value=120, value=35, step=1,
        disabled=(not wait_enabled or wait_mode!="temp")
    )
    if wait_mode == "temp":
        st.caption("❓ Temperatura a la que debe enfriar la cama antes del cambio. Se usa M140 S0 + M190 R<temp>.")

with st.expander("Plantilla de 'change plates'"):
    tpl = st.text_area("Plantilla {{CYCLES}}", value=DEFAULT_CHANGE_TEMPLATE, height=220)
    st.caption("❓ Podés usar {{CYCLES}} como marcador donde quieras inyectar los ciclos Z. Si no lo usás, se insertan tras la segunda línea.")

# ========= Uploads (normales) =========
uploads = st.file_uploader("Subí uno o más .3mf", type=["3mf"], accept_multiple_files=True)
st.caption("❓ Podés subir varios .3mf. A cada uno le podrás asignar cuántas repeticiones querés.")

# ========= Tarjetas por modelo =========
models = []
if uploads:
    cols = st.columns(len(uploads))
    for i, up in enumerate(uploads):
        data = up.read()
        meta = read_3mf(data)
        with cols[i]:
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown(f"**{up.name}**  \n<span class='small'>/{meta['plate_name'].split('/')[-1].split('.')[0]}</span>",
                        unsafe_allow_html=True)
            preview = select_preview_from_files(meta["files"], meta["plate_name"])
            st.image(preview if preview else "https://via.placeholder.com/320x200?text=No+preview",
                     use_container_width=True)
            reps = st.number_input("Repeticiones", min_value=1, value=1, step=1, key=f"reps_{i}")
            st.caption("❓ Cantidad de veces que se imprimirá este modelo dentro de la cola.")
            st.markdown('</div>', unsafe_allow_html=True)
        models.append({
            "name": up.name, "raw": data, "repeats": int(reps),
            "plate_name": meta["plate_name"], "core": meta["core"],
            "shutdown": meta["shutdown"], "files": meta["files"],
        })

# ========= Bloque de cambio (espera por tiempo o temperatura) =========
cycle_block = rebuild_cycles(cycles, down_mm, up_mm, None, None)
change_block = (tpl if use_tpl else DEFAULT_CHANGE_TEMPLATE).replace("{{CYCLES}}", cycle_block)

pre_wait_block = ""
if wait_enabled:
    if wait_mode == "time" and wait_minutes > 0:
        seconds = int(wait_minutes * 60)
        pre_wait_block = (
            "; PrintLooper: esperar por tiempo antes del cambio de placa\n"
            "M140 S0\n"
            f"G4 S{seconds}\n"
        )
    elif wait_mode == "temp":
        pre_wait_block = (
            "; PrintLooper: enfriar cama a temperatura objetivo antes del cambio de placa\n"
            "M140 S0\n"
            f"M190 R{int(target_bed)}\n"
        )

change_block_final = pre_wait_block + change_block

# ========= Generar 3MF compuesto (modo normal) =========
if uploads and st.button("Generar 3MF compuesto"):
    st.caption("❓ Construye un único .3mf con todos los modelos y sus repeticiones, insertando el bloque de cambio entre cada impresión.")
    try:
        seq_items = [{"name": m["name"], "core": m["core"], "shutdown": m["shutdown"], "repeats": m["repeats"]}
                     for m in models]
        composite_gcode = compose_sequence(seq_items, change_block_final, mode)
        base = models[0]
        final_3mf = build_final_3mf(base["files"], base["plate_name"], composite_gcode)

        st.success("✅ Cola compuesta generada.")
        st.download_button(
            "⬇️ Descargar 3MF compuesto",
            data=final_3mf,
            file_name=f"queue_{models[0]['name'].rsplit('.',1)[0]}.3mf",
            mime="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
        )
    except Exception as e:
        st.error(f"Error: {e}")

# ========= Modo prueba (solo movimientos) =========
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
    st.caption("❓ Genera un .3mf que solo hace movimientos (sin extrusión) para validar tiempos y la rutina de cambio.")
    test_repeats = st.number_input("Repeticiones de prueba", min_value=1, value=3, step=1)
    st.caption("❓ Cuántas veces repetir el bucle de prueba.")
    test_safety_z = st.number_input("Altura segura Z (mm)", min_value=1.0, value=10.0, step=1.0, format="%.1f")
    st.caption("❓ Altura a la que se mueve Z para evitar colisiones.")
    test_xy_speed = st.number_input("Velocidad XY (mm/min)", min_value=100, value=6000, step=100)
    st.caption("❓ Velocidad de los movimientos XY del test.")

st.markdown("---")
if st.button("🧪 Generar 3MF de prueba (solo movimientos)"):
    try:
        core_test = build_test_core(test_safety_z, int(test_xy_speed))
        shutdown_test = build_test_shutdown()
        seq_test = [{"name": "TEST", "core": core_test, "shutdown": shutdown_test, "repeats": int(test_repeats)}]
        composite_gcode = compose_sequence(seq_test, change_block_final, mode)

        # Esqueleto: si hay uploads, usamos el 1º; si no, uno mínimo
        if uploads:
            base_files = models[0]["files"]
            plate_name = models[0]["plate_name"]
        else:
            base_files = minimal_3mf_skeleton()
            plate_name = "Metadata/plate_1.gcode"

        final_3mf = build_final_3mf(base_files, plate_name, composite_gcode)

        st.success("✅ 3MF de prueba generado.")
        st.download_button(
            "⬇️ Descargar 3MF de prueba",
            data=final_3mf,
            file_name="printlooper_test_moves.3mf",
            mime="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
        )
    except Exception as e:
        st.error(f"Error: {e}")
