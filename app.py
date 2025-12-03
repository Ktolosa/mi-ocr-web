import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil
import re
from PIL import Image, ImageDraw

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Regal OCR V6 (Rescue)", layout="wide")
st.title("🚑 Extractor Regal Trading - V6 (Recuperación)")

if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# ==========================================
# 🔧 CALIBRACIÓN (Valores aproximados de tus fotos)
# ==========================================
# Puedes mover estos valores si en la previsualización no encajan
DEFAULT_CFG = {
    'QTY_END': 0.13,      # Fin Cantidad (Linea AZUL)
    'DESC_END': 0.56,     # Fin Descripción (Linea ROJA)
    'UPC_START': 0.59,    # Inicio UPC (Linea VERDE)
    'PRICE_START': 0.74   # Inicio Precio (Linea NARANJA)
}

# ==========================================
# 🧠 LÓGICA DE TEXTO (ENCABEZADO ROBUSTO)
# ==========================================
def clean_text(text):
    return text.replace('\n', ' ').strip()

def extract_header_force(full_text):
    """Busca en todo el texto sin importar posición."""
    data = {}
    
    # FACTURA (Busca # seguido de 6 dígitos)
    inv = re.search(r'(?:#|No\.|297107)\s*(\d{6})', full_text)
    if not inv: inv = re.search(r'#\s*(\d{6})', full_text)
    data['Factura'] = inv.group(1) if inv else ""

    # FECHA
    date = re.search(r'(?:DATE|FECHA)\s*[:.,]?\s*([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})', full_text, re.IGNORECASE)
    data['Fecha'] = date.group(1) if date else ""

    # ORDEN (Busca la palabra ORDEN o ORDER)
    orden = re.search(r'(?:ORDER|ORDEN)\s*#?\s*[:.,]?\s*(\d+)', full_text, re.IGNORECASE)
    data['Orden'] = orden.group(1) if orden else ""

    # REF
    ref = re.search(r'(?:FILE|REF)\s*[:.,]?\s*([A-Z0-9]+)', full_text, re.IGNORECASE)
    data['Ref'] = ref.group(1) if ref else ""

    # DIRECCIONES (Usando delimitadores de texto)
    sold = re.search(r'SOLD TO/VENDIDO A:(.*?)(?=SHIP TO|124829|\d{2}/\d{2})', full_text, re.DOTALL)
    data['Vendido A'] = clean_text(sold.group(1)) if sold else ""

    ship = re.search(r'SHIP TO/EMBARCADO A:(.*?)(?=PAYMENT|DUE DATE|PAGE)', full_text, re.DOTALL)
    data['Embarcado A'] = clean_text(ship.group(1)) if ship else ""
    
    return data

# ==========================================
# 🧠 LÓGICA DE ITEMS (TOLERANTE A FALLOS)
# ==========================================
def extract_items_calibrated(image, cfg):
    # 1. Obtener datos crudos
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    # Límites en pixeles
    X_QTY = w * cfg['QTY_END']
    X_DESC = w * cfg['DESC_END']
    X_UPC = w * cfg['UPC_START']
    X_PRICE = w * cfg['PRICE_START']

    # 2. ENCONTRAR FILAS (ANCLAS)
    # Buscamos cualquier cosa que parezca un número en la columna izquierda
    anchors = []
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        
        # Ignorar encabezado y pie (ajusta estos % si corta items)
        if cy < h * 0.32: continue 
        if cy > h * 0.85: continue
        
        # Condición relajada: Está a la izquierda Y contiene dígitos
        # Antes era re.match(r'^\d+$') -> Muy estricto
        if cx < X_QTY and re.search(r'\d+', text): 
            # Evitar capturar palabras como "CANTIDAD" o basura pequeña
            if len(text) < 5: 
                anchors.append({'y': cy, 'qty': text, 'h': d['height'][i]})

    if not anchors: return []

    # 3. LEER ENTRE FILAS
    items = []
    for idx, anchor in enumerate(anchors):
        # Definir techo y piso de la fila
        y_top = anchor['y'] - 15 
        
        if idx + 1 < len(anchors):
            y_bottom = anchors[idx+1]['y'] - 5
        else:
            y_bottom = anchor['y'] + 150 # Último item
            
        # Contenedores
        desc_raw = []
        upc_raw = []
        unit_raw = []
        total_raw = []
        
        for i in range(n_boxes):
            word = d['text'][i].strip()
            if not word: continue
            wx, wy = d['left'][i], d['top'][i]
            
            # Si cae en la franja vertical
            if y_top <= wy < y_bottom:
                
                # Clasificar por columna horizontal
                if X_QTY < wx < X_DESC:
                    desc_raw.append((wy, wx, word))
                elif X_UPC < wx < X_PRICE:
                    # Filtro UPC: al menos 3 caracteres para evitar ruido
                    if len(word) > 3: upc_raw.append(word)
                elif X_PRICE < wx < (w * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word): unit_raw.append(word)
                elif wx > (w * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word): total_raw.append(word)

        # Ordenar descripción (Lectura natural)
        desc_raw.sort(key=lambda k: (k[0], k[1]))
        full_desc = " ".join([x[2] for x in desc_raw])
        
        items.append({
            "Cantidad": anchor['qty'],
            "Descripción": full_desc,
            "UPC": " ".join(upc_raw),
            "Precio": unit_raw[0] if unit_raw else "",
            "Total": total_raw[0] if total_raw else ""
        })
        
    return items, anchors # Devolvemos anchors para dibujar debug

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================
st.sidebar.header("🎚️ Ajuste Fino")
qty_slider = st.sidebar.slider("Límite Cantidad (Azul)", 5, 25, int(DEFAULT_CFG['QTY_END']*100))
desc_slider = st.sidebar.slider("Límite Desc (Rojo)", 40, 70, int(DEFAULT_CFG['DESC_END']*100))
upc_slider = st.sidebar.slider("Inicio UPC (Verde)", 50, 75, int(DEFAULT_CFG['UPC_START']*100))
price_slider = st.sidebar.slider("Inicio Precio (Naranja)", 65, 85, int(DEFAULT_CFG['PRICE_START']*100))

# Configuración activa
ACTIVE_CFG = {
    'QTY_END': qty_slider / 100,
    'DESC_END': desc_slider / 100,
    'UPC_START': upc_slider / 100,
    'PRICE_START': price_slider / 100
}

uploaded_file = st.file_uploader("Sube Factura (PDF)", type=["pdf"])

if uploaded_file is not None:
    # 1. Convertir (DPI 200 es buen balance velocidad/precisión)
    images = convert_from_bytes(uploaded_file.read(), dpi=200)
    target_img = images[0]
    
    # 2. Dibujar previsualización
    preview = target_img.copy()
    draw = ImageDraw.Draw(preview)
    w, h = preview.size
    
    # Líneas de columnas
    draw.line([(w*ACTIVE_CFG['QTY_END'], 0), (w*ACTIVE_CFG['QTY_END'], h)], fill="blue", width=3)
    draw.line([(w*ACTIVE_CFG['DESC_END'], 0), (w*ACTIVE_CFG['DESC_END'], h)], fill="red", width=3)
    draw.line([(w*ACTIVE_CFG['UPC_START'], 0), (w*ACTIVE_CFG['UPC_START'], h)], fill="green", width=3)
    draw.line([(w*ACTIVE_CFG['PRICE_START'], 0), (w*ACTIVE_CFG['PRICE_START'], h)], fill="orange", width=3)
    
    st.image(preview, caption="Asegúrate que la línea AZUL no corte el número de cantidad", use_column_width=True)
    
    if st.button("🚀 EXTRAER DATOS AHORA"):
        with st.status("Analizando...", expanded=True) as status:
            try:
                # Header
                full_txt = pytesseract.image_to_string(target_img, lang='spa')
                header = extract_header_force(full_txt)
                
                # Items
                items, anchors = extract_items_calibrated(target_img, ACTIVE_CFG)
                
                status.update(label="¡Hecho!", state="complete")
                
                # --- RESULTADOS ---
                c1, c2, c3 = st.columns(3)
                c1.success(f"Factura: {header.get('Factura')}")
                c2.info(f"Orden: {header.get('Orden')}")
                c3.warning(f"Items encontrados: {len(items)}")
                
                # --- VISUALIZAR QUE DETECTÓ ---
                # Dibujamos cajas verdes donde encontró items para que veas si funcionó
                debug_img = preview.copy()
                draw_dbg = ImageDraw.Draw(debug_img)
                for anc in anchors:
                    # Dibuja un círculo verde en cada cantidad detectada
                    y = anc['y']
                    draw_dbg.ellipse([(10, y), (40, y+30)], fill="green", outline="green")
                
                with st.expander("🕵️ Ver qué filas detectó (Círculos Verdes)"):
                    st.image(debug_img, caption="Si ves círculos verdes a la izquierda, detectó la fila.", use_column_width=True)

                if items:
                    df = pd.DataFrame(items)
                    st.dataframe(df, use_container_width=True)
                    
                    # Excel
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        pd.DataFrame([header]).to_excel(writer, sheet_name="General", index=False)
                        df.to_excel(writer, sheet_name="Items", index=False)
                        writer.sheets['Items'].set_column('B:B', 50)
                    
                    st.download_button("📥 Descargar Excel", buffer.getvalue(), "reporte_regal.xlsx")
                else:
                    st.error("No se detectaron filas. Prueba moviendo la línea AZUL un poco a la derecha.")
                    
            except Exception as e:
                st.error(f"Error: {e}")
# ==========================================
# 🧠 LÓGICA DE ITEMS (TOLERANTE A FALLOS)
# ==========================================
def extract_items_calibrated(image, cfg):
    # 1. Obtener datos crudos
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    # Límites en pixeles
    X_QTY = w * cfg['QTY_END']
    X_DESC = w * cfg['DESC_END']
    X_UPC = w * cfg['UPC_START']
    X_PRICE = w * cfg['PRICE_START']

    # 2. ENCONTRAR FILAS (ANCLAS)
    # Buscamos cualquier cosa que parezca un número en la columna izquierda
    anchors = []
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        
        # Ignorar encabezado y pie (ajusta estos % si corta items)
        if cy < h * 0.32: continue 
        if cy > h * 0.85: continue
        
        # Condición relajada: Está a la izquierda Y contiene dígitos
        # Antes era re.match(r'^\d+$') -> Muy estricto
        if cx < X_QTY and re.search(r'\d+', text): 
            # Evitar capturar palabras como "CANTIDAD" o basura pequeña
            if len(text) < 5: 
                anchors.append({'y': cy, 'qty': text, 'h': d['height'][i]})

    if not anchors: return []

    # 3. LEER ENTRE FILAS
    items = []
    for idx, anchor in enumerate(anchors):
        # Definir techo y piso de la fila
        y_top = anchor['y'] - 15 
        
        if idx + 1 < len(anchors):
            y_bottom = anchors[idx+1]['y'] - 5
        else:
            y_bottom = anchor['y'] + 150 # Último item
            
        # Contenedores
        desc_raw = []
        upc_raw = []
        unit_raw = []
        total_raw = []
        
        for i in range(n_boxes):
            word = d['text'][i].strip()
            if not word: continue
            wx, wy = d['left'][i], d['top'][i]
            
            # Si cae en la franja vertical
            if y_top <= wy < y_bottom:
                
                # Clasificar por columna horizontal
                if X_QTY < wx < X_DESC:
                    desc_raw.append((wy, wx, word))
                elif X_UPC < wx < X_PRICE:
                    # Filtro UPC: al menos 3 caracteres para evitar ruido
                    if len(word) > 3: upc_raw.append(word)
                elif X_PRICE < wx < (w * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word): unit_raw.append(word)
                elif wx > (w * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word): total_raw.append(word)

        # Ordenar descripción (Lectura natural)
        desc_raw.sort(key=lambda k: (k[0], k[1]))
        full_desc = " ".join([x[2] for x in desc_raw])
        
        items.append({
            "Cantidad": anchor['qty'],
            "Descripción": full_desc,
            "UPC": " ".join(upc_raw),
            "Precio": unit_raw[0] if unit_raw else "",
            "Total": total_raw[0] if total_raw else ""
        })
        
    return items, anchors # Devolvemos anchors para dibujar debug

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================
st.sidebar.header("🎚️ Ajuste Fino")
qty_slider = st.sidebar.slider("Límite Cantidad (Azul)", 5, 25, int(DEFAULT_CFG['QTY_END']*100))
desc_slider = st.sidebar.slider("Límite Desc (Rojo)", 40, 70, int(DEFAULT_CFG['DESC_END']*100))
upc_slider = st.sidebar.slider("Inicio UPC (Verde)", 50, 75, int(DEFAULT_CFG['UPC_START']*100))
price_slider = st.sidebar.slider("Inicio Precio (Naranja)", 65, 85, int(DEFAULT_CFG['PRICE_START']*100))

# Configuración activa
ACTIVE_CFG = {
    'QTY_END': qty_slider / 100,
    'DESC_END': desc_slider / 100,
    'UPC_START': upc_slider / 100,
    'PRICE_START': price_slider / 100
}

uploaded_file = st.file_uploader("Sube Factura (PDF)", type=["pdf"])

if uploaded_file is not None:
    # 1. Convertir (DPI 200 es buen balance velocidad/precisión)
    images = convert_from_bytes(uploaded_file.read(), dpi=200)
    target_img = images[0]
    
    # 2. Dibujar previsualización
    preview = target_img.copy()
    draw = ImageDraw.Draw(preview)
    w, h = preview.size
    
    # Líneas de columnas
    draw.line([(w*ACTIVE_CFG['QTY_END'], 0), (w*ACTIVE_CFG['QTY_END'], h)], fill="blue", width=3)
    draw.line([(w*ACTIVE_CFG['DESC_END'], 0), (w*ACTIVE_CFG['DESC_END'], h)], fill="red", width=3)
    draw.line([(w*ACTIVE_CFG['UPC_START'], 0), (w*ACTIVE_CFG['UPC_START'], h)], fill="green", width=3)
    draw.line([(w*ACTIVE_CFG['PRICE_START'], 0), (w*ACTIVE_CFG['PRICE_START'], h)], fill="orange", width=3)
    
    st.image(preview, caption="Asegúrate que la línea AZUL no corte el número de cantidad", use_column_width=True)
    
    if st.button("🚀 EXTRAER DATOS AHORA"):
        with st.status("Analizando...", expanded=True) as status:
            try:
                # Header
                full_txt = pytesseract.image_to_string(target_img, lang='spa')
                header = extract_header_force(full_txt)
                
                # Items
                items, anchors = extract_items_calibrated(target_img, ACTIVE_CFG)
                
                status.update(label="¡Hecho!", state="complete")
                
                # --- RESULTADOS ---
                c1, c2, c3 = st.columns(3)
                c1.success(f"Factura: {header.get('Factura')}")
                c2.info(f"Orden: {header.get('Orden')}")
                c3.warning(f"Items encontrados: {len(items)}")
                
                # --- VISUALIZAR QUE DETECTÓ ---
                # Dibujamos cajas verdes donde encontró items para que veas si funcionó
                debug_img = preview.copy()
                draw_dbg = ImageDraw.Draw(debug_img)
                for anc in anchors:
                    # Dibuja un círculo verde en cada cantidad detectada
                    y = anc['y']
                    draw_dbg.ellipse([(10, y), (40, y+30)], fill="green", outline="green")
                
                with st.expander("🕵️ Ver qué filas detectó (Círculos Verdes)"):
                    st.image(debug_img, caption="Si ves círculos verdes a la izquierda, detectó la fila.", use_column_width=True)

                if items:
                    df = pd.DataFrame(items)
                    st.dataframe(df, use_container_width=True)
                    
                    # Excel
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        pd.DataFrame([header]).to_excel(writer, sheet_name="General", index=False)
                        df.to_excel(writer, sheet_name="Items", index=False)
                        writer.sheets['Items'].set_column('B:B', 50)
                    
                    st.download_button("📥 Descargar Excel", buffer.getvalue(), "reporte_regal.xlsx")
                else:
                    st.error("No se detectaron filas. Prueba moviendo la línea AZUL un poco a la derecha.")
                    
            except Exception as e:
                st.error(f"Error: {e}")

