# app.py
import io, zipfile, re
import streamlit as st
from core.gcode_loop import rebuild_cycles, DEFAULT_CHANGE_TEMPLATE
from core.queue_builder import read_3mf, compose_sequence, build_final_3mf

APP_NAME   = "PrintLooper — Auto Swap for 3MF"
LOGO_PATH  = "assets/PrintLooper.png"
LOGO_SIZE  = 180  # ajustá a gusto

st.set_page_config(page_title=APP_NAME, page_icon="🖨️", layout="wide")

# ===== CSS =====
st.markdown("""
<style>
.main .block-container {max-width: 1200px; padding-top: 1.2rem;}
h1, h2, h3 { background: linear-gradient(90deg,#e6e6e6,#8AE234);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.stButton>button, .stDownloadButton>button {
  border-radius: 14px; padding: 0.6rem 1.1rem; font-weight: 700; }
.card { border:1px solid #2a2f3a; border-radius:16px; padding:10px; background:#141821; }
.thumb { border-radius:10px; border:1px solid #2a2f3a; }
.footer { opacity:.7; font-size:.85rem; padding-top:1.2rem; border-top:1px dashed #2a2f3a; }
</style>
""", unsafe_allow_html=True)

# ===== Utils: obtener preview PNG del 3MF =====
def get_preview_png(three_mf_bytes: bytes) -> bytes | None:
    """
    Prioriza Metadata/plate_1.png (case-insensitive).
    Fallback: cualquier Metadata/plate_*.png, luego top_*.png o thumbnail_*.png.
    """
    with zipfile.ZipFile(io.BytesIO(three_mf_bytes), "r", allowZip64=True) as z:
        names = [i.filename for i in z.infolist()]
        low   = {n.lower(): n for n in names}

        # prioridad 1: plate_1.png exacto en metadata/
        target = None
        for k, v in low.items():
            if k == "metadata/plate_1.png":
                target = v; break
        # prioridad 2: cualquier plate_*.png
        if target is None:
            for k, v in low.items():
                if k.startswith("metadata/plate_") and k.endswith(".png"):
                    target = v; break
        # prioridad 3: top_*.png
        if target is None:
            for k, v in low.items():
                if k.startswith("metadata/top_") and k.endswith(".png"):
                    target = v; break
        # prioridad 4: thumbnail_*.png
        if target is None:
            for k, v in low.items():
                if k.startswith("metadata/thumbnail_") and k.endswith(".png"):
                    target = v; break

        return z.read(target) if target else None

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
cols = st.columns(len(uploads))
for i, up in enumerate(uploads):
    data = up.read()
    meta = read_3mf(data)  # core, shutdown, plate_name, files

    with cols[i]:
        st.markdown('<div class="card">', unsafe_allow_html=True)
        st.markdown(f"**{up.name}**")
        preview = get_preview_png(data)
        if preview:
            st.image(preview, use_container_width=True)
        else:
            st.image("https://via.placeholder.com/320x200?text=plate_1.png+not+found",
                     use_container_width=True)
        reps = st.number_input("Repeticiones", min_value=1, value=1, step=1, key=f"reps_{i}")
        st.markdown('</div>', unsafe_allow_html=True)

    models.append({
        "name": up.name,
        "raw": data,
        "repeats": int(reps),
        "plate_name": meta["plate_name"],
        "core": meta["core"],
        "shutdown": meta["shutdown"],
        "files": meta["files"],
    })

# ===== Cambio de placa =====
cycle_block = rebuild_cycles(cycles, down_mm, up_mm, None, None)
change_block = (tpl if use_tpl else DEFAULT_CHANGE_TEMPLATE).replace("{{CYCLES}}", cycle_block)

if st.button("Generar 3MF compuesto"):
    try:
        seq_items = [{"name": m["name"], "core": m["core"], "shutdown": m["shutdown"], "repeats": m["repeats"]}
                     for m in models]
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
        st.code(
            f"Orden: {'Serie' if mode=='serial' else 'Intercalado'}\n" +
            "\n".join([f"- {m['name']}: x{m['repeats']}" for m in models]),
            language="text"
        )
        st.markdown('<div class="footer">Hecho con ❤️ por PrintLooper</div>', unsafe_allow_html=True)

    except Exception as e:
        st.error(f"Error: {e}")
