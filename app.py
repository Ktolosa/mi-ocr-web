import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil
import re
import json
from datetime import datetime

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Sistema OCR Híbrido", layout="wide")

# --- MENÚ LATERAL ---
st.sidebar.title("🔧 Configuración")
modo_procesamiento = st.sidebar.selectbox(
    "Selecciona el tipo de documento:",
    ["Universal (Cualquier PDF)", "Específico: Factura Regal Trading"]
)

# --- VERIFICACIÓN DE SISTEMA ---
if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# ==========================================
# 🧠 MÓDULO 1: LÓGICA UNIVERSAL (TU CÓDIGO ANTERIOR)
# ==========================================
def smart_layout_analysis(image):
    """Agrupación inteligente basada en espacios visuales."""
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
                current_cell += " " + str(word['text'])
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
            max_cols = max(len(r) for r in rows)
            col_names = [f"Col {j+1}" for j in range(max_cols)]
            df = pd.DataFrame(rows)
            workbook_data.append((f"Página {i+1}", df))
    return workbook_data

# ==========================================
# 🎯 MÓDULO 2: LÓGICA ESPECÍFICA (FACTURA REGAL)
# ==========================================

def parse_date(date_str):
    """Convierte AUG 07, 2025 a 2025-08-07"""
    try:
        # Mapeo de meses en inglés/español por si acaso
        months = {
            'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05', 'JUN': '06',
            'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12',
            'ENE': '01', 'ABR': '04', 'AGO': '08', 'DIC': '12'
        }
        # Limpiar texto
        clean_date = date_str.upper().replace('.', '').strip()
        parts = re.split(r'\s+|,', clean_date)
        parts = [p for p in parts if p] # Quitar vacíos
        
        if len(parts) >= 3:
            month_txt = parts[0][:3]
            day = parts[1]
            year = parts[2]
            month_num = months.get(month_txt, '01')
            return f"{year}-{month_num}-{day.zfill(2)}"
        return date_str
    except:
        return date_str

def extract_regal_specific(text_content):
    """
    Extrae datos específicos usando Regex basado en el formato Regal Worldwide.
    """
    data = {}
    
    # 1. Datos Generales
    invoice_match = re.search(r'(?:COMMERCIAL INVOICE|FACTURA)\s*#?\s*(\d{6})', text_content)
    data['invoice_number'] = invoice_match.group(1) if invoice_match else "No encontrado"
    data['invoice_type'] = "COMMERCIAL INVOICE"
    
    # Tipo de copia (Original/Duplicado)
    if "Duplicado" in text_content: data['copy_type'] = "Duplicado"
    else: data['copy_type'] = "Original"

    # 2. Vendedor (Seller) - Hardcoded o extraído si varía
    data['seller'] = {
        "name": "REGAL WORLDWIDE TRADING LLC.",
        "address_lines": ["703 Waterford way, Suite 530", "Miami, FL 33126"],
        "email": "info@regalwt.com",
        "phone": "305 400-4978"
    }

    # 3. Comprador (Sold To)
    # Buscamos el bloque entre "SOLD TO" y "SHIP TO"
    buyer_match = re.search(r'SOLD TO/VENDIDO A:\s*\n(.*?)\s*SHIP TO', text_content, re.DOTALL)
    buyer_lines = []
    if buyer_match:
        raw_buyer = buyer_match.group(1).strip().split('\n')
        buyer_lines = [line.strip() for line in raw_buyer if line.strip()]
    
    data['buyer'] = {
        "name": buyer_lines[0] if buyer_lines else "",
        "address_lines": buyer_lines[1:-1] if len(buyer_lines) > 2 else [],
        "reference": buyer_lines[-1] if buyer_lines else "" # Asumiendo que el ID fiscal está al final
    }

    # 4. Ship To
    ship_match = re.search(r'SHIP TO/EMBARCADO A:\s*\n(.*?)\s*PAYMENT TERMS', text_content, re.DOTALL)
    ship_lines = []
    if ship_match:
        raw_ship = ship_match.group(1).strip().split('\n')
        ship_lines = [line.strip() for line in raw_ship if line.strip()]
        
    data['ship_to'] = {
        "name": ship_lines[0] if ship_lines else "",
        "address_lines": ship_lines[1:] if len(ship_lines) > 1 else []
    }

    # 5. Logística y Fechas
    data['logistics'] = {
        "file_ref": re.search(r'FILE/REF\s*:?\s*([A-Z0-9]+)', text_content).group(1) if re.search(r'FILE/REF\s*:?\s*([A-Z0-9]+)', text_content) else None,
        "order_number": re.search(r'ORDER/ORDEN\s*#\s*:?\s*(\d+)', text_content).group(1) if re.search(r'ORDER/ORDEN\s*#\s*:?\s*(\d+)', text_content) else None,
        "bill_of_lading": re.search(r'B/L#\s*:?\s*([A-Z0-9]+)', text_content).group(1) if re.search(r'B/L#\s*:?\s*([A-Z0-9]+)', text_content) else None,
        "incoterm": re.search(r'INCOTERM\s*:?\s*([A-Z]+)', text_content).group(1) if re.search(r'INCOTERM\s*:?\s*([A-Z]+)', text_content) else None,
        "container_number": re.search(r'(?:CONTENEDOR|CONTAINER)\s*:?\s*([A-Z0-9]+)', text_content).group(1) if re.search(r'(?:CONTENEDOR|CONTAINER)\s*:?\s*([A-Z0-9]+)', text_content) else None,
        "country_of_origin": "China" if "CHINA" in text_content else "Unknown"
    }
    
    # Fechas
    date_match = re.search(r'DATE/FECHA\s*:?\s*([A-Za-z]{3}\s\d{2},\s\d{4})', text_content)
    due_match = re.search(r'DUE DATE/FECHA VENCIMIENTO\s*([A-Za-z]{3}\s\d{2},\s\d{4})', text_content)
    
    data['dates'] = {
        "invoice_date": parse_date(date_match.group(1)) if date_match else None,
        "due_date": parse_date(due_match.group(1)) if due_match else None,
        "payment_terms": "90 DAYS / DIAS" # Detectado del texto o default
    }

    # 6. Items (Esta es la parte difícil con Regex)
    items = []
    # Buscamos líneas que empiecen con cantidad (dígitos) y sigan con modelo (letras mayusculas)
    # Patrón aproximado: CANTIDAD -> MODELO -> DESC -> PAIS -> UPC -> PRECIO -> TOTAL
    item_pattern = r'(\d+)\s+([A-Z0-9\s-]+?)\s+(CHN)\s+(\d{10,13})\s+([\d,]+\.\d{2})\s*\$?\s*\$?\s*([\d,]+\.\d{2})'
    
    item_matches = re.finditer(item_pattern, text_content)
    
    for idx, match in enumerate(item_matches):
        # Limpiar descripción (el modelo suele capturar parte de la desc en regex simple)
        full_desc_capture = match.group(2).strip()
        # Separar modelo de descripción (asumimos que el modelo es la primera palabra o dos)
        parts = full_desc_capture.split(' ', 2)
        model = parts[0] + " " + parts[1] if len(parts) > 1 else parts[0]
        desc = parts[2] if len(parts) > 2 else ""
        
        items.append({
            "line_number": idx + 1,
            "quantity": int(match.group(1)),
            "model": model,
            "description": full_desc_capture, # Simplificado para demo
            "unit_cost": float(match.group(5).replace(',', '')),
            "amount": float(match.group(6).replace(',', '')),
            "upc": match.group(4),
            "country_of_origin": match.group(3)
        })
    data['line_items'] = items

    # 7. Totales
    total_match = re.search(r'TOTAL\s*([\d,]+\.\d{2})', text_content)
    amount_text_match = re.search(r'(CIENTO.*?DOLARES)', text_content)
    
    total_val = float(total_match.group(1).replace(',', '')) if total_match else 0.0
    
    data['totals'] = {
        "currency": "USD",
        "subtotal": total_val, # Asumiendo subtotal igual a total por ahora
        "total": total_val,
        "amount_in_words_es": amount_text_match.group(1) if amount_text_match else ""
    }
    
    # 8. Shipment Summary (Cartons, Weight)
    cartons = re.search(r'(\d+)\s*CTNS', text_content)
    weight = re.search(r'([\d\.]+)\s*KGS', text_content)
    volume = re.search(r'([\d\.]+)\s*CBM', text_content)
    
    data['shipment_summary'] = {
        "cartons": int(cartons.group(1)) if cartons else 0,
        "weight_kg": float(weight.group(1)) if weight else 0.0,
        "volume_cbm": float(volume.group(1)) if volume else 0.0
    }

    return data

def process_regal_pdf(file_bytes):
    try:
        images = convert_from_bytes(file_bytes)
        # Solo necesitamos OCR de texto crudo, no posiciones
        full_text = ""
        for image in images:
            # Usamos psm 6 para mantener el orden de lectura de bloques
            text = pytesseract.image_to_string(image, lang='spa', config='--psm 6')
            full_text += text + "\n"
            
        # Llamamos al extractor específico
        json_data = extract_regal_specific(full_text)
        
        # Agregar info de fuente
        json_data['source'] = {
            "file_name": "Procesado_Streamlit.pdf",
            "pages": len(images)
        }
        
        return json_data, full_text
    except Exception as e:
        return {"error": str(e)}, ""

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

st.title(f"📄 Procesador de Documentos: {modo_procesamiento}")

uploaded_file = st.file_uploader("Sube tu archivo PDF", type=["pdf"])

if uploaded_file is not None:
    # ---------------- MODO ESPECÍFICO ----------------
    if modo_procesamiento == "Específico: Factura Regal Trading":
        st.info("ℹ️ Este modo extrae datos estructurados (JSON) de facturas Regal.")
        
        if st.button("🔍 Extraer Datos JSON"):
            with st.spinner("Analizando texto y aplicando patrones..."):
                file_bytes = uploaded_file.read()
                json_result, raw_text = process_regal_pdf(file_bytes)
                
                if "error" in json_result:
                    st.error(f"Error: {json_result['error']}")
                else:
                    st.success("✅ Datos extraídos con éxito")
                    
                    # Mostrar JSON bonito
                    st.json(json_result, expanded=False)
                    
                    # Botón descarga JSON
                    json_str = json.dumps(json_result, indent=2, ensure_ascii=False)
                    st.download_button(
                        label="📥 Descargar JSON",
                        data=json_str,
                        file_name="factura_data.json",
                        mime="application/json"
                    )
                    
                    # Opción de ver Texto Crudo (Debugging)
                    with st.expander("Ver texto crudo detectado por OCR"):
                        st.text(raw_text)

    # ---------------- MODO UNIVERSAL ----------------
    else:
        st.info("ℹ️ Este modo intenta replicar visualmente cualquier tabla a Excel.")
        
        if st.button("✨ Procesar a Excel"):
            with st.status("Analizando geometría...", expanded=True) as status:
                file_bytes = uploaded_file.read()
                resultado = process_universal(file_bytes)
                
                if not resultado:
                    status.update(label="Error", state="error")
                    st.error("No se pudo extraer texto.")
                else:
                    status.update(label="¡Listo!", state="complete")
                    
                    # Excel Logic
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        for sheet_name, rows in resultado:
                            if rows:
                                max_cols = max(len(r) for r in rows)
                                col_names = [f"Col {j+1}" for j in range(max_cols)]
                                df = pd.DataFrame(rows)
                                df.to_excel(writer, sheet_name=sheet_name, index=False, header=False)
                                worksheet = writer.sheets[sheet_name]
                                for idx, col in enumerate(df.columns):
                                    max_len = max(df[col].astype(str).map(len).max(), 10)
                                    worksheet.set_column(idx, idx, max_len + 2)

                    st.download_button(
                        label="📥 Descargar Excel Limpio",
                        data=buffer.getvalue(),
                        file_name="Universal_OCR.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )    final_rows_for_excel = []
    
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

