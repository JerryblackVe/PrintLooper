# app.py
import io, zipfile
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
    mode    = st.radio("Orden de impresión general", options=["serial", "interleaved"],
                       format_func=lambda x: "Serie" if x=="serial" else "Intercalado")
    use_tpl = st.checkbox("Usar plantilla custom", value=True)

with st.expander("Plantilla de 'change plates'"):
    tpl = st.text_area("Plantilla {{CYCLES}}", value=DEFAULT_CHANGE_TEMPLATE, height=220)

uploads = st.file_uploader("Subí uno o más .3mf", type=["3mf"], accept_multiple_files=True)
if not uploads:
    st.stop()

# Mostrar miniaturas y pedir repeticiones (el orden será el de carga en el uploader)
models = []
cols = st.columns(len(uploads))
for i, up in enumerate(uploads):
    data = up.read()
    meta = read_3mf(data)

    with cols[i]:
        st.markdown(f"**{up.name}**")
        if meta["thumbs"]:
            z = zipfile.ZipFile(io.BytesIO(data), "r")
            st.image(z.read(meta["thumbs"][0]))
            z.close()
        else:
            st.info("Sin thumbnail en 3MF.")

    reps = st.number_input(f"Repeticiones — {up.name}", min_value=1, value=1, step=1, key=f"reps_{i}")
    models.append({
        "name": up.name,
        "raw": data,
        "repeats": int(reps),
        "plate_name": meta["plate_name"],
        "core": meta["core"],
        "shutdown": meta["shutdown"],
        "files": meta["files"],
    })

# Construcción del bloque de cambio
cycle_block = rebuild_cycles(cycles, down_mm, up_mm, None, None)
change_block = (tpl if use_tpl else DEFAULT_CHANGE_TEMPLATE).replace("{{CYCLES}}", cycle_block)

if st.button("Generar 3MF compuesto"):
    try:
        # La secuencia respeta el orden en que fueron cargados (uploads)
        seq_items = [{"name": m["name"], "core": m["core"], "shutdown": m["shutdown"], "repeats": m["repeats"]}
                     for m in models]
        composite_gcode = compose_sequence(seq_items, change_block, mode)
        # Esqueleto: el primer archivo
        base = models[0]
        final_3mf = build_final_3mf(base["files"], base["plate_name"], composite_gcode)

        st.success("Cola compuesta generada.")
        st.download_button(
            "Descargar 3MF compuesto",
            data=final_3mf,
            file_name=f"queue_{models[0]['name'].rsplit('.',1)[0]}.3mf",
            mime="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
        )
        st.code(
            f"Orden: {'Serie' if mode=='serial' else 'Intercalado'}\n" +
            "\n".join([f"- {m['name']}: x{m['repeats']}" for m in models]),
            language="text"
        )
    except Exception as e:
        st.error(f"Error: {e}")
