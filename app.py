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
.main .block-container {max-width: 1200px; padding-top: 1.2rem;}
h1, h2, h3 { background: linear-gradient(90deg,#e6e6e6,#8AE234);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
.stButton>button, .stDownloadButton>button { border-radius: 14px; padding: 0.6rem 1.1rem; font-weight: 700; }
.card { border:1px solid #2a2f3a; border-radius:16px; padding:12px; background:#141821; }
.small { opacity:.8; font-size:.9rem; }
.footer { opacity:.7; font-size:.85rem; padding-top:1.2rem; border-top:1px dashed #2a2f3a; }
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

# --- Secuencia: cálculo de pasos (lista)
def compute_sequence_preview(models, mode, wait_enabled, wait_mode, wait_minutes, target_bed):
    steps = []
    total_prints = sum(m["repeats"] for m in models)
    if total_prints == 0:
        return steps

    def add_wait_and_swap(step_index, is_last_print):
        if is_last_print:
            return step_index
        if wait_enabled:
            if wait_mode == "time":
                steps.append({"#": step_index, "Acción": "Esperar", "Detalle": f"{wait_minutes:.1f} min"})
                step_index += 1
            else:
                steps.append({"#": step_index, "Acción": "Esperar", "Detalle": f"Cama ≤ {int(target_bed)}°C"})
                step_index += 1
        steps.append({"#": step_index, "Acción": "Cambio de placa", "Detalle": "Bloque G-code fijo"})
        step_index += 1
        return step_index

    idx = 1
    printed = 0

    if mode == "serial":
        for m in models:
            for r in range(1, m["repeats"] + 1):
                steps.append({"#": idx, "Acción": "Imprimir", "Modelo": m["name"], "Repetición": r})
                idx += 1
                printed += 1
                idx = add_wait_and_swap(idx, printed == total_prints)
    else:  # interleaved
        max_r = max(m["repeats"] for m in models)
        for r in range(1, max_r + 1):
            for m in models:
                if r <= m["repeats"]:
                    steps.append({"#": idx, "Acción": "Imprimir", "Modelo": m["name"], "Repetición": r})
                    idx += 1
                    printed += 1
                    idx = add_wait_and_swap(idx, printed == total_prints)
    return steps

# ========== BLOQUE DE CAMBIO (FIJO, SIN CICLOS) ==========
CHANGE_BLOCK_FIXED = """;======== Starting custom sequence =================          ; Bloque inicial personalizado
; Home all axes                                               ; Comentario descriptivo
G28 ;                                                         ; Home de todos los ejes (X/Y/Z)

; Subir Z a 250 mm                                            ; Comentario descriptivo
G90 ; modo absoluto                                           ; Pone el modo de posicionamiento en absoluto
G1 Z250 F3000 ;                                               ; Sube el eje Z hasta 250 mm a 3000 mm/min

;======== Starting to change plates =================         ; Inicio de la secuencia de cambio de placas
G91 ;                                                         ; Modo relativo (movimientos referidos a la posición actual)
; {{CYCLES}}                                                  ; (ELIMINADO) No se ejecutan ciclos Z
G1 Z5 F1200                                                   ; Eleva Z 5 mm a 1200 mm/min (clearance)
G90 ;                                                         ; Vuelve a modo absoluto
G28 Y ;                                                       ; Home solo del eje Y
G91 ;                                                         ; Cambia a modo relativo
G380 S2 Z30 F1200                                             ; Probing/movimiento Z especial (según firmware) S2, distancia 30, F1200
G90 ;                                                         ; Vuelve a modo absoluto
M211 Y0 Z0 ;                                                  ; (Dependiendo del firmware) desactiva límites suaves en Y y Z
G91 ;                                                         ; Modo relativo
G90 ;                                                         ; Vuelve a modo absoluto

G1 Y250 F2000 ;                                               ; Mueve a Y=250 a 2000 mm/min
G1 Y266 F500                                                  ; Mueve a Y=266 a 500 mm/min (más lento para precisión)
G1 Z260 F500                                                  ; Ajuste Z (si se requiere)
G1 Y35 F1000                                                  ; Mueve a Y=35 a 1000 mm/min
G1 Y0 F2500                                                   ; Mueve a Y=0 a 2500 mm/min (rápido)
G91 ;                                                         ; Modo relativo
G380 S3 Z-15 F1200                                            ; Movimiento/probing Z hacia abajo 15 mm (S3)
G90 ;                                                         ; Modo absoluto

G1 Y266 F2000                                                 ; Mueve a Y=266 a 2000 mm/min
G1 Y53 F2000                                                  ; Mueve a Y=53 a 2000 mm/min
G1 Y100 F2000                                                 ; Mueve a Y=100 a 2000 mm/min
G1 Y266 F2000                                                 ; Mueve a Y=266 a 2000 mm/min
G1 Y10 F1000                                                  ; Mueve a Y=10 a 1000 mm/min
G1 Y1 F500                                                    ; Mueve a Y=1 a 500 mm/min
G1 Y150 F1000                                                 ; Mueve a Y=150 a 1000 mm/min
G28 Y ;                                                       ; Home del eje Y nuevamente
;======== Finish to change plates =================           ; Fin de la secuencia de cambio de placas

"""

# ========== Header ==========
c1, c2 = st.columns([0.22, 0.78])
with c1:
    try: st.image(LOGO_PATH, width=LOGO_SIZE)
    except Exception: st.write("🖨️")
with c2:
    st.markdown("## PrintLooper")
    st.caption("Duplica y encadena placas con cambios automáticos (MOD Bambu Lab A1 — cambio de cama PEI).")

# ========== Sidebar ==========
with st.sidebar:
    st.markdown("### Parámetros")
    mode = st.radio(
        "Orden de impresión", ["serial","interleaved"],
        format_func=lambda x: "Serie" if x=="serial" else "Intercalado",
        help="Serie: imprime todas las repeticiones de un modelo y luego el siguiente. Intercalado: alterna modelos por turno."
    )

    st.markdown("---")
    st.markdown("### Espera antes del cambio de placa")
    wait_enabled = st.checkbox(
        "Activar espera", value=False,
        help="Si se activa, la impresora esperará antes de iniciar el cambio de placa (por tiempo o por temperatura)."
    )
    wait_mode = st.radio(
        "Modo de espera", ["time", "temp"],
        format_func=lambda v: "Por tiempo (min)" if v=="time" else "Por temperatura (cama ≤ °C)",
        horizontal=True, disabled=not wait_enabled,
        help="Tiempo: pausa fija (G4). Temperatura: espera a que la cama alcance la temperatura objetivo (M140 S0 + M190 R)."
    )
    wait_minutes = st.number_input(
        "Minutos de espera", min_value=0.0, value=2.0, step=0.5, format="%.1f",
        disabled=(not wait_enabled or wait_mode!="time"),
        help="Duración de la pausa antes del cambio. Se apaga la cama (M140 S0) y se espera G4 S<segundos>."
    )
    target_bed = st.number_input(
        "Temperatura objetivo de cama (°C)", min_value=0, max_value=120, value=48, step=1,
        disabled=(not wait_enabled or wait_mode!="temp"),
        help="Temperatura de cama a la que debe enfriar antes del cambio. Se usa M140 S0 + M190 R<temp>."
    )

with st.expander("Bloque G-code fijo que se insertará entre repeticiones"):
    st.code(CHANGE_BLOCK_FIXED, language="gcode")

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

# ========== Secuencia (previa) — LISTA VISIBLE ==========
if models:
    st.markdown("### 🔄 Secuencia de impresión")
    preview_steps = compute_sequence_preview(
        models=models,
        mode=mode,
        wait_enabled=wait_enabled,
        wait_mode=wait_mode,
        wait_minutes=wait_minutes,
        target_bed=target_bed
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

# ========== Construcción del bloque de cambio (pre-wait + fijo) ==========
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

change_block_final = pre_wait_block + CHANGE_BLOCK_FIXED

# ========== Generar 3MF compuesto ==========
if uploads and st.button("Generar 3MF compuesto", help="Construye un único .3mf con todos los modelos y sus repeticiones, insertando el bloque G-code fijo entre cada impresión."):
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
