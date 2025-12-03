import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="OCR Inteligente", layout="wide")
st.title("🧠 OCR con Limpieza de Espacios")
st.markdown("""
Este sistema utiliza **Agrupación Inteligente**. 
Detecta si las palabras pertenecen a la misma frase o si son columnas distintas basándose en la distancia entre ellas.
**Resultado:** Un Excel limpio, sin celdas vacías infinitas.
""")

# --- VERIFICACIÓN ---
if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# --- LÓGICA DE AGRUPACIÓN INTELIGENTE ---

def smart_layout_analysis(image):
    """
    Convierte la imagen en una estructura de tabla lógica,
    eliminando espacios vacíos innecesarios.
    """
    # 1. Obtener datos crudos con coordenadas
    custom_config = r'--psm 6' # Asumir bloque de texto uniforme
    df = pytesseract.image_to_data(image, output_type=Output.DATAFRAME, lang='spa', config=custom_config)
    
    # Limpiar basura (confianza baja o espacios vacíos)
    df = df[df.conf != -1]
    df = df[df.text.str.strip() != ""]
    df = df.dropna()
    
    # Ordenar primero por posición vertical (top), luego horizontal (left)
    df = df.sort_values(by=['top', 'left'])
    
    lines = []
    current_line = []
    
    # 2. AGRUPACIÓN VERTICAL (Detectar Filas)
    # Si la diferencia de altura ('top') entre palabras es pequeña (<10px), es la misma línea.
    prev_top = -100
    
    for index, row in df.iterrows():
        # Si la palabra está mucho más abajo (más de 10px), es una nueva línea
        if row['top'] > prev_top + 15: # Umbral vertical de 15px
            if current_line:
                lines.append(current_line)
            current_line = []
            prev_top = row['top']
        
        # Añadir palabra a la línea actual
        current_line.append(row)
        
    # Añadir la última línea pendiente
    if current_line:
        lines.append(current_line)
    
    # 3. AGRUPACIÓN HORIZONTAL (Detectar Columnas dentro de cada fila)
    final_rows_for_excel = []
    
    for line in lines:
        # Ordenar palabras de izquierda a derecha dentro de la línea
        line.sort(key=lambda x: x['left'])
        
        excel_row = []
        current_cell_text = ""
        prev_right = -100
        
        for word in line:
            word_left = word['left']
            word_width = word['width']
            word_text = str(word['text'])
            word_right = word_left + word_width
            
            # Calcular la brecha (gap) con la palabra anterior
            gap = word_left - prev_right
            
            # --- EL CEREBRO DEL SISTEMA ---
            # Umbral Horizontal: 30 pixeles (ajustable)
            # Si el espacio es menor a 30px, es la misma frase (ej: "Calle" y "Principal").
            # Si es mayor, es una nueva columna (ej: "Principal" ..... "100.00").
            
            if gap > 35 and prev_right != -100:
                # ¡Brecha grande detectada! Guardamos lo anterior y cambiamos de celda
                excel_row.append(current_cell_text.strip())
                current_cell_text = word_text # Empezamos nueva celda
            else:
                # Brecha pequeña: concatenamos con espacio
                if current_cell_text:
                    current_cell_text += " " + word_text
                else:
                    current_cell_text = word_text
            
            prev_right = word_right
            
        # Guardar el último fragmento de la fila
        excel_row.append(current_cell_text.strip())
        final_rows_for_excel.append(excel_row)
            
    return final_rows_for_excel

def process_pdf(file_bytes):
    try:
        images = convert_from_bytes(file_bytes)
        workbook_data = [] # Lista de (NombreHoja, DataFrame)
        
        for i, image in enumerate(images):
            # Procesar página con el algoritmo inteligente
            rows = smart_layout_analysis(image)
            
            # Convertir a DataFrame
            # Normalizamos el ancho (rellenar con vacíos si una fila tiene menos columnas)
            if rows:
                max_cols = max(len(r) for r in rows)
                # Crear nombres de columna genéricos
                col_names = [f"Col {j+1}" for j in range(max_cols)]
                df = pd.DataFrame(rows)
                workbook_data.append((f"Página {i+1}", df))
                
        return workbook_data
        
    except Exception as e:
        return str(e)

# --- INTERFAZ ---
uploaded_file = st.file_uploader("Sube PDF (Factura o Tabla)", type=["pdf"])

if uploaded_file is not None:
    if st.button("✨ Procesar y Limpiar"):
        
        with st.status("Analizando geometría del documento...", expanded=True) as status:
            file_bytes = uploaded_file.read()
            resultado = process_pdf(file_bytes)
            
            if isinstance(resultado, str):
                status.update(label="Error", state="error")
                st.error(resultado)
            else:
                status.update(label="¡Listo!", state="complete")
                st.success("✅ Documento digitalizado y compactado.")
                
                # PREPARAR EXCEL
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    for sheet_name, df in resultado:
                        df.to_excel(writer, sheet_name=sheet_name, index=False, header=False)
                        
                        # Autoajustar columnas (Estética)
                        worksheet = writer.sheets[sheet_name]
                        for idx, col in enumerate(df.columns):
                            # Ajustar ancho basado en la longitud del texto
                            max_len = max(df[col].astype(str).map(len).max(), 10)
                            worksheet.set_column(idx, idx, max_len + 2)

                st.download_button(
                    label="📥 Descargar Excel Limpio",
                    data=buffer.getvalue(),
                    file_name="Reporte_Smart_OCR.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                
                # Mostrar vista previa de la primera página
                if resultado:
                    st.write("Vista previa (Página 1):")
                    st.dataframe(resultado[0][1], use_container_width=True)
