import streamlit as st
import pytesseract
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil
import re

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="OCR Universal", layout="wide")
st.title("📄 Digitalizador de Tablas (Formato Libre)")
st.markdown("""
Este sistema no busca palabras clave. **Intenta reconstruir la tabla visualmente**.
Funciona detectando los espacios en blanco entre columnas.
""")

# --- VERIFICACIÓN DE SISTEMA ---
if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# --- LÓGICA UNIVERSAL ---
def extract_general_data(image):
    """
    Extrae texto intentando conservar la estructura de columnas
    basada en espacios visuales.
    """
    # CONFIGURACIÓN CLAVE:
    # --psm 6: Asume un bloque de texto uniforme (bueno para tablas)
    # preserve_interword_spaces=1: NO borres los espacios grandes, los necesitamos
    custom_config = r'--oem 3 --psm 6 -c preserve_interword_spaces=1'
    
    raw_text = pytesseract.image_to_string(image, lang='spa', config=custom_config)
    
    rows = []
    
    # Procesar línea por línea
    for line in raw_text.split('\n'):
        line = line.strip()
        if not line:
            continue
            
        # EL TRUCO: Cortar cuando haya 2 o más espacios seguidos
        # Esto separa "Descripción      $10.00" en ["Descripción", "$10.00"]
        # pero mantiene "San Salvador" junto (porque solo tiene 1 espacio).
        cells = re.split(r'\s{2,}', line)
        rows.append(cells)
    
    return rows

def process_pdf(file_bytes):
    try:
        images = convert_from_bytes(file_bytes)
        all_data = []
        
        # Procesamos las páginas
        for i, image in enumerate(images):
            page_rows = extract_general_data(image)
            
            # Añadimos una marca de qué página es
            for row in page_rows:
                # Agregamos el número de página al principio de la fila
                row.insert(0, f"Pág {i+1}")
                all_data.append(row)
                
        return all_data
        
    except Exception as e:
        return f"Error: {str(e)}"

# --- INTERFAZ ---
uploaded_file = st.file_uploader("Sube cualquier PDF con tablas", type=["pdf"])

if uploaded_file is not None:
    if st.button("🚀 Digitalizar Documento"):
        
        with st.status("Analizando estructura visual...", expanded=True) as status:
            file_bytes = uploaded_file.read()
            raw_data = process_pdf(file_bytes)
            
            if isinstance(raw_data, str): # Si devolvió un mensaje de error
                status.update(label="Falló", state="error")
                st.error(raw_data)
            else:
                status.update(label="¡Completado!", state="complete")
                
                # --- NORMALIZAR DATAFRAME ---
                # Como cada fila puede tener diferente número de columnas, 
                # buscamos la fila más larga para crear las columnas del Excel.
                if raw_data:
                    max_cols = max(len(row) for row in raw_data)
                    column_names = [f"Columna {i}" for i in range(max_cols)]
                    
                    # Convertir a DataFrame rellenando huecos
                    df = pd.DataFrame(raw_data, columns=column_names) # Pandas rellena auto los None
                    
                    st.success("✅ Datos extraídos respetando el formato visual")
                    
                    # Mostrar tabla
                    st.dataframe(df, use_container_width=True)
                    
                    # Exportar a Excel
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        df.to_excel(writer, index=False, header=False) # Sin encabezados forzados
                        
                    st.download_button(
                        label="📥 Descargar Excel (Formato Original)",
                        data=buffer.getvalue(),
                        file_name="tabla_digitalizada.xlsx",
                        mime="application/vnd.ms-excel"
                    )
                else:
                    st.warning("No se pudo extraer texto legible del documento.")
