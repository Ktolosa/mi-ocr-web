import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil
import re

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor Regal V11", layout="wide")
st.title("📄 Extractor Regal Trading (V11: Validación Inteligente)")

if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# ==========================================
# 🛠️ UTILIDADES DE LIMPIEZA
# ==========================================
def clean_text_block(text):
    return " ".join(text.split())

def clean_upc(text):
    """Corrige errores comunes de OCR en códigos UPC"""
    if not text: return ""
    # Error común: Tesseract lee '4' como 'A' al inicio
    if text.startswith('A') and len(text) > 10:
        text = '4' + text[1:]
    # Quitar guiones o espacios
    return text.replace('-', '').replace(' ', '')

def extract_money(text_list):
    """Busca precios válidos en una lista de palabras detectadas"""
    for text in reversed(text_list):
        clean = text.replace('$', '').replace('S', '').strip()
        # Busca formatos como 6.25, 1,200.00, etc.
        if re.search(r'\d+[.,]\d{2}', clean):
            return clean
    return ""

# ==========================================
# 🧠 LÓGICA DE ITEMS (V11: CON VALIDACIÓN DE FILA)
# ==========================================
def extract_items_v11(image):
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    # --- ZONAS ---
    X_QTY_LIMIT = w * 0.14     
    X_DESC_START = w * 0.14
    X_DESC_END = w * 0.58
    X_UPC_END = w * 0.73
    X_PRICE_END = w * 0.88
    
    # --- PASO 1: DETECTAR CANDIDATOS A FILA ---
    candidates = []
    min_y = h * 0.25 
    max_y = h * 0.85
    
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        
        if cy < min_y or cy > max_y: continue
        
        # Si es un número en la zona izquierda
        if cx < X_QTY_LIMIT and re.match(r'^[0-9.,]+$', text):
            if d['height'][i] > 8: # Filtro de ruido
                candidates.append({'y': cy, 'qty': text, 'index': i})

    # --- PASO 2: VALIDAR SI SON FILAS REALES ---
    # Una fila es REAL solo si tiene PRECIO o TOTAL a su derecha.
    # Si no, es un número suelto de la descripción (el problema del "Item 2")
    
    valid_anchors = []
    
    for cand in candidates:
        # Definir una franja horizontal estrecha para buscar precios
        row_y = cand['y']
        search_top = row_y - 10
        search_bottom = row_y + 20
        
        has_price_data = False
        
        # Barrer horizontalmente buscando $ o formatos de precio
        for i in range(n_boxes):
            wy = d['top'][i]
            wx = d['left'][i]
            word = d['text'][i].strip()
            
            # Si está en la misma linea visual
            if search_top <= wy <= search_bottom:
                # Si está a la derecha (zona de precios)
                if wx > X_UPC_END:
                    # ¿Parece dinero?
                    if re.match(r'[\d,]+\.\d{2}', word) or '$' in word:
                        has_price_data = True
                        break
        
        # SI TIENE PRECIO, ES UNA FILA VÁLIDA.
        # SI NO TIENE PRECIO, ES EL "2" FANTASMA DE LA DESCRIPCIÓN -> LO DESCARTAMOS.
        if has_price_data:
            valid_anchors.append(cand)

    # Filtrar duplicados cercanos (OCR a veces lee doble)
    valid_anchors.sort(key=lambda k: k['y'])
    final_anchors = []
    if valid_anchors:
        final_anchors.append(valid_anchors[0])
        for anc in valid_anchors[1:]:
            if anc['y'] - final_anchors[-1]['y'] > 10:
                final_anchors.append(anc)

    # --- PASO 3: EXTRAER DATOS ---
    items = []
    for idx, anchor in enumerate(final_anchors):
        # Mirar ARRIBA para capturar el modelo
        y_top = anchor['y'] - 30 
        
        if idx + 1 < len(final_anchors):
            y_bottom = final_anchors[idx+1]['y'] - 5
        else:
            y_bottom = anchor['y'] + 150
            
        desc_tokens = []
        upc_tokens = []
        unit_tokens = []
        total_tokens = []
        
        for i in range(n_boxes):
            word = d['text'][i].strip()
            if not word: continue
            bx, by = d['left'][i], d['top'][i]
            
            if y_top <= by < y_bottom:
                # 1. DESCRIPCIÓN
                if X_DESC_START < bx < X_DESC_END:
                    desc_tokens.append((by, bx, word))
                
                # 2. UPC (Con corrección de 'A')
                elif X_DESC_END < bx < X_UPC_END:
                    if len(word) > 3 and word != "CHN":
                        # Aplicamos corrección inmediata
                        word = clean_upc(word) 
                        upc_tokens.append(word)
                        
                # 3. PRECIO
                elif X_UPC_END < bx < X_PRICE_END:
                    unit_tokens.append(word)
                        
                # 4. TOTAL
                elif bx > X_PRICE_END:
                    total_tokens.append(word)

        desc_tokens.sort(key=lambda k: (k[0], k[1]))
        full_desc = " ".join([t[2] for t in desc_tokens])
        
        items.append({
            "Cantidad": anchor['qty'],
            "Descripción": full_desc,
            "UPC": " ".join(upc_tokens),
            "Precio Unit.": extract_money(unit_tokens),
            "Total": extract_money(total_tokens)
        })
        
    return items

# ==========================================
# 🧠 LÓGICA DE CABECERA
# ==========================================
def extract_header_data(full_text):
    data = {}
    inv = re.search(r'(?:#|No\.|297107)\s*(\d{6})', full_text)
    if not inv: inv = re.search(r'#\s*(\d{4,6})', full_text)
    data['Factura'] = inv.group(1) if inv else ""

    date = re.search(r'(?:DATE|FECHA)\s*[:.,]?\s*([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})', full_text, re.IGNORECASE)
    data['Fecha'] = date.group(1) if date else ""

    orden = re.search(r'(?:ORDER|ORDEN).*?[:#]\s*(\d+)', full_text, re.IGNORECASE)
    data['Orden'] = orden.group(1) if orden else ""

    ref = re.search(r'(?:FILE|REF)\s*[:.,]?\s*([A-Z0-9-]+)', full_text, re.IGNORECASE)
    data['Ref'] = ref.group(1) if ref else ""
    
    bl = re.search(r'B/L#\s*[:.,]?\s*([A-Z0-9]+)', full_text, re.IGNORECASE)
    data['BL'] = bl.group(1) if bl else ""
    
    incoterm = re.search(r'INCOTERM\s*[:.,]?\s*([A-Z]+)', full_text, re.IGNORECASE)
    data['Incoterm'] = incoterm.group(1) if incoterm else ""

    sold = re.search(r'SOLD TO/VENDIDO A:(.*?)(?=SHIP TO|124829|\d{2}/\d{2})', full_text, re.DOTALL | re.IGNORECASE)
    data['Vendido A'] = clean_text_block(sold.group(1)) if sold else ""

    ship = re.search(r'SHIP TO/EMBARCADO A:(.*?)(?=PAYMENT|DUE DATE|PAGE)', full_text, re.DOTALL | re.IGNORECASE)
    data['Embarcado A'] = clean_text_block(ship.group(1)) if ship else ""
    
    return data

# ==========================================
# 🕵️‍♂️ DETECTOR DE DUPLICADOS
# ==========================================
def is_duplicate_page(image):
    w, h = image.size
    header_crop = image.crop((0, 0, w, h * 0.35))
    text = pytesseract.image_to_string(header_crop, lang='spa')
    return bool(re.search(r'Duplicado', text, re.IGNORECASE))

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_files = st.file_uploader("Sube tus Facturas Regal (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    if st.button("🚀 Extraer Datos"):
        
        all_data_export = []
        progress_bar = st.progress(0)
        
        for idx, uploaded_file in enumerate(uploaded_files):
            with st.expander(f"📄 Procesando: {uploaded_file.name}", expanded=True):
                try:
                    # Convertir (Alta Calidad)
                    images = convert_from_bytes(uploaded_file.read(), dpi=300)
                    
                    file_items = []
                    header = {}
                    pages_processed = 0
                    
                    for i, img in enumerate(images):
                        
                        # DETECTOR DE DUPLICADOS
                        if is_duplicate_page(img):
                            st.warning(f"⚠️ Página {i+1}: 'Duplicado' detectado -> Omitida.")
                            continue 
                        
                        st.success(f"✅ Página {i+1}: Original -> Procesando")
                        
                        if pages_processed == 0:
                            txt_full = pytesseract.image_to_string(img, lang='spa')
                            header = extract_header_data(txt_full)
                        
                        # USAR LÓGICA V11
                        page_items = extract_items_v11(img)
                        file_items.extend(page_items)
                        
                        pages_processed += 1
                    
                    # RESULTADOS
                    if header:
                        c1, c2, c3 = st.columns(3)
                        c1.info(f"Factura: {header.get('Factura')}")
                        c2.info(f"Orden: {header.get('Orden')}")
                        c3.metric("Items", len(file_items))
                    
                    if file_items:
                        df = pd.DataFrame(file_items)
                        st.dataframe(df, use_container_width=True)
                        
                        for it in file_items:
                            row = header.copy()
                            row.update(it)
                            row['Archivo'] = uploaded_file.name
                            all_data_export.append(row)
                    else:
                        if pages_processed > 0:
                            st.error("No se encontraron items válidos.")
                        
                except Exception as e:
                    st.error(f"Error en {uploaded_file.name}: {e}")
            
            progress_bar.progress((idx + 1) / len(uploaded_files))

        if all_data_export:
            df_final = pd.DataFrame(all_data_export)
            
            cols_order = ['Archivo', 'Factura', 'Fecha', 'Orden', 'Ref', 'BL', 'Incoterm', 
                          'Vendido A', 'Embarcado A', 
                          'Cantidad', 'Descripción', 'UPC', 'Precio Unit.', 'Total']
            
            final_cols = [c for c in cols_order if c in df_final.columns]
            df_final = df_final[final_cols]
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_final.to_excel(writer, sheet_name="Consolidado", index=False)
                ws = writer.sheets['Consolidado']
                ws.set_column('J:J', 10)
                ws.set_column('K:K', 60)
                
            st.success("✅ ¡Proceso finalizado!")
            st.download_button("📥 Descargar Reporte Excel", buffer.getvalue(), "Reporte_Regal_V11.xlsx")
