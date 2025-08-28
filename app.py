# app.py
import io, zipfile, re
import streamlit as st
from core.gcode_loop import rebuild_cycles, DEFAULT_CHANGE_TEMPLATE
from core.queue_builder import read_3mf, compose_sequence, build_final_3mf

APP_NAME  = "PrintLooper — Auto Swap for 3MF"
LOGO_PATH = "assets/PrintLooper.png"
LOGO_SIZE = 180  # ajustá a gusto

st.set_page_config(page_title=APP_NAME, page_icon="🖨️", layout="wide")

# ====== Estilos ======
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
</style>
""", unsafe_allow_html=True)

# ====== Helper: preview según plate activo ======
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

# ====== Header ======
c1, c2 = st.columns([0.22, 0.78])
with c1:
    try: st.image(LOGO_PATH, width=LOGO_SIZE)
    except Exception: st.write("🖨️")
with c2:
    st.markdown("## PrintLooper")
    st.caption("Duplica y encadena placas con cambios automáticos para tu granja de impresión.")

# ====== Sidebar: parámetros globales ======
with st.sidebar:
    st.markdown("### Parámetros globales")
    cycles  = st.number_input("Ciclos Z (por cambio)", min_value=0, value=5, step=1)  # default 5
    down_mm = st.number_input("Descenso Z (mm)", min_value=1.0, value=20.0, step=0.5, format="%.1f")
    up_mm   = st.number_input("Ascenso Z (mm)",   min_value=1.0, value=75.0, step=0.5, format="%.1f")
    mode    = st.radio("Orden de impresión", ["serial","interleaved"],
                       format_func=lambda x: "Serie" if x=="serial" else "Intercalado")
    use_tpl = st.checkbox("Usar plantilla custom", value=True)

    st.markdown("---")
    st.markdown("### Espera antes del cambio de placa")
    wait_enabled = st.checkbox("Apagar cama y esperar antes de cambiar", value=False)
    wait_minutes = st.number_input("Minutos de espera", min_value=0.0, value=2.0, step=0.5,
                                   format="%.1f", disabled=not wait_enabled)

with st.expander("Plantilla de 'change plates'"):
    tpl = st.text_area("Plantilla {{CYCLES}}", value=DEFAULT_CHANGE_TEMPLATE, height=220)

# ====== Uploader ======
uploads = st.file_uploader("Subí uno o más .3mf", type=["3mf"], accept_multiple_files=True)
if not uploads:
    st.stop()

# ====== Tarjetas por modelo: preview + repeticiones ======
models = []
cols = st.columns(len(uploads)) if len(uploads) else [st]
for i, up in enumerate(uploads):
    data = up.read()
    meta = read_3mf(data)  # {files, plate_name, core, shutdown}

    with cols[i]:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown(f"**{up.name}**  \n<span class='small'>/{meta['plate_name'].split('/')[-1].split('.')[0]}</span>",
                    unsafe_allow_html=True)

        preview = select_preview_from_files(meta["files"], meta["plate_name"])
        if preview:
            st.image(preview, use_container_width=True)
        else:
            st.image("https://via.placeholder.com/320x200?text=No+preview", use_container_width=True)

        reps = st.number_input("Repeticiones", min_value=1, value=1, step=1, key=f"reps_{i}")
        st.markdown('</div>', unsafe_allow_html=True)

    models.append({
        "name": up.name,
        "raw": data,
        "repeats": int(reps),
        "plate_name": meta["plate_name"],
        "core": meta["core"],          # para componer
        "shutdown": meta["shutdown"],
        "files": meta["files"],
    })

# ====== Construcción del bloque de cambio ======
cycle_block = rebuild_cycles(cycles, down_mm, up_mm, None, None)
change_block = (tpl if use_tpl else DEFAULT_CHANGE_TEMPLATE).replace("{{CYCLES}}", cycle_block)

# Bloque previo: apagar cama + esperar (si está habilitado)
pre_wait_block = ""
if wait_enabled and wait_minutes > 0:
    seconds = int(wait_minutes * 60)
    pre_wait_block = (
        "; PrintLooper: apagar cama y esperar antes del cambio de placa\n"
        "M140 S0\n"
        f"G4 S{seconds}\n"
    )

# change_block final que se inserta entre impresiones
change_block_final = pre_wait_block + change_block

# ====== Botón generar ======
if st.button("Generar 3MF compuesto"):
    try:
        seq_items = [{"name": m["name"], "core": m["core"], "shutdown": m["shutdown"], "repeats": m["repeats"]}
                     for m in models]

        # Usamos el bloque con espera opcional
        composite_gcode = compose_sequence(seq_items, change_block_final, mode)

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

        # Resumen
        resumen = [
            "Orden: " + ("Serie" if mode == "serial" else "Intercalado"),
            f"Espera antes de cambio: {'Sí' if wait_enabled else 'No'}"
            + (f" ({wait_minutes:.1f} min)" if wait_enabled and wait_minutes > 0 else "")
        ]
        resumen += [f"- {m['name']}: x{m['repeats']}" for m in models]
        st.code("\n".join(resumen), language="text")

        st.markdown('<div class="footer">Hecho con ❤️ por PrintLooper</div>', unsafe_allow_html=True)

    except Exception as e:
        st.error(f"Error: {e}")
