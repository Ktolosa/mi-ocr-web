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
st.set_page_config(page_title="Regal OCR Final", layout="wide")
st.title("📄 Extractor Regal Trading - Versión Final")

# Verificar instalación de Tesseract
if not shutil.which("tesseract"):
    st.error("❌ Error Crítico: Tesseract no está instalado en el servidor. Revisa packages.txt")
    st.stop()

# ==========================================
# 🔧 CALIBRACIÓN (Con claves únicas para evitar error DuplicateId)
# ==========================================
st.sidebar.header("🎚️ Ajuste de Columnas")
st.sidebar.info("Si los datos salen vacíos, ajusta estos controles.")

# NOTA: Agregué el parámetro 'key' para evitar el error StreamlitDuplicateElementId
qty_slider = st.sidebar.slider("Fin Cantidad (Azul)", 5, 25, 13, key="qty_slider_key")
desc_slider = st.sidebar.slider("Fin Descripción (Rojo)", 40, 70, 56, key="desc_slider_key")
upc_slider = st.sidebar.slider("Inicio UPC (Verde)", 50, 75, 59, key="upc_slider_key")
price_slider = st.sidebar.slider("Inicio Precio (Naranja)", 65, 85, 74, key="price_slider_key")

# Configuración activa
CFG = {
    'QTY_END': qty_slider / 100,
    'DESC_END': desc_slider / 100,
    'UPC_START': upc_slider / 100,
    'PRICE_START': price_slider / 100
}

# ==========================================
# 🧠 LÓGICA DE TEXTO (ENCABEZADO)
# ==========================================
def clean_text(text):
    return text.replace('\n', ' ').strip()

def extract_header(full_text):
    """Busca en todo el texto sin importar posición."""
    data = {}
    
    # FACTURA (Prioridad al número de 6 dígitos)
    inv = re.search(r'(?:#|No\.|297107)\s*(\d{6})', full_text)
    if not inv: inv = re.search(r'#\s*(\d{6})', full_text)
    data['Factura'] = inv.group(1) if inv else ""

    # FECHA
    date = re.search(r'(?:DATE|FECHA)\s*[:.,]?\s*([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})', full_text, re.IGNORECASE)
    data['Fecha'] = date.group(1) if date else ""

    # ORDEN
    orden = re.search(r'(?:ORDER|ORDEN)\s*#?\s*[:.,]?\s*(\d+)', full_text, re.IGNORECASE)
    data['Orden'] = orden.group(1) if orden else ""

    # REF / BL
    ref = re.search(r'(?:FILE|REF)\s*[:.,]?\s*([A-Z0-9]+)', full_text, re.IGNORECASE)
    data['Ref'] = ref.group(1) if ref else ""

    # DIRECCIONES
    sold = re.search(r'SOLD TO/VENDIDO A:(.*?)(?=SHIP TO|124829|\d{2}/\d{2})', full_text, re.DOTALL)
    data['Vendido A'] = clean_text(sold.group(1)) if sold else ""

    ship = re.search(r'SHIP TO/EMBARCADO A:(.*?)(?=PAYMENT|DUE DATE|PAGE)', full_text, re.DOTALL)
    data['Embarcado A'] = clean_text(ship.group(1)) if ship else ""
    
    return data

# ==========================================
# 🧠 LÓGICA DE ITEMS (ROBUSTA)
# ==========================================
def extract_items(image, cfg):
    # Obtener datos crudos
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    # Límites en pixeles
    X_QTY = w * cfg['QTY_END']
    X_DESC = w * cfg['DESC_END']
    X_UPC = w * cfg['UPC_START']
    X_PRICE = w * cfg['PRICE_START']

    # 1. ENCONTRAR FILAS (ANCLAS)
    anchors = []
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        
        # Ignorar zonas muy arriba o abajo
        if cy < h * 0.32: continue 
        if cy > h * 0.85: continue
        
        # Condición: Está a la izquierda Y contiene dígitos
        if cx < X_QTY and re.search(r'\d+', text): 
            if len(text) < 5: # Filtro de ruido
                anchors.append({'y': cy, 'qty': text})

    if not anchors: return [], []

    # 2. LEER ENTRE FILAS
    items = []
    for idx, anchor in enumerate(anchors):
        # Definir techo y piso de la fila
        y_top = anchor['y'] - 20 
        
        if idx + 1 < len(anchors):
            y_bottom = anchors[idx+1]['y'] - 5
        else:
            y_bottom = anchor['y'] + 150
            
        desc_parts = []
        upc_parts = []
        unit_parts = []
        total_parts = []
        
        for i in range(n_boxes):
            word = d['text'][i].strip()
            if not word: continue
            wx, wy = d['left'][i], d['top'][i]
            
            # Si cae en la franja vertical
            if y_top <= wy < y_bottom:
                # Clasificar por columna horizontal
                if X_QTY < wx < X_DESC:
                    desc_parts.append((wy, wx, word))
                elif X_UPC < wx < X_PRICE:
                    if len(word) > 3: upc_parts.append(word)
                elif X_PRICE < wx < (w * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word): unit_parts.append(word)
                elif wx > (w * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word): total_parts.append(word)

        # Ordenar descripción
        desc_parts.sort(key=lambda k: (k[0], k[1]))
        full_desc = " ".join([x[2] for x in desc_parts])
        
        items.append({
            "Cantidad": anchor['qty'],
            "Descripción": full_desc,
            "UPC": " ".join(upc_parts),
            "Precio": unit_parts[0] if unit_parts else "",
            "Total": total_parts[0] if total_parts else ""
        })
        
    return items, anchors

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_file = st.file_uploader("Sube Factura (PDF)", type=["pdf"], key="file_uploader_key")

if uploaded_file is not None:
    # Previsualización
    images = convert_from_bytes(uploaded_file.read(), dpi=200)
    target_img = images[0]
    
    # Dibujar líneas guía
    preview = target_img.copy()
    draw = ImageDraw.Draw(preview)
    w, h = preview.size
    
    draw.line([(w*CFG['QTY_END'], 0), (w*CFG['QTY_END'], h)], fill="blue", width=4)
    draw.line([(w*CFG['DESC_END'], 0), (w*CFG['DESC_END'], h)], fill="red", width=4)
    draw.line([(w*CFG['UPC_START'], 0), (w*CFG['UPC_START'], h)], fill="green", width=4)
    draw.line([(w*CFG['PRICE_START'], 0), (w*CFG['PRICE_START'], h)], fill="orange", width=4)
    
    st.image(preview, caption="Asegúrate que la línea AZUL no corte los números de cantidad.", use_column_width=True)
    
    if st.button("🚀 PROCESAR DOCUMENTO", key="process_btn"):
        with st.status("Analizando...", expanded=True) as status:
            try:
                # 1. Header
                full_txt = pytesseract.image_to_string(target_img, lang='spa')
                header = extract_header(full_txt)
                
                # 2. Items
                items, anchors = extract_items(target_img, CFG)
                
                status.update(label="¡Procesado!", state="complete")
                
                # --- RESULTADOS ---
                c1, c2, c3 = st.columns(3)
                c1.success(f"Factura: {header.get('Factura')}")
                c2.info(f"Orden: {header.get('Orden')}")
                c3.warning(f"Items: {len(items)}")
                
                # Depuración: Mostrar dónde detectó filas
                if anchors:
                    dbg_img = preview.copy()
                    dbg_draw = ImageDraw.Draw(dbg_img)
                    for anc in anchors:
                        # Dibuja círculo verde en cada cantidad detectada
                        dbg_draw.ellipse([(10, anc['y']), (40, anc['y']+30)], fill="green", outline="black")
                    with st.expander("🕵️ Ver filas detectadas (Puntos Verdes)"):
                        st.image(dbg_img, caption="Puntos verdes = Filas encontradas", use_column_width=True)
                
                if items:
                    df = pd.DataFrame(items)
                    st.dataframe(df, use_container_width=True)
                    
                    # Excel
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        pd.DataFrame([header]).to_excel(writer, sheet_name="General", index=False)
                        df.to_excel(writer, sheet_name="Items", index=False)
                        writer.sheets['Items'].set_column('B:B', 60)
                        
                    st.download_button("📥 Descargar Excel", buffer.getvalue(), "factura.xlsx", key="download_btn")
                else:
                    st.error("⚠️ No se encontraron items. Prueba moviendo el slider AZUL a la derecha.")
                    
            except Exception as e:
                st.error(f"Error técnico: {e}")
