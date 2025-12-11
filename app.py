import streamlit as st
import pandas as pd
import google.generativeai as genai
from pdf2image import convert_from_path
import tempfile
import os
import json
import time

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor IA Multi-Formato", layout="wide")
st.title("🤖 Nexus Extractor: Filtro Inteligente")

# 1. Configurar API Key
if "GOOGLE_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
else:
    st.error("❌ Falta la API KEY. Configura 'GOOGLE_API_KEY' en los secrets.")
    st.stop()

# ==========================================
# 🧠 DEFINICIÓN DE PROMPTS MEJORADA
# ==========================================
PROMPTS_POR_TIPO = {
    "Factura Internacional (Regal/General)": """
        Actúa como un experto en comercio exterior. Analiza la imagen de esta factura.
        
        TU TAREA PRINCIPAL:
        1. Identifica si el documento dice explícitamente "Original", "Duplicado", "Copia" o no dice nada.
        2. Extrae TODOS los datos solicitados sin importar si es copia u original. Nosotros filtraremos después.

        Devuelve un JSON con esta estructura exacta:
        {
            "tipo_documento": "Indica aqui textualmente si dice Original, Duplicado o Copia",
            "numero_factura": "Extraer Invoice #",
            "fecha": "Extraer Date (Formato YYYY-MM-DD)",
            "orden_compra": "Extraer Order #",
            "proveedor": "Nombre de la empresa vendedora",
            "cliente": "Nombre de Sold To",
            "items": [
                {
                    "modelo": "...",
                    "descripcion": "...",
                    "cantidad": 0,
                    "precio_unitario": 0.00,
                    "total_linea": 0.00
                }
            ],
            "total_factura": 0.00
        }
    """,
    
    "Otro Tipo de Documento (Ejemplo)": """
        Analiza este documento... (Aquí pondrías otras reglas)
    """
}

# ==========================================
# 🧠 SELECCIÓN DE MODELO
# ==========================================
def conseguir_mejor_modelo():
    try:
        modelos = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        # Prioridad: Flash -> Pro -> Cualquiera
        for m in modelos:
            if 'flash' in m.lower() and '1.5' in m: return genai.GenerativeModel(m)
        for m in modelos:
            if 'pro' in m.lower() and '1.5' in m: return genai.GenerativeModel(m)
        return genai.GenerativeModel(modelos[0]) if modelos else None
    except:
        return None

model = conseguir_mejor_modelo()
if not model:
    st.error("No se encontraron modelos de Gemini.")
    st.stop()

# ==========================================
# 🧠 LÓGICA DE EXTRACCIÓN
# ==========================================
def analizar_pagina(image, prompt_especifico):
    try:
        response = model.generate_content([prompt_especifico, image])
        texto = response.text.strip()
        
        # Limpieza JSON agresiva
        if "```json" in texto: texto = texto.replace("```json", "").replace("```", "")
        if "```" in texto: texto = texto.replace("```", "")
        
        datos = json.loads(texto)
        return datos
    except Exception as e:
        print(f"Error procesando respuesta IA: {e}")
        return {}

def process_pdf(pdf_path, tipo_seleccionado):
    st.info(f"Usando IA: {model.model_name} | Modo: {tipo_seleccionado}")
    prompt = PROMPTS_POR_TIPO[tipo_seleccionado]
    
    try:
        images = convert_from_path(pdf_path, dpi=200)
    except Exception as e:
        st.error(f"Error leyendo PDF: {e}")
        return [], pd.DataFrame()

    items_totales = []
    resumen_facturas = []
    
    bar = st.progress(0)
    
    for i, img in enumerate(images):
        data = analizar_pagina(img, prompt)
        
        # === LÓGICA DE FILTRADO ROBUSTA (EN PYTHON) ===
        # Normalizamos a minusculas para evitar errores de "Original" vs "original"
        tipo_doc_raw = str(data.get("tipo_documento", "Original")).lower()
        
        # Lista negra: Si dice duplicado o copia, lo marcamos como tal.
        es_duplicado = any(x in tipo_doc_raw for x in ["duplicado", "copia", "duplicate", "copy"])
        
        if not data:
            st.warning(f"⚠️ Página {i+1}: No se pudieron extraer datos (JSON vacío).")
        elif es_duplicado:
            st.warning(f"🚫 Página {i+1} ignorada. Tipo detectado: '{data.get('tipo_documento')}'")
        else:
            st.success(f"✅ Página {i+1} procesada. Tipo detectado: '{data.get('tipo_documento')}'")
            
            factura_id = data.get("numero_factura", "S/N")
            cliente = data.get("cliente", "")
            
            resumen_facturas.append({
                "Factura": factura_id,
                "Fecha": data.get("fecha"),
                "Total": data.get("total_factura"),
                "Cliente": cliente,
                "Tipo Detectado": data.get("tipo_documento") 
            })
            
            if "items" in data and isinstance(data["items"], list):
                for item in data["items"]:
                    item["Factura_Origen"] = factura_id
                    items_totales.append(item)
        
        bar.progress((i + 1) / len(images))
        time.sleep(1)

    return resumen_facturas, pd.DataFrame(items_totales)

# ==========================================
# 🖥️ INTERFAZ
# ==========================================
with st.sidebar:
    st.header("Configuración")
    tipo_pdf = st.selectbox(
        "Selecciona el Tipo de PDF:",
        list(PROMPTS_POR_TIPO.keys())
    )
    st.info("El sistema filtrará automáticamente duplicados y copias.")

uploaded_file = st.file_uploader("Sube tu Factura (PDF)", type=["pdf"])

if uploaded_file is not None and st.button("🚀 Extraer Datos"):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(uploaded_file.read())
        path = tmp.name
    
    resumen, df_items = process_pdf(path, tipo_pdf)
    
    if not df_items.empty:
        st.divider()
        st.subheader("📦 Detalle de Productos (Items)")
        st.dataframe(df_items, use_container_width=True)
        
        st.subheader("📄 Resumen de Documentos Procesados")
        st.dataframe(pd.DataFrame(resumen), use_container_width=True)
        
        csv = df_items.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Descargar Detalle Items (CSV)", csv, "items_originales.csv", "text/csv")
    else:
        st.error("No se encontraron datos válidos en las páginas ORIGINALES.")
    
    os.remove(path)
