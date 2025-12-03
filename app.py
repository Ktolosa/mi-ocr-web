import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil
import re

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Sistema OCR Dual V18", layout="wide")
st.title("🧰 Sistema OCR Multiusos (General Optimizado)")

if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# --- BARRA LATERAL (SELECTOR DE MODO) ---
st.sidebar.header("⚙️ Configuración")
modo_app = st.sidebar.radio(
    "Selecciona la Herramienta:",
    ["1. Extractor Regal Trading (Específico)", "2. OCR General (Réplica Limpia)"]
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
# 🧩 MÓDULO 2: OCR GENERAL MEJORADO (COMPRESIÓN DE ESPACIOS)
# ==============================================================================

def generate_compact_spatial_excel(images):
    """
    Crea una réplica visual pero elimina el exceso de filas y columnas vacías.
    Agrupa elementos cercanos.
    """
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        cell_fmt = workbook.add_format({'text_wrap': False, 'valign': 'top', 'font_size': 10})
        
        for i, image in enumerate(images):
            # 1. Obtener datos detallados
            # --psm 6 ayuda a leer líneas completas
            data = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
            
            # Crear DataFrame para manipular coordenadas fácilmente
            df = pd.DataFrame(data)
            
            # Filtrar basura (texto vacío o confianza -1)
            df = df[df.text.str.strip() != ""]
            df = df.dropna(subset=['text'])
            
            if df.empty: continue

            # 2. AGRUPACIÓN DE FILAS (Row Snapping)
            # Dividimos la posición Y entre 20. Esto significa que todo texto
            # dentro de un rango de 20px de altura caerá en la misma fila de Excel.
            ROW_HEIGHT_PX = 20
            df['row_idx'] = (df['top'] // ROW_HEIGHT_PX).astype(int)
            
            # Normalizar filas (Para que empiece en la fila 0 de Excel)
            min_row = df['row_idx'].min()
            df['row_idx'] = df['row_idx'] - min_row
            
            # 3. AGRUPACIÓN DE COLUMNAS (Column Compression)
            # Dividimos la posición X. Un valor más alto aquí (ej: 25)
            # reduce más los espacios en blanco horizontales.
            COL_WIDTH_PX = 18 
            df['col_idx'] = (df['left'] // COL_WIDTH_PX).astype(int)
            
            # Normalizar columnas (Para que empiece en la columna A)
            min_col = df['col_idx'].min()
            df['col_idx'] = df['col_idx'] - min_col

            # 4. Escribir en Excel
            sheet_name = f"Pagina_{i+1}"
            worksheet = workbook.add_worksheet(sheet_name)
            
            # Diccionario para evitar superposiciones
            # Si dos palabras caen en la misma celda, las concatenamos
            grid_map = {}
            
            for _, row in df.iterrows():
                r, c = row['row_idx'], row['col_idx']
                txt = str(row['text'])
                
                if (r, c) in grid_map:
                    # Si ya hay texto, agregamos un espacio y concatenamos
                    # Esto arregla frases que se partieron
                    grid_map[(r, c)] += " " + txt
                else:
                    grid_map[(r, c)] = txt
            
            # Volcar el mapa al Excel
            for (r, c), text in grid_map.items():
                worksheet.write(r, c, text, cell_fmt)
            
            # Ajuste estético de columnas (Ancho fijo pequeño para simular grilla)
            worksheet.set_column(0, df['col_idx'].max(), 2.5) 
            
    return output

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_files = st.file_uploader("Sube tus archivos PDF", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    
    # ---------------------------------------------------------
    # MODO 1: REGAL TRADING (ESTRUCTURADO)
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
    # MODO 2: OCR GENERAL (RÉPLICA LIMPIA)
    # ---------------------------------------------------------
    elif modo_app == "2. OCR General (Réplica Limpia)":
        st.info("ℹ️ Modo activo: Réplica visual compacta (elimina espacios vacíos excesivos).")
        
        if st.button("✨ Generar Réplica Excel"):
            with st.status("Generando réplica optimizada...", expanded=True) as status:
                try:
                    master_buffer = io.BytesIO()
                    
                    # Procesamos para crear el excel
                    # Nota: Para este modo iteramos archivo por archivo y generamos un ZIP o un solo Excel con muchas hojas
                    # Aquí haremos un solo Excel multi-hoja
                    
                    # Recargamos punteros de archivos
                    for f in uploaded_files: f.seek(0)
                    
                    # Como pdf2image necesita leer bytes, leemos el primero para el ejemplo o todos
                    # Simplificación: Procesamos todos los archivos en un solo libro Excel
                    
                    all_images = []
                    for f in uploaded_files:
                        imgs = convert_from_bytes(f.read(), dpi=200)
                        all_images.extend(imgs)
                    
                    excel_data = generate_compact_spatial_excel(all_images)
                    
                    status.update(label="¡Réplica Creada!", state="complete")
                    st.success("✅ Excel generado con espacios optimizados.")
                    
                    st.download_button(
                        "📥 Descargar Réplica Compacta", 
                        excel_data.getvalue(), 
                        "Replica_General.xlsx"
                    )
                    
                except Exception as e:
                    st.error(f"Error técnico: {e}")
