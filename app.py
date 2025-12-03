import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil
import re

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Sistema OCR Híbrido", layout="wide")

# --- MENÚ LATERAL ---
st.sidebar.title("🔧 Configuración")
modo_procesamiento = st.sidebar.selectbox(
    "Selecciona el tipo de documento:",
    ["Universal (Cualquier PDF)", "Específico: Factura Regal Trading"]
)

# --- VERIFICACIÓN DE SISTEMA ---
if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado en el servidor.")
    st.stop()

# ==========================================
# 🛠️ FUNCIONES AUXILIARES (ANTI-ERRORES)
# ==========================================

def safe_extract(pattern, text, group=1, default=""):
    """Busca un patrón y devuelve el resultado. Si falla, devuelve vacío."""
    try:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(group).strip()
    except:
        pass
    return default

# ==========================================
# 🧠 MÓDULO 1: LÓGICA UNIVERSAL
# ==========================================

def smart_layout_analysis(image):
    custom_config = r'--psm 6'
    df = pytesseract.image_to_data(image, output_type=Output.DATAFRAME, lang='spa', config=custom_config)
    
    # Limpieza
    df = df[df.conf != -1]
    df = df[df.text.str.strip() != ""]
    df = df.dropna()
    df = df.sort_values(by=['top', 'left'])
    
    lines = []
    current_line = []
    prev_top = -100
    
    # Agrupación Vertical
    for index, row in df.iterrows():
        if row['top'] > prev_top + 15:
            if current_line: lines.append(current_line)
            current_line = []
            prev_top = row['top']
        current_line.append(row)
    if current_line: lines.append(current_line)
    
    # Agrupación Horizontal
    final_rows = []
    for line in lines:
        # AQUÍ ESTABA TU ERROR ANTES. AHORA ESTÁ CORREGIDO:
        line.sort(key=lambda x: x['left'])
        
        excel_row = []
        current_cell = ""
        prev_right = -100
        for word in line:
            gap = word['left'] - prev_right
            if gap > 35 and prev_right != -100:
                excel_row.append(current_cell.strip())
                current_cell = str(word['text'])
            else:
                current_cell += " " + str(word['text']) if current_cell else str(word['text'])
            prev_right = word['left'] + word['width']
        excel_row.append(current_cell.strip())
        final_rows.append(excel_row)
    return final_rows

def process_universal(file_bytes):
    images = convert_from_bytes(file_bytes)
    workbook_data = []
    for i, image in enumerate(images):
        rows = smart_layout_analysis(image)
        if rows:
            df = pd.DataFrame(rows)
            workbook_data.append((f"Página {i+1}", df))
    return workbook_data

# ==========================================
# 🎯 MÓDULO 2: LÓGICA ESPECÍFICA (FACTURA REGAL)
# ==========================================

def extract_regal_data(text):
    """Extrae datos específicos y los prepara para Excel."""
    data = {}
    
    # 1. DATOS GENERALES
    data['Factura #'] = safe_extract(r'(?:#|No\.|297107)\s*(\d{6})', text)
    data['Fecha'] = safe_extract(r'DATE/FECHA\s*[:.,]?\s*([A-Za-z]{3}\s\d{2},\s\d{4})', text)
    data['Vencimiento'] = safe_extract(r'DUE DATE.*?\s*([A-Za-z]{3}\s\d{2},\s\d{4})', text)
    
    # Logística
    data['Orden #'] = safe_extract(r'ORDER/ORDEN\s*#\s*[:.,]?\s*(\d+)', text)
    data['File Ref'] = safe_extract(r'FILE/REF\s*[:.,]?\s*([A-Z0-9]+)', text)
    data['Contenedor'] = safe_extract(r'(?:CONTENEDOR|CONTAINER).*?([A-Z]{4}\d{7})', text)
    data['Términos'] = safe_extract(r'PAYMENT TERMS.*?:?\s*(.*?)(?:The|$)', text)
    
    # Cliente
    buyer_block = safe_extract(r'SOLD TO/VENDIDO A:(.*?)SHIP TO', text)
    data['Comprador'] = buyer_block.replace('\n', ' ').strip() if buyer_block else ""

    # Totales (Busca el último número grande con decimales)
    totals_found = re.findall(r'(\d{1,3}(?:,\d{3})*\.\d{2})', text)
    data['Total'] = totals_found[-1] if totals_found else "0.00"

    # 2. PRODUCTOS (Extracción línea por línea)
    items = []
    lines = text.split('\n')
    
    for line in lines:
        # Regex: Busca líneas que empiecen con numero (cantidad) y terminen con precio
        # Ejemplo: "234 TCL 65C6K... 90,890.28"
        if re.search(r'^\s*\d+\s+.*?\d+\.\d{2}', line):
            
            # Cantidad (Primer número)
            qty = safe_extract(r'^(\d+)', line)
            
            # Precios (Busca todos los montos monetarios en la línea)
            prices = re.findall(r'(\d{1,3}(?:,\d{3})*\.\d{2})', line)
            
            if qty and prices:
                total_line = prices[-1] # El último suele ser el total
                unit_price = prices[-2] if len(prices) >= 2 else ""
                
                # Descripción: Quitamos la cantidad y los precios para dejar el texto
                desc = line
                desc = re.sub(r'^\d+', '', desc) # Quitar cantidad inicio
                for p in prices:
                    desc = desc.replace(p, '') # Quitar precios
                
                # Limpieza final de descripción
                desc = re.sub(r'\s+', ' ', desc).replace('$', '').strip()

                items.append({
                    "Cantidad": qty,
                    "Descripción": desc,
                    "Precio Unit.": unit_price,
                    "Total": total_line
                })
    
    return data, items

def create_regal_excel(general_data, items_data):
    """Crea el Excel estructurado."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        worksheet = workbook.add_worksheet("Factura")
        
        # Formatos
        fmt_header = workbook.add_format({'bold': True, 'bg_color': '#D9E1F2', 'border': 1})
        fmt_bold = workbook.add_format({'bold': True})
        
        # 1. Escribir Cabecera
        worksheet.write(0, 0, "DATOS GENERALES", fmt_header)
        row = 1
        for k, v in general_data.items():
            worksheet.write(row, 0, k, fmt_bold)
            worksheet.write(row, 1, v)
            row += 1
            
        row += 2 # Espacio
        
        # 2. Escribir Productos
        worksheet.write(row, 0, "DETALLE DE PRODUCTOS", fmt_header)
        row += 1
        
        if items_data:
            df = pd.DataFrame(items_data)
            # Cabeceras de tabla
            for col_num, val in enumerate(df.columns):
                worksheet.write(row, col_num, val, fmt_header)
            
            # Filas de tabla
            row += 1
            for _, item in df.iterrows():
                for col_num, val in enumerate(item):
                    worksheet.write(row, col_num, val)
                row += 1
                
            # Ajustar anchos
            worksheet.set_column(0, 0, 15) # Cantidad
            worksheet.set_column(1, 1, 60) # Descripcion larga
            worksheet.set_column(2, 3, 15) # Precios
            
    return output

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

st.title(f"📄 Procesador: {modo_procesamiento}")

uploaded_file = st.file_uploader("Sube tu archivo PDF", type=["pdf"])

if uploaded_file is not None:
    
    # --- MODO 1: FACTURA REGAL (Visualización + Excel) ---
    if modo_procesamiento == "Específico: Factura Regal Trading":
        st.info("ℹ️ Extrayendo información clave para reporte Excel.")
        
        if st.button("🔍 Analizar Factura"):
            with st.status("Procesando...", expanded=True) as status:
                try:
                    # OCR
                    file_bytes = uploaded_file.read()
                    images = convert_from_bytes(file_bytes)
                    full_text = ""
                    for img in images:
                        full_text += pytesseract.image_to_string(img, lang='spa', config='--psm 6') + "\n"
                    
                    # Extracción
                    general, items = extract_regal_data(full_text)
                    
                    status.update(label="¡Completado!", state="complete")
                    
                    # Mostrar en Pantalla
                    c1, c2 = st.columns(2)
                    with c1:
                        st.subheader("📋 Generales")
                        st.dataframe(pd.DataFrame(list(general.items()), columns=["Dato", "Valor"]), hide_index=True)
                    
                    with c2:
                        st.subheader("📦 Items")
                        if items:
                            st.dataframe(pd.DataFrame(items), hide_index=True)
                        else:
                            st.warning("No se detectaron items con el formato estándar.")
                            
                    # Generar Excel
                    excel_data = create_regal_excel(general, items)
                    
                    st.download_button(
                        "📥 Descargar Reporte Excel",
                        data=excel_data.getvalue(),
                        file_name=f"Factura_{general.get('Factura #', 'Regal')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    
                except Exception as e:
                    status.update(label="Error", state="error")
                    st.error(f"Error técnico: {e}")

    # --- MODO 2: UNIVERSAL (Réplica Visual) ---
    else:
        st.info("ℹ️ Digitaliza el PDF manteniendo la estructura visual (filas y columnas).")
        
        if st.button("✨ Convertir a Excel"):
            with st.status("Digitalizando...", expanded=True) as status:
                file_bytes = uploaded_file.read()
                resultado = process_universal(file_bytes)
                
                if not resultado:
                    status.update(label="Error", state="error")
                    st.error("No se pudo leer el documento.")
                else:
                    status.update(label="¡Listo!", state="complete")
                    
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        for sheet_name, df in resultado:
                            df.to_excel(writer, sheet_name=sheet_name, index=False, header=False)
                            worksheet = writer.sheets[sheet_name]
                            for idx, col in enumerate(df.columns):
                                max_len = max(df[col].astype(str).map(len).max(), 10)
                                worksheet.set_column(idx, idx, max_len + 2)
                    
                    st.download_button(
                        "📥 Descargar Excel Universal",
                        data=buffer.getvalue(),
                        file_name="Universal_OCR.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
