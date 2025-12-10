import streamlit as st
import pandas as pd
import google.generativeai as genai
from pdf2image import convert_from_path
import tempfile
import os
import json
import time

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor SAC con Gemini", layout="wide")
st.title("🤖 Extractor SAC Inteligente (Auto-Detect Model)")

# 1. Configurar API Key
if "GOOGLE_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
else:
    st.error("❌ Falta la API KEY. Configura 'GOOGLE_API_KEY' en los secrets.")
    st.stop()

# ==========================================
# 🧠 SELECCIÓN AUTOMÁTICA DE MODELO
# ==========================================
def conseguir_mejor_modelo():
    """
    Consulta a Google qué modelos tiene tu API Key y elige el mejor disponible.
    Prioridad: 1.5-Flash (Rápido) -> 1.5-Pro (Potente) -> gemini-pro (Legacy)
    """
    try:
        # Listar modelos que soportan generar contenido
        modelos_disponibles = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                modelos_disponibles.append(m.name)
        
        # Lógica de selección
        # 1. Buscar Flash (Ideal para OCR rápido)
        for m in modelos_disponibles:
            if 'flash' in m.lower() and '1.5' in m:
                return genai.GenerativeModel(m)
        
        # 2. Buscar 1.5 Pro
        for m in modelos_disponibles:
            if 'pro' in m.lower() and '1.5' in m:
                return genai.GenerativeModel(m)
                
        # 3. Fallback a lo que sea que haya (gemini-pro estándar)
        if modelos_disponibles:
            return genai.GenerativeModel(modelos_disponibles[0])
            
        return None
    except Exception as e:
        st.error(f"Error al listar modelos: {e}")
        return None

# Inicializar modelo
model = conseguir_mejor_modelo()

if model is None:
    st.error("❌ No se encontraron modelos disponibles en tu cuenta de Google AI.")
    st.stop()
else:
    # Mostramos qué modelo se seleccionó para que estés tranquilo
    print(f"✅ Usando modelo: {model.model_name}") 

# ==========================================
# 🧠 LÓGICA DE EXTRACCIÓN (GEMINI)
# ==========================================
def analizar_imagen_con_gemini(image):
    prompt = """
    Actúa como un sistema OCR especializado en tablas de aduanas (SAC).
    Analiza la imagen adjunta.
    1. Extrae los datos de la tabla (Código, Descripción, DAI).
    2. Si hay descripciones en múltiples líneas, únelas.
    3. Si el OCR es confuso, usa tu lógica para corregir (ej: 'C0DIGO' -> 'CODIGO').
    4. Devuelve SOLO una lista JSON válida, sin markdown:
       [{"CODIGO": "...", "DESCRIPCION": "...", "DAI": "..."}]
    """
    try:
        response = model.generate_content([prompt, image])
        texto = response.text.strip()
        
        # Limpieza de bloques de código
        if "```json" in texto: texto = texto.replace("```json", "").replace("```", "")
        if "```" in texto: texto = texto.replace("```", "")
            
        return json.loads(texto)
    except Exception as e:
        st.warning(f"⚠️ Error en una página: {e}")
        return []

# ==========================================
# 🚜 PROCESADOR DE PDF
# ==========================================
def process_pdf(pdf_path):
    st.info(f"Using AI Model: {model.model_name.split('/')[-1]}")
    
    try:
        images = convert_from_path(pdf_path, dpi=200)
    except Exception as e:
        st.error(f"Error leyendo PDF: {e}")
        return pd.DataFrame()

    all_data = []
    bar = st.progress(0)
    
    for i, img in enumerate(images):
        data = analizar_imagen_con_gemini(img)
        if data: all_data.extend(data)
        bar.progress((i + 1) / len(images))
        time.sleep(1) # Respetar rate limits
        
    return pd.DataFrame(all_data)

# ==========================================
# 🖥️ INTERFAZ
# ==========================================
uploaded_file = st.file_uploader("Sube SAC (PDF)", type=["pdf"])

if uploaded_file is not None and st.button("🚀 Iniciar Extracción Inteligente"):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(uploaded_file.read())
        path = tmp.name
    
    df = process_pdf(path)
    
    if not df.empty:
        st.success(f"✅ Extracción completa: {len(df)} registros.")
        st.dataframe(df)
        csv = df.to_csv(index=False).encode('utf-8')
        st.download_button("Descargar CSV", csv, "sac_ia.csv", "text/csv")
    else:
        st.warning("No se extrajeron datos.")
    
    os.remove(path)
