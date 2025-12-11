import streamlit as st
import pandas as pd
import google.generativeai as genai
from google.api_core.exceptions import ResourceExhausted, TooManyRequests, InternalServerError, ServiceUnavailable
from pdf2image import convert_from_path
import tempfile
import os
import json
import time

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor IA Multi-Key", layout="wide")
st.title("🤖 Nexus Extractor: Multi-Llave Robusto")

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
# 🧠 FUNCIÓN DE GENERACIÓN CON ROTACIÓN (CORREGIDA)
# ==========================================
def intentar_generar_con_rotacion(image, prompt):
    # Configuración de seguridad muy permisiva para evitar bloqueos falsos
    safety = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]
    generation_config = {"temperature": 0.1, "response_mime_type": "application/json"}

    errores_log = []

    # Bucle que recorre las llaves una por una
    for index, key in enumerate(API_KEYS):
        try:
            # 1. Configurar la llave actual
            genai.configure(api_key=key)
            
            # 2. Instanciar modelo (Flash es más rápido y tiene mejor cuota gratuita)
            model = genai.GenerativeModel("gemini-1.5-flash", generation_config=generation_config, safety_settings=safety)
            
            # 3. Llamada a la API
            response = model.generate_content([prompt, image])
            
            # 4. Validar bloqueo de seguridad
            if response.prompt_feedback and response.prompt_feedback.block_reason:
                # Si se bloquea por "seguridad", cambiar de llave a veces ayuda si es un falso positivo por cuenta
                print(f"Llave {index+1} bloqueada por seguridad. Saltando...")
                errores_log.append(f"Key {index+1}: Bloqueo Seguridad")
                continue 

            # 5. Procesar Texto
            texto = response.text.strip()
            # Limpieza extra de markdown
            if "```json" in texto: texto = texto.replace("```json", "").replace("```", "")
            if "```" in texto: texto = texto.replace("```", "")
            
            # 6. ÉXITO: Retornamos los datos y salimos del bucle
            return json.loads(texto), None

        # === AQUÍ ESTÁ LA CORRECCIÓN CLAVE ===
        # Atrapamos los errores técnicos específicos de Google
        except (ResourceExhausted, TooManyRequests) as e:
            msg = f"⚠️ Llave {index+1} AGOTADA (Cuota). Saltando a la siguiente..."
            print(msg)
            st.toast(msg) # Notificación visual para ti
            errores_log.append(f"Key {index+1}: Exhausted")
            continue # Forzamos el salto a la siguiente iteración (siguiente llave)

        except (InternalServerError, ServiceUnavailable) as e:
            msg = f"⚠️ Llave {index+1} error servidor Google. Saltando..."
            print(msg)
            errores_log.append(f"Key {index+1}: Server Error")
            continue

        except Exception as e:
            # Error genérico (ej: JSON mal formado, o error de red local)
            err_str = str(e).lower()
            # Doble verificación por si el error viene como texto plano
            if "429" in err_str or "exhausted" in err_str or "quota" in err_str:
                st.toast(f"⚠️ Llave {index+1} agotada (Detectado por texto). Saltando...")
                continue
            
            # Si es otro error, lo guardamos y probamos suerte con la siguiente llave 
            # (a veces cambiar de cuenta 'refresca' la conexión)
            errores_log.append(f"Key {index+1} Error genérico: {err_str}")
            continue

    # Si terminamos el bucle y nadie respondió:
    return {}, f"FALLO TOTAL. Se probaron {len(API_KEYS)} llaves. Detalles: {errores_log}"

# ==========================================
# 🧠 LÓGICA DE PROCESAMIENTO
# ==========================================
def process_single_pdf(pdf_path, filename, tipo_seleccionado):
    prompt = PROMPTS_POR_TIPO[tipo_seleccionado]
    try:
        images = convert_from_path(pdf_path, dpi=200)
    except Exception as e:
        return [], [], f"Error dañado/legibilidad PDF: {e}"

    items_locales = []
    resumen_local = []
    
    for i, img in enumerate(images):
        # Llamamos a la función blindada
        data, error = intentar_generar_con_rotacion(img, prompt)
        
        if error:
            # Si después de todas las llaves hay error, lo mostramos
            st.error(f"Error en {filename} Pág {i+1}: {error}")
            continue
            
        # Filtro "Copia"
        if not data or "copia" in str(data.get("tipo_documento", "")).lower():
            continue 
        
        # Procesar Original
        factura_id = data.get("numero_factura", "S/N")
        
        # Guardar Items
        if "items" in data and isinstance(data["items"], list):
            for item in data["items"]:
                item["Archivo_Origen"] = filename
                item["Factura_Origen"] = factura_id
                items_locales.append(item)
        
        # Guardar Resumen
        resumen_local.append({
            "Archivo": filename,
            "Factura": factura_id,
            "Total": data.get("total_factura"),
            "Cliente": data.get("cliente")
        })
        
        # Pequeña pausa para no saturar si usas la misma llave muy rápido
        time.sleep(1)

    return resumen_local, items_locales, None

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================
with st.sidebar:
    st.header("Configuración")
    tipo_pdf = st.selectbox("Plantilla:", list(PROMPTS_POR_TIPO.keys()))
    st.success(f"🔑 {len(API_KEYS)} Llaves cargadas y listas para rotar.")

uploaded_files = st.file_uploader("Sube Facturas (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files and st.button("🚀 Procesar"):
    
    gran_acumulado = []
    
    st.divider()
    st.subheader(f"Resultados ({len(uploaded_files)} archivos)")
    
    for idx, uploaded_file in enumerate(uploaded_files):
        with st.expander(f"📄 {uploaded_file.name}", expanded=True):
            with st.spinner(f"Procesando {uploaded_file.name}..."):
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    tmp.write(uploaded_file.read())
                    path = tmp.name
                    fname = uploaded_file.name
                
                resumen, items, error = process_single_pdf(path, fname, tipo_pdf)
                os.remove(path)
                
                if items:
                    df = pd.DataFrame(items)
                    st.success(f"✅ {len(items)} items extraídos.")
                    st.dataframe(df, use_container_width=True)
                    gran_acumulado.extend(items)
                elif error:
                    st.error(error)
                else:
                    st.warning("⚠️ Sin datos (Posible copia o PDF vacío).")

    if gran_acumulado:
        st.divider()
        csv = pd.DataFrame(gran_acumulado).to_csv(index=False).encode('utf-8')
        st.download_button("📥 Descargar Todo (CSV)", csv, "extraccion_total.csv", "text/csv")
