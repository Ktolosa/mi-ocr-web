import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil
import re

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor Regal Trading", layout="wide")
st.title("📄 Extractor Regal Trading (Corregido)")

if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# ==========================================
# 🛠️ UTILIDADES
# ==========================================
def clean_text(text):
    return " ".join(text.split())

# ==========================================
# 🧠 LÓGICA DE ITEMS (CORREGIDA)
# ==========================================

def extract_items_fixed(image):
    # 1. Obtener datos
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    # --- CONFIGURACIÓN DE ZONAS (AQUÍ ESTABA EL ERROR) ---
    
    # 1. Dónde buscar el NÚMERO DE CANTIDAD (Anchor)
    # Lo pongo en 0.14 (14%) para asegurar que detecte el número "234" o "207"
    X_QTY_SEARCH_LIMIT = w * 0.15 
    
    # 2. Dónde empieza la DESCRIPCIÓN
    # Lo pongo en 0.10 (10%) para que empiece a leer MUY a la izquierda (captura "TCL")
    X_DESC_START = w * 0.10 
    X_DESC_END = w * 0.58
    
    X_UPC_START = w * 0.60
    X_PRICE_START = w * 0.74
    
    # 2. ENCONTRAR FILAS (ANCLAS)
    anchors = []
    
    start_y = h * 0.30
    end_y = h * 0.85
    
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        
        if cy < start_y or cy > end_y: continue
        
        # BUSCAMOS EL NÚMERO USANDO EL LÍMITE SEGURO (15%)
        if cx < X_QTY_SEARCH_LIMIT and re.match(r'^\d+$', text):
             # Filtro: Ignorar números sueltos muy pequeños o basura
             # Normalmente la cantidad tiene altura similar a otras letras
             anchors.append({'y': cy, 'qty': text})

    if not anchors: return []

    # 3. EXTRAER DATOS
    items = []
    
    for idx, anchor in enumerate(anchors):
        # Miramos 35 píxeles ARRIBA para capturar texto superior
        y_top = anchor['y'] - 35 
        
        if idx + 1 < len(anchors):
            y_bottom = anchors[idx+1]['y'] - 10
        else:
            y_bottom = anchor['y'] + 200
            
        desc_words = []
        upc_words = []
        unit_words = []
        total_words = []
        
        for i in range(n_boxes):
            word = d['text'][i].strip()
            if not word: continue
            wx, wy = d['left'][i], d['top'][i]
            
            if y_top <= wy < y_bottom:
                
                # Descripción: Usamos el inicio EXPANDIDO (10%)
                if X_DESC_START < wx < X_DESC_END:
                    desc_words.append((wy, wx, word))
                    
                elif X_UPC_START < wx < X_PRICE_START:
                    if len(word) > 2: upc_words.append(word)
                    
                elif X_PRICE_START < wx < (w * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word): unit_words.append(word)
                    
                elif wx > (w * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word): total_words.append(word)

        desc_words.sort(key=lambda k: (k[0], k[1]))
        full_desc = " ".join([w[2] for w in desc_words])
        
        items.append({
            "Cantidad": anchor['qty'],
            "Descripción": full_desc,
            "UPC": " ".join(upc_words),
            "Precio": unit_words[0] if unit_words else "",
            "Total": total_words[0] if total_words else ""
        })
        
    return items

# ==========================================
# 🧠 LÓGICA DE ENCABEZADO
# ==========================================
def extract_header_robust(full_text):
    data = {}
    
    inv = re.search(r'(?:#|No\.|297107)\s*(\d{6})', full_text)
    if not inv: inv = re.search(r'#\s*(\d{6})', full_text)
    data['Factura'] = inv.group(1) if inv else ""

    date = re.search(r'(?:DATE|FECHA)\s*[:.,]?\s*([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})', full_text, re.IGNORECASE)
    data['Fecha'] = date.group(1) if date else ""

    orden = re.search(r'(?:ORDER|ORDEN)\s*#?\s*[:.,]?\s*(\d+)', full_text, re.IGNORECASE)
    data['Orden'] = orden.group(1) if orden else ""

    ref = re.search(r'(?:FILE|REF)\s*[:.,]?\s*([A-Z0-9]+)', full_text, re.IGNORECASE)
    data['Ref'] = ref.group(1) if ref else ""

    sold = re.search(r'SOLD TO/VENDIDO A:(.*?)(?=SHIP TO|124829|\d{2}/\d{2})', full_text, re.DOTALL)
    data['Vendido A'] = clean_text(sold.group(1)) if sold else ""

    ship = re.search(r'SHIP TO/EMBARCADO A:(.*?)(?=PAYMENT|DUE DATE|PAGE)', full_text, re.DOTALL)
    data['Embarcado A'] = clean_text(ship.group(1)) if ship else ""
    
    return data

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_file = st.file_uploader("Sube Factura (PDF)", type=["pdf"])

if uploaded_file is not None:
    if st.button("🚀 PROCESAR"):
        
        with st.status("Analizando...", expanded=True) as status:
            try:
                # 1. Convertir
                images = convert_from_bytes(uploaded_file.read(), dpi=250)
                target_img = images[0]
                
                # 2. Header
                full_text = pytesseract.image_to_string(target_img, lang='spa')
                header = extract_header_robust(full_text)
                
                # 3. Items
                items = extract_items_fixed(target_img)
                
                status.update(label="¡Listo!", state="complete")
                
                # --- RESULTADOS ---
                c1, c2, c3 = st.columns(3)
                c1.success(f"Factura: {header.get('Factura')}")
                c2.info(f"Orden: {header.get('Orden')}")
                
                if items:
                    c3.success(f"Items Detectados: {len(items)}")
                    df = pd.DataFrame(items)
                    st.dataframe(df, use_container_width=True)
                    
                    # Excel
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        pd.DataFrame([header]).to_excel(writer, sheet_name="General", index=False)
                        df.to_excel(writer, sheet_name="Items", index=False)
                        writer.sheets['Items'].set_column('B:B', 70)
                        
                    st.download_button("📥 Descargar Excel", buffer.getvalue(), "factura_regal.xlsx")
                else:
                    st.error("⚠️ No se encontraron items.")
                    st.write("Debug: El sistema buscó cantidades en el primer 15% del ancho de la página.")
                    
            except Exception as e:
                st.error(f"Error técnico: {e}")
