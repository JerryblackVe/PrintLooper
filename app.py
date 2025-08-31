with st.expander("Plantilla de 'change plates'"):
    tpl = st.text_area(
        "Plantilla {{CYCLES}}", value=DEFAULT_CHANGE_TEMPLATE, height=220,
        help="Podés usar {{CYCLES}} donde quieras inyectar los ciclos Z. Si no lo usás, se insertan tras la segunda línea."
    )
uploads = st.file_uploader(
    "Subí uno o más .3mf", type=["3mf"], accept_multiple_files=True,
    help="Podés subir varios .3mf; a cada uno le asignás cuántas repeticiones querés."
)
# ========== Model cards ==========
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
            st.image
