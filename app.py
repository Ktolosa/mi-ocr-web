import streamlit as st
import pytesseract
from pdf2image import convert_from_bytes
import pandas as pd
import re
import io

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Extractor de Facturas OCR", layout="wide")

st.title("📄 Sistema OCR de Facturas Web")
st.markdown("Carga tu PDF, visualiza los datos y descarga el Excel.")

# --- LÓGICA DE OCR ---

# NOTA: En Streamlit Cloud (Linux), no necesitamos definir rutas .exe
# Tesseract y Poppler se instalan en el sistema vía packages.txt

def parse_invoice_data(text):
    """Analiza el texto de UNA página."""
    data = {}
    
    # 1. Fecha
    fecha_match = re.search(r'(\d{2}[-/]\d{2}[-/]\d{4})', text)
    data['Fecha'] = fecha_match.group(1) if fecha_match else "No detectada"

    # 2. Total (Mejorado para detectar comas y puntos)
    total_match = re.search(r'Total.*?\s*[\$]?\s*([\d,]+\.\d{2})', text, re.IGNORECASE)
    data['Total'] = total_match.group(1) if total_match else "0.00"

    # 3. Folio
    folio_match = re.search(r'(?:Factura|Folio)\s*[:.]?\s*([A-Za-z0-9-]+)', text, re.IGNORECASE)
    data['Folio'] = folio_match.group(1) if folio_match else "No detectado"
    
    # 4. Texto completo (Opcional, para debug)
    # data['Texto_Raw'] = text[:100] + "..." 
    
    return data

def process_pdf(uploaded_file):
    # Convertir PDF a imágenes (usando bytes directamente)
    try:
        images = convert_from_bytes(uploaded_file.read())
        all_pages_data = []
        
        # Barra de progreso
        progreso = st.progress(0)
        
        for i, image in enumerate(images):
            # Actualizar barra
            progreso.progress((i + 1) / len(images))
            
            # OCR
            text = pytesseract.image_to_string(image, lang='spa')
            
            # Analizar datos
            page_data = parse_invoice_data(text)
            page_data['Página_PDF'] = i + 1  # Agregar número de página
            
            all_pages_data.append(page_data)
            
        progreso.empty()
        return all_pages_data
        
    except Exception as e:
        st.error(f"Error al procesar: {e}")
        return []

# --- INTERFAZ DE USUARIO ---

uploaded_file = st.file_uploader("Sube tu factura (PDF)", type=["pdf"])

if uploaded_file is not None:
    with st.spinner('Procesando documento... esto puede tardar unos segundos...'):
        datos = process_pdf(uploaded_file)
    
    if datos:
        df = pd.DataFrame(datos)
        
        # Reordenar columnas
        cols = ['Página_PDF', 'Fecha', 'Folio', 'Total']
        df = df[cols]

        st.success("✅ Procesamiento completado")
        
        # Mostrar tabla interactiva
        st.subheader("Vista Previa de Resultados")
        st.dataframe(df, use_container_width=True)
        
        # Botón de descarga
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Facturas')
        
        st.download_button(
            label="📥 Descargar Excel",
            data=output.getvalue(),
            file_name="Reporte_Facturas.xlsx",
            mime="application/vnd.ms-excel"
        )