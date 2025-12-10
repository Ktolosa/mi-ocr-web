import streamlit as st
import pandas as pd
import google.generativeai as genai
from pdf2image import convert_from_path
import tempfile
import os
import json
import time

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Nexus AI - Extractor Inteligente", layout="wide")
st.title("🤖 Nexus AI: Extractor Multi-Formato")

# 1. Configuración de API Key (Desde Secrets o Environment)
if "GOOGLE_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GOOGLE_API_KEY"])
else:
    # Fallback por si lo corres local con variable de entorno
    api_key = os.getenv("GOOGLE_API_KEY")
    if api_key:
        genai.configure(api_key=api_key)
    else:
        st.error("❌ Falta la API KEY. Configura 'GOOGLE_API_KEY' en los secrets de Streamlit.")
        st.stop()

# ==========================================
# 🧠 DICCIONARIO DE PROMPTS (CEREBRO)
# ==========================================
PROMPTS_POR_TIPO = {
    "Factura Regal (Filtra Duplicados)": """
        Actúa como un auditor de aduanas. Analiza esta imagen de factura comercial.
        
        REGLAS DE NEGOCIO CRÍTICAS:
        1. Busca en el documento las palabras "Original" o "Duplicado" (suelen estar en la esquina superior o inferior).
        2. SI ES "DUPLICADO" o "COPIA": Devuelve un JSON con {"tipo_documento": "Duplicado"}. NO extraigas items.
        3. SI ES "ORIGINAL": Extrae todos los datos detallados.

        ESTRUCTURA JSON REQUERIDA (Para Originales):
        {
            "tipo_documento": "Original",
            "numero_factura": "Extraer Invoice #",
            "fecha": "Extraer Date",
            "proveedor": "REGAL WORLDWIDE TRADING",
            "cliente": "Extraer Sold To",
            "items": [
                {
                    "codigo": "UPC o Model",
                    "descripcion": "Description (Une líneas si es necesario)",
                    "cantidad": 0,
                    "precio_unitario": 0.00,
                    "total_linea": 0.00
                }
            ],
            "total_factura": 0.00
        }
    """,

    "Factura Goodyear (Procesa Todo)": """
        Actúa como un digitador experto. Analiza esta factura de llantas (Goodyear).
        Esta factura NO tiene duplicados, debes procesar CADA PÁGINA que contenga una tabla de items.
        
        INSTRUCCIONES DE MAPEO:
        1. Columna 'Code' -> campo 'codigo'
        2. Columna 'Description' -> campo 'descripcion'
        3. Columna 'Qty' -> campo 'cantidad'
        4. Columna 'Unit Value' -> campo 'precio_unitario'
        5. Columna 'Total Value' -> campo 'total_linea'
        
        TRUCO DE SISTEMA:
        Para que el sistema acepte los datos, incluye SIEMPRE "tipo_documento": "Original" en tu JSON, aunque no lo diga el papel.

        ESTRUCTURA JSON REQUERIDA:
        {
            "tipo_documento": "Original",
            "numero_factura": "Extraer Invoice Number",
            "fecha": "Extraer Date",
            "proveedor": "Goodyear International Corporation",
            "cliente": "Extraer Sold To",
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
# 🧠 SELECTOR AUTOMÁTICO DE MODELO
# ==========================================
def conseguir_mejor_modelo():
    """Busca el modelo más capaz disponible en tu cuenta (Flash > Pro > Legacy)"""
    try:
        modelos_disponibles = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                modelos_disponibles.append(m.name)
        
        # 1. Prioridad: Gemini 1.5 Flash (Rápido y bueno con docs)
        for m in modelos_disponibles:
            if 'flash' in m.lower() and '1.5' in m: return genai.GenerativeModel(m)
        
        # 2. Prioridad: Gemini 1.5 Pro (Más potente, más lento)
        for m in modelos_disponibles:
            if 'pro' in m.lower() and '1.5' in m: return genai.GenerativeModel(m)
            
        # 3. Fallback
        return genai.GenerativeModel(modelos_disponibles[0]) if modelos_disponibles else None
    except Exception as e:
        st.error(f"Error conectando con Google AI: {e}")
        return None

# Inicializamos el modelo al cargar la app
model = conseguir_mejor_modelo()
if not model:
    st.error("❌ No se pudo inicializar ningún modelo de IA.")
    st.stop()

# ==========================================
# ⚙️ LÓGICA DE PROCESAMIENTO
# ==========================================
def limpiar_json(texto_respuesta):
    """Limpia los bloques de código markdown que Gemini suele poner"""
    texto = texto_respuesta.strip()
    if "```json" in texto:
        texto = texto.split("```json")[1]
    if "```" in texto:
        texto = texto.split("```")[0]
    return texto

def procesar_pdf(pdf_path, tipo_seleccionado):
    st.toast(f"Iniciando análisis con {model.model_name}...", icon="🚀")
    prompt_base = PROMPTS_POR_TIPO[tipo_seleccionado]
    
    try:
        # Convertir PDF a imágenes (200 DPI es buen balance calidad/velocidad)
        images = convert_from_path(pdf_path, dpi=200)
    except Exception as e:
        st.error(f"Error crítico leyendo el PDF (¿Poppler instalado?): {e}")
        return [], pd.DataFrame()

    items_totales = []
    resumen_documentos = []
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    total_paginas = len(images)

    for i, img in enumerate(images):
        status_text.caption(f"Analizando página {i+1} de {total_paginas}...")
        
        try:
            # LLAMADA A LA IA
            response = model.generate_content([prompt_base, img])
            json_str = limpiar_json(response.text)
            
            if not json_str:
                continue # Si devuelve vacío, saltamos

            data = json.loads(json_str)
            
            # === FILTRO INTELIGENTE ===
            # Si es Regal y dice Duplicado -> data['tipo_documento'] será 'Duplicado'
            # Si es Goodyear -> data['tipo_documento'] será 'Original' (forzado por prompt)
            
            es_valido = data.get("tipo_documento", "").lower() == "original"
            
            if es_valido:
                # Extraer datos de cabecera para resumen
                factura = data.get("numero_factura", "S/N")
                resumen_documentos.append({
                    "Página": i + 1,
                    "Factura": factura,
                    "Fecha": data.get("fecha", ""),
                    "Total": data.get("total_factura", 0),
                    "Items Detectados": len(data.get("items", []))
                })
                
                # Aplanar items para el CSV detallado
                lista_items = data.get("items", [])
                if isinstance(lista_items, list):
                    for item in lista_items:
                        # Añadimos contexto al item
                        item["Factura_Ref"] = factura
                        item["Pagina_Origen"] = i + 1
                        items_totales.append(item)
            else:
                st.warning(f"⚠️ Página {i+1} ignorada: Marcada como '{data.get('tipo_documento')}' por la IA.")

        except json.JSONDecodeError:
            print(f"Error de JSON en pág {i+1}")
        except Exception as e:
            print(f"Error general en pág {i+1}: {e}")
        
        # Actualizar barra y esperar un poco (Rate Limit safety)
        progress_bar.progress((i + 1) / total_paginas)
        time.sleep(1)

    status_text.empty()
    return resumen_documentos, pd.DataFrame(items_totales)

# ==========================================
# 🖥️ INTERFAZ DE USUARIO (UI)
# ==========================================
with st.sidebar:
    st.header("📋 Configuración de Lectura")
    
    tipo_pdf = st.selectbox(
        "¿Qué tipo de documento es?",
        list(PROMPTS_POR_TIPO.keys()),
        index=0,
        help="Selecciona 'Regal' para filtrar duplicados o 'Goodyear' para leer todo."
    )
    
    st.divider()
    st.info(f"🟢 Motor IA Activo:\n{model.model_name.split('/')[-1]}")

uploaded_file = st.file_uploader("Sube el PDF aquí", type=["pdf"])

if uploaded_file is not None:
    # Botón grande para iniciar
    if st.button("🚀 EXTRAER DATOS AHORA", type="primary"):
        
        # Gestión de archivo temporal
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp.write(uploaded_file.read())
            pdf_path = tmp.name
        
        # Procesar
        with st.spinner("🧠 La IA está leyendo y estructurando el documento..."):
            resumen, df_items = procesar_pdf(pdf_path, tipo_pdf)
        
        # Mostrar Resultados
        if not df_items.empty:
            st.success("✅ ¡Extracción Completada con Éxito!")
            
            # Pestañas para organizar la vista
            tab1, tab2 = st.tabs(["📦 Detalle de Items (CSV)", "📄 Resumen de Facturas"])
            
            with tab1:
                st.dataframe(df_items, use_container_width=True)
                # Botón de descarga CSV
                csv = df_items.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Descargar Tabla de Items (.csv)",
                    data=csv,
                    file_name="items_extraidos.csv",
                    mime="text/csv"
                )
                
            with tab2:
                st.dataframe(pd.DataFrame(resumen), use_container_width=True)
                
        else:
            st.error("No se encontraron datos válidos. Revisa si el documento es 'Duplicado' o si la imagen es legible.")
            
        # Limpieza
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
