import streamlit as st
import pandas as pd
import google.generativeai as genai
from pdf2image import convert_from_path
import tempfile
import os
import json
import time

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor SAC con Gemini AI", layout="wide")
st.title("🤖 Extractor SAC Potenciado por Google Gemini")

# Configurar API Key desde los secretos de Streamlit
# Si estás en local sin secrets.toml, asegúrate de tener la variable de entorno seteada
if "GOOGLE_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
else:
    st.error("❌ Falta la API KEY. Configura 'GOOGLE_API_KEY' en los secrets de Streamlit.")
    st.stop()

# Usamos Gemini 1.5 Flash (Rápido, barato y excelente con imágenes/documentos)
model = genai.GenerativeModel('gemini-1.5-flash')

# ==========================================
# 🧠 CEREBRO GEMINI (OCR + ESTRUCTURACIÓN)
# ==========================================
def analizar_imagen_con_gemini(image):
    """
    Envía una imagen (página del PDF) a Gemini y le pide JSON estructurado.
    """
    prompt = """
    Actúa como un experto digitador de aduanas y OCR avanzado.
    Analiza esta imagen de un documento SAC (Sistema Arancelario Centroamericano).
    
    Tu tarea:
    1. Identifica la tabla de códigos arancelarios.
    2. Extrae TODOS los registros visibles.
    3. Ignora encabezados de página, números de página o notas al pie.
    4. Si una descripción abarca varias líneas visuales, únelas en una sola cadena de texto.
    5. Devuelve EXCLUSIVAMENTE una lista de objetos JSON con este formato exacto:
       [{"CODIGO": "0101.21.00", "DESCRIPCION": "Caballos reproductores de raza pura", "DAI": "0"}, ...]
    
    Salida requerida: Solo el array JSON, sin bloques de código markdown (```json), sin texto introductorio.
    """
    
    try:
        # Enviamos el prompt + la imagen
        response = model.generate_content([prompt, image])
        texto_respuesta = response.text.strip()
        
        # Limpieza por si Gemini devuelve bloques markdown
        if "```json" in texto_respuesta:
            texto_respuesta = texto_respuesta.replace("```json", "").replace("```", "")
        if "```" in texto_respuesta:
            texto_respuesta = texto_respuesta.replace("```", "")
            
        return json.loads(texto_respuesta)
        
    except Exception as e:
        st.error(f"Error procesando página con IA: {e}")
        return []

# ==========================================
# 🚜 PROCESADOR PRINCIPAL
# ==========================================
def process_pdf_with_gemini(pdf_path):
    st.info("🔄 Convirtiendo PDF a imágenes para que Gemini pueda leerlas...")
    
    # 1. Convertir PDF a imágenes
    try:
        # dpi=150 es suficiente para Gemini (ahorra ancho de banda), 300 es mejor si hay letra pequeña
        images = convert_from_path(pdf_path, dpi=200) 
    except Exception as e:
        st.error(f"Error leyendo el PDF (Posiblemente falta Poppler): {e}")
        return pd.DataFrame()

    all_data = []
    total_pages = len(images)
    
    progress_bar = st.progress(0)
    status_text = st.empty()

    # 2. Iterar por cada página
    for i, img in enumerate(images):
        status_text.markdown(f"**Analizando página {i+1} de {total_pages} con Gemini Vision...**")
        
        # Llamada a la IA
        datos_pagina = analizar_imagen_con_gemini(img)
        
        if datos_pagina:
            all_data.extend(datos_pagina)
            
        # Actualizar barra
        progress_bar.progress((i + 1) / total_pages)
        
        # Pequeña pausa para no saturar el límite de velocidad de la API (Rate Limit) si usas la capa gratuita
        time.sleep(1) 

    status_text.success("✅ Análisis completado.")
    return pd.DataFrame(all_data)

# ==========================================
# 🖥️ INTERFAZ
# ==========================================
with st.sidebar:
    st.header("Instrucciones")
    st.write("Esta herramienta usa **Google Gemini Vision**.")
    st.write("1. La IA 'mira' el documento.")
    st.write("2. Lee el texto (incluso si está borroso).")
    st.write("3. Estructura la tabla automáticamente.")

uploaded_file = st.file_uploader("Sube tu archivo SAC (PDF)", type=["pdf"])

if uploaded_file is not None:
    if st.button("🚀 Iniciar Extracción con IA"):
        
        # Guardar archivo temporal
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(uploaded_file.read())
            pdf_path = tmp.name
        
        # Procesar
        df_result = process_pdf_with_gemini(pdf_path)
        
        # Mostrar resultados
        if not df_result.empty:
            st.divider()
            st.subheader("📊 Resultados de la IA")
            
            # Asegurar columnas correctas
            columnas_orden = ["CODIGO", "DESCRIPCION", "DAI"]
            # Filtrar solo columnas que existan en el resultado
            cols_final = [c for c in columnas_orden if c in df_result.columns]
            df_show = df_result[cols_final] if cols_final else df_result
            
            st.dataframe(df_show, use_container_width=True)
            
            # Descarga
            csv = df_show.to_csv(index=False).encode('utf-8')
            st.download_button(
                "📥 Descargar Excel/CSV",
                csv,
                "sac_gemini_export.csv",
                "text/csv",
                key='download-csv'
            )
        else:
            st.warning("Gemini no encontró datos tabulares o hubo un error de conexión.")
            
        # Limpieza
        if os.path.exists(pdf_path): os.remove(pdf_path)
