import streamlit as st
import pandas as pd
import google.generativeai as genai
from pdf2image import convert_from_path
import tempfile
import os
import json
import time

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor IA (Single Key)", layout="wide")
st.title("🤖 Nexus Extractor: Versión Estable (1 Llave)")

# 1. Configurar API Key Única
if "GOOGLE_API_KEY" in st.secrets:
    api_key = st.secrets["GOOGLE_API_KEY"]
    genai.configure(api_key=api_key)
else:
    st.error("❌ Falta la API KEY. Configura 'GOOGLE_API_KEY' en los secrets.")
    st.stop()

# ==========================================
# 🧠 DEFINICIÓN DE PROMPTS
# ==========================================
PROMPTS_POR_TIPO = {
    "Factura Internacional (Regal/General)": """
        Actúa como experto en comercio exterior.
        REGLA: Si dice "Duplicado" o "Copia", devuelve "tipo_documento": "Copia" y items []. Si es "Original", extrae todo.
        JSON: {"tipo_documento": "Original/Copia", "numero_factura": "...", "fecha": "...", "orden_compra": "...", "proveedor": "...", "cliente": "...", "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.0, "total_linea": 0.0}], "total_factura": 0.0}
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
# 🧠 BUSCADOR DE MODELO (Evita error 404)
# ==========================================
def obtener_modelo_dinamico():
    """Busca el nombre exacto del modelo disponible en tu cuenta."""
    try:
        modelos = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        
        # 1. Buscar Flash 1.5
        flash = next((m for m in modelos if 'flash' in m.lower() and '1.5' in m), None)
        if flash: return flash
        
        # 2. Buscar Pro 1.5
        pro = next((m for m in modelos if 'pro' in m.lower() and '1.5' in m), None)
        if pro: return pro
        
        # 3. Fallback
        return modelos[0] if modelos else "models/gemini-1.5-flash"
    except:
        return "models/gemini-1.5-flash"

# Inicializamos el modelo una sola vez
NOMBRE_MODELO = obtener_modelo_dinamico()
st.sidebar.info(f"✅ Modelo conectado: {NOMBRE_MODELO}")

# ==========================================
# 🧠 LÓGICA DE ANÁLISIS
# ==========================================
def analizar_pagina(image, prompt):
    generation_config = {"temperature": 0.1, "response_mime_type": "application/json"}
    safety = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    try:
        model = genai.GenerativeModel(NOMBRE_MODELO, generation_config=generation_config, safety_settings=safety)
        response = model.generate_content([prompt, image])
        
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            return {}, "Bloqueo de Seguridad (Safety Filter)"

        texto = response.text.strip()
        if "```json" in texto: texto = texto.replace("```json", "").replace("```", "")
        if "```" in texto: texto = texto.replace("```", "")
        
        return json.loads(texto), None

    except Exception as e:
        err_msg = str(e)
        if "429" in err_msg or "exhausted" in err_msg:
            return {}, "⚠️ CUOTA EXCEDIDA: Has alcanzado el límite diario de Google."
        return {}, f"Error técnico: {err_msg}"

def procesar_pdf(pdf_path, filename, tipo_seleccionado):
    prompt = PROMPTS_POR_TIPO[tipo_seleccionado]
    try:
        images = convert_from_path(pdf_path, dpi=200)
    except Exception as e:
        return [], [], f"Error leyendo PDF: {e}"

    items_locales = []
    resumen_local = []
    
    for i, img in enumerate(images):
        data, error = analizar_pagina(img, prompt)
        
        if error:
            st.error(f"{filename} Pág {i+1}: {error}")
            continue
            
        # Filtro Copias
        if not data or "copia" in str(data.get("tipo_documento", "")).lower():
            continue 
            
        factura_id = data.get("numero_factura", "S/N")
        
        if "items" in data and isinstance(data["items"], list):
            for item in data["items"]:
                item["Archivo_Origen"] = filename
                item["Factura_Origen"] = factura_id
                items_locales.append(item)
        
        resumen_local.append({
            "Archivo": filename,
            "Factura": factura_id,
            "Total": data.get("total_factura"),
            "Cliente": data.get("cliente")
        })
        
        time.sleep(1) # Pausa breve para cuidar la cuota

    return resumen_local, items_locales, None

# ==========================================
# 🖥️ INTERFAZ
# ==========================================
with st.sidebar:
    st.header("Configuración")
    tipo_pdf = st.selectbox("Plantilla:", list(PROMPTS_POR_TIPO.keys()))

uploaded_files = st.file_uploader("Sube Facturas (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files and st.button("🚀 Procesar Archivos"):
    
    gran_acumulado = []
    st.divider()
    
    for uploaded_file in uploaded_files:
        with st.expander(f"📄 {uploaded_file.name}", expanded=True):
            with st.spinner(f"Analizando {uploaded_file.name}..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(uploaded_file.read())
                    path = tmp.name
                    fname = uploaded_file.name
                
                resumen, items, error = procesar_pdf(path, fname, tipo_pdf)
                os.remove(path)
                
                if items:
                    df = pd.DataFrame(items)
                    st.success(f"✅ Extracción exitosa: {len(items)} items.")
                    st.dataframe(df, use_container_width=True)
                    gran_acumulado.extend(items)
                elif error:
                    st.error(error)
                else:
                    st.warning("⚠️ Sin datos (Documento 'Copia' o vacío).")

    if gran_acumulado:
        st.divider()
        csv = pd.DataFrame(gran_acumulado).to_csv(index=False).encode('utf-8')
        st.download_button("📥 Descargar Todo (CSV)", csv, "extraccion_completa.csv", "text/csv")
