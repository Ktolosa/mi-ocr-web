import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil
import re
from datetime import datetime

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
    """Busca un patrón y devuelve el resultado. Si falla, no rompe el programa."""
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
    df = df[df.conf != -1]
    df = df[df.text.str.strip() != ""]
    df = df.dropna()
    df = df.sort_values(by=['top', 'left'])
    
    lines = []
    current_line = []
    prev_top = -100
    
    for index, row in df.iterrows():
        if row['top'] > prev_top + 15:
            if current_line: lines.append(current_line)
            current_line = []
            prev_top = row['top']
        current_line.append(row)
    if current_line: lines.append(current_line)
    
    final_rows = []
    for line in lines:
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
    """Extrae datos específicos de manera segura para Excel."""
    data = {}
    
    # 1. CABECERA Y DATOS GENERALES
    # Usamos safe_extract para evitar el error 'NoneType'
    data['Factura #'] = safe_extract(r'(?:#|No\.|297107)\s*(\d{6})', text)
    data['Fecha'] = safe_extract(r'DATE/FECHA\s*[:.,]?\s*([A-Za-z]{3}\s\d{2},\s\d{4})', text)
    data['Vencimiento'] = safe_extract(r'DUE DATE.*?\s*([A-Za-z]{3}\s\d{2},\s\d{4})', text)
    
    # Datos de Logística
    data['Orden #'] = safe_extract(r'ORDER/ORDEN\s*#\s*[:.,]?\s*(\d+)', text)
    data['File Ref'] = safe_extract(r'FILE/REF\s*[:.,]?\s*([A-Z0-9]+)', text)
    data['BL #'] = safe_extract(r'B/L#\s*[:.,]?\s*([A-Z0-9]+)', text)
    data['Contenedor'] = safe_extract(r'(?:CONTENEDOR|CONTAINER).*?([A-Z]{4}\d{7})', text)
    
    # Cliente (Buyer) - Intenta capturar el bloque
    buyer_block = safe_extract(r'SOLD TO/VENDIDO A:(.*?)SHIP TO', text)
    data['Comprador'] = buyer_block.replace('\n', ' ').strip() if buyer_block else ""

    # Totales
    total_match = re.findall(r'(\d{1,3}(?:,\d{3})*\.\d{2})', text)
    data['Total'] = total_match[-1] if total_match else "0.00"

    # 2. PRODUCTOS (Items)
    items = []
    # Regex ajustada para encontrar líneas de productos
    # Busca: Cantidad ... Codigo ... Precio
    # Ejemplo: 234 TCL... 388.42
    lines = text.split('\n')
    for line in lines:
        # Buscamos líneas que empiecen con numero y tengan precio al final
        if re.search(r'^\s*\d+\s+.*?\d+\.\d{2}', line):
            # Intentar desglosar
            qty = safe_extract(r'^(\d+)', line)
            price_parts = re.findall(r'(\d+\.\d{2})', line)
            
            if qty and price_parts:
                unit_price = price_parts[-2] if len(price_parts) >= 2 else price_parts[0]
                total_price = price_parts[-1]
                
                # Descripción es todo lo que está en medio
                desc = line
                desc = re.sub(r'^\d+', '', desc) # Quitar cantidad inicial
                desc = re.sub(r'\d+\.\d{2}.*$', '', desc) # Quitar precios finales
                desc = desc.strip()

                items.append({
                    "Cantidad": qty,
                    "Descripción": desc,
                    "Precio Unitario": unit_price,
                    "Total Línea": total_price
                })
    
    return data, items

def create_regal_excel(general_data, items_data):
    """Genera un Excel bonito con los datos extraídos."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        worksheet = workbook.add_worksheet("Factura")
        
        # Estilos
        header_fmt = workbook.add_format({'bold': True, 'bg_color': '#DCE6F1', 'border': 1})
        bold_fmt = workbook.add_format({'bold': True})
        
        # 1. Escribir Datos Generales (Arriba)
        row = 0
        worksheet.write(row, 0, "DATOS GENERALES", bold_fmt)
        row += 1
        
        for key, value in general_data.items():
            worksheet.write(row, 0, key, bold_fmt)
            worksheet.write(row, 1, value)
            row += 1
            
        # Espacio
        row += 2
        
        # 2. Escribir Tabla de Productos
        if items_data:
            # Convertir lista de dicts a DataFrame
            df_items = pd.DataFrame(items_data)
            
            # Escribir cabeceras
            for col_num, value in enumerate(df_items.columns.values):
                worksheet.write(row, col_num, value, header_fmt)
            
            # Escribir datos
            row += 1
            for _, item in df_items.iterrows():
                for col_num, value in enumerate(item):
                    worksheet.write(row, col_num, value)
                row += 1
                
            # Ajustar columnas
            worksheet.set_column(0, 0, 15) # Cantidad
            worksheet.set_column(1, 1, 50) # Descripcion
            worksheet.set_column(2, 3, 15) # Precios
            
    return output

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

st.title(f"📄 Procesador: {modo_procesamiento}")

uploaded_file = st.file_uploader("Sube tu archivo PDF", type=["pdf"])

if uploaded_file is not None:
    
    # --- MODO ESPECÍFICO (REGAL) ---
    if modo_procesamiento == "Específico: Factura Regal Trading":
        st.info("ℹ️ Extrayendo datos clave y generando Excel estructurado.")
        
        if st.button("🔍 Procesar Factura"):
            with st.status("Analizando documento...", expanded=True) as status:
                try:
                    # 1. OCR
                    file_bytes = uploaded_file.read()
                    images = convert_from_bytes(file_bytes)
                    full_text = ""
                    for img in images:
                        full_text += pytesseract.image_to_string(img, lang='spa', config='--psm 6') + "\n"
                    
                    # 2. Extracción Segura
                    general_info, products_list = extract_regal_data(full_text)
                    
                    status.update(label="¡Extracción completada!", state="complete")
                    
                    # 3. MOSTRAR DATOS EN PANTALLA
                    st.subheader("📋 Datos Generales")
                    # Convertir diccionario a DataFrame para mostrarlo bonito
                    df_general = pd.DataFrame(list(general_info.items()), columns=["Campo", "Valor"])
                    st.dataframe(df_general, use_container_width=True, hide_index=True)
                    
                    st.subheader("📦 Productos Detectados")
                    if products_list:
                        df_products = pd.DataFrame(products_list)
                        st.dataframe(df_products, use_container_width=True, hide_index=True)
                    else:
                        st.warning("No se detectaron productos automáticamente o el formato varía.")

                    # 4. GENERAR EXCEL
                    excel_file = create_regal_excel(general_info, products_list)
                    
                    st.download_button(
                        label="📥 Descargar Reporte Excel",
                        data=excel_file.getvalue(),
                        file_name=f"Factura_{general_info.get('Factura #', 'Regal')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                    
                except Exception as e:
                    status.update(label="Error", state="error")
                    st.error(f"Ocurrió un error inesperado: {str(e)}")

    # --- MODO UNIVERSAL ---
    else:
        st.info("ℹ️ Digitaliza tablas manteniendo el diseño visual.")
        
        if st.button("✨ Procesar a Excel"):
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
                    )        line.sort(key=lambda x: x['left'])
        excel_row = []
        current_cell = ""
        prev_right = -100
        
        for word in line:
            # Calcular brecha
            gap = word['left'] - prev_right
            
            # Umbral de 35px para separar columnas
            if gap > 35 and prev_right != -100:
                excel_row.append(current_cell.strip())
                current_cell = str(word['text'])
            else:
                if current_cell:
                    current_cell += " " + str(word['text'])
                else:
                    current_cell = str(word['text'])
            
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
            # Normalizar columnas para DataFrame
            max_cols = max(len(r) for r in rows)
            df = pd.DataFrame(rows)
            workbook_data.append((f"Página {i+1}", df))
            
    return workbook_data

# ==========================================
# 🎯 MÓDULO 2: LÓGICA ESPECÍFICA (FACTURA REGAL)
# ==========================================

def parse_date_regal(date_str):
    """Normaliza fechas como AUG 07, 2025"""
    if not date_str: return None
    try:
        months = {
            'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05', 'JUN': '06',
            'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
        }
        # Limpiar caracteres raros
        clean = re.sub(r'[^a-zA-Z0-9\s,]', '', date_str).strip().upper()
        parts = re.split(r'\s+|,', clean)
        parts = [p for p in parts if p]
        
        if len(parts) >= 3:
            mm = months.get(parts[0][:3], '01')
            dd = parts[1].zfill(2)
            yyyy = parts[2]
            return f"{yyyy}-{mm}-{dd}"
        return date_str
    except:
        return date_str

def extract_regal_specific(text):
    """Extrae JSON estructurado específico para Regal Trading"""
    data = {}
    
    # Limpieza previa del texto para facilitar regex
    text_clean = text.replace('\n', ' ') 

    # 1. CABECERA
    inv_match = re.search(r'(?:#|No\.)\s*(\d{6})', text)
    data['invoice_number'] = inv_match.group(1) if inv_match else None
    data['invoice_type'] = "COMMERCIAL INVOICE"
    data['copy_type'] = "Duplicado" if "Duplicado" in text else "Original"

    # 2. INVOLUCRADOS
    data['seller'] = {
        "name": "REGAL WORLDWIDE TRADING LLC.",
        "address_lines": ["703 Waterford way, Suite 530", "Miami, FL 33126"],
        "email": "info@regalwt.com",
        "phone": "305 400-4978"
    }

    # Regex multilinea para Buyer
    buyer_block = re.search(r'SOLD TO/VENDIDO A:(.*?)SHIP TO', text, re.DOTALL)
    buyer_lines = [l.strip() for l in buyer_block.group(1).split('\n') if l.strip()] if buyer_block else []
    
    data['buyer'] = {
        "name": buyer_lines[0] if buyer_lines else None,
        "address_lines": buyer_lines[1:-1] if len(buyer_lines) > 2 else [],
        "reference": buyer_lines[-1] if buyer_lines else None
    }

    ship_block = re.search(r'SHIP TO/EMBARCADO A:(.*?)PAYMENT TERMS', text, re.DOTALL)
    ship_lines = [l.strip() for l in ship_block.group(1).split('\n') if l.strip()] if ship_block else []

    data['ship_to'] = {
        "name": ship_lines[0] if ship_lines else None,
        "address_lines": ship_lines[1:] if len(ship_lines) > 1 else []
    }

    # 3. LOGÍSTICA
    # Buscamos en el texto completo usando patrones específicos
    data['logistics'] = {
        "file_ref": re.search(r'FILE/REF\s*[:.,]?\s*([A-Z0-9]+)', text).group(1) if re.search(r'FILE/REF', text) else None,
        "order_number": re.search(r'ORDER/ORDEN\s*#\s*[:.,]?\s*(\d+)', text).group(1) if re.search(r'ORDER/ORDEN', text) else None,
        "bill_of_lading": re.search(r'B/L#\s*[:.,]?\s*([A-Z0-9]+)', text).group(1) if re.search(r'B/L#', text) else None,
        "incoterm": "FOB", # Hardcoded según ejemplo o detectado
        "carrier": "Hapag Lloyd" if "Hapag Lloyd" in text else None,
        "country_of_origin": "China",
        "container_number": re.search(r'UETU\d+', text).group(0) if re.search(r'UETU\d+', text) else None
    }
    
    # 4. FECHAS
    date_raw = re.search(r'DATE/FECHA\s*[:.,]?\s*([A-Za-z]{3}\s\d{2},\s\d{4})', text)
    due_raw = re.search(r'DUE DATE/FECHA VENCIMIENTO\s*([A-Za-z]{3}\s\d{2},\s\d{4})', text)
    
    data['dates'] = {
        "invoice_date": parse_date_regal(date_raw.group(1)) if date_raw else None,
        "due_date": parse_date_regal(due_raw.group(1)) if due_raw else None,
        "payment_terms": "90 DAYS / DIAS"
    }

    # 5. LINE ITEMS
    # Patrón complejo para filas de productos
    # Busca: Cantidad (digitos) -> Modelo/Desc -> Pais -> UPC -> Precio -> Total
    # Nota: El OCR a veces parte las líneas, esto es una aproximación robusta
    items = []
    
    # Buscamos bloques que parecen items
    item_pattern = r'(\d+)\s+(TCL.*?)\s+(CHN)\s+(\d+)\s+([\d,]+\.\d{2})\s*\$?\s*\$?\s*([\d,]+\.\d{2})'
    matches = re.finditer(item_pattern, text_clean)
    
    for i, m in enumerate(matches):
        full_desc = m.group(2).strip()
        # Intentar separar modelo de descripción
        model = full_desc.split(' ', 2)[0] + " " + full_desc.split(' ', 2)[1]
        
        items.append({
            "line_number": i + 1,
            "quantity": int(m.group(1)),
            "model": model,
            "description": full_desc,
            "unit_cost": float(m.group(5).replace(',', '')),
            "amount": float(m.group(6).replace(',', '')),
            "upc": m.group(4),
            "country_of_origin": m.group(3)
        })
    
    data['line_items'] = items

    # 6. TOTALES
    # Buscar el último gran número al final
    totals_block = re.findall(r'(\d{1,3}(?:,\d{3})*\.\d{2})', text)
    total_val = float(totals_block[-1].replace(',', '')) if totals_block else 0.0
    
    data['totals'] = {
        "currency": "USD",
        "subtotal": total_val,
        "insurance": 0.0,
        "freight": 0.0,
        "other_charges": 0.0,
        "total": total_val,
        "amount_in_words_es": re.search(r'(CIENTO.*?DOLARES)', text).group(1) if re.search(r'CIENTO', text) else None
    }

    # 7. RESUMEN ENVÍO
    ctns = re.search(r'(\d+)\s*CTNS', text)
    kgs = re.search(r'([\d\.]+)\s*KGS', text)
    cbm = re.search(r'([\d\.]+)\s*CBM', text)
    
    data['shipment_summary'] = {
        "cartons": int(ctns.group(1)) if ctns else 0,
        "weight_kg": float(kgs.group(1)) if kgs else 0.0,
        "volume_cbm": float(cbm.group(1)) if cbm else 0.0
    }
    
    # 8. METADATA
    data['source'] = {
        "file_name": "Procesado_Streamlit.pdf",
        "pages": 1, # Se actualizará en la función principal
        "note": "Extracción automática"
    }

    return data

def process_regal_pdf(file_bytes):
    try:
        images = convert_from_bytes(file_bytes)
        full_text = ""
        # Extraer texto de todas las páginas
        for img in images:
            full_text += pytesseract.image_to_string(img, lang='spa', config='--psm 6') + "\n"
            
        json_data = extract_regal_specific(full_text)
        json_data['source']['pages'] = len(images)
        
        return json_data, full_text
    except Exception as e:
        return {"error": str(e)}, ""

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

st.title(f"📄 Procesador de Documentos: {modo_procesamiento}")

uploaded_file = st.file_uploader("Sube tu archivo PDF", type=["pdf"])

if uploaded_file is not None:
    
    # --- MODO 1: JSON ESPECÍFICO ---
    if modo_procesamiento == "Específico: Factura Regal Trading":
        st.info("ℹ️ Modo JSON: Extrae datos estructurados de facturas Regal.")
        
        if st.button("🔍 Extraer JSON"):
            with st.spinner("Analizando patrones..."):
                file_bytes = uploaded_file.read()
                json_result, raw_txt = process_regal_pdf(file_bytes)
                
                if "error" in json_result:
                    st.error(f"Error: {json_result['error']}")
                else:
                    st.success("✅ Extracción completada")
                    st.json(json_result, expanded=False)
                    
                    # Descargar JSON
                    st.download_button(
                        "📥 Descargar JSON",
                        data=json.dumps(json_result, indent=2, ensure_ascii=False),
                        file_name="factura_regal.json",
                        mime="application/json"
                    )
                    
                    with st.expander("Ver texto crudo (Debug)"):
                        st.text(raw_txt)

    # --- MODO 2: EXCEL UNIVERSAL ---
    else:
        st.info("ℹ️ Modo Excel: Digitaliza tablas manteniendo el diseño visual.")
        
        if st.button("✨ Procesar a Excel"):
            with st.status("Digitalizando...", expanded=True) as status:
                file_bytes = uploaded_file.read()
                resultado = process_universal(file_bytes)
                
                if not resultado:
                    status.update(label="Error", state="error")
                    st.error("No se pudo leer el documento.")
                else:
                    status.update(label="¡Listo!", state="complete")
                    
                    # Crear Excel
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        for sheet_name, df in resultado:
                            df.to_excel(writer, sheet_name=sheet_name, index=False, header=False)
                            # Ajuste de columnas
                            worksheet = writer.sheets[sheet_name]
                            for idx, col in enumerate(df.columns):
                                max_len = max(df[col].astype(str).map(len).max(), 10)
                                worksheet.set_column(idx, idx, max_len + 2)
                    
                    st.download_button(
                        "📥 Descargar Excel",
                        data=buffer.getvalue(),
                        file_name="Universal_OCR.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )

