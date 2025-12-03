import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil
import re
from PIL import Image

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Regal Trading OCR Final", layout="wide")
st.title("📄 Extractor Definitivo: Regal Trading")

if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# ==========================================
# 🛠️ HERRAMIENTAS DE VISIÓN (RECORTES)
# ==========================================

def extract_text_from_area(image, area_coords, config='--psm 6'):
    """
    Recorta una zona específica de la imagen y lee el texto solo ahí.
    area_coords: (left_percent, top_percent, right_percent, bottom_percent)
    """
    w, h = image.size
    left = w * area_coords[0]
    top = h * area_coords[1]
    right = w * area_coords[2]
    bottom = h * area_coords[3]
    
    # Recortar
    cropped_img = image.crop((left, top, right, bottom))
    
    # Leer
    text = pytesseract.image_to_string(cropped_img, lang='spa', config=config)
    return text.strip()

def clean_text(text):
    """Limpia saltos de línea extraños"""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    return " ".join(lines)

# ==========================================
# 🧠 1. EXTRACCIÓN DE ENCABEZADO (POR ZONAS)
# ==========================================

def extract_header_visual(image, full_text_fallback):
    data = {}
    
    # A. FACTURA (Zona Superior Derecha)
    # Buscamos en el rectángulo superior derecho donde suele estar el #
    # Coordenadas aprox: x(60%-100%), y(0%-15%)
    header_text = extract_text_from_area(image, (0.6, 0.0, 1.0, 0.20))
    
    # Buscar # Factura
    inv_match = re.search(r'(?:#|No\.|297107)\s*(\d{6})', header_text)
    if not inv_match:
         # Fallback al texto completo si el recorte falla
         inv_match = re.search(r'#\s*(\d{6})\s+[A-Z]', full_text_fallback)
    data['Factura'] = inv_match.group(1) if inv_match else ""

    # B. DATOS LOGÍSTICOS (Zona Derecha Media)
    # Recortamos la caja que tiene Fechas y Orden
    # Coordenadas aprox: x(60%-100%), y(15%-35%)
    logistics_text = extract_text_from_area(image, (0.6, 0.15, 1.0, 0.40))
    
    # Extraer datos de ese bloque limpio
    data['Fecha Emisión'] = ""
    date_match = re.search(r'DATE/FECHA\s*[:.,]?\s*([A-Za-z]{3}\s\d{1,2}[,.]?\s\d{4})', logistics_text, re.IGNORECASE)
    if date_match: data['Fecha Emisión'] = date_match.group(1)
    
    # Orden (Ahora debería salir bien porque no hay ruido alrededor)
    order_match = re.search(r'ORDER/ORDEN\s*#?\s*[:.,]?\s*(\d+)', logistics_text, re.IGNORECASE)
    data['Orden Compra'] = order_match.group(1) if order_match else ""
    
    ref_match = re.search(r'FILE/REF\s*[:.,]?\s*([A-Z0-9]+)', logistics_text, re.IGNORECASE)
    data['Referencia'] = ref_match.group(1) if ref_match else ""

    # C. DIRECCIONES (Recortes Específicos)
    
    # VENDIDO A (Izquierda, Mitad) -> x(0%-50%), y(20%-40%)
    sold_raw = extract_text_from_area(image, (0.02, 0.20, 0.50, 0.40))
    # Limpiar título
    sold_clean = re.sub(r'SOLD TO/VENDIDO A:?', '', sold_raw, flags=re.IGNORECASE)
    data['Vendido A'] = clean_text(sold_clean)

    # EMBARCADO A (Derecha, Mitad) -> x(50%-100%), y(20%-40%)
    ship_raw = extract_text_from_area(image, (0.50, 0.20, 0.98, 0.40))
    ship_clean = re.sub(r'SHIP TO/EMBARCADO A:?', '', ship_raw, flags=re.IGNORECASE)
    data['Embarcado A'] = clean_text(ship_clean)

    return data

# ==========================================
# 🧠 2. EXTRACCIÓN DE ITEMS (LÓGICA MEJORADA)
# ==========================================

def extract_items_super_robust(image):
    # Obtenemos datos detallados
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    # --- 1. DEFINIR COLUMNAS (Ajustadas para UPC) ---
    # Según tus imagenes:
    X_QTY_END = w * 0.12     # Cantidad termina al 12%
    X_DESC_START = w * 0.13  # Desc empieza al 13%
    X_DESC_END = w * 0.58    # Desc termina al 58% (Antes chocaba con UPC)
    X_UPC_START = w * 0.60   # UPC empieza al 60%
    X_UPC_END = w * 0.75     # UPC termina al 75%
    X_PRICE_START = w * 0.76 # Precio empieza
    
    # --- 2. ENCONTRAR FILAS (ANCLAS) ---
    rows = []
    
    # Buscamos números en la columna "Cantidad"
    for i in range(n_boxes):
        text = d['text'][i].strip()
        if not text: continue
        
        # Filtros de posición
        cx = d['left'][i]
        cy = d['top'][i]
        
        # Ignorar encabezados/pies
        if cy < h * 0.35: continue # Muy arriba
        if cy > h * 0.85: continue # Muy abajo
        
        # Si está a la izquierda y es un número puro
        if cx < X_QTY_END and re.match(r'^\d+$', text):
            rows.append({'y': cy, 'qty': text})
            
    if not rows: return []

    # --- 3. BARRIDO DE DATOS ---
    extracted_items = []
    
    for idx, row in enumerate(rows):
        # DEFINIR TECHO Y PISO DE LA FILA
        # Truco: Miramos 20px ARRIBA de la cantidad para capturar negritas superiores
        y_top = row['y'] - 20 
        
        # El piso es la siguiente cantidad, o el final de la página
        if idx + 1 < len(rows):
            y_bottom = rows[idx+1]['y'] - 10
        else:
            y_bottom = row['y'] + 150 # Margen fijo para el último item
            
        # Contenedores
        desc_words = []
        upc_words = []
        unit_words = []
        total_words = []
        
        # Recorrer TODAS las palabras y ver si caen en esta franja Y
        for i in range(n_boxes):
            word = d['text'][i].strip()
            if not word: continue
            
            wx = d['left'][i]
            wy = d['top'][i]
            
            # ¿Está dentro de la altura de este producto?
            if y_top <= wy < y_bottom:
                
                # Clasificar por Columna X
                if X_DESC_START < wx < X_DESC_END:
                    desc_words.append((wy, wx, word)) # Guardamos Y, X para ordenar
                    
                elif X_UPC_START < wx < X_UPC_END:
                    # Filtro UPC: ignorar simbolos raros
                    if len(word) > 2: upc_words.append(word)
                    
                elif X_UPC_END < wx < (w * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word): unit_words.append(word)
                    
                elif wx > (w * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word): total_words.append(word)

        # Ordenar descripción (Arriba->Abajo, Izq->Der)
        desc_words.sort(key=lambda k: (k[0], k[1]))
        full_desc = " ".join([w[2] for w in desc_words])
        
        # Armar Item
        extracted_items.append({
            "Cantidad": row['qty'],
            "Descripción": full_desc,
            "UPC/Ref": " ".join(upc_words),
            "Precio Unit.": unit_words[0] if unit_words else "",
            "Total": total_words[0] if total_words else ""
        })
        
    return extracted_items

# ==========================================
# 🖥️ INTERFAZ
# ==========================================

uploaded_file = st.file_uploader("Sube Factura Regal (PDF)", type=["pdf"])

if uploaded_file is not None:
    if st.button("🚀 Extraer Datos (V4 Final)"):
        with st.status("Aplicando visión por computadora...", expanded=True) as status:
            try:
                images = convert_from_bytes(uploaded_file.read())
                target_img = images[0]
                
                # 1. Encabezado por Zonas
                # Pasamos texto completo solo como respaldo
                full_raw_text = pytesseract.image_to_string(target_img, lang='spa', config='--psm 4')
                header = extract_header_visual(target_img, full_raw_text)
                
                # 2. Items por Bloques Verticales Ajustados
                items = extract_items_super_robust(target_img)
                
                status.update(label="¡Listo!", state="complete")
                
                # --- VISUALIZACIÓN ---
                st.subheader(f"Factura: {header.get('Factura', 'ND')}")
                
                c1, c2, c3 = st.columns(3)
                c1.metric("Orden #", header.get('Orden Compra', 'ND'))
                c2.metric("Fecha", header.get('Fecha Emisión', 'ND'))
                c3.metric("Ref", header.get('Referencia', 'ND'))
                
                with st.expander("📍 Direcciones Extraídas", expanded=True):
                    col_a, col_b = st.columns(2)
                    col_a.text_area("Vendido A:", header.get('Vendido A'), height=100)
                    col_b.text_area("Embarcado A:", header.get('Embarcado A'), height=100)
                
                st.divider()
                
                if items:
                    df = pd.DataFrame(items)
                    st.dataframe(df, use_container_width=True)
                    
                    # Excel
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        pd.DataFrame([header]).to_excel(writer, sheet_name="General", index=False)
                        df.to_excel(writer, sheet_name="Items", index=False)
                        writer.sheets['Items'].set_column('B:B', 60)
                        
                    st.download_button("📥 Descargar Excel", buffer.getvalue(), f"Regal_{header.get('Factura')}.xlsx")
                else:
                    st.warning("No se detectaron items. Revisa la calidad del PDF.")
                    
            except Exception as e:
                st.error(f"Error: {e}")
