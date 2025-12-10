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
    # Fallback para entorno local
    api_key = os.getenv("GOOGLE_API_KEY")
    if api_key:
        genai.configure(api_key=api_key)
    else:
        st.error("❌ Falta la API KEY. Configura 'GOOGLE_API_KEY' en los secrets.")
        st.stop()

# ==========================================
# 🧠 DEFINICIÓN DE PROMPTS MEJORADA
# ==========================================
PROMPTS_POR_TIPO = {
    "Factura Regal (Filtra Duplicados)": """
        Actúa como un auditor de aduanas minucioso. Analiza la imagen de esta factura.
        
        TU MISIÓN PRINCIPAL ES VALIDAR SI ES "ORIGINAL".
        
        INSTRUCCIONES VISUALES ESPECÍFICAS:
        1. Escanea todo el encabezado y las esquinas.
        2. Busca la palabra "Original". ¡OJO! Puede aparecer de dos formas:
           - TIPO A: Texto pequeño dentro de un RECUADRO SOMBREADO (azul o gris) cerca del centro-derecha.
           - TIPO B: Texto suelto tipo máquina de escribir en la esquina inferior derecha del encabezado.
        3. Busca la palabra "Duplicado", "Copia" o "Copy".

        LÓGICA DE DECISIÓN:
        - Si encuentras "Duplicado", "Copia" o "File Copy" -> ES DUPLICADO.
        - Si encuentras "Original" -> ES ORIGINAL.
        - Si NO encuentras ninguna marca (ni Original ni Duplicado) -> ASUME QUE ES ORIGINAL (Por seguridad).

        SALIDA JSON REQUERIDA:
        Si es DUPLICADO devuelve: {"tipo_documento": "Duplicado"}
        Si es ORIGINAL devuelve:
        {
            "tipo_documento": "Original",
            "numero_factura": "Invoice #",
            "fecha": "Date",
            "proveedor": "REGAL WORLDWIDE TRADING",
            "cliente": "Sold To",
            "items": [
                {
                    "codigo": "Model/UPC",
                    "descripcion": "Description",
                    "cantidad": 0,
                    "precio_unitario": 0.00,
                    "total_linea": 0.00
                }
            ],
            "total_factura": 0.00
        }
    """,
    
    "Factura Goodyear (Lee Todo)": """
        Actúa como experto digitador para facturas de Goodyear.
        Esta factura NO tiene duplicados, procesa todas las páginas.

        INSTRUCCIONES:
        1. Extrae la tabla mapeando: Code->codigo, Description->descripcion, Qty->cantidad, Unit Value->precio_unitario.
        2. IMPORTANTE: Incluye SIEMPRE "tipo_documento": "Original" en tu respuesta JSON para que el sistema acepte los datos.
        
        ESTRUCTURA JSON:
        {
            "tipo_documento": "Original",
            "numero_factura": "Invoice Number",
            "fecha": "Date",
            "proveedor": "Goodyear International Corporation",
            "cliente": "Sold To",
            "items": [
                {
                    "codigo": "...",
                    "descripcion": "...",
                    "cantidad": 0,
                    "precio_unitario": 0.00,
                    "total_linea": 0.00
                }
            ],
            "total_factura": 0.00
        }
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
        
        # Limpieza JSON
        if "```json" in texto: texto = texto.replace("```json", "").replace("```", "")
        if "```" in texto: texto = texto.replace("```", "")
        
        datos = json.loads(texto)
        return datos
    except Exception as e:
        print(f"Error o página vacía: {e}")
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
        
        # LÓGICA DE FILTRADO
        # Si la IA dice explícitamente "Duplicado", la ignoramos.
        # Si dice "Original" O si por error no puso el tipo pero trajo items, asumimos que es buena.
        es_duplicado = data and data.get("tipo_documento") == "Duplicado"
        trae_datos = data and len(data.get("items", [])) > 0
        
        if es_duplicado:
            st.warning(f"🚫 Página {i+1} ignorada (Detectada como DUPLICADO).")
        elif data and (data.get("tipo_documento") == "Original" or trae_datos):
            st.success(f"✅ Página {i+1} procesada como ORIGINAL.")
            
            factura_id = data.get("numero_factura", "S/N")
            cliente = data.get("cliente", "")
            
            resumen_facturas.append({
                "Página": i + 1,
                "Factura": factura_id,
                "Fecha": data.get("fecha"),
                "Total": data.get("total_factura"),
                "Cliente": cliente
            })
            
            if "items" in data and isinstance(data["items"], list):
                for item in data["items"]:
                    item["Factura_Origen"] = factura_id
                    items_totales.append(item)
        else:
             st.warning(f"⚠️ Página {i+1} sin datos claros (¿Posible página en blanco o error?).")
        
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
    st.info("Regal: Filtra duplicados.\nGoodyear: Lee todo.")

uploaded_file = st.file_uploader("Sube tu Factura (PDF)", type=["pdf"])

if uploaded_file is not None and st.button("🚀 Extraer Datos"):
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(uploaded_file.read())
        path = tmp.name
    
    resumen, df_items = process_pdf(path, tipo_pdf)
    
    if not df_items.empty:
        st.divider()
        st.subheader("📦 Detalle de Productos")
        st.dataframe(df_items, use_container_width=True)
        
        st.subheader("📄 Resumen")
        st.dataframe(pd.DataFrame(resumen), use_container_width=True)
        
        csv = df_items.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Descargar CSV", csv, "items_extracted.csv", "text/csv")
    else:
        st.warning("No se encontraron datos. Revisa si el documento es legible.")
    
    os.remove(path)
