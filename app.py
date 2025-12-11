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
# 🧠 DEFINICIÓN DE PROMPTS POR TIPO (ACTUALIZADO)
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
        
        INSTRUCCIONES ESPECÍFICAS:
        1. El número de factura suele estar bajo el texto "COMMERCIAL INVOICE" (ej: 7791).
        2. La tabla de items tiene columnas: HTSU, SKU, Descripción, Marca, Origen, Cant., Precio Unitario, Valor Total.
        3. Usa 'SKU' como 'modelo'.
        4. Extrae también datos logísticos (Peso, Volumen, Contenedor) y añádelos a la descripción del primer item o concaténalos si es posible, o simplemente asegúrate de extraer bien los montos.
        
        OUTPUT JSON:
        {
            "tipo_documento": "Original",
            "numero_factura": "Extraer número grande (ej: 7791)",
            "fecha": "Extraer FECHA FACTURA (ej: 30-SEP-25)",
            "orden_compra": "Extraer P.O.#",
            "proveedor": "RadioShack Worldwide Corp",
            "cliente": "Extraer de VENDIDO A",
            "items": [
                {
                    "modelo": "Columna SKU",
                    "descripcion": "Columna DESCRIPCION",
                    "cantidad": 0,
                    "precio_unitario": 0.00,
                    "total_linea": 0.00
                }
            ],
            "total_factura": 0.00
        }
    """,

    "Factura Mabe": """
        Analiza esta factura de exportación de Mabe (Controladora Mabe).
        
        INSTRUCCIONES ESPECÍFICAS:
        1. El número de factura está bajo 'Factura Exportacion / Commercial Invoice' (ej: 0901248186).
        2. La tabla es compleja. Busca las columnas: 'CODIGO MABE', 'DESCRIPCIÓN', 'CANT/QTY', 'PRECIO UNIT/UNIT PRICE', 'IMPORTE NETO/AMOUNT'.
        3. Ignora las líneas que sean solo texto legal o impuestos (IVA 0.00).
        4. Extrae el Folio Fiscal (UUID) si aparece y ponlo junto al número de factura (ej: 'Factura # - UUID...').
        
        OUTPUT JSON:
        {
            "tipo_documento": "Original",
            "numero_factura": "Extraer #FACTURA CLIENTE o Commercial Invoice",
            "fecha": "Extraer FECHA/DATE",
            "orden_compra": "Extraer ORDEN DE COMPRA/PURCHASE ORDER",
            "proveedor": "Controladora Mabe S.A. de C.V.",
            "cliente": "Extraer VENDIDO A / SOLD TO",
            "items": [
                {
                    "modelo": "Columna CODIGO MABE o CODIGO CLIENTE",
                    "descripcion": "Columna DESCRIPCIÓN",
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
        # Analizar página con el prompt seleccionado
        data = analizar_pagina(img, prompt)
        
        # LÓGICA DE FILTRADO: Si la IA devuelve vacío (porque era Duplicado), ignoramos
        if not data or data.get("tipo_documento") != "Original":
            st.warning(f"Página {i+1} ignorada (Detectada como Duplicado o sin datos).")
        else:
            st.success(f"✅ Página {i+1} procesada como ORIGINAL.")
            
            # Aplanamos los datos para la tabla de items
            factura_id = data.get("numero_factura", "S/N")
            cliente = data.get("cliente", "")
            
            # Guardamos resumen cabecera
            resumen_facturas.append({
                "Factura": factura_id,
                "Fecha": data.get("fecha"),
                "Total": data.get("total_factura"),
                "Cliente": cliente
            })
            
            # Guardamos items detalle
            if "items" in data and isinstance(data["items"], list):
                for item in data["items"]:
                    item["Factura_Origen"] = factura_id # Vinculamos item a su factura
                    items_totales.append(item)
        
        bar.progress((i + 1) / len(images))
        time.sleep(1)

    return resumen_facturas, pd.DataFrame(items_totales)

# ==========================================
# 🖥️ INTERFAZ
# ==========================================
with st.sidebar:
    st.header("Configuración")
    # AQUI ESTÁ TU VARIABLE PARA EL TIPO DE PDF
    tipo_pdf = st.selectbox(
        "Selecciona el Tipo de PDF:",
        list(PROMPTS_POR_TIPO.keys())
    )
    st.info("El sistema buscará automáticamente solo las páginas marcadas como ORIGINAL.")

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
        
        # Descarga
        csv = df_items.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Descargar Detalle Items (CSV)", csv, "items_originales.csv", "text/csv")
    else:
        st.warning("No se encontraron páginas marcadas como 'Original' o hubo un error.")
    
    os.remove(path)

