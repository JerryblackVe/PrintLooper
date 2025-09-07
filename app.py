# app.py
import io, zipfile, re, hashlib
import streamlit as st
from core.queue_builder import read_3mf, compose_sequence, build_final_3mf

APP_NAME  = "PrintLooper — Auto Swap for 3MF"
LOGO_PATH = "assets/PrintLooper.png"
LOGO_SIZE = 180

st.set_page_config(page_title=APP_NAME, page_icon="🖨️", layout="wide")

# ========== Styles ==========
st.markdown("""
<style>
.main .block-container {max-width: 1200px; padding-top: 1.0rem;}
h1, h2, h3 { background: linear-gradient(90deg,#e6e6e6,#8AE234);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.stButton>button, .stDownloadButton>button { border-radius: 14px; padding: 0.6rem 1.1rem; font-weight: 700; }
.card { border:1px solid #2a2f3a; border-radius:16px; padding:12px; background:#141821; }
.small { opacity:.8; font-size:.9rem; }
.footer { opacity:.7; font-size:.85rem; padding-top:1.2rem; border-top:1px dashed #2a2f3a; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
.help { opacity:.75; font-size:.85rem; }
</style>
""", unsafe_allow_html=True)

# ========== Helpers ==========
PLATE_NUM_RE = re.compile(r"plate_(\d+)\.gcode$", re.IGNORECASE)

def select_preview_from_files(files: dict, plate_name: str) -> bytes | None:
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

def md5_bytes(b:bytes):
    h = hashlib.md5(); h.update(b); return h.hexdigest()

# ====== Generador de bloque paramétrico (cambio de placa) ======
def make_change_block(
    z_cycles:int=5, z_up_mm:float=60.0, z_down_mm:float=20.0, z_f:int=1200,
    y_stage1:float=240.0, y_target:float=265.0, y_f_fast:int=2000, y_f_slow:int=800,
    add_z_lift_after_y_hit:bool=True, z_lift_mm:float=10.0, z_lift_f:int=1200,
    add_end_release:bool=True, end_release_mm:float=15.0, end_release_f:int=1200
) -> str:
    # Cabecera mínima segura
    lines = [
        "; ===== PrintLooper: Cambio de placa (paramétrico) =====",
        "G90",
        "M82",
        "G92 E0",
        "M73 P0 R1",
        "",
        "; --- Ciclos Z ---",
        "G91",
    ]
    for _ in range(int(z_cycles)):
        lines.append(f"G380 S2 Z{float(z_up_mm):.0f} F{int(z_f)}")
        lines.append(f"G380 S3 Z-{float(z_down_mm):.0f} F{int(z_f)}")
    lines.append("G90")
    lines.append("")
    lines += [
        "; --- Home SOLO Y (sin home Z) ---",
        "G28 Y",
        "",
        "; --- Aproximación Y en 2 etapas ---",
        "G90",
        f"G1 Y{float(y_stage1):.0f} F{int(y_f_fast)}",
        f"G1 Y{float(y_target):.0f} F{int(y_f_slow)}",
        ""
    ]
    if add_z_lift_after_y_hit and z_lift_mm > 0:
        lines += [
            "; --- Lift de Z tras llegar a Y target ---",
            "G91",
            f"G1 Z{float(z_lift_mm):.0f} F{int(z_lift_f)}",
            "G90",
            ""
        ]
    # Trayectoria/expulsión Y con clamp al y_target
    lines += [
        "; ----- Expulsión/retornos en Y -----",
        "G90",
        "G1 Y150 F500",
        "G1 Y35  F1000",
        "G1 Y0   F2500",
    ]
    if add_end_release and end_release_mm > 0:
        lines += [
            "G91",
            f"G380 S3 Z-{float(end_release_mm):.0f} F{int(end_release_f)}",
            "G90",
        ]
    lines += [
        "G1 Y{:.0f} F2000".format(float(y_target)),
        "G1 Y53  F2000",
        "G1 Y100 F2000",
        "G1 Y{:.0f} F2000".format(float(y_target)),
        "G1 Y250 F8000",
        "G1 Y{:.0f} F8000".format(float(y_target)),
        "G1 Y0   F1000",
        "G1 Y150 F1000",
        "",
        "M400",
        "M84",
        ""
    ]
    return "\n".join(lines)

def build_pre_wait_block(wait_enabled:bool, wait_mode:str, wait_minutes:float, target_bed:int) -> str:
    if not wait_enabled: return ""
    if wait_mode == "time" and wait_minutes > 0:
        seconds = int(wait_minutes * 60)
        return (
            "; PrintLooper: esperar por tiempo antes del cambio de placa\n"
            "M140 S0\n"
            f"G4 S{seconds}\n"
        )
    if wait_mode == "temp":
        return (
            "; PrintLooper: enfriar cama a temperatura objetivo antes del cambio de placa\n"
            "M140 S0\n"
            f"M190 R{int(target_bed)}\n"
        )
    return ""

# ========== Header ==========
c1, c2 = st.columns([0.22, 0.78])
with c1:
    try: st.image(LOGO_PATH, width=LOGO_SIZE)
    except Exception: st.write("🖨️")
with c2:
    st.markdown("## PrintLooper")
    st.caption("Duplica y encadena placas con cambios automáticos (MOD Bambu Lab A1 — cambio de cama PEI).")

# ===================== TABS PRINCIPALES =====================
tab_queue, tab_swap = st.tabs(["🧩 Cola de impresión", "🛠️ 3MF — Cambio de Cama"])

# ===================== TAB 1: COLA DE IMPRESIÓN =====================
with tab_queue:
    st.markdown("### Parámetros globales de cola")
    colA, colB = st.columns([1,1])
    with colA:
        mode = st.radio(
            "Orden de impresión", ["serial","interleaved"],
            format_func=lambda x: "Serie" if x=="serial" else "Intercalado",
            help="Serie: imprime todas las repeticiones de un modelo y luego el siguiente. Intercalado: alterna modelos por turno."
        )
    with colB:
        queue_change_mode = st.radio(
            "Bloque de cambio a insertar",
            ["param","fixed"],
            format_func=lambda v: "Paramétrico (ajustes)" if v=="param" else "Fijo (legacy)",
            help="Elegí si querés insertar el bloque paramétrico (ajustable) o el bloque fijo clásico."
        )

    st.markdown("---")
    st.markdown("#### Espera antes del cambio")
    colW1, colW2, colW3 = st.columns([1,1,1])
    with colW1:
        wait_enabled_q = st.checkbox(
            "Activar espera", value=False,
            help="Si se activa, la impresora esperará antes del cambio (por tiempo o por temperatura)."
        )
    with colW2:
        wait_mode_q = st.radio(
            "Modo", ["time", "temp"],
            format_func=lambda v: "Por tiempo (min)" if v=="time" else "Por temperatura (≤°C)",
            horizontal=True, disabled=not wait_enabled_q
        )
    with colW3:
        target_bed_q = st.number_input(
            "Cama objetivo (°C)", min_value=0, max_value=120, value=48, step=1,
            disabled=(not wait_enabled_q or wait_mode_q!="temp")
        )
    wait_minutes_q = st.number_input(
        "Minutos (si es por tiempo)", min_value=0.0, value=2.0, step=0.5, format="%.1f",
        disabled=(not wait_enabled_q or wait_mode_q!="time")
    )

    # Bloque fijo legacy (por compatibilidad)
    CHANGE_BLOCK_FIXED = """;======== Starting custom sequence =================          ; Bloque inicial personalizado
; Subir Z a 255 mm desde el punto donde terminó la impresión
G90
G1 Z255 F1500

;======== Starting to change plates =================
G91
; {{CYCLES}}  (no usado)
G1 Z5 F1200
G90

G28 Y
G91
G380 S2 Z30 F1200
G90
; M211 Y0 Z0   ; NO recomendable (mantener límites activos)
G91
G90

; ----- Secuencia de expulsión en Y -----
G1 Y250 F2000
G1 Y266 F500
G1 Z260 F500
G1 Y150 F500
G1 Y35 F1000
G1 Y0 F2500
G91
G380 S3 Z-15 F1200
G90

G1 Y266 F2000
G1 Y53  F2000
G1 Y100 F2000
G1 Y266 F2000
G1 Y250 F8000
G1 Y266 F8000
G1 Y0   F1000
G1 Y150 F1000
G28 Y
;======== Finish to change plates =================
"""

    # Ajustes del bloque paramétrico (se reutilizan en TAB 2)
    st.markdown("#### Ajustes del bloque paramétrico")
    colZ1, colZ2, colZ3, colZ4 = st.columns(4)
    with colZ1:
        z_cycles = st.number_input("Ciclos Z", min_value=0, value=5, step=1,
                                   help="Cantidad de veces que se repite (sube+baja) el eje Z.")
    with colZ2:
        z_up_mm  = st.number_input("Subida Z (mm)", min_value=0.0, value=60.0, step=1.0, format="%.0f",
                                   help="Milímetros hacia arriba en cada ciclo (G380 S2).")
    with colZ3:
        z_down_mm = st.number_input("Bajada Z (mm)", min_value=0.0, value=20.0, step=1.0, format="%.0f",
                                    help="Milímetros hacia abajo en cada ciclo (G380 S3).")
    with colZ4:
        z_f = st.number_input("Feedrate Z (mm/min)", min_value=100, value=1200, step=50,
                              help="Velocidad de los ciclos Z.")

    colY1, colY2, colY3 = st.columns(3)
    with colY1:
        y_stage1 = st.number_input("Y etapa 1", min_value=0.0, value=240.0, step=1.0, format="%.0f",
                                   help="Primer acercamiento en Y.")
    with colY2:
        y_target = st.number_input("Y objetivo (clamp)", min_value=0.0, value=265.0, step=1.0, format="%.0f",
                                   help="Límite seguro de Y (ej. 265 en A1).")
    with colY3:
        y_f_slow = st.number_input("Y F lento (mm/min)", min_value=100, value=800, step=50,
                                   help="Velocidad al llegar al objetivo Y.")

    colYL, colYR = st.columns(2)
    with colYL:
        y_f_fast = st.number_input("Y F rápido (mm/min)", min_value=500, value=2000, step=100,
                                   help="Velocidad del acercamiento inicial en Y.")
    with colYR:
        add_z_lift = st.checkbox("Lift Z tras llegar a Y", value=True,
                                 help="Eleva Z unos mm luego de alcanzar Y objetivo.")
    colL1, colL2 = st.columns(2)
    with colL1:
        z_lift_mm = st.number_input("Lift Z (mm)", min_value=0.0, value=10.0, step=1.0, format="%.0f",
                                    disabled=not add_z_lift)
    with colL2:
        z_lift_f  = st.number_input("Lift Z F (mm/min)", min_value=100, value=1200, step=50,
                                    disabled=not add_z_lift)

    # Espera antes del cambio (para el bloque que se inserta en la cola)
    pre_wait_block_q = build_pre_wait_block(wait_enabled_q, wait_mode_q, wait_minutes_q, int(target_bed_q))

    # Uploader de modelos 3MF
    uploads = st.file_uploader(
        "Subí uno o más .3mf", type=["3mf"], accept_multiple_files=True,
        help="Podés subir varios .3mf; a cada uno le asignás cuántas repeticiones querés."
    )

    # Tarjetas de modelos
    models = []
    if uploads:
        cols = st.columns(len(uploads))
        for i, up in enumerate(uploads):
            data = up.read()
            meta = read_3mf(data)
            with cols[i]:
                st.markdown('<div class="card">', unsafe_allow_html=True)
                st.markdown(
                    f"**{up.name}**  \n"
                    f"<span class='small'>/{meta['plate_name'].split('/')[-1].split('.')[0]}</span>",
                    unsafe_allow_html=True
                )
                preview = select_preview_from_files(meta["files"], meta["plate_name"])
                st.image(preview if preview else "https://via.placeholder.com/320x200?text=No+preview",
                         use_container_width=True)
                reps = st.number_input(
                    "Repeticiones", min_value=1, value=1, step=1, key=f"reps_{i}",
                    help="Cuántas veces se imprimirá este modelo dentro de la cola."
                )
                st.markdown('</div>', unsafe_allow_html=True)
            models.append({
                "name": up.name, "raw": data, "repeats": int(reps),
                "plate_name": meta["plate_name"], "core": meta["core"],
                "shutdown": meta["shutdown"], "files": meta["files"],
            })

    # Vista previa de secuencia (lista)
    def compute_sequence_preview(models, mode, wait_enabled, wait_mode, wait_minutes, target_bed):
        steps = []
        total_prints = sum(m["repeats"] for m in models)
        if total_prints == 0: return steps

        def add_wait_and_swap(step_index, is_last_print):
            if is_last_print: return step_index
            if wait_enabled:
                if wait_mode == "time":
                    steps.append({"#": step_index, "Acción": "Esperar", "Detalle": f"{wait_minutes:.1f} min"})
                    step_index += 1
                else:
                    steps.append({"#": step_index, "Acción": "Esperar", "Detalle": f"Cama ≤ {int(target_bed)}°C"})
                    step_index += 1
            steps.append({"#": step_index, "Acción": "Cambio de placa", "Detalle": "Bloque G-code"})
            return step_index + 1

        idx, printed = 1, 0
        if mode == "serial":
            for m in models:
                for r in range(1, m["repeats"] + 1):
                    steps.append({"#": idx, "Acción": "Imprimir", "Modelo": m["name"], "Repetición": r}); idx += 1
                    printed += 1
                    idx = add_wait_and_swap(idx, printed == total_prints)
        else:
            max_r = max(m["repeats"] for m in models)
            for r in range(1, max_r + 1):
                for m in models:
                    if r <= m["repeats"]:
                        steps.append({"#": idx, "Acción": "Imprimir", "Modelo": m["name"], "Repetición": r}); idx += 1
                        printed += 1
                        idx = add_wait_and_swap(idx, printed == total_prints)
        return steps

    if models:
        st.markdown("### 🔄 Secuencia de impresión")
        preview_steps = compute_sequence_preview(
            models=models,
            mode=mode,
            wait_enabled=wait_enabled_q,
            wait_mode=wait_mode_q,
            wait_minutes=wait_minutes_q,
            target_bed=target_bed_q
        )
        total_prints = sum(m["repeats"] for m in models)
        total_swaps  = sum(1 for s in preview_steps if s["Acción"] == "Cambio de placa")
        total_waits  = sum(1 for s in preview_steps if s["Acción"] == "Esperar")
        st.caption(f"Impresiones: {total_prints} • Esperas: {total_waits} • Cambios: {total_swaps}")
        for s in preview_steps:
            if s["Acción"] == "Imprimir":
                st.write(f"{s['#']}. 🖨️ {s.get('Modelo','-')} — repetición {s.get('Repetición','-')}")
            elif s["Acción"] == "Esperar":
                st.write(f"{s['#']}. ⏳ Esperar {s['Detalle']}")
            else:
                st.write(f"{s['#']}. 🔁 {s['Detalle']}")

    st.markdown("---")

    # Construcción del bloque a insertar en la cola
    pre_wait_block_for_queue = build_pre_wait_block(wait_enabled_q, wait_mode_q, wait_minutes_q, int(target_bed_q))

    if queue_change_mode == "param":
        dynamic_block = make_change_block(
            z_cycles=z_cycles, z_up_mm=z_up_mm, z_down_mm=z_down_mm, z_f=z_f,
            y_stage1=y_stage1, y_target=y_target, y_f_fast=y_f_fast, y_f_slow=y_f_slow,
            add_z_lift_after_y_hit=add_z_lift, z_lift_mm=z_lift_mm, z_lift_f=z_lift_f,
            add_end_release=True, end_release_mm=15.0, end_release_f=1200
        )
        change_block_final = pre_wait_block_for_queue + dynamic_block
    else:
        change_block_final = pre_wait_block_for_queue + CHANGE_BLOCK_FIXED

    # Generar 3MF compuesto
    if st.button("Generar 3MF compuesto", help="Construye un único .3mf con todos los modelos y sus repeticiones, insertando el bloque de cambio seleccionado entre cada impresión."):
        if not models:
            st.warning("Subí al menos un .3mf.")
        else:
            try:
                seq_items = [{"name": m["name"], "core": m["core"], "shutdown": m["shutdown"], "repeats": m["repeats"]}
                             for m in models]
                composite_gcode = compose_sequence(seq_items, change_block_final, mode)
                base = models[0]
                final_3mf = build_final_3mf(base["files"], base["plate_name"], composite_gcode)

                st.success("✅ Cola compuesta generada.")
                st.download_button(
                    "⬇️ Descargar 3MF compuesto", data=final_3mf,
                    file_name=f"queue_{models[0]['name'].rsplit('.',1)[0]}.3mf",
                    mime="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
                )
            except Exception as e:
                st.error(f"Error: {e}")

# ===================== TAB 2: 3MF — CAMBIO DE CAMA =====================
with tab_swap:
    st.markdown("### Generar 3MF de cambio de cama (solo movimientos)")
    st.caption("Ajustá parámetros hasta lograr el movimiento exacto. Podés usar un 3MF base de Orca/Bambu para conservar la 'envoltura' o un esqueleto mínimo.")

    colS1, colS2 = st.columns([1,1])
    with colS1:
        base_3mf = st.file_uploader("3MF base (opcional, recomendado)", type=["3mf"],
                                    help="Si cargás un .3mf ya sliced por Orca/Bambu, se conserva su estructura y previews.")
    with colS2:
        repeats_swap = st.number_input("Repeticiones de la rutina", min_value=1, value=1, step=1,
                                       help="Cuántas veces querés repetir el bloque completo de cambio dentro del mismo G-code.")

    st.markdown("#### Espera previa (opcional)")
    colE1, colE2, colE3 = st.columns([1,1,1])
    with colE1:
        wait_enabled = st.checkbox("Activar espera previa", value=False)
    with colE2:
        wait_mode = st.radio("Modo de espera", ["time","temp"],
                             format_func=lambda v: "Por tiempo (min)" if v=="time" else "Por temperatura (≤°C)",
                             horizontal=True, disabled=not wait_enabled)
    with colE3:
        target_bed = st.number_input("Cama objetivo (°C)", min_value=0, max_value=120, value=48, step=1,
                                     disabled=(not wait_enabled or wait_mode!="temp"))
    wait_minutes = st.number_input("Minutos (si es por tiempo)", min_value=0.0, value=2.0, step=0.5, format="%.1f",
                                   disabled=(not wait_enabled or wait_mode!="time"))

    st.markdown("#### Ajustes del bloque")
    colP1, colP2, colP3, colP4 = st.columns(4)
    with colP1:
        z_cycles2 = st.number_input("Ciclos Z", min_value=0, value=z_cycles, step=1)
    with colP2:
        z_up_mm2  = st.number_input("Subida Z (mm)", min_value=0.0, value=z_up_mm, step=1.0, format="%.0f")
    with colP3:
        z_down_mm2 = st.number_input("Bajada Z (mm)", min_value=0.0, value=z_down_mm, step=1.0, format="%.0f")
    with colP4:
        z_f2 = st.number_input("Feedrate Z (mm/min)", min_value=100, value=z_f, step=50)

    colQ1, colQ2, colQ3 = st.columns(3)
    with colQ1:
        y_stage1_2 = st.number_input("Y etapa 1", min_value=0.0, value=y_stage1, step=1.0, format="%.0f")
    with colQ2:
        y_target_2 = st.number_input("Y objetivo (clamp)", min_value=0.0, value=y_target, step=1.0, format="%.0f")
    with colQ3:
        y_f_slow_2 = st.number_input("Y F lento", min_value=100, value=y_f_slow, step=50)

    colQ4, colQ5 = st.columns(2)
    with colQ4:
        y_f_fast_2 = st.number_input("Y F rápido", min_value=500, value=y_f_fast, step=100)
    with colQ5:
        add_z_lift_2 = st.checkbox("Lift Z tras Y", value=add_z_lift)

    colQ6, colQ7 = st.columns(2)
    with colQ6:
        z_lift_mm_2 = st.number_input("Lift Z (mm)", min_value=0.0, value=z_lift_mm, step=1.0, format="%.0f",
                                      disabled=not add_z_lift_2)
    with colQ7:
        z_lift_f_2  = st.number_input("Lift Z F", min_value=100, value=z_lift_f, step=50,
                                      disabled=not add_z_lift_2)

    st.markdown("#### Vista previa del bloque generado")
    preview_pre_wait = build_pre_wait_block(wait_enabled, wait_mode, wait_minutes, int(target_bed))
    preview_block = make_change_block(
        z_cycles=z_cycles2, z_up_mm=z_up_mm2, z_down_mm=z_down_mm2, z_f=z_f2,
        y_stage1=y_stage1_2, y_target=y_target_2, y_f_fast=y_f_fast_2, y_f_slow=y_f_slow_2,
        add_z_lift_after_y_hit=add_z_lift_2, z_lift_mm=z_lift_mm_2, z_lift_f=z_lift_f_2,
        add_end_release=True, end_release_mm=15.0, end_release_f=1200
    )
    st.code(preview_pre_wait + preview_block, language="gcode")

    st.markdown("---")
    if st.button("🧪 Generar 3MF — Cambio de Cama (solo movimientos)", help="Crea un .3mf con la rutina de cambio. Si subiste un .3mf base, se conserva su estructura y previews."):
        try:
            # Construir G-code final (repeticiones de la rutina)
            seq = []
            for _ in range(int(repeats_swap)):
                seq.append(preview_pre_wait + preview_block)
            swap_gcode = "\n; ===== Separador =====\n".join(seq) + "\n"

            if base_3mf is not None:
                # Usar 3MF base (envoltura de Orca/Bambu)
                src_bytes = base_3mf.read()
                zin = zipfile.ZipFile(io.BytesIO(src_bytes), "r")
                files = {info.filename: zin.read(info.filename) for info in zin.infolist()}
                zin.close()

                # Buscar Metadata/plate_*.gcode y reemplazar
                plate_gcode_name = None
                for name in files.keys():
                    if name.lower().startswith("metadata/") and name.lower().endswith(".gcode"):
                        plate_gcode_name = name; break
                if not plate_gcode_name:
                    st.error("No se encontró Metadata/plate_*.gcode en el 3MF base.")
                else:
                    files[plate_gcode_name] = swap_gcode.encode("utf-8")
                    # Actualizar MD5 si hay sidecar
                    md5_name = plate_gcode_name + ".md5"
                    if md5_name in files:
                        files[md5_name] = (md5_bytes(files[plate_gcode_name]) + "\n").encode("ascii")
                    # Escribir 3MF final
                    out_mem = io.BytesIO()
                    zout = zipfile.ZipFile(out_mem, "w", compression=zipfile.ZIP_DEFLATED)
                    for name, data in files.items():
                        zout.writestr(name, data)
                    zout.close()
                    st.success("✅ 3MF de cambio generado (con base).")
                    st.download_button(
                        "⬇️ Descargar 3MF — Cambio de Cama",
                        data=out_mem.getvalue(),
                        file_name="printlooper_change_only.3mf",
                        mime="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
                    )
            else:
                # Usar esqueleto 3MF mínimo
                files = minimal_3mf_skeleton()
                files["Metadata/plate_1.gcode"] = swap_gcode.encode("utf-8")
                files["Metadata/plate_1.gcode.md5"] = (md5_bytes(files["Metadata/plate_1.gcode"]) + "\n").encode("ascii")

                out_mem = io.BytesIO()
                zout = zipfile.ZipFile(out_mem, "w", compression=zipfile.ZIP_DEFLATED)
                for name, data in files.items():
                    zout.writestr(name, data)
                zout.close()

                st.success("✅ 3MF de cambio generado (esqueleto mínimo).")
                st.download_button(
                    "⬇️ Descargar 3MF — Cambio de Cama",
                    data=out_mem.getvalue(),
                    file_name="printlooper_change_only_min.3mf",
                    mime="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"
                )
        except Exception as e:
            st.error(f"Error: {e}")
