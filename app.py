import streamlit as st
import pytesseract
from pdf2image import convert_from_bytes
import pandas as pd
import re
import io
import shutil

# --- 1. CONFIGURACIÓN DE LA PÁGINA ---
st.set_page_config(page_title="OCR Facturas", layout="centered")

st.title("📄 Extractor de Facturas (Versión Nube)")
st.info("Sube tu PDF y el sistema extraerá Fecha, Folio y Total.")

# --- 2. AUTODIAGNÓSTICO (Para evitar errores silenciosos) ---
# Esto verifica si packages.txt se instaló bien
tesseract_check = shutil.which("tesseract")
poppler_check = shutil.which("pdftoppm")

if not tesseract_check:
    st.error("❌ ERROR CRÍTICO: Tesseract no está instalado. Revisa el archivo 'packages.txt' en GitHub.")
    st.stop()
    
if not poppler_check:
    st.error("❌ ERROR CRÍTICO: Poppler no está instalado. Revisa el archivo 'packages.txt' en GitHub.")
    st.stop()

# --- 3. FUNCIONES DE EXTRACCIÓN ---
def parse_invoice_data(text):
    """Busca los datos clave en el texto extraído."""
    data = {}
    
    # Fecha (dd/mm/yyyy o dd-mm-yyyy)
    fecha_match = re.search(r'(\d{2}[-/]\d{2}[-/]\d{4})', text)
    data['Fecha'] = fecha_match.group(1) if fecha_match else "No detectada"

    # Total (Busca 'Total' seguido de precio)
    total_match = re.search(r'Total.*?\s*[\$]?\s*([\d,]+\.\d{2})', text, re.IGNORECASE)
    data['Total'] = total_match.group(1) if total_match else "0.00"

    # Folio (Busca Factura o Folio)
    folio_match = re.search(r'(?:Factura|Folio)\s*[:.]?\s*([A-Za-z0-9-]+)', text, re.IGNORECASE)
    data['Folio'] = folio_match.group(1) if folio_match else "No detectado"
    
    return data

def process_pdf(file_bytes):
    try:
        # Convertir PDF a imágenes
        images = convert_from_bytes(file_bytes)
        all_pages_data = []
        
        for i, image in enumerate(images):
            # Extraer texto de la imagen
            text = pytesseract.image_to_string(image, lang='spa')
            
            # Analizar texto
            page_data = parse_invoice_data(text)
            page_data['Página'] = i + 1
            all_pages_data.append(page_data)
            
        return all_pages_data
    except Exception as e:
        return f"Error: {str(e)}"

# --- 4. INTERFAZ DE USUARIO ---
uploaded_file = st.file_uploader("Arrastra tu PDF aquí", type=["pdf"])

if uploaded_file is not None:
    # Botón de acción manual para estabilidad
    if st.button("🔍 Extraer Datos"):
        
        with st.spinner('Procesando... esto puede tardar unos segundos...'):
            # Leemos el archivo
            file_bytes = uploaded_file.read()
            resultado = process_pdf(file_bytes)

        # Verificar resultados
        if isinstance(resultado, str):
            st.error(resultado) # Mostrar error si falló la función
        elif resultado:
            st.success("✅ ¡Procesamiento Exitoso!")
            
            # Crear tabla
            df = pd.DataFrame(resultado)
            cols = ['Página', 'Fecha', 'Folio', 'Total']
            # Filtrar columnas existentes
            df = df[[c for c in cols if c in df.columns]]
            
            # Mostrar datos
            st.dataframe(df, use_container_width=True)
            
            # Botón descargar Excel
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df.to_excel(writer, index=False)
                
            st.download_button(
                label="📥 Descargar Excel",
                data=buffer.getvalue(),
                file_name="reporte_facturas.xlsx",
                mime="application/vnd.ms-excel"
            )
