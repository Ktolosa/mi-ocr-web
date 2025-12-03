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

# --- BARRA LATERAL PARA API KEY ---
with st.sidebar:
    st.header("🔑 Configuración")
    api_key = st.text_input("Ingresa tu Google API Key", type="password")
    st.markdown("[Obtener API Key Gratis](https://aistudio.google.com/app/apikey)")
    
    st.info("Nota: Gemini 1.5 Flash es rápido y gratuito para este volumen de datos.")

# ==========================================
# 🧠 CEREBRO DE LA IA
# ==========================================

def analyze_image_with_gemini(image, api_key):
    """
    Envía la imagen a Google Gemini y pide un JSON estructurado.
    """
    genai.configure(api_key=api_key)
    
model = genai.GenerativeModel('gemini-pro')
    
    # El prompt maestro: Le damos instrucciones precisas de cómo queremos los datos
    prompt = """
    Actúa como un experto en extracción de datos de facturas (Data Entry).
    Analiza esta imagen de una factura y extrae la información en formato JSON estricto.
    
    Reglas de Extracción:
    1. CABECERA: Extrae Factura #, Fecha (Date), Orden (Order #), Referencia (File/Ref), B/L, Incoterm.
    2. DIRECCIONES: Extrae el bloque completo de "Sold To" y "Ship To" limpiando saltos de línea.
    3. DUPLICADOS: Revisa si en la parte superior derecha dice "Duplicado" o "Duplicate". Si dice, marca "is_duplicate": true.
    4. ITEMS (La parte más importante):
       - Extrae la tabla de productos fila por fila.
       - Campos: Cantidad, Descripción (Une modelo y descripción), UPC, Precio Unitario, Total.
       - CUIDADO: A veces la descripción es muy larga e invade la columna del UPC. Si el texto en UPC no parece un código (son letras o palabras), es parte de la descripción.
       - Ignora números sueltos que no tengan precio asociado.
    
    Retorna SOLAMENTE este JSON (sin markdown ```json):
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
    
    try:
        # Enviamos la imagen y el prompt
        response = model.generate_content([prompt, image])
        
        # Limpiamos la respuesta por si la IA pone ```json al principio
        text_response = response.text.replace("```json", "").replace("```", "").strip()
        
        return json.loads(text_response)
        
    except Exception as e:
        return {"error": str(e)}

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_files = st.file_uploader("Sube tus Facturas (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    
    if not api_key:
        st.warning("⚠️ Por favor ingresa tu API Key en la barra lateral para activar la IA.")
        st.stop()
        
    if st.button("🚀 Procesar con Inteligencia Artificial"):
        
        all_data_export = []
        progress_bar = st.progress(0)
        total_files = len(uploaded_files)
        
        for idx, uploaded_file in enumerate(uploaded_files):
            with st.expander(f"📄 Procesando: {uploaded_file.name}", expanded=True):
                try:
                    # 1. Convertir PDF a imágenes
                    # Bajamos un poco el DPI porque a la IA no le importa tanto la ultra resolución como a Tesseract
                    images = convert_from_bytes(uploaded_file.read(), dpi=200)
                    
                    file_items = []
                    header_info = {}
                    
                    # 2. Analizar página por página
                    for i, img in enumerate(images):
                        st.write(f"Analizando página {i+1} con Gemini...")
                        
                        # LLAMADA A LA IA
                        data = analyze_image_with_gemini(img, api_key)
                        
                        # Verificar errores
                        if "error" in data:
                            st.error(f"Error en pág {i+1}: {data['error']}")
                            continue
                            
                        # Lógica de Duplicados
                        if data.get("is_duplicate"):
                            st.warning(f"⚠️ Página {i+1} marcada como DUPLICADO por la IA. Omitiendo.")
                            continue
                        
                        st.success(f"✅ Página {i+1} procesada correctamente.")
                        
                        # Guardar cabecera (de la primera página válida)
                        if not header_info:
                            header_info = {
                                "Factura": data.get("invoice_number"),
                                "Fecha": data.get("date"),
                                "Orden": data.get("order"),
                                "Ref": data.get("ref"),
                                "BL": data.get("bl"),
                                "Incoterm": data.get("incoterm"),
                                "Vendido A": data.get("sold_to"),
                                "Embarcado A": data.get("ship_to")
                            }
                        
                        # Acumular items
                        for item in data.get("items", []):
                            # Limpieza extra por si acaso
                            flat_item = {
                                "Cantidad": item.get("qty"),
                                "Descripción": item.get("description"),
                                "UPC": item.get("upc"),
                                "Precio Unit.": item.get("unit_price"),
                                "Total": item.get("total")
                            }
                            file_items.append(flat_item)
                        
                        # Pausa pequeña para no saturar la API (Rate Limiting)
                        time.sleep(1) 

                    # --- MOSTRAR RESULTADOS ---
                    if header_info:
                        c1, c2, c3 = st.columns(3)
                        c1.info(f"Factura: {header_info.get('Factura')}")
                        c2.info(f"Orden: {header_info.get('Orden')}")
                        c3.metric("Total Items", len(file_items))
                    
                    if file_items:
                        df = pd.DataFrame(file_items)
                        st.dataframe(df, use_container_width=True)
                        
                        # Preparar para Excel Consolidado
                        for it in file_items:
                            row = header_info.copy()
                            row.update(it)
                            row['Archivo'] = uploaded_file.name
                            all_data_export.append(row)
                    else:
                        st.warning("La IA no encontró items o todas las páginas eran duplicadas.")

                except Exception as e:
                    st.error(f"Error técnico con el archivo: {e}")
            
            progress_bar.progress((idx + 1) / total_files)

        # --- EXCEL FINAL ---
        if all_data_export:
            df_final = pd.DataFrame(all_data_export)
            
            # Ordenar columnas
            cols_order = ['Archivo', 'Factura', 'Fecha', 'Orden', 'Ref', 'BL', 'Incoterm', 
                          'Vendido A', 'Embarcado A', 
                          'Cantidad', 'Descripción', 'UPC', 'Precio Unit.', 'Total']
            
            final_cols = [c for c in cols_order if c in df_final.columns]
            df_final = df_final[final_cols]
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_final.to_excel(writer, sheet_name="Consolidado IA", index=False)
                ws = writer.sheets['Consolidado IA']
                ws.set_column('J:J', 10)
                ws.set_column('K:K', 60)
                
            st.balloons()
            st.success("✅ ¡Extracción Inteligente Completada!")
            st.download_button("📥 Descargar Excel", buffer.getvalue(), "Reporte_Regal_AI.xlsx")

