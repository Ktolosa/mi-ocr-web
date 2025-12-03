import streamlit as st
import google.generativeai as genai
from pdf2image import convert_from_bytes
import pandas as pd
import io
import json
import time

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor IA (Gemini)", layout="wide")
st.title("🤖 Extractor de Facturas con IA (Gemini)")

# --- BARRA LATERAL ---
with st.sidebar:
    st.header("🔑 Configuración")
    api_key = st.text_input("Ingresa tu Google API Key", type="password")
    st.markdown("[Obtener API Key Gratis Aquí](https://aistudio.google.com/app/apikey)")
    st.info("Nota: Si te da error 404, actualiza tu requirements.txt")

# ==========================================
# 🧠 CEREBRO DE LA IA
# ==========================================

def analyze_image_with_gemini(image, api_key):
    """Envía la imagen a Google Gemini."""
    try:
        genai.configure(api_key=api_key)
        
        # Intentamos usar el modelo Flash (más rápido)
        # Si da error, asegúrate de tener google-generativeai>=0.7.0 en requirements.txt
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = """
        Eres un experto en extracción de datos contables (OCR Inteligente).
        Analiza esta imagen de factura y extrae los datos en un JSON estricto.

        REGLAS CLAVE PARA ESTE DOCUMENTO:
        1. **DUPLICADOS:** Mira la esquina superior derecha. Si dice "Duplicado" o "Duplicate", marca "is_duplicate": true.
        2. **CABECERA:** Extrae Factura (Invoice #), Fecha (Date), Orden (Order #), Referencia (Ref/File), B/L, Incoterm.
        3. **DIRECCIONES:** Extrae el bloque completo de "Sold To" y "Ship To" en una sola línea de texto.
        4. **TABLA DE PRODUCTOS (ITEMS):**
           - **CUIDADO CON LA CANTIDAD:** A veces, las descripciones largas tienen números. NO los confundas con cantidades.
           - **FILAS REALES:** Una fila debe tener Cantidad Y Precio.
           - **UPC:** Si el código UPC empieza con 'A', cámbialo a '4'.
           - **DESCRIPCIÓN:** Une el modelo y la descripción completa.

        Retorna SOLO este JSON:
        {
            "is_duplicate": boolean,
            "invoice_number": "string",
            "date": "string",
            "order": "string",
            "ref": "string",
            "bl": "string",
            "incoterm": "string",
            "sold_to": "string",
            "ship_to": "string",
            "items": [
                {
                    "qty": "number",
                    "description": "string",
                    "upc": "string",
                    "unit_price": "number",
                    "total": "number"
                }
            ]
        }
        """
        
        response = model.generate_content([prompt, image])
        # Limpieza de respuesta
        text = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(text)
        
    except Exception as e:
        return {"error": str(e)}

# ==========================================
# 🖥️ INTERFAZ
# ==========================================

uploaded_files = st.file_uploader("Sube Facturas (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    if not api_key:
        st.warning("⚠️ Pega tu API Key en la izquierda para continuar.")
        st.stop()
        
    if st.button("🚀 Procesar con IA"):
        
        all_data = []
        bar = st.progress(0)
        
        for idx, file in enumerate(uploaded_files):
            with st.expander(f"Procesando: {file.name}", expanded=True):
                try:
                    # Convertir PDF (DPI 200 es suficiente para IA)
                    images = convert_from_bytes(file.read(), dpi=200)
                    
                    header_saved = {}
                    file_items = []
                    
                    for i, img in enumerate(images):
                        st.write(f"Analizando pág {i+1}...")
                        result = analyze_image_with_gemini(img, api_key)
                        
                        if "error" in result:
                            st.error(f"Error: {result['error']}")
                            continue
                            
                        # Filtro de Duplicados
                        if result.get("is_duplicate"):
                            st.warning(f"⚠️ Pág {i+1} es DUPLICADO. Ignorada.")
                            continue
                        
                        st.success(f"✅ Pág {i+1} OK.")
                        
                        # Guardar cabecera de la primera página válida
                        if not header_saved:
                            header_saved = {
                                "Factura": result.get("invoice_number"),
                                "Fecha": result.get("date"),
                                "Orden": result.get("order"),
                                "Ref": result.get("ref"),
                                "BL": result.get("bl"),
                                "Incoterm": result.get("incoterm"),
                                "Cliente": result.get("sold_to"),
                                "Envio": result.get("ship_to")
                            }
                        
                        # Guardar items
                        for item in result.get("items", []):
                            flat = item.copy()
                            file_items.append(flat)
                            
                        time.sleep(1) # Pausa de cortesía

                    # Mostrar Tabla
                    if file_items:
                        df = pd.DataFrame(file_items)
                        st.dataframe(df, use_container_width=True)
                        
                        # Agregar al consolidado
                        for it in file_items:
                            row = header_saved.copy()
                            row.update(it)
                            row["Archivo"] = file.name
                            all_data.append(row)
                    else:
                        st.warning("No se encontraron datos.")

                except Exception as e:
                    st.error(f"Error crítico: {e}")
            
            bar.progress((idx + 1) / len(uploaded_files))

        if all_data:
            df_final = pd.DataFrame(all_data)
            # Ordenar columnas si existen
            cols = ["Archivo", "Factura", "Fecha", "Orden", "Ref", "BL", "Incoterm", "Cliente", "Envio", 
                    "qty", "description", "upc", "unit_price", "total"]
            final_cols = [c for c in cols if c in df_final.columns]
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                if not df_final.empty:
                    df_final[final_cols].to_excel(writer, index=False)
            
            st.download_button("📥 Descargar Excel Final", buffer.getvalue(), "Reporte_IA.xlsx")
