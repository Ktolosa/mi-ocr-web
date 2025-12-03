import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil
import re

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Sistema OCR V20", layout="wide")
st.title("🧰 Sistema OCR Multiusos (Con Agrupación de Frases)")

if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# --- BARRA LATERAL ---
st.sidebar.header("⚙️ Configuración")
modo_app = st.sidebar.radio(
    "Selecciona la Herramienta:",
    ["1. Extractor Regal Trading (Específico)", "2. OCR General (Frases Completas)"]
)

# ==============================================================================
# 🧩 MÓDULO 1: HERRAMIENTAS REGAL TRADING (Tu versión V16 intacta)
# ==============================================================================

def clean_text_block(text):
    if not text: return ""
    return " ".join(text.split())

def clean_upc(text):
    if not text: return ""
    text = text.replace(" ", "").strip()
    if len(text) > 8 and text.startswith("A"):
        return "4" + text[1:]
    return text

def extract_money(text_list):
    for text in reversed(text_list):
        clean = text.replace('$', '').replace('S', '').strip()
        if re.search(r'\d+[.,]\d{2}', clean):
            return clean
    return ""

def extract_items_regal(image):
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    X_QTY_LIMIT = w * 0.14     
    X_DESC_START = w * 0.14
    X_DESC_END = w * 0.58
    X_UPC_END = w * 0.72
    X_PRICE_END = w * 0.88
    
    candidates = []
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        if cy < h*0.25 or cy > h*0.85: continue
        
        if cx < X_QTY_LIMIT and re.match(r'^[0-9.,]+$', text):
            if d['height'][i] > 8: candidates.append({'y': cy, 'qty': text})

    valid_anchors = []
    for cand in candidates:
        row_y = cand['y']
        has_price = False
        for i in range(n_boxes):
            word = d['text'][i].strip()
            wy, wx = d['top'][i], d['left'][i]
            if (row_y - 20) <= wy <= (row_y + 20) and wx > X_UPC_END:
                if re.search(r'\d+[.,]\d{2}', word) or '$' in word:
                    has_price = True; break
        if has_price: valid_anchors.append(cand)
    
    if not valid_anchors and candidates:
        valid_anchors = candidates

    valid_anchors.sort(key=lambda k: k['y'])
    final_anchors = []
    if valid_anchors:
        final_anchors.append(valid_anchors[0])
        for anc in valid_anchors[1:]:
            if anc['y'] - final_anchors[-1]['y'] > 15: final_anchors.append(anc)

    items = []
    for idx, anchor in enumerate(final_anchors):
        y_top = anchor['y'] - 30 
        y_bottom = final_anchors[idx+1]['y'] - 5 if idx + 1 < len(final_anchors) else anchor['y'] + 150
        
        desc, upc, unit, total = [], [], [], []
        
        for i in range(n_boxes):
            word = d['text'][i].strip()
            if not word: continue
            bx, by = d['left'][i], d['top'][i]
            
            if y_top <= by < y_bottom:
                if X_DESC_START < bx < X_DESC_END: desc.append((by, bx, word))
                elif X_DESC_END <= bx < X_UPC_END: 
                    if len(word)>3 and word!="CHN": upc.append(clean_upc(word))
                elif X_UPC_END <= bx < X_PRICE_END: unit.append(word)
                elif bx >= X_PRICE_END: total.append(word)

        desc.sort(key=lambda k: (k[0], k[1]))
        full_desc = " ".join([t[2] for t in desc])
        
        items.append({
            "Cantidad": anchor['qty'],
            "Descripción": full_desc,
            "UPC": " ".join(upc),
            "Precio Unit.": extract_money(unit),
            "Total": extract_money(total)
        })
    return items

def extract_header_regal(full_text):
    data = {}
    inv = re.search(r'(?:#|No\.|297107)\s*(\d{6})', full_text)
    if not inv: inv = re.search(r'#\s*(\d{4,6})', full_text)
    data['Factura'] = inv.group(1) if inv else ""
    
    date = re.search(r'(?:DATE|FECHA)\s*[:.,]?\s*([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})', full_text, re.IGNORECASE)
    data['Fecha'] = date.group(1) if date else ""
    
    orden = re.search(r'(?:ORDER|ORDEN).*?[:#]\s*(\d+)', full_text, re.IGNORECASE)
    data['Orden'] = orden.group(1) if orden else ""
    
    sold = re.search(r'SOLD TO/VENDIDO A:(.*?)(?=SHIP TO|124829)', full_text, re.DOTALL | re.IGNORECASE)
    data['Vendido A'] = clean_text_block(sold.group(1)) if sold else ""
    
    ship = re.search(r'SHIP TO/EMBARCADO A:(.*?)(?=PAYMENT|DUE DATE|PAGE)', full_text, re.DOTALL | re.IGNORECASE)
    data['Embarcado A'] = clean_text_block(ship.group(1)) if ship else ""
    return data

def is_duplicate(image):
    w, h = image.size
    header = image.crop((0, 0, w, h * 0.35))
    txt = pytesseract.image_to_string(header, lang='spa')
    return bool(re.search(r'Duplicado', txt, re.IGNORECASE))

# ==============================================================================
# 🧩 MÓDULO 2: OCR GENERAL INTELIGENTE (AGRUPACIÓN DE FRASES)
# ==============================================================================

def generate_smart_excel(images):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        cell_fmt = workbook.add_format({'text_wrap': False, 'valign': 'top', 'font_size': 10})
        
        for i, image in enumerate(images):
            # 1. Obtener datos detallados
            # --psm 6 es clave para mantener la estructura de líneas
            data = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
            n_boxes = len(data['text'])
            
            # Recolectar palabras
            words = []
            for j in range(n_boxes):
                txt = data['text'][j].strip()
                if not txt: continue
                words.append({
                    'text': txt,
                    'top': data['top'][j],
                    'left': data['left'][j],
                    'width': data['width'][j],
                    'right': data['left'][j] + data['width'][j]
                })
            
            if not words: continue

            # 2. AGRUPAR POR FILAS (Y-Axis Clustering)
            words.sort(key=lambda k: k['top'])
            lines = []
            current_line = [words[0]]
            
            for w in words[1:]:
                prev = current_line[-1]
                # Si la diferencia vertical es pequeña (<15px), es la misma línea
                if abs(w['top'] - prev['top']) < 15:
                    current_line.append(w)
                else:
                    lines.append(current_line)
                    current_line = [w]
            lines.append(current_line)

            # 3. CONSTRUIR CELDAS CON FRASES (X-Axis Clustering)
            sheet_name = f"Pagina_{i+1}"
            worksheet = workbook.add_worksheet(sheet_name)
            
            for row_idx, line in enumerate(lines):
                # Ordenar de izquierda a derecha
                line.sort(key=lambda k: k['left'])
                
                col_idx = 0
                current_phrase = line[0]['text']
                last_right = line[0]['right']
                
                # Umbral de "imán": Si están a menos de 40px, es la misma frase
                GAP_THRESHOLD = 40 
                
                for w in line[1:]:
                    gap = w['left'] - last_right
                    
                    if gap < GAP_THRESHOLD:
                        # Brecha pequeña -> Concatenar a la misma celda
                        current_phrase += " " + w['text']
                    else:
                        # Brecha grande -> Escribir celda y saltar a la siguiente
                        worksheet.write(row_idx, col_idx, current_phrase, cell_fmt)
                        col_idx += 1
                        current_phrase = w['text'] # Iniciar nueva frase
                    
                    last_right = w['right']
                
                # Escribir el último remanente de la línea
                worksheet.write(row_idx, col_idx, current_phrase, cell_fmt)
            
            # Ajuste de columnas
            worksheet.set_column(0, 50, 20)
            
    return output

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_files = st.file_uploader("Sube tus archivos PDF", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    
    # ---------------------------------------------------------
    # MODO 1: REGAL TRADING
    # ---------------------------------------------------------
    if modo_app == "1. Extractor Regal Trading (Específico)":
        st.info("ℹ️ Modo activo: Tablas estructuradas para Regal Trading.")
        
        if st.button("🚀 Extraer Datos (Regal)"):
            all_data = []
            bar = st.progress(0)
            
            for idx, f in enumerate(uploaded_files):
                try:
                    images = convert_from_bytes(f.read(), dpi=300)
                    header = {}
                    file_items = []
                    pg_count = 0
                    
                    for i, img in enumerate(images):
                        if is_duplicate(img): continue
                        if pg_count == 0:
                            txt = pytesseract.image_to_string(img, lang='spa')
                            header = extract_header_regal(txt)
                        
                        items = extract_items_regal(img)
                        if items: file_items.extend(items)
                        pg_count += 1
                    
                    if file_items:
                        for it in file_items:
                            row = header.copy()
                            row.update(it)
                            row['Archivo'] = f.name
                            all_data.append(row)
                    else:
                        st.warning(f"{f.name}: No se encontraron items válidos.")
                        
                except Exception as e:
                    st.error(f"Error en {f.name}: {e}")
                bar.progress((idx+1)/len(uploaded_files))
            
            if all_data:
                df = pd.DataFrame(all_data)
                cols = ['Archivo', 'Factura', 'Fecha', 'Orden', 'Vendido A', 'Embarcado A', 
                        'Cantidad', 'Descripción', 'UPC', 'Precio Unit.', 'Total']
                final_cols = [c for c in cols if c in df.columns]
                
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df[final_cols].to_excel(writer, index=False)
                    writer.sheets['Sheet1'].set_column('H:H', 50)
                
                st.success("✅ Extracción Completada")
                st.download_button("📥 Descargar Reporte Regal", buffer.getvalue(), "Reporte_Regal.xlsx")

    # ---------------------------------------------------------
    # MODO 2: OCR GENERAL (FRASES COMPLETAS)
    # ---------------------------------------------------------
    elif modo_app == "2. OCR General (Frases Completas)":
        st.info("ℹ️ Modo activo: Agrupa palabras cercanas en la misma celda para evitar fragmentación.")
        
        if st.button("✨ Generar Excel Inteligente"):
            with st.status("Analizando geometría de palabras...", expanded=True) as status:
                try:
                    all_images = []
                    for f in uploaded_files:
                        f.seek(0)
                        imgs = convert_from_bytes(f.read(), dpi=200)
                        all_images.extend(imgs)
                    
                    excel_data = generate_smart_excel(all_images)
                    
                    status.update(label="¡Listo!", state="complete")
                    st.success("✅ Excel generado. Las frases ahora están unidas.")
                    
                    st.download_button(
                        "📥 Descargar Excel General", 
                        excel_data.getvalue(), 
                        "OCR_General_Smart.xlsx"
                    )
                    
                except Exception as e:
                    st.error(f"Error técnico: {e}")
