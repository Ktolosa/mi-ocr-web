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
st.set_page_config(page_title="Regal OCR - Por Coordenadas", layout="wide")
st.title("🎯 Extractor Regal Trading - Por Zonas Exactas")

if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# ==============================================================================
# 📍 MAPA DE COORDENADAS (AJUSTA ESTOS NÚMEROS SEGÚN TU IMAGEN)
# ==============================================================================
# Formato: (Izquierda%, Arriba%, Derecha%, Abajo%)
# Ejemplo: (0.0, 0.0, 0.5, 0.1) es la mitad izquierda superior.

ZONES = {
    # --- ENCABEZADO DERECHO ---
    "Factura":    (0.70, 0.05, 0.95, 0.15),  # Donde está el # 297107
    "Fecha":      (0.70, 0.18, 0.95, 0.23),  # Donde dice AUG 07
    "Orden":      (0.70, 0.25, 0.95, 0.29),  # Donde dice ORDER 173900
    "Ref":        (0.70, 0.20, 0.95, 0.24),  # Donde dice REF
    
    # --- DIRECCIONES (Cajas del medio) ---
    "Vendido A":  (0.01, 0.20, 0.49, 0.38),  # Caja Izquierda
    "Embarcado A":(0.51, 0.20, 0.99, 0.38)   # Caja Derecha
}

# --- COLUMNAS DE LA TABLA (Solo posición X) ---
TABLE_COLS = {
    "QTY_END": 0.13,      # Fin de la columna Cantidad
    "DESC_START": 0.13,   # Inicio Descripción
    "DESC_END": 0.58,     # Fin Descripción / Inicio UPC
    "UPC_END": 0.73,      # Fin UPC / Inicio Precio
    "PRICE_END": 0.88     # Fin Precio / Inicio Total
}

# ==========================================
# 🛠️ MOTORES DE EXTRACCIÓN
# ==========================================

def crop_and_extract(image, coords, config='--psm 6'):
    """Recorta un rectángulo y lee el texto dentro."""
    w, h = image.size
    left = w * coords[0]
    top = h * coords[1]
    right = w * coords[2]
    bottom = h * coords[3]
    
    cropped = image.crop((left, top, right, bottom))
    text = pytesseract.image_to_string(cropped, lang='spa', config=config)
    return text.strip().replace('\n', ' ')

def extract_header_zones(image):
    """Extrae datos fijos basados en las ZONES configuradas arriba."""
    data = {}
    
    # Extraer texto crudo de cada zona
    raw_inv = crop_and_extract(image, ZONES["Factura"])
    raw_date = crop_and_extract(image, ZONES["Fecha"])
    raw_ord = crop_and_extract(image, ZONES["Orden"])
    raw_ref = crop_and_extract(image, ZONES["Ref"])
    
    # Limpieza con Regex (Por si se cuela la etiqueta "DATE:")
    # Factura
    m_inv = re.search(r'(\d{6})', raw_inv)
    data['Factura'] = m_inv.group(1) if m_inv else raw_inv
    
    # Fecha
    m_date = re.search(r'([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})', raw_date)
    data['Fecha'] = m_date.group(1) if m_date else raw_date.replace("DATE/FECHA", "").strip()
    
    # Orden
    m_ord = re.search(r'(\d{4,})', raw_ord)
    data['Orden'] = m_ord.group(1) if m_ord else raw_ord.replace("ORDER/ORDEN", "").strip()

    # Ref
    data['Ref'] = raw_ref.replace("FILE/REF", "").replace(":", "").strip()

    # Direcciones (Texto completo del bloque)
    data['Vendido A'] = crop_and_extract(image, ZONES["Vendido A"])
    data['Embarcado A'] = crop_and_extract(image, ZONES["Embarcado A"])
    
    return data

def extract_table_rows(image):
    """Detecta filas basándose en la columna Cantidad y corta horizontalmente."""
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    # 1. ENCONTRAR FILAS (Buscando números en la zona de Cantidad)
    anchors = []
    limit_qty_px = w * TABLE_COLS["QTY_END"]
    
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        
        # Filtro de zona vertical (cuerpo de factura)
        if cy < h * 0.35 or cy > h * 0.85: continue
        
        # Si está a la izquierda y es número
        if cx < limit_qty_px and re.match(r'^\d+$', text):
            anchors.append({'y': cy, 'qty': text})
            
    if not anchors: return []

    # 2. PROCESAR CADA FILA
    items = []
    
    # Límites horizontales en pixeles
    x_desc_start = w * TABLE_COLS["DESC_START"]
    x_desc_end = w * TABLE_COLS["DESC_END"]
    x_upc_end = w * TABLE_COLS["UPC_END"]
    x_price_end = w * TABLE_COLS["PRICE_END"]
    
    for idx, anchor in enumerate(anchors):
        # Definir altura de la fila
        # Miramos 25px arriba para capturar negritas, y hasta la siguiente fila abajo
        y_start = anchor['y'] - 25
        
        if idx + 1 < len(anchors):
            y_end = anchors[idx+1]['y'] - 5
        else:
            y_end = anchor['y'] + 150 # Última fila
            
        # Recortamos la franja horizontal completa de esta fila
        # (left, top, right, bottom)
        row_img = image.crop((0, y_start, w, y_end))
        
        # Ahora leemos TODA la fila y clasificamos palabras por su posición X relativa
        row_data = pytesseract.image_to_data(row_img, output_type=Output.DICT, lang='spa', config='--psm 6')
        n_row = len(row_data['text'])
        
        desc_parts = []
        upc_parts = []
        unit_parts = []
        total_parts = []
        
        for i in range(n_row):
            word = row_data['text'][i].strip()
            if not word: continue
            
            # Coordenada X dentro de la fila
            wx = row_data['left'][i]
            
            # Clasificar
            if x_desc_start < wx < x_desc_end:
                desc_parts.append(word)
            elif x_desc_end < wx < x_upc_end:
                if len(word) > 2: upc_parts.append(word)
            elif x_upc_end < wx < x_price_end:
                if re.match(r'[\d,]+\.\d{2}', word): unit_parts.append(word)
            elif wx > x_price_end:
                if re.match(r'[\d,]+\.\d{2}', word): total_parts.append(word)
                
        items.append({
            "Cantidad": anchor['qty'],
            "Descripción": " ".join(desc_parts),
            "UPC": " ".join(upc_parts),
            "Precio": unit_parts[0] if unit_parts else "",
            "Total": total_parts[0] if total_parts else ""
        })
        
    return items

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_file = st.file_uploader("Sube Factura (PDF)", type=["pdf"])

if uploaded_file is not None:
    # Cargar imagen en alta calidad
    images = convert_from_bytes(uploaded_file.read(), dpi=250)
    target_img = images[0]
    w, h = target_img.size
    
    # --- PESTAÑAS ---
    tab_run, tab_debug = st.tabs(["🚀 Extracción", "👁️ Verificación Visual"])
    
    with tab_debug:
        st.write("Verifica que las cajas (Rojo) y líneas (Azul) coincidan con tus datos.")
        
        # Dibujar sobre la imagen
        debug_img = target_img.copy()
        draw = ImageDraw.Draw(debug_img)
        
        # 1. Dibujar Zonas de Encabezado (Cajas Rojas)
        for name, coords in ZONES.items():
            left, top, right, bottom = w*coords[0], h*coords[1], w*coords[2], h*coords[3]
            draw.rectangle([left, top, right, bottom], outline="red", width=3)
            draw.text((left, top), name, fill="red")
            
        # 2. Dibujar Columnas de Tabla (Líneas Azules)
        for col_name, pct in TABLE_COLS.items():
            x = w * pct
            draw.line([(x, h*0.35), (x, h*0.85)], fill="blue", width=3)
            
        st.image(debug_img, use_column_width=True)
        st.caption("Si las cajas rojas no cubren el texto, edita el diccionario 'ZONES' en el código (Líneas 24-34).")

    with tab_run:
        if st.button("Extraer Datos"):
            with st.spinner("Recortando y leyendo zonas..."):
                try:
                    # 1. Header
                    header = extract_header_zones(target_img)
                    
                    # 2. Items
                    items = extract_table_rows(target_img)
                    
                    # Resultados
                    c1, c2, c3 = st.columns(3)
                    c1.success(f"Factura: {header['Factura']}")
                    c2.info(f"Orden: {header['Orden']}")
                    c3.warning(f"Items: {len(items)}")
                    
                    st.write("**Direcciones:**")
                    d1, d2 = st.columns(2)
                    d1.text_area("Vendido A", header['Vendido A'], height=100)
                    d2.text_area("Embarcado A", header['Embarcado A'], height=100)
                    
                    if items:
                        df = pd.DataFrame(items)
                        st.dataframe(df, use_container_width=True)
                        
                        # Excel
                        buffer = io.BytesIO()
                        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                            pd.DataFrame([header]).to_excel(writer, sheet_name="General", index=False)
                            df.to_excel(writer, sheet_name="Items", index=False)
                            writer.sheets['Items'].set_column('B:B', 60)
                        
                        st.download_button("📥 Excel Final", buffer.getvalue(), "regal_zones.xlsx")
                    else:
                        st.error("No se detectaron items. Revisa la pestaña 'Verificación Visual'.")
                        
                except Exception as e:
                    st.error(f"Error: {e}")
