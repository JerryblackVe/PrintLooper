import streamlit as st
from core.gcode_loop import rebuild_cycles, DEFAULT_CHANGE_TEMPLATE
from core.queue_builder import read_3mf, compose_sequence, build_final_3mf

st.set_page_config(page_title="3MF Queue + Plate Changer", page_icon="🛠️", layout="wide")
st.title("3MF Queue + Plate Changer")

with st.sidebar:
    st.markdown("### Parámetros globales")
    cycles  = st.number_input("Ciclos Z (por cambio)", min_value=0, value=4, step=1)
    down_mm = st.number_input("Descenso Z (mm)", min_value=1.0, value=20.0, step=0.5, format="%.1f")
    up_mm   = st.number_input("Ascenso Z (mm)",   min_value=1.0, value=75.0, step=0.5, format="%.1f")
    mode    = st.radio("Modo de cola", options=["serial", "interleaved"], format_func=lambda x: "Serie" if x=="serial" else "Intercalado")
    use_tpl = st.checkbox("Usar plantilla custom", value=True)
    st.caption("Insertamos cambio de placa entre cada segmento.")

with st.expander("Plantilla de 'change plates'"):
    tpl = st.text_area("Plantilla {{CYCLES}}", value=DEFAULT_CHANGE_TEMPLATE, height=220)

uploads = st.file_uploader("Subí uno o más .3mf", type=["3mf"], accept_multiple_files=True)
if not uploads:
    st.stop()

# Leer archivos y thumbnails
models = []
cols = st.columns(len(uploads)) if uploads else []
for i, up in enumerate(uploads):
    data = up.read()
    meta = read_3mf(data)
    # miniaturas (si hay)
    with cols[i]:
        st.markdown(f"**{up.name}**")
        thumbs = meta["thumbs"]
        if thumbs:
            # mostrar la primera
            z = zipfile.ZipFile(io.BytesIO(data), "r")
            st.image(z.read(thumbs[0]))
            z.close()
        else:
            st.info("Sin thumbnail en 3MF.")
    # parámetros por modelo
    order = st.number_input(f"Orden — {up.name}", min_value=1, value=i+1, step=1, key=f"order_{i}")
    reps  = st.number_input(f"Repeticiones — {up.name}", min_value=1, value=1, step=1, key=f"reps_{i}")
    models.append({
        "name": up.name,
        "raw": data,
        "order": int(order),
        "repeats": int(reps),
        "plate_name": meta["plate_name"],  # para esqueleto
        "core": meta["core"],
        "shutdown": meta["shutdown"],
        "files": meta["files"]
    })

# Ordenar según 'order'
models.sort(key=lambda m: m["order"])

# Construir bloque de cambio
cycle_block = rebuild_cycles(cycles, down_mm, up_mm, None, None)
change_block = (tpl if use_tpl else DEFAULT_CHANGE_TEMPLATE).replace("{{CYCLES}}", cycle_block)

if st.button("Generar 3MF compuesto"):
    try:
        # Secuencia (lista con nombre, core, shutdown, repeats)
        seq_items = [{"name": m["name"], "core": m["core"], "shutdown": m["shutdown"], "repeats": m["repeats"]} for m in models]
        composite_gcode = compose_sequence(seq_items, change_block, mode)
        # Usar el primer archivo como esqueleto
        base = models[0]
        final_3mf = build_final_3mf(base["files"], base["plate_name"], composite_gcode)
        st.success("Cola compuesta generada.")
        st.download_button(
            "Descargar 3MF compuesto",
            data=final_3mf,
            file_name=f"queue_{models[0]['name'].rsplit('.',1)[0]}.3mf",
            mime="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
        )
        # Log de vista previa
        st.code(
            f"Modo: {mode}\n" +
            "\n".join([f"- {m['name']}: x{m['repeats']}" for m in models]),
            language="text"
        )
    except Exception as e:
        st.error(f"Error: {e}")
