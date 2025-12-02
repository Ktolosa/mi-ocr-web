import streamlit as st
import pytesseract
from pdf2image import convert_from_bytes
import pandas as pd
import re
import io
import shutil

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="OCR Facturas", layout="wide")
st.title("📄 Extractor de Facturas")

# --- VERIFICACIÓN DE SISTEMA (Debugging) ---
# Esto nos dirá si el servidor instaló bien las cosas
if shutil.which("tesseract") is None:
    st.error("❌ ERROR CRÍTICO: Tesseract no está instalado en el servidor. Revisa tu archivo packages.txt")
if shutil.which("pdftoppm") is None: # pdftoppm es parte de poppler
    st.error("❌ ERROR CRÍTICO: Poppler no está instalado. Revisa tu archivo packages.txt")

# --- LÓGICA ---
def parse_invoice_data(text):
    data = {}
    # Fecha
    fecha_match = re.search(r'(\d{2}[-/]\d{2}[-/]\d{4})', text)
    data['Fecha'] = fecha_match.group(1) if fecha_match else "No detectada"
    # Total
    total_match = re.search(r'Total.*?\s*[\$]?\s*([\d,]+\.\d{2})', text, re.IGNORECASE)
    data['Total'] = total_match.group(1) if total_match else "0.00"
    # Folio
    folio_match = re.search(r'(?:Factura|Folio)\s*[:.]?\s*([A-Za-z0-9-]+)', text, re.IGNORECASE)
    data['Folio'] = folio_match.group(1) if folio_match else "No detectado"
    return data

def process_pdf(file_bytes):
    try:
        # Convertir PDF a imágenes
        images = convert_from_bytes(file_bytes)
        all_pages_data = []
        
        for i, image in enumerate(images):
            # OCR
            text = pytesseract.image_to_string(image, lang='spa')
            # Extraer
            page_data = parse_invoice_data(text)
            page_data['Página'] = i + 1
            all_pages_data.append(page_data)
            
        return all_pages_data
    except Exception as e:
        return f"Error técnico: {e}"

# --- INTERFAZ ---
uploaded_file = st.file_uploader("Sube tu PDF", type=["pdf"])

if uploaded_file is not None:
    if st.button("PROCESAR DOCUMENTO"):
        with st.status("Procesando...", expanded=True) as status:
            st.write("Leeyendo PDF...")
            resultado = process_pdf(uploaded_file.read())
            
            if isinstance(resultado, str):
                status.update(label="Error", state="error")
                st.error(resultado)
            else:
                status.update(label="¡Completado!", state="complete")
                
                df = pd.DataFrame(resultado)
                st.dataframe(df)
                
                # Excel
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df.to_excel(writer, index=False)
                    
                st.download_button("Descargar Excel", buffer.getvalue(), "facturas.xlsx")        )
