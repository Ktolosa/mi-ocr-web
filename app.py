import streamlit as st
import pandas as pd
import google.generativeai as genai
from pdf2image import convert_from_path
import tempfile
import os
import json
import time

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Nexus Extractor Pro", layout="wide")
st.title("🤖 Nexus Extractor: Filtro Inteligente & Robusto")

# 1. Configurar API Key
if "GOOGLE_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
else:
    st.error("❌ Falta la API KEY. Configura 'GOOGLE_API_KEY' en los secrets.")
    st.stop()

# ==========================================
# 🧠 DEFINICIÓN DE PROMPTS (ESTRATEGIA ROBUSTA)
# ==========================================
PROMPTS_POR_TIPO = {
    "Factura Internacional (Regal/General)": """
        Actúa como un experto en comercio exterior. Analiza la imagen de esta factura.
        
        TU OBJETIVO: Extraer datos estructurados independientemente de si es original o copia.
        
        INSTRUCCIONES DE EXTRACCIÓN:
        1. Busca en el documento palabras como "Original", "Duplicado", "Copia", "Copy".
        2. Extrae ese texto exacto en el campo 'tipo_documento'.
        3. Extrae TODOS los datos de la factura (Items, montos, fechas) SIN FILTRAR NADA AÚN.
        
        Devuelve SOLAMENTE un JSON con esta estructura:
        {
            "tipo_documento": "Texto encontrado (ej: Original, Duplicado, Copia)",
            "numero_factura": "Invoice Number",
            "fecha": "Date (YYYY-MM-DD)",
            "orden_compra": "Order Number",
            "proveedor": "Vendedor / Shipper",
            "cliente": "Sold To / Consignee",
            "items": [
                {
                    "modelo": "Model / Item Code",
                    "descripcion": "Description",
                    "cantidad": 0,
                    "precio_unitario": 0.00,
                    "total_linea": 0.00
                }
            ],
            "total_factura": 0.00
        }
    """,
    "Otro Tipo de Documento": "Analiza este documento y extrae los datos clave en JSON."
}

# ==========================================
# 🧠 SELECCIÓN DE MODELO Y SEGURIDAD
# ==========================================
def conseguir_mejor_modelo():
    # 1. Configuración de generación para forzar JSON
    generation_config = {
        "temperature": 0.1,
        "response_mime_type": "application/json",
    }
    
    # 2. Desactivar filtros de seguridad (Crucial para documentos comerciales)
    safety_settings = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    try:
        modelos = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        
        # Prioridad: Flash 1.5 -> Pro 1.5
        modelo_seleccionado = next((m for m in modelos if 'flash' in m.lower() and '1.5' in m), None)
        if not modelo_seleccionado:
            modelo_seleccionado = next((m for m in modelos if 'pro' in m.lower() and '1.5' in m), None)
            
        if modelo_seleccionado:
            return genai.GenerativeModel(
                model_name=modelo_seleccionado,
                generation_config=generation_config,
                safety_settings=safety_settings
            )
        return None
    except Exception as e:
        st.error(f"Error conectando con Google AI: {e}")
        return None

model = conseguir_mejor_modelo()
if not model:
    st.error("No se pudo iniciar el modelo de IA.")
    st.stop()

# ==========================================
# 🧠 LÓGICA DE ANÁLISIS
# ==========================================
def analizar_pagina(image, prompt):
    try:
        response = model.generate_content([prompt, image])
        
        # Verificar bloqueos de seguridad explícitos
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            st.error(f"⚠️ Bloqueo de IA: {response.prompt_feedback}")
            return {}

        texto = response.text.strip()
        # Limpieza extra por seguridad
        if "```json" in texto: texto = texto.replace("```json", "").replace("```", "")
        
        return json.loads(texto)

    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg:
            st.error("📉 ERROR DE CUOTA (429): Has excedido el límite de consultas diarias a la API.")
        else:
            st.error(f"❌ Error al procesar página: {error_msg}")
        return {}

def process_pdf(pdf_path, tipo_seleccionado):
    st.info(f"⚡ Motor IA: {model.model_name} | Estrategia: Extracción + Filtrado Python")
    prompt = PROMPTS_POR_TIPO[tipo_seleccionado]
    
    try:
        images = convert_from_path(pdf_path, dpi=200)
    except Exception as e:
        st.error(f"Error crítico leyendo PDF (Poppler): {e}")
        return [], pd.DataFrame()

    items_totales = []
    resumen_docs = []
    
    bar = st.progress(0)
    
    for i, img in enumerate(images):
        st.write(f"⏳ Analizando página {i+1}...")
        data = analizar_pagina(img, prompt)
        
        if not data:
            st.warning(f"⚠️ Página {i+1}: La IA no devolvió datos válidos.")
        else:
            # === FILTRO PYTHON (MÁS SEGURO) ===
            tipo_doc = str(data.get("tipo_documento", "Original")).lower()
            palabras_prohibidas = ["duplicado", "copia", "duplicate", "copy"]
            
            es_copia = any(p in tipo_doc for p in palabras_prohibidas)
            
            if es_copia:
                st.warning(f"🚫 Página {i+1} DESCARTADA. Detectado como: '{data.get('tipo_documento')}'")
            else:
                st.success(f"✅ Página {i+1} APROBADA. Tipo: '{data.get('tipo_documento')}'")
                
                # Procesar datos
                factura_id = data.get("numero_factura", f"PAG-{i+1}")
                resumen_docs.append({
                    "Página": i+1,
                    "Factura": factura_id,
                    "Fecha": data.get("fecha"),
                    "Total": data.get("total_factura"),
                    "Cliente": data.get("cliente"),
                    "Estado": "Procesado"
                })
                
                if "items" in data and isinstance(data["items"], list):
                    for item in data["items"]:
                        item["Factura_Origen"] = factura_id
                        items_totales.append(item)
        
        bar.progress((i + 1) / len(images))
        time.sleep(1) # Pequeña pausa para no saturar la API

    return resumen_docs, pd.DataFrame(items_totales)

# ==========================================
# 🖥️ INTERFAZ DE USUARIO
# ==========================================
with st.sidebar:
    st.header("🎛️ Panel de Control")
    tipo_pdf = st.selectbox("Plantilla de Extracción:", list(PROMPTS_POR_TIPO.keys()))
    st.info("ℹ️ Nota: El sistema leerá todas las páginas y descartará automáticamente las que digan 'Duplicado'.")

uploaded_file = st.file_uploader("📂 Sube tu archivo PDF aquí", type=["pdf"])

if uploaded_file and st.button("🚀 Iniciar Extracción"):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(uploaded_file.read())
        path = tmp.name
    
    resumen, df_items = process_pdf(path, tipo_pdf)
    
    if not df_items.empty:
        st.divider()
        st.subheader("📦 Items Extraídos (Originales)")
        st.dataframe(df_items, use_container_width=True)
        
        st.subheader("📄 Resumen de Facturas")
        st.dataframe(pd.DataFrame(resumen), use_container_width=True)
        
        # Botón Descarga
        csv = df_items.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Descargar Excel / CSV",
            data=csv,
            file_name="extraccion_facturas.csv",
            mime="text/csv"
        )
    else:
        st.warning("No se encontraron items válidos. Revisa si todas las páginas eran copias o si hubo error de API.")
    
    os.remove(path)
