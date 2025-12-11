import streamlit as st
import pandas as pd
import google.generativeai as genai
from pdf2image import convert_from_path
import tempfile
import os
import json
import time

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor IA Multi-Key", layout="wide")
st.title("🤖 Nexus Extractor: Sistema Multi-Llave")

# 1. Cargar lista de API Keys desde secrets
# Se espera que en secrets.toml tengas: mis_llaves = ["KEY1", "KEY2", "..."]
if "mis_llaves" in st.secrets:
    API_KEYS = st.secrets["mis_llaves"]
elif "GOOGLE_API_KEY" in st.secrets:
    # Soporte retroactivo por si solo tienes una
    API_KEYS = [st.secrets["GOOGLE_API_KEY"]]
else:
    st.error("❌ Falta configuración de llaves. Configura 'mis_llaves' (lista) en secrets.")
    st.stop()

# ==========================================
# 🧠 DEFINICIÓN DE PROMPTS (ACTUALIZADO)
# ==========================================
PROMPTS_POR_TIPO = {
    "Factura Internacional (Regal/General)": """
        Actúa como un experto en comercio exterior. Analiza la imagen de esta factura.
        
        REGLA CRÍTICA DE FILTRADO:
        1. Busca si dice "Original", "Duplicado" o "Copia".
        2. Si es Duplicado/Copia, devuelve JSON con "tipo_documento": "Copia" y lista de items vacía.
        3. Si es Original, extrae todo.

        ESTRUCTURA JSON ESPERADA:
        {
            "tipo_documento": "Original/Copia",
            "numero_factura": "Invoice #",
            "fecha": "Date",
            "orden_compra": "PO #",
            "proveedor": "Vendor Name",
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
        Analiza esta factura de RadioShack Worldwide Corp.
        INSTRUCCIONES:
        1. Factura # bajo 'COMMERCIAL INVOICE'.
        2. Items: HTSU, SKU (modelo), Descripción, Marca, Cant, Precio, Valor.
        3. Extrae todo.
        OUTPUT JSON: {
            "tipo_documento": "Original", "numero_factura": "...", "fecha": "...", "orden_compra": "...", "proveedor": "RadioShack", "cliente": "...", 
            "items": [{"modelo": "SKU", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.00, "total_linea": 0.00}], "total_factura": 0.00
        }
    """,
    "Factura Mabe": """
        Analiza esta factura de Mabe.
        INSTRUCCIONES:
        1. Factura # bajo 'Factura Exportacion'.
        2. Items: CODIGO MABE (modelo), Descripción, Cant, Precio Unit, Importe.
        3. Ignora filas de impuestos.
        OUTPUT JSON: {
            "tipo_documento": "Original", "numero_factura": "...", "fecha": "...", "orden_compra": "...", "proveedor": "Mabe", "cliente": "...", 
            "items": [{"modelo": "...", "descripcion": "...", "cantidad": 0, "precio_unitario": 0.00, "total_linea": 0.00}], "total_factura": 0.00
        }
    """
}

# ==========================================
# 🧠 FUNCIÓN ROBUSTA DE CONEXIÓN CON ROTACIÓN
# ==========================================
def intentar_generar_con_rotacion(image, prompt):
    """
    Intenta generar contenido rotando las API Keys si una falla por cuota.
    """
    # Configuración base del modelo
    generation_config = {
        "temperature": 0.1,
        "response_mime_type": "application/json"
    }
    safety = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    errores_log = []

    # Bucle para probar cada llave disponible
    for index, key in enumerate(API_KEYS):
        try:
            # 1. Configurar Gemini con la llave actual
            genai.configure(api_key=key)
            
            # 2. Instanciar modelo (Prioridad Flash -> Pro)
            model_name = "gemini-1.5-flash" # Default rápido
            # Intentar buscar nombre exacto (opcional, o usar string directo)
            
            model = genai.GenerativeModel(
                model_name=model_name,
                generation_config=generation_config,
                safety_settings=safety
            )

            # 3. Intentar generar
            # st.toast(f"🔑 Probando llave {index+1}...") # Descomentar para debug
            response = model.generate_content([prompt, image])
            
            if response.prompt_feedback and response.prompt_feedback.block_reason:
                return {}, f"Bloqueo Seguridad Llave {index+1}"

            texto = response.text.strip()
            if "```json" in texto: texto = texto.replace("```json", "").replace("```", "")
            if "```" in texto: texto = texto.replace("```", "")
            
            # Si llegamos aquí, funcionó. Retornamos datos y None error
            return json.loads(texto), None

        except Exception as e:
            err_msg = str(e)
            errores_log.append(f"Llave {index+1}: {err_msg}")
            
            # Si el error es 429 (Cuota) o 500/503 (Server), probamos la siguiente llave
            if "429" in err_msg or "Resource has been exhausted" in err_msg:
                print(f"⚠️ Llave {index+1} agotada. Cambiando a la siguiente...")
                continue # Salta a la siguiente iteración del bucle (siguiente key)
            else:
                # Si es otro error (ej: imagen corrupta), quizás no sirva cambiar de llave, 
                # pero por seguridad seguimos intentando.
                continue

    # Si sale del bucle, fallaron todas las llaves
    return {}, f"TODAS LAS LLAVES FALLARON. Log: {errores_log}"

# ==========================================
# 🧠 LÓGICA DE PROCESAMIENTO
# ==========================================
def process_pdf(pdf_path, tipo_seleccionado):
    st.info(f"Modo: {tipo_seleccionado} | Claves disponibles: {len(API_KEYS)}")
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
        # Usamos la nueva función con rotación
        data, error = intentar_generar_con_rotacion(img, prompt)
        
        if error:
             st.warning(f"⚠️ Página {i+1} falló: {error}")
        
        # LOGICA DE FILTRADO (Manteniendo tu lógica original)
        elif not data or data.get("tipo_documento") == "Copia" or "opia" in str(data.get("tipo_documento")):
            st.warning(f"🚫 Página {i+1} ignorada (Detectada como Copia/Duplicado).")
        else:
            st.success(f"✅ Página {i+1} procesada (Original).")
            
            factura_id = data.get("numero_factura", "S/N")
            cliente = data.get("cliente", "")
            
            resumen_facturas.append({
                "Factura": factura_id,
                "Fecha": data.get("fecha"),
                "Total": data.get("total_factura"),
                "Cliente": cliente
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
    st.caption(f"🔑 Sistema Multi-Llave activo ({len(API_KEYS)} credenciales cargadas)")

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
        st.warning("No se encontraron páginas marcadas como 'Original' o todas las llaves fallaron.")
    
    os.remove(path)
