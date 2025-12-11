import streamlit as st
import pandas as pd
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, TooManyRequests, InternalServerError, ServiceUnavailable, NotFound, InvalidArgument
from pdf2image import convert_from_path
import tempfile
import os
import json
import time

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor IA Auto-Detect", layout="wide")
st.title("🤖 Nexus Extractor: Auto-Detección Inteligente")

# 1. Cargar lista de API Keys
if "mis_llaves" in st.secrets:
    API_KEYS = st.secrets["mis_llaves"]
elif "GOOGLE_API_KEY" in st.secrets:
    API_KEYS = [st.secrets["GOOGLE_API_KEY"]]
else:
    st.error("❌ Falta configuración. Añade 'mis_llaves' en secrets.toml")
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
# 🧠 FUNCIÓN DE AUTO-DETECCIÓN DE MODELO
# ==========================================
def obtener_mejor_modelo_disponible():
    """
    Pregunta a la API qué modelos tiene activos esta API Key y elige el mejor.
    Prioridad: Flash 1.5 -> Pro 1.5 -> Flash 2.0 (si existe) -> Cualquiera disponible.
    """
    try:
        # Listar todos los modelos que soportan generar contenido
        todos_modelos = list(genai.list_models())
        modelos_generativos = [m for m in todos_modelos if 'generateContent' in m.supported_generation_methods]
        nombres = [m.name for m in modelos_generativos]
        
        # Estrategia de búsqueda
        # 1. Buscar Flash 1.5 (Balance perfecto velocidad/costo)
        for nombre in nombres:
            if 'flash' in nombre.lower() and '1.5' in nombre:
                return nombre
        
        # 2. Buscar Pro 1.5 (Más potente, menos cuota)
        for nombre in nombres:
            if 'pro' in nombre.lower() and '1.5' in nombre:
                return nombre
                
        # 3. Buscar cualquier "Gemini" si los anteriores fallan
        for nombre in nombres:
            if 'gemini' in nombre.lower():
                return nombre

        # Si no encuentra nada conocido, devuelve el primero de la lista
        if nombres:
            return nombres[0]
            
        return None
    except Exception as e:
        print(f"Error listando modelos: {e}")
        return None

# ==========================================
# 🧠 LOGICA DE ROTACIÓN BLINDADA
# ==========================================
def intentar_generar_con_rotacion(image, prompt):
    generation_config = {"temperature": 0.1, "response_mime_type": "application/json"}
    # Filtros de seguridad en NULO para evitar bloqueos falsos
    safety = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    errores_log = []

    for index, key in enumerate(API_KEYS):
        try:
            # 1. Configurar la llave
            genai.configure(api_key=key)
            
            # 2. AUTO-DETECTAR MODELO (Aquí está la magia)
            nombre_modelo = obtener_mejor_modelo_disponible()
            
            if not nombre_modelo:
                errores_log.append(f"Key {index+1}: No se encontraron modelos disponibles.")
                continue

            # 3. Instanciar con el nombre REAL encontrado
            model = genai.GenerativeModel(nombre_modelo, generation_config=generation_config, safety_settings=safety)
            
            # 4. Generar
            response = model.generate_content([prompt, image])
            
            # 5. Validaciones
            if response.prompt_feedback and response.prompt_feedback.block_reason:
                errores_log.append(f"Key {index+1}: Bloqueo Seguridad")
                continue 

            texto = response.text.strip()
            if "```json" in texto: texto = texto.replace("```json", "").replace("```", "")
            if "```" in texto: texto = texto.replace("```", "")
            
            # Si llegamos aquí, funcionó
            return json.loads(texto), None

        # Captura de errores de Cuota (429) y Servidor
        except (ResourceExhausted, TooManyRequests) as e:
            st.toast(f"⚠️ Llave {index+1} agotada. Cambiando...")
            errores_log.append(f"Key {index+1}: Cuota Agotada")
            continue
        
        # Captura de error "No encontrado" o "Invalido"
        except (NotFound, InvalidArgument) as e:
            errores_log.append(f"Key {index+1}: Error modelo ({str(e)})")
            continue

        except Exception as e:
            # Captura genérica (incluye 404 si viene como texto)
            err_str = str(e).lower()
            if "429" in err_str or "exhausted" in err_str:
                st.toast(f"⚠️ Llave {index+1} agotada. Cambiando...")
                continue
            
            errores_log.append(f"Key {index+1} Error: {err_str}")
            continue

    return {}, f"FALLO TOTAL. Detalles: {errores_log}"

# ==========================================
# 🧠 PROCESAMIENTO
# ==========================================
def process_single_pdf(pdf_path, filename, tipo_seleccionado):
    prompt = PROMPTS_POR_TIPO[tipo_seleccionado]
    try:
        images = convert_from_path(pdf_path, dpi=200)
    except Exception as e:
        return [], [], f"Error PDF dañado: {e}"

    items_locales = []
    resumen_local = []
    
    for i, img in enumerate(images):
        data, error = intentar_generar_con_rotacion(img, prompt)
        
        if error:
            st.error(f"Error {filename} Pág {i+1}: {error}")
            continue
            
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
        
        time.sleep(1)

    return resumen_local, items_locales, None

# ==========================================
# 🖥️ INTERFAZ
# ==========================================
with st.sidebar:
    st.header("Configuración")
    tipo_pdf = st.selectbox("Plantilla:", list(PROMPTS_POR_TIPO.keys()))
    st.info(f"🔑 Sistema activo: {len(API_KEYS)} credenciales.")

uploaded_files = st.file_uploader("Sube Facturas (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files and st.button("🚀 Procesar"):
    gran_acumulado = []
    st.divider()
    
    for idx, uploaded_file in enumerate(uploaded_files):
        with st.expander(f"📄 {uploaded_file.name}", expanded=True):
            with st.spinner(f"Analizando {uploaded_file.name}..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(uploaded_file.read())
                    path = tmp.name
                    fname = uploaded_file.name
                
                resumen, items, error = process_single_pdf(path, fname, tipo_pdf)
                os.remove(path)
                
                if items:
                    df = pd.DataFrame(items)
                    st.dataframe(df, use_container_width=True)
                    gran_acumulado.extend(items)
                elif error:
                    st.error(error)
                else:
                    st.warning("⚠️ Sin datos (Copia o vacío).")

    if gran_acumulado:
        st.divider()
        csv = pd.DataFrame(gran_acumulado).to_csv(index=False).encode('utf-8')
        st.download_button("📥 Descargar Todo (CSV)", csv, "extraccion_total.csv", "text/csv")
