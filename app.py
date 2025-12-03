import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil
import re

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Sistema OCR Dual", layout="wide")
st.title("🧰 Sistema OCR Multiusos")

if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# --- BARRA LATERAL (SELECTOR DE MODO) ---
st.sidebar.header("⚙️ Configuración")
modo_app = st.sidebar.radio(
    "Selecciona la Herramienta:",
    ["1. Extractor Regal Trading (Específico)", "2. OCR General (Réplica Visual)"]
)

# ==============================================================================
# 🧩 MÓDULO 1: HERRAMIENTAS REGAL TRADING (Lógica V16)
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
    
    # 1. Detectar candidatos (Números a la izquierda)
    candidates = []
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        if cy < h*0.25 or cy > h*0.85: continue
        
        if cx < X_QTY_LIMIT and re.match(r'^[0-9.,]+$', text):
            if d['height'][i] > 8: candidates.append({'y': cy, 'qty': text})

    # 2. Validación (Modo adaptativo)
    # Intentamos filtrar estrictamente primero
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
    
    # Si el modo estricto falló (0 items), usamos el modo relajado (todos los candidatos)
    if not valid_anchors and candidates:
        valid_anchors = candidates

    # Filtrar duplicados
    valid_anchors.sort(key=lambda k: k['y'])
    final_anchors = []
    if valid_anchors:
        final_anchors.append(valid_anchors[0])
        for anc in valid_anchors[1:]:
            if anc['y'] - final_anchors[-1]['y'] > 15: final_anchors.append(anc)

    # 3. Extraer texto
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
# 🧩 MÓDULO 2: OCR GENERAL (RÉPLICA VISUAL)
# ==============================================================================

def generate_spatial_excel(images):
    """
    Crea un Excel mapeando cada palabra a una celda basada en sus coordenadas X,Y.
    El resultado se ve visualmente igual al PDF.
    """
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        # Estilo para texto pequeño y alineado
        cell_format = workbook.add_format({'text_wrap': False, 'valign': 'top', 'font_size': 10})
        
        for i, image in enumerate(images):
            sheet_name = f"Pagina_{i+1}"
            worksheet = workbook.add_worksheet(sheet_name)
            
            # Obtener datos con coordenadas
            data = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa')
            n_boxes = len(data['text'])
            
            # Factores de escala (Pixel -> Celda Excel)
            # Ajustar estos valores cambia qué tan "apretado" queda el Excel
            SCALE_Y = 16  # Cada 16 pixeles de altura es 1 Fila
            SCALE_X = 9   # Cada 9 pixeles de ancho es 1 Columna
            
            # Diccionario para evitar sobreescritura
            grid = {}
            
            for j in range(n_boxes):
                text = data['text'][j].strip()
                if not text: continue
                
                # Calcular coordenadas Excel
                row = int(data['top'][j] / SCALE_Y)
                col = int(data['left'][j] / SCALE_X)
                
                # Si la celda está ocupada, mover a la derecha
                while (row, col) in grid:
                    col += 1
                
                grid[(row, col)] = text
                worksheet.write(row, col, text, cell_format)
            
            # Hacer las columnas estrechas para simular una hoja milimétrica
            worksheet.set_column(0, 250, 1.2)
            
    return output

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL (LÓGICA DE MODOS)
# ==========================================

uploaded_files = st.file_uploader("Sube tus archivos PDF", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    
    # ---------------------------------------------------------
    # MODO 1: REGAL TRADING (ESTRUCTURADO)
    # ---------------------------------------------------------
    if modo_app == "1. Extractor Regal Trading (Específico)":
        st.info("ℹ️ Modo activo: Extracción de tablas y datos específicos de Regal Trading.")
        
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
                st.write("### Vista Previa:")
                st.dataframe(df.head(), use_container_width=True)
                
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
    # MODO 2: OCR GENERAL (RÉPLICA VISUAL)
    # ---------------------------------------------------------
    elif modo_app == "2. OCR General (Réplica Visual)":
        st.info("ℹ️ Modo activo: Réplica visual exacta. Crea un Excel que se ve igual al PDF (ideal para otros formatos).")
        
        if st.button("✨ Generar Réplica Excel"):
            with st.status("Generando réplica visual...", expanded=True) as status:
                try:
                    # Usamos solo el primer archivo para este modo (o iteramos si quieres)
                    # Aquí unimos todos en un solo excel con pestañas
                    
                    master_buffer = io.BytesIO()
                    with pd.ExcelWriter(master_buffer, engine='xlsxwriter') as writer:
                        workbook = writer.book
                        cell_fmt = workbook.add_format({'font_size': 9})
                        
                        for f in uploaded_files:
                            f.seek(0) # Reiniciar puntero
                            images = convert_from_bytes(f.read(), dpi=200)
                            
                            st.write(f"Procesando {f.name} ({len(images)} páginas)...")
                            
                            for i, img in enumerate(images):
                                # Nombre hoja: Archivo_Pagina
                                sheet_name = f"{f.name[:10]}_{i+1}"
                                # Limpiar caracteres inválidos para excel sheet
                                sheet_name = re.sub(r'[\[\]:*?/\\]', '', sheet_name)
                                worksheet = workbook.add_worksheet(sheet_name)
                                
                                # OCR Data
                                d = pytesseract.image_to_data(img, output_type=Output.DICT, lang='spa')
                                
                                # Mapeo Espacial (Pixel -> Celda)
                                SCALE_Y = 15 # Ajusta altura
                                SCALE_X = 8  # Ajusta ancho
                                grid = {}
                                
                                for j in range(len(d['text'])):
                                    txt = d['text'][j].strip()
                                    if not txt: continue
                                    
                                    row = int(d['top'][j] / SCALE_Y)
                                    col = int(d['left'][j] / SCALE_X)
                                    
                                    # Evitar colisión
                                    while (row, col) in grid: col += 1
                                    grid[(row, col)] = txt
                                    
                                    worksheet.write(row, col, txt, cell_fmt)
                                
                                # Ajuste visual de columnas
                                worksheet.set_column(0, 200, 1.1) 
                    
                    status.update(label="¡Réplica Creada!", state="complete")
                    st.success("✅ Se ha generado un Excel que imita la posición del texto original.")
                    
                    st.download_button(
                        "📥 Descargar Réplica Visual", 
                        master_buffer.getvalue(), 
                        "Replica_Visual.xlsx"
                    )
                    
                except Exception as e:
                    st.error(f"Error técnico: {e}")
