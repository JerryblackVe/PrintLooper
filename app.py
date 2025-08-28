import streamlit as st
from core.gcode_loop import process_3mf, DEFAULT_CHANGE_TEMPLATE

st.set_page_config(page_title="3MF Loop + Plate Changer", page_icon="🛠️", layout="centered")
st.title("3MF Loop + Plate Changer (Bambu / G-code)")

with st.sidebar:
    st.markdown("### Parámetros")
    repeats = st.number_input("Repeticiones totales", min_value=1, value=2, step=1)
    cycles  = st.number_input("Ciclos Z (por cambio)", min_value=0, value=4, step=1)
    down_mm = st.number_input("Descenso Z (mm)", min_value=1.0, value=20.0, step=0.5, format="%.1f")
    up_mm   = st.number_input("Ascenso Z (mm)",   min_value=1.0, value=75.0, step=0.5, format="%.1f")
    use_existing_tpl = st.checkbox("Usar PRIMERA sección 'change plates' existente (si hay)", value=True)
    sim_only = st.checkbox("Simulación (no escribir, sólo reporte)", value=False)

st.markdown("Subí un **.3mf**, define las **repeticiones** y la app insertará bloques de **cambio de placa** entre cada repetición, dejando el **apagado** sólo al final.")

with st.expander("Plantilla de 'change plates' (opcional)"):
    st.caption("Usá {{CYCLES}} para inyectar los ciclos Z. Si no hay sección existente o desmarcás la casilla, se usará esta plantilla.")
    user_template = st.text_area("Plantilla", value=DEFAULT_CHANGE_TEMPLATE, height=260)

uploaded = st.file_uploader("Archivo .3mf", type=["3mf"])

if uploaded:
    st.info(f"Archivo: **{uploaded.name}** — {uploaded.size/1024:.1f} KB")
    run = st.button("Procesar 3MF")
    if run:
        if repeats < 1 or down_mm <= 0 or up_mm <= 0:
            st.error("Parámetros inválidos: revisá repeticiones y mm de Z.")
        else:
            data = uploaded.read()
            with st.spinner("Procesando…"):
                try:
                    out_bytes, modified, report = process_3mf(
                        data, int(repeats), int(cycles), float(down_mm), float(up_mm),
                        user_template, use_existing_tpl
                    )
                except Exception as e:
                    st.error(f"Error: {e}")
                else:
                    st.success(f"OK. GCODEs modificados: {modified}.")
                    st.code("\n".join(report[-30:]) or "(sin novedades)", language="text")
                    if not sim_only:
                        st.download_button(
                            label="Descargar 3MF modificado",
                            data=out_bytes,
                            file_name=f"modified_{uploaded.name}",
                            mime="application/vnd.ms-package.3dmanufacturing-3dmodel+xml",
                        )
                    else:
                        st.info("Modo simulación: no se ofrece descarga.")
else:
    st.caption("Subí un archivo para habilitar el procesamiento.")
