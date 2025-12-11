import streamlit as st
import pandas as pd
import google.generativeai as genai
from pdf2image import convert_from_path
import tempfile
import os
import json
import time

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor IA Separado", layout="wide")
st.title("🤖 Nexus Extractor: Tablas Individuales por Archivo")

# 1. Cargar lista de API Keys desde secrets
if "mis_llaves" in st.secrets:
    API_KEYS = st.secrets["mis_llaves"]
elif "GOOGLE_API_KEY" in st.secrets:
    API_KEYS = [st.secrets["GOOGLE_API_KEY"]]
else:
    st.error("❌ Falta configuración de llaves. Configura 'mis_llaves' en secrets.")
    st.stop()

# ==========================================
# 🧠 DEFINICIÓN DE PROMPTS
# ==========================================
PROMPTS_POR_TIPO = {
    "Factura Internacional (Regal/General)": """
        Actúa como experto en comercio exterior.
        REGLA DE FILTRADO:
        1. Si dice "Duplicado" o "Copia", devuelve "tipo_documento": "Copia" y items vacíos.
        2. Si es Original, extrae todo.
        JSON ESPERADO:
        {
            "tipo_documento": "Original/Copia",
            "numero_factura": "Invoice #",
            "fecha": "Date",
            "orden_compra": "PO #",
            "proveedor": "Vendor",
            "cliente": "Sold To",
            "items": [
                {
                    "modelo": "Model",
                    "descripcion": "Description",
                    "cantidad": 0,
                    "precio_unitario": 0.00,
                    "total_linea": 0.00
                }
            ],
            "total_factura": 0.00
        }
    """,
    "Factura RadioShack": """
        Factura RadioShack.
        1. Factura # bajo 'COMMERCIAL INVOICE'.
        2. Items: SKU (modelo), Descripción, Cant, Precio, Valor.
        OUTPUT JSON: { "tipo_documento": "Original", "numero_factura": "...", "fecha": "...", "proveedor": "RadioShack", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "total_linea": 0.0}], "total_factura": 0.0 }
    """,
    "Factura Mabe": """
        Factura Mabe.
        1. Factura # bajo 'Factura Exportacion'.
        2. Items: CODIGO MABE (modelo), Descripción, Cant, Precio Unit, Importe.
        OUTPUT JSON: { "tipo_documento": "Original", "numero_factura": "...", "fecha": "...", "proveedor": "Mabe", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "total_linea": 0.0}], "total_factura": 0.0 }
    """
}

# ==========================================
# 🧠 FUNCIÓN CON ROTACIÓN DE LLAVES
# ==========================================
def intentar_generar_con_rotacion(image, prompt):
    generation_config = {"temperature": 0.1, "response_mime_type": "application/json"}
    safety = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    errores_log = []

    for index, key in enumerate(API_KEYS):
        try:
            genai.configure(api_key=key)
            # Prioridad: Flash -> Pro
            model = genai.GenerativeModel("gemini-1.5-flash", generation_config=generation_config, safety_settings=safety)
            
            response = model.generate_content([prompt, image])
            
            if response.prompt_feedback and response.prompt_feedback.block_reason:
                return {}, f"Bloqueo Seguridad Llave {index+1}"

            texto = response.text.strip()
            if "```json" in texto: texto = texto.replace("```json", "").replace("```", "")
            if "```" in texto: texto = texto.replace("```", "")
            
            return json.loads(texto), None

        except Exception as e:
            err_msg = str(e)
            errores_log.append(f"Llave {index+1}: {err_msg}")
            if "429" in err_msg or "Resource has been exhausted" in err_msg:
                continue 
            else:
                continue

    return {}, f"TODAS LAS LLAVES FALLARON. Log: {errores_log}"

# ==========================================
# 🧠 LÓGICA DE PROCESAMIENTO INDIVIDUAL
# ==========================================
def process_single_pdf(pdf_path, filename, tipo_seleccionado):
    prompt = PROMPTS_POR_TIPO[tipo_seleccionado]
    try:
        images = convert_from_path(pdf_path, dpi=200)
    except Exception as e:
        return [], [], f"Error leyendo PDF: {e}"

    items_locales = []
    resumen_local = []
    
    # Barra de progreso pequeña en la barra lateral o toast
    for i, img in enumerate(images):
        data, error = intentar_generar_con_rotacion(img, prompt)
        
        # Filtro de copias
        if not data or "copia" in str(data.get("tipo_documento", "")).lower():
            continue # Ignorar copias
        
        # Procesar Original
        factura_id = data.get("numero_factura", "S/N")
        
        # Guardar Item
        if "items" in data and isinstance(data["items"], list):
            for item in data["items"]:
                item["Archivo_Origen"] = filename
                item["Factura_Origen"] = factura_id
                items_locales.append(item)
                
        # Guardar Resumen (solo 1 vez por factura detectada para no duplicar en resumen)
        # (Aquí simplificamos agregando siempre que hay data, luego puedes filtrar unique)
        resumen_local.append({
            "Archivo": filename,
            "Factura": factura_id,
            "Total": data.get("total_factura"),
            "Cliente": data.get("cliente")
        })
        
        time.sleep(1) # Pausa de cortesía a la API

    return resumen_local, items_locales, None

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================
with st.sidebar:
    st.header("Configuración")
    tipo_pdf = st.selectbox("Selecciona el Tipo de PDF:", list(PROMPTS_POR_TIPO.keys()))
    st.caption(f"🔑 Llaves activas: {len(API_KEYS)}")

uploaded_files = st.file_uploader("Sube tus Facturas (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files and st.button("🚀 Procesar Archivos"):
    
    gran_acumulado_items = [] # Para el botón de "Descargar Todo" al final
    
    st.divider()
    st.subheader(f"📊 Resultados por Archivo ({len(uploaded_files)})")
    
    # BUCLE PRINCIPAL: Procesar y MOSTRAR archivo por archivo
    for idx, uploaded_file in enumerate(uploaded_files):
        
        # Crear contenedor visual para este archivo
        with st.expander(f"📄 Archivo {idx+1}: {uploaded_file.name}", expanded=True):
            
            # Spinner local
            with st.spinner(f"Analizando {uploaded_file.name}..."):
                # Crear temporal
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(uploaded_file.read())
                    path = tmp.name
                    filename = uploaded_file.name
                
                # Procesar
                resumen, items, error_msg = process_single_pdf(path, filename, tipo_pdf)
                os.remove(path) # Limpiar
                
                if error_msg:
                    st.error(error_msg)
                elif items:
                    # === AQUÍ ESTÁ EL CAMBIO: TABLA INDIVIDUAL ===
                    df_local = pd.DataFrame(items)
                    
                    # Mostramos tabla específica de este archivo
                    st.success(f"✅ Se encontraron {len(items)} items.")
                    st.dataframe(df_local, use_container_width=True)
                    
                    # Acumulamos para el csv final
                    gran_acumulado_items.extend(items)
                else:
                    st.warning("⚠️ No se extrajeron datos (Posible duplicado o copia).")

    # --- ZONA DE DESCARGA GLOBAL ---
    if gran_acumulado_items:
        st.divider()
        st.subheader("📥 Descarga Consolidada")
        st.info("Aunque ves las tablas separadas arriba, puedes descargar todo junto en un solo Excel aquí:")
        
        df_master = pd.DataFrame(gran_acumulado_items)
        csv = df_master.to_csv(index=False).encode('utf-8')
        
        st.download_button(
            label="Descargar TODAS las tablas en CSV",
            data=csv,
            file_name="extraccion_completa.csv",
            mime="text/csv"
        )
