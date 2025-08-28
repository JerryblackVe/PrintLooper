# 🖨️ PrintLooper

**PrintLooper** es una aplicación web hecha con [Streamlit](https://streamlit.io/) que permite **automatizar la impresión en serie** con impresoras 3D que utilizan un sistema de **intercambio automático de camas PEI**.  

Esta app fue creada como parte del **MOD para la impresora Bambu Lab A1** que implementa un sistema de **cambio de cama PEI** para producción continua.  

---

## ✨ Características

- 📂 Soporte para múltiples archivos `.3mf` (con preview automático de cada placa).
- 🔄 Repeticiones configurables para cada modelo.
- 🛠️ Inserción automática de bloque **change plates** (plantilla editable).
- ⚙️ Parámetros ajustables:
  - Ciclos Z, descenso/ascenso en mm.
  - Orden de impresión: **Serie** o **Intercalado**.
  - Espera antes de cambio de placa:
    - ⏱️ Por tiempo (minutos).
    - 🌡️ Por temperatura de cama (ej. hasta ≤35 °C con `M190 R35`).
- 🧪 **Modo Prueba (solo movimientos)**:
  - Genera un `.3mf` con solo movimientos, homing y rutinas de cambio.
  - Ideal para calibrar tiempos de enfriado y expulsión de placa en la **Bambu Lab A1 modificada**.
- 🎨 Interfaz moderna y responsive en Streamlit.

---

## 🚀 Instalación

1. Clona este repositorio:

   ```bash
   git clone https://github.com/tuusuario/printlooper.git
   cd printlooper

   
📜 Licencia

Este proyecto se distribuye bajo la licencia MIT.
Podés usarlo, modificarlo y compartirlo libremente, siempre manteniendo la atribución original.

❤️ Créditos

🔧 Desarrollado como herramienta open-source para makers y granjas de impresión.

⚡ Pensado especialmente para la Bambu Lab A1 con MOD de cambio de cama PEI, permitiendo producción continua y automatizada.
