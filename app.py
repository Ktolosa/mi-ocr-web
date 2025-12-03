import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil
import re

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor Regal V13", layout="wide")
st.title("📄 Extractor Regal (V13: Anti-Fantasmas)")

if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# ==========================================
# 🛠️ UTILIDADES
# ==========================================
def clean_text_block(text):
    if not text: return ""
    return " ".join(text.split())

def clean_upc(text):
    """Corrección específica: Cambia 'A' inicial por '4' en UPCs largos"""
    if not text: return ""
    text = text.replace(" ", "").strip()
    # Si tiene más de 8 caracteres y empieza con A, es un error de OCR común
    if len(text) > 8 and text.startswith("A"):
        return "4" + text[1:]
    return text

def extract_money(text_list):
    """Busca un valor monetario válido en una lista de candidatos"""
    for text in reversed(text_list):
        clean = text.replace('$', '').replace('S', '').strip()
        # Busca formato: digitos + punto/coma + 2 decimales
        if re.search(r'\d+[.,]\d{2}', clean):
            return clean
    return ""

# ==========================================
# 🧠 LÓGICA DE ITEMS (V13: FILTRO DE PRECIO OBLIGATORIO)
# ==========================================
def extract_items_v13(image):
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    # --- ZONAS DE COLUMNAS ---
    X_QTY_LIMIT = w * 0.14     
    X_DESC_START = w * 0.14
    X_DESC_END = w * 0.58
    X_UPC_END = w * 0.72
    X_PRICE_END = w * 0.88
    
    # 1. DETECTAR CANDIDATOS (Cualquier número a la izquierda)
    candidates = []
    min_y = h * 0.25 
    max_y = h * 0.85
    
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        
        if cy < min_y or cy > max_y: continue
        
        # Si es número y está a la izquierda
        if cx < X_QTY_LIMIT and re.match(r'^[0-9.,]+$', text):
            if d['height'][i] > 8: 
                candidates.append({'y': cy, 'qty': text})

    # 2. VALIDACIÓN ESTRICTA (ELIMINAR EL "2" FANTASMA)
    valid_anchors = []
    
    for cand in candidates:
        row_y = cand['y']
        
        # Buscamos SI EXISTE UN PRECIO en la misma línea visual
        # Si no hay precio, asumimos que el número a la izquierda es basura de la descripción
        has_price_confirmation = False
        
        for i in range(n_boxes):
            word = d['text'][i].strip()
            if not word: continue
            wy = d['top'][i]
            wx = d['left'][i]
            
            # Margen vertical +/- 20px
            if (row_y - 20) <= wy <= (row_y + 20):
                # Miramos en la zona de PRECIO o TOTAL
                if wx > X_UPC_END: 
                    # ¿Tiene formato de dinero (xx.xx)?
                    if re.search(r'\d+[.,]\d{2}', word):
                        has_price_confirmation = True
                        break
        
        # SOLO GUARDAMOS LA FILA SI TIENE PRECIO CONFIRMADO
        if has_price_confirmation:
            valid_anchors.append(cand)

    # Filtrar duplicados cercanos
    valid_anchors.sort(key=lambda k: k['y'])
    final_anchors = []
    if valid_anchors:
        final_anchors.append(valid_anchors[0])
        for anc in valid_anchors[1:]:
            if anc['y'] - final_anchors[-1]['y'] > 15:
                final_anchors.append(anc)

    # 3. EXTRAER DATOS
    items = []
    for idx, anchor in enumerate(final_anchors):
        # TECHO: 30px arriba para capturar modelo
        y_top = anchor['y'] - 30 
        
        # PISO: Siguiente fila
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
                # Descripción
                if X_DESC_START < bx < X_DESC_END:
                    desc_tokens.append((by, bx, word))
                
                # UPC (Con corrección automática de A -> 4)
                elif X_DESC_END <= bx < X_UPC_END:
                    if len(word) > 3 and word != "CHN":
                        clean_word = clean_upc(word)
                        upc_tokens.append(clean_word)

                # Precio Unitario
                elif X_UPC_END <= bx < X_PRICE_END:
                    unit_tokens.append(word)
                        
                # Total
                elif bx >= X_PRICE_END:
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
    
    sold = re.search(r'SOLD TO/VENDIDO A:(.*?)(?=SHIP TO|124829)', full_text, re.DOTALL | re.IGNORECASE)
    data['Vendido A'] = clean_text_block(sold.group(1)) if sold else ""

    ship = re.search(r'SHIP TO/EMBARCADO A:(.*?)(?=PAYMENT|DUE DATE|PAGE)', full_text, re.DOTALL | re.IGNORECASE)
    data['Embarcado A'] = clean_text_block(ship.group(1)) if ship else ""
    return data

# ==========================================
# 🕵️‍♂️ DETECTOR DUPLICADOS
# ==========================================
def is_duplicate_page(image):
    w, h = image.size
    header = image.crop((0, 0, w, h * 0.35))
    text = pytesseract.image_to_string(header, lang='spa')
    return bool(re.search(r'Duplicado', text, re.IGNORECASE))

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================
uploaded_files = st.file_uploader("Sube tus Facturas (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    if st.button("🚀 Extraer Datos"):
        all_data = []
        bar = st.progress(0)
        
        for idx, f in enumerate(uploaded_files):
            with st.expander(f"Procesando: {f.name}", expanded=True):
                try:
                    images = convert_from_bytes(f.read(), dpi=300)
                    header = {}
                    file_items = []
                    pg_count = 0
                    
                    for i, img in enumerate(images):
                        if is_duplicate_page(img):
                            st.warning(f"Página {i+1}: Duplicado omitido.")
                            continue
                        
                        st.success(f"Página {i+1}: Original procesada.")
                        if pg_count == 0:
                            txt = pytesseract.image_to_string(img, lang='spa')
                            header = extract_header_data(txt)
                        
                        items = extract_items_v13(img)
                        file_items.extend(items)
                        pg_count += 1
                    
                    if file_items:
                        df = pd.DataFrame(file_items)
                        st.dataframe(df, use_container_width=True)
                        for it in file_items:
                            row = header.copy()
                            row.update(it)
                            row['Archivo'] = f.name
                            all_data.append(row)
                    else:
                        st.error("No se encontraron items válidos.")
                        
                except Exception as e:
                    st.error(f"Error: {e}")
            bar.progress((idx+1)/len(uploaded_files))
            
        if all_data:
            df_final = pd.DataFrame(all_data)
            cols = ['Archivo', 'Factura', 'Fecha', 'Orden', 'Vendido A', 'Embarcado A', 
                    'Cantidad', 'Descripción', 'UPC', 'Precio Unit.', 'Total']
            final_cols = [c for c in cols if c in df_final.columns]
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_final[final_cols].to_excel(writer, index=False)
                writer.sheets['Sheet1'].set_column('H:H', 50)
            
            st.download_button("📥 Excel Final", buffer.getvalue(), "Reporte_Regal.xlsx")
