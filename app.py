import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil
import re

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Sistema OCR V24", layout="wide")
st.title("🧰 Sistema de Extracción: Facturas & DUCA")

if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# --- BARRA LATERAL ---
st.sidebar.header("⚙️ Tipo de Documento")
modo_app = st.sidebar.radio(
    "Selecciona:",
    ["1. Facturas Regal Trading", "2. Declaración DUCA (Aduanas)"]
)

# ==============================================================================
# 🧩 MÓDULO 1: REGAL TRADING (Versión V16 - Estable)
# ==============================================================================
def clean_text_block(text):
    if not text: return ""
    return " ".join(text.split())

def clean_upc(text):
    if not text: return ""
    text = text.replace(" ", "").strip()
    if len(text) > 8 and text.startswith("A"): return "4" + text[1:]
    return text

def extract_money(text_list):
    for text in reversed(text_list):
        clean = text.replace('$', '').replace('S', '').strip()
        if re.search(r'\d+[.,]\d{2}', clean): return clean
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
                if re.search(r'\d+[.,]\d{2}', word) or '$' in word: has_price = True; break
        if has_price: valid_anchors.append(cand)
    
    if not valid_anchors and candidates: valid_anchors = candidates
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
            "Cantidad": anchor['qty'], "Descripción": full_desc, "UPC": " ".join(upc),
            "Precio Unit.": extract_money(unit), "Total": extract_money(total)
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
# 🧩 MÓDULO 2: EXTRACTOR DUCA (CORREGIDO Y LIMPIO)
# ==============================================================================

def extract_duca_header(full_text):
    """Extrae datos generales de la DUCA"""
    header = {}
    
    # 1. Referencia (Campo 2)
    # Buscamos números grandes después de "Referencia"
    ref = re.search(r'Referencia.*?(\d{8,})', full_text, re.DOTALL)
    header['Referencia'] = ref.group(1) if ref else ""
    
    # 2. Fecha (Campo 3)
    fecha = re.search(r'(\d{2}/\d{2}/\d{4})', full_text)
    header['Fecha'] = fecha.group(1) if fecha else ""
    
    # 3. Declarante (Campo 10)
    decl = re.search(r'Nombre.*?Declarante.*?\n(.*?)\n', full_text, re.IGNORECASE)
    header['Declarante'] = decl.group(1).replace('"','').strip() if decl else ""
    
    return header

def extract_duca_items(full_text):
    """Extrae items de DUCA limpiando la basura del OCR (comillas, comas)"""
    items = []
    
    # LIMPIEZA PREVIA IMPORTANTE
    # Quitamos comillas dobles y comas raras que el OCR pone en la tabla
    clean_txt = full_text.replace('"', ' ').replace("'", "")
    
    # Dividir por "22. Item"
    blocks = re.split(r'22\.\s*Item', clean_txt)
    
    for i, block in enumerate(blocks[1:]): # Ignorar bloque 0 (encabezado)
        try:
            item_data = {}
            
            # --- 1. DESCRIPCIÓN (Campo 29) ---
            # Buscamos texto después de "29. Descripción Comercial"
            # y antes de "30. Valor FOB" o cualquier número de campo "30."
            desc_match = re.search(r'29\.\s*Descripci.*?Comercial(.*?)(?=30\.|Valor FOB)', block, re.DOTALL | re.IGNORECASE)
            
            if desc_match:
                raw_desc = desc_match.group(1)
                # Limpiamos saltos de linea y basura numérica (códigos arancelarios que se cuelan)
                clean_desc = re.sub(r'[\n\r]', ' ', raw_desc)
                clean_desc = re.sub(r'^\s*[:\.]?\s*', '', clean_desc) # Quitar dos puntos al inicio
                item_data['Descripción'] = clean_desc.strip()
            else:
                item_data['Descripción'] = "No detectada"

            # --- 2. VALOR FOB (Campo 30) ---
            # Buscamos el número decimal después de "30. Valor FOB"
            fob_match = re.search(r'30\.\s*Valor.*?FOB.*?([\d,]+\.\d{2})', block, re.DOTALL | re.IGNORECASE)
            item_data['Valor FOB'] = fob_match.group(1) if fob_match else "0.00"
            
            # --- 3. TOTAL (Campo 38) ---
            # Buscamos el número decimal después de "38. Total"
            total_match = re.search(r'38\.\s*Total.*?([\d,]+\.\d{2})', block, re.DOTALL | re.IGNORECASE)
            
            # Si no encuentra etiqueta, buscamos el último precio del bloque
            if total_match:
                item_data['Total'] = total_match.group(1)
            else:
                # Fallback: Buscar todos los precios y tomar el último
                all_prices = re.findall(r'([\d,]+\.\d{2})', block)
                item_data['Total'] = all_prices[-1] if all_prices else "0.00"

            item_data['Item #'] = i + 1
            items.append(item_data)
            
        except Exception:
            continue
            
    return items

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_files = st.file_uploader("Sube tus archivos PDF", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    
    # ---------------------------------------------------------
    # MODO 1: REGAL TRADING
    # ---------------------------------------------------------
    if modo_app == "1. Facturas Regal Trading":
        st.info("ℹ️ Modo: Facturas Comerciales.")
        if st.button("🚀 Extraer Regal"):
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
                except Exception as e: st.error(f"Error {f.name}: {e}")
                bar.progress((idx+1)/len(uploaded_files))
            
            if all_data:
                df = pd.DataFrame(all_data)
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df.to_excel(writer, index=False)
                st.success("✅ Listo")
                st.download_button("📥 Excel Regal", buffer.getvalue(), "Regal_Report.xlsx")

    # ---------------------------------------------------------
    # MODO 2: DUCA ADUANAS (CORREGIDO)
    # ---------------------------------------------------------
    elif modo_app == "2. Declaración DUCA (Aduanas)":
        st.info("ℹ️ Modo: Documentos de Aduana (DUCA Simplificada).")
        
        if st.button("🚢 Extraer DUCA"):
            all_duca_data = []
            bar = st.progress(0)
            
            for idx, f in enumerate(uploaded_files):
                try:
                    # DUCA necesita texto completo
                    images = convert_from_bytes(f.read(), dpi=300)
                    
                    header_duca = {}
                    
                    for i, img in enumerate(images):
                        # Psm 4 para mantener el orden de lectura
                        full_text = pytesseract.image_to_string(img, lang='spa', config='--psm 4')
                        
                        # Extraer cabecera (Pág 1)
                        if i == 0:
                            # AQUÍ ESTABA EL ERROR DE NOMBRE, YA CORREGIDO:
                            header_duca = extract_duca_header(full_text)
                        
                        # Extraer items
                        page_items = extract_duca_items(full_text)
                        
                        for item in page_items:
                            row = header_duca.copy()
                            row.update(item)
                            row['Página'] = i + 1
                            row['Archivo'] = f.name
                            all_duca_data.append(row)
                            
                except Exception as e:
                    st.error(f"Error en {f.name}: {e}")
                
                bar.progress((idx+1)/len(uploaded_files))
            
            if all_duca_data:
                df_duca = pd.DataFrame(all_duca_data)
                st.write("### Vista Previa DUCA")
                st.dataframe(df_duca.head(), use_container_width=True)
                
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df_duca.to_excel(writer, index=False)
                    ws = writer.sheets['Sheet1']
                    ws.set_column('E:E', 60) # Columna descripción ancha
                
                st.success("✅ DUCA Procesada")
                st.download_button("📥 Descargar Excel DUCA", buffer.getvalue(), "Reporte_DUCA.xlsx")
            else:
                st.warning("No se encontraron items DUCA. Asegúrate que el PDF sea legible.")
