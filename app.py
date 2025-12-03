import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil
import re

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor Regal V16", layout="wide")
st.title("📄 Extractor Regal Trading (V16: Adaptativo)")

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
    if not text: return ""
    text = text.replace(" ", "").strip()
    if len(text) > 8 and text.startswith("A"):
        return "4" + text[1:]
    return text

def extract_money(text_list):
    """Busca precio válido en lista"""
    for text in reversed(text_list):
        clean = text.replace('$', '').replace('S', '').strip()
        if re.search(r'\d+[.,]\d{2}', clean):
            return clean
    return ""

# ==========================================
# 🧠 LÓGICA DE ITEMS (ADAPTATIVA)
# ==========================================
def get_anchors(n_boxes, d, w, h, strict_mode=True):
    """
    Busca las filas (anclas).
    strict_mode=True: Solo acepta la fila si tiene PRECIO a la derecha.
    strict_mode=False: Acepta la fila si tiene CANTIDAD válida (Respaldo).
    """
    X_QTY_LIMIT = w * 0.14
    X_PRICE_START = w * 0.72
    
    candidates = []
    min_y = h * 0.25
    max_y = h * 0.85
    
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        
        if cy < min_y or cy > max_y: continue
        
        # 1. ¿Es una cantidad válida?
        if cx < X_QTY_LIMIT and re.match(r'^[0-9.,]+$', text):
            if d['height'][i] > 8: # Filtro de ruido
                
                if not strict_mode:
                    # MODO RELAJADO: Aceptamos cualquier número bueno a la izquierda
                    candidates.append({'y': cy, 'qty': text})
                else:
                    # MODO ESTRICTO: Verificamos si hay precio a la derecha
                    has_price = False
                    for j in range(n_boxes):
                        w_txt = d['text'][j].strip()
                        w_y = d['top'][j]
                        w_x = d['left'][j]
                        
                        # Misma altura (+/- 15px) y a la derecha
                        if (cy - 15) <= w_y <= (cy + 15) and w_x > X_PRICE_START:
                            if re.search(r'\d+[.,]\d{2}', w_txt) or '$' in w_txt:
                                has_price = True
                                break
                    
                    if has_price:
                        candidates.append({'y': cy, 'qty': text})

    # Filtrar duplicados verticales
    candidates.sort(key=lambda k: k['y'])
    final_anchors = []
    if candidates:
        final_anchors.append(candidates[0])
        for anc in candidates[1:]:
            if anc['y'] - final_anchors[-1]['y'] > 15:
                final_anchors.append(anc)
                
    return final_anchors

def extract_items_v16(image):
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    # 1. INTENTO 1: MODO ESTRICTO (Para evitar el "2" fantasma)
    anchors = get_anchors(n_boxes, d, w, h, strict_mode=True)
    
    # 2. INTENTO 2: MODO RELAJADO (Si el estricto falló y no encontró nada)
    # Esto salva los PDFs donde el precio no se lee bien
    if not anchors:
        anchors = get_anchors(n_boxes, d, w, h, strict_mode=False)

    # Definir zonas X
    X_DESC_START = w * 0.14
    X_DESC_END = w * 0.58
    X_UPC_END = w * 0.72
    X_PRICE_END = w * 0.88

    items = []
    for idx, anchor in enumerate(anchors):
        y_top = anchor['y'] - 30 # Mirar arriba para modelo
        
        if idx + 1 < len(anchors):
            y_bottom = anchors[idx+1]['y'] - 5
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
                if X_DESC_START < bx < X_DESC_END:
                    desc_tokens.append((by, bx, word))
                elif X_DESC_END <= bx < X_UPC_END:
                    if len(word) > 3 and word != "CHN":
                        upc_tokens.append(clean_upc(word))
                elif X_UPC_END <= bx < X_PRICE_END:
                    unit_tokens.append(word)
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
# 🧠 LÓGICA CABECERA
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
                        
                        items = extract_items_v16(img)
                        if items:
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
