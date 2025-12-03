import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Replicador Visual PDF", layout="wide")
st.title("🎨 Replicador Visual de Documentos")
st.markdown("""
Este sistema utiliza **OCR Espacial**. 
Toma las coordenadas (X, Y) de cada palabra en el PDF y las "dibuja" en las celdas de Excel 
para mantener la posición visual original (Logos, tablas, direcciones, etc.).
""")

# --- VERIFICACIÓN ---
if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# --- LÓGICA DE REPLICACIÓN VISUAL ---
def create_spatial_excel(images):
    # Buffer para guardar el Excel en memoria
    output = io.BytesIO()
    
    # Creamos el Excel con el motor XlsxWriter (necesario para formato avanzado)
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        
        # Estilo para que el texto se ajuste y se vea limpio
        fmt_text = workbook.add_format({'text_wrap': False, 'valign': 'top', 'font_size': 10})
        
        for i, image in enumerate(images):
            sheet_name = f"Pagina_{i+1}"
            worksheet = workbook.add_worksheet(sheet_name)
            
            # 1. Obtener DATOS y COORDENADAS (No solo texto)
            # Esto devuelve: left, top, width, height, conf, text
            df = pytesseract.image_to_data(image, output_type=Output.DATAFRAME, lang='spa')
            
            # Limpiar datos vacíos o de baja confianza
            df = df[df.conf != -1]
            df = df[df.text.str.strip() != ""]
            
            # --- ALGORITMO DE MAPEO ESPACIAL ---
            
            # FACTORES DE ESCALA (La magia matemática)
            # Un PDF suele tener ~1600 pixeles de ancho. Excel tiene columnas.
            # Dividimos los pixeles para saber en qué fila/columna cae.
            SCALE_Y = 15  # Cada 15 pixeles de altura es 1 Fila de Excel
            SCALE_X = 8   # Cada 8 pixeles de ancho es 1 Columna de Excel
            
            # Diccionario para evitar sobreescribir celdas: {(fila, col): "texto"}
            grid_map = {}
            
            for index, row in df.iterrows():
                text = str(row['text']).strip()
                if not text: continue
                
                # Calcular coordenadas en Excel
                row_idx = int(row['top'] / SCALE_Y)
                col_idx = int(row['left'] / SCALE_X)
                
                # Ajuste fino: Si la celda ya está ocupada, mover a la derecha
                while (row_idx, col_idx) in grid_map:
                    col_idx += 1
                
                # Guardar en el mapa
                grid_map[(row_idx, col_idx)] = text
                
                # Escribir en Excel
                worksheet.write(row_idx, col_idx, text, fmt_text)
            
            # --- TRUCO VISUAL ---
            # Hacemos las columnas estrechas para simular una "grilla fina"
            # Así el texto puede caer en cualquier lugar con precisión.
            worksheet.set_column(0, 200, 1.2) # Ancho de columna muy pequeño
            
    return output

# --- INTERFAZ ---
uploaded_file = st.file_uploader("Sube PDF (Factura, Carta, Plano, etc.)", type=["pdf"])

if uploaded_file is not None:
    if st.button("🎨 Generar Réplica en Excel"):
        
        with st.status("Reconstruyendo diseño visual...", expanded=True) as status:
            try:
                # 1. Convertir PDF a imágenes
                st.write("📸 Escaneando documento...")
                images = convert_from_bytes(uploaded_file.read())
                
                # 2. Procesar algoritmo espacial
                st.write("📐 Calculando coordenadas y geometría...")
                excel_data = create_spatial_excel(images)
                
                status.update(label="¡Diseño reconstruido!", state="complete")
                st.success("✅ El Excel generado imita la posición visual de los textos.")
                
                # 3. Descargar
                st.download_button(
                    label="📥 Descargar Excel Visual",
                    data=excel_data.getvalue(),
                    file_name="Documento_Replicado.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                
            except Exception as e:
                status.update(label="Error", state="error")
                st.error(f"Error técnico: {e}")
