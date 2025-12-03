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
st.set_page_config(page_title="Regal OCR V5 (Alta Definición)", layout="wide")
st.title("📄 Extractor Regal Trading - V5 (Alta Resolución)")

if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# ==========================================
# 🛠️ UTILIDADES ROBUSTAS
# ==========================================

def clean_text(text):
    if not text: return ""
    # Quitar caracteres extraños al inicio/final pero mantener saltos internos
    return text.strip()

def safe_regex(pattern, text, group=1):
    try:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(group).strip()
    except:
        pass
    return ""

def preprocess_image_area(image, coords):
    """Recorta una zona. Coords: (left_pct, top_pct, right_pct, bottom_pct)"""
    w, h = image.size
    left = w * coords[0]
    top = h * coords[1]
    right = w * coords[2]
    bottom = h * coords[3]
    return image.crop((left, top, right, bottom))

# ==========================================
# 🧠 1. ENCABEZADO (HÍBRIDO: ZONA + FULL TEXT)
# ==========================================

def extract_header_v5(image, full_text_fallback):
    data = {}
    w, h = image.size
    
    # --- A. FACTURA # ---
    # Intento 1: Zona Superior Derecha (Donde suele estar)
    # Ampliamos la zona para asegurar captura
    roi_inv = preprocess_image_area(image, (0.50, 0.0, 1.0, 0.20)) 
    txt_inv = pytesseract.image_to_string(roi_inv, lang='spa', config='--psm 6')
    
    inv_match = re.search(r'(?:#|No\.|297107)\s*(\d{6})', txt_inv)
    if not inv_match:
        # Intento 2: Buscar en todo el texto
        inv_match = re.search(r'(?:COMMERCIAL INVOICE|FACTURA).*?#\s*(\d{6})', full_text_fallback, re.DOTALL)
        if not inv_match:
             inv_match = re.search(r'#\s*(\d{6})\s+[A-Z]', full_text_fallback)
             
    data['Factura'] = inv_match.group(1) if inv_match else "No encontrado"

    # --- B. DATOS LOGÍSTICOS (ORDEN, FECHA) ---
    # Zona: Mitad derecha, parte superior media
    roi_log = preprocess_image_area(image, (0.50, 0.10, 1.0, 0.45))
    txt_log = pytesseract.image_to_string(roi_log, lang='spa', config='--psm 6')
    
    # 1. ORDEN (Patrón robusto)
    ord_match = re.search(r'ORDEN\s*#?\s*[:.,]?\s*(\d+)', txt_log, re.IGNORECASE)
    if not ord_match: # Fallback
        ord_match = re.search(r'ORDEN\s*#?\s*[:.,]?\s*(\d+)', full_text_fallback, re.IGNORECASE)
    data['Orden'] = ord_match.group(1) if ord_match else ""

    # 2. FECHAS
    date_match = re.search(r'FECHA\s*[:.,]?\s*([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})', txt_log, re.IGNORECASE)
    if not date_match:
        date_match = re.search(r'FECHA\s*[:.,]?\s*([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})', full_text_fallback, re.IGNORECASE)
    data['Fecha'] = date_match.group(1) if date_match else ""

    # 3. REF / BL
    ref_match = re.search(r'REF\s*[:.,]?\s*([A-Z0-9]+)', txt_log)
    data['Ref'] = ref_match.group(1) if ref_match else ""
    
    bl_match = re.search(r'B/L#\s*[:.,]?\s*([A-Z0-9]+)', txt_log)
    data['BL'] = bl_match.group(1) if bl_match else ""

    # --- C. DIRECCIONES (ZONAS ESTRICTAS) ---
    # Vendido A (Cuadrante Izquierdo)
    roi_sold = preprocess_image_area(image, (0.01, 0.18, 0.50, 0.40))
    txt_sold = pytesseract.image_to_string(roi_sold, lang='spa', config='--psm 6')
    # Limpieza: Quitamos títulos
    txt_sold = re.sub(r'SOLD TO/VENDIDO A:?', '', txt_sold, flags=re.IGNORECASE).strip()
    data['Vendido A'] = txt_sold

    # Embarcado A (Cuadrante Derecho)
    roi_ship = preprocess_image_area(image, (0.50, 0.18, 0.99, 0.40))
    txt_ship = pytesseract.image_to_string(roi_ship, lang='spa', config='--psm 6')
    txt_ship = re.sub(r'SHIP TO/EMBARCADO A:?', '', txt_ship, flags=re.IGNORECASE).strip()
    data['Embarcado A'] = txt_ship

    return data, roi_inv, roi_log, roi_sold # Devolvemos imágenes para debug

# ==========================================
# 🧠 2. ITEMS (CON ESCANEO DE ALTA PRECISIÓN)
# ==========================================

def extract_items_v5(image):
    # Usamos image_to_data para tener coordenadas
    # IMPORTANTE: Al subir DPI, las coordenadas cambian, pero los % se mantienen
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    # LÍMITES HORIZONTALES (Ajustados)
    X_QTY_END = w * 0.12     
    X_DESC_START = w * 0.12  
    X_DESC_END = w * 0.55    # Fin de descripción
    X_UPC_START = w * 0.58   # Inicio UPC (dejamos un hueco)
    X_PRICE_START = w * 0.73 
    
    # 1. ENCONTRAR FILAS (ANCLAS)
    anchors = []
    
    start_y = h * 0.35 # Ignorar encabezado superior
    end_y = h * 0.85   # Ignorar pie de página
    
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        
        if cy < start_y or cy > end_y: continue
        
        # Si es un número entero y está pegado a la izquierda
        if cx < X_QTY_END and re.match(r'^\d+$', text):
            anchors.append({'y': cy, 'qty': text})

    if not anchors: return []

    # 2. CAPTURAR DATOS ENTRE ANCLAS
    items = []
    for idx, anchor in enumerate(anchors):
        # Definir franja vertical
        y_top = anchor['y'] - 25 # Miramos más arriba para capturar negritas
        
        if idx + 1 < len(anchors):
            y_bottom = anchors[idx+1]['y'] - 10
        else:
            y_bottom = anchor['y'] + 200 # Margen generoso para el último item
            
        desc_words = []
        upc_words = []
        unit_words = []
        total_words = []
        
        for i in range(n_boxes):
            word = d['text'][i].strip()
            if not word: continue
            wx, wy = d['left'][i], d['top'][i]
            
            # Si cae en la franja vertical
            if y_top <= wy < y_bottom:
                # Clasificar por columna X
                if X_DESC_START < wx < X_DESC_END:
                    desc_words.append((wy, wx, word))
                elif X_UPC_START < wx < X_PRICE_START:
                    if len(word) > 2: upc_words.append(word)
                elif X_PRICE_START < wx < (w * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word): unit_words.append(word)
                elif wx > (w * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word): total_words.append(word)

        # Reconstruir descripción
        desc_words.sort(key=lambda k: (k[0], k[1])) # Ordenar Y luego X
        full_desc = " ".join([word[2] for word in desc_words])
        
        items.append({
            "Cantidad": anchor['qty'],
            "Descripción": full_desc,
            "UPC": " ".join(upc_words),
            "Unitario": unit_words[0] if unit_words else "",
            "Total": total_words[0] if total_words else ""
        })
        
    return items

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_file = st.file_uploader("Sube Factura (PDF)", type=["pdf"])

if uploaded_file is not None:
    if st.button("🚀 Extraer Datos (Alta Definición)"):
        
        with st.status("Procesando...", expanded=True) as status:
            try:
                # 1. CONVERTIR A ALTA RESOLUCIÓN (CLAVE PARA QUE NO FALLE)
                # dpi=300 hace que la imagen sea grande y nítida
                images = convert_from_bytes(uploaded_file.read(), dpi=300)
                target_img = images[0]
                
                # Texto completo de respaldo
                full_raw = pytesseract.image_to_string(target_img, lang='spa')
                
                # 2. Extracción
                header, img_inv, img_log, img_sold = extract_header_v5(target_img, full_raw)
                items = extract_items_v5(target_img)
                
                status.update(label="¡Listo!", state="complete")
                
                # --- PESTAÑAS DE RESULTADOS ---
                tab1, tab2, tab3 = st.tabs(["📊 Resultados", "📦 Items", "👀 Depuración Visual"])
                
                with tab1:
                    st.subheader(f"Factura: {header.get('Factura')}")
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Orden", header.get('Orden'))
                    c2.metric("Fecha", header.get('Fecha'))
                    c3.metric("Ref", header.get('Ref'))
                    
                    st.info(f"**Vendido A:** {header.get('Vendido A')}")
                    st.info(f"**Embarcado A:** {header.get('Embarcado A')}")
                
                with tab2:
                    if items:
                        df = pd.DataFrame(items)
                        st.dataframe(df, use_container_width=True)
                        
                        # Excel
                        buffer = io.BytesIO()
                        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                            pd.DataFrame([header]).to_excel(writer, sheet_name="General", index=False)
                            df.to_excel(writer, sheet_name="Items", index=False)
                            writer.sheets['Items'].set_column('B:B', 60)
                        
                        st.download_button("📥 Excel", buffer.getvalue(), "factura.xlsx")
                    else:
                        st.warning("No se encontraron items. Revisa la pestaña 'Depuración Visual'.")

                with tab3:
                    st.write("Aquí es donde el robot está buscando los datos. Verifica si corta el texto.")
                    c_dbg1, c_dbg2, c_dbg3 = st.columns(3)
                    c_dbg1.image(img_inv, caption="Zona Factura")
                    c_dbg2.image(img_log, caption="Zona Logística")
                    c_dbg3.image(img_sold, caption="Zona Dirección")
                    
                    # Dibujar líneas de columnas sobre la imagen original
                    draw = ImageDraw.Draw(target_img)
                    w, h = target_img.size
                    # Líneas verticales donde cortamos columnas
                    for x_pct in [0.12, 0.55, 0.58, 0.73, 0.88]:
                        draw.line([(w*x_pct, h*0.35), (w*x_pct, h*0.85)], fill="red", width=5)
                    
                    st.image(target_img, caption="Líneas rojas = División de columnas que ve el robot", use_column_width=True)

            except Exception as e:
                st.error(f"Error crítico: {e}")
