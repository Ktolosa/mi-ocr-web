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
st.set_page_config(page_title="Regal OCR Calibrable", layout="wide")
st.title("🎚️ Extractor Regal Trading (Con Calibración)")

if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# ==========================================
# 🛠️ BARRA LATERAL DE CALIBRACIÓN
# ==========================================
st.sidebar.header("📏 Calibración de Columnas")
st.sidebar.info("Mueve los deslizadores hasta que las líneas rojas separen correctamente las columnas sin cortar texto.")

# Valores por defecto (en porcentaje del ancho 0-100)
# Ajusta estos valores iniciales si quieres
val_qty = st.sidebar.slider("Fin Columna Cantidad (%)", 5, 30, 13)
val_desc_end = st.sidebar.slider("Fin Columna Descripción (%)", 30, 70, 55)
val_upc_start = st.sidebar.slider("Inicio Columna UPC (%)", 40, 80, 58)
val_price_start = st.sidebar.slider("Inicio Columna Precio (%)", 60, 90, 75)

# Convertir a decimales para el código (ej: 13 -> 0.13)
CFG = {
    'QTY_END': val_qty / 100,
    'DESC_END': val_desc_end / 100,
    'UPC_START': val_upc_start / 100,
    'PRICE_START': val_price_start / 100
}

# ==========================================
# 🧠 LÓGICA DE ENCABEZADO (BÚSQUEDA EN TEXTO COMPLETO)
# ==========================================
# Ya no usamos zonas recortadas para el encabezado para evitar errores de posición.
# Buscamos en el texto completo de la mitad superior.

def extract_header_robust(full_text):
    data = {}
    
    # 1. FACTURA
    # Busca # seguido de 6 dígitos, ignorando saltos de línea
    inv_match = re.search(r'(?:#|No\.|297107)\s*(\d{6})', full_text)
    if not inv_match:
        inv_match = re.search(r'#\s*(\d{6})\s+[A-Z]', full_text)
    data['Factura'] = inv_match.group(1) if inv_match else ""

    # 2. FECHAS
    date_match = re.search(r'(?:DATE|FECHA)\s*[:.,]?\s*([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})', full_text, re.IGNORECASE)
    data['Fecha Emisión'] = date_match.group(1) if date_match else ""

    # 3. ORDEN
    # Busca "ORDEN" seguido de números
    ord_match = re.search(r'ORDEN\s*#?\s*[:.,]?\s*(\d+)', full_text, re.IGNORECASE)
    data['Orden'] = ord_match.group(1) if ord_match else ""

    # 4. DIRECCIONES (Usando delimitadores de texto, no coordenadas)
    # Vendido A
    sold_block = re.search(r'SOLD TO/VENDIDO A:(.*?)(?=SHIP TO|124829)', full_text, re.DOTALL)
    data['Vendido A'] = sold_block.group(1).strip().replace('\n', ' ') if sold_block else ""

    # Embarcado A
    ship_block = re.search(r'SHIP TO/EMBARCADO A:(.*?)(?=PAYMENT|DUE DATE|PAGE)', full_text, re.DOTALL)
    data['Embarcado A'] = ship_block.group(1).strip().replace('\n', ' ') if ship_block else ""
    
    return data

# ==========================================
# 🧠 LÓGICA DE ITEMS (DINÁMICA)
# ==========================================

def extract_items_dynamic(image, config):
    # OCR con coordenadas
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    # Convertir porcentajes a pixeles reales
    X_QTY_END = w * config['QTY_END']
    X_DESC_END = w * config['DESC_END']
    X_UPC_START = w * config['UPC_START']
    X_PRICE_START = w * config['PRICE_START']
    
    # 1. ENCONTRAR ANCLAS (Filas)
    anchors = []
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        
        # Ignorar encabezados muy arriba o pies muy abajo
        if cy < h * 0.30 or cy > h * 0.85: continue
        
        # Si es un número entero a la izquierda de la línea roja de Cantidad
        if cx < X_QTY_END and re.match(r'^\d+$', text):
            anchors.append({'y': cy, 'qty': text})
            
    if not anchors: return []

    # 2. EXTRAER INFO ENTRE ANCLAS
    items = []
    for idx, anchor in enumerate(anchors):
        y_top = anchor['y'] - 20 # Mirar un poco arriba
        
        # Definir el piso (siguiente item o fin)
        if idx + 1 < len(anchors):
            y_bottom = anchors[idx+1]['y'] - 10
        else:
            y_bottom = anchor['y'] + 150
            
        desc_words = []
        upc_words = []
        unit_words = []
        total_words = []
        
        for i in range(n_boxes):
            word = d['text'][i].strip()
            if not word: continue
            wx, wy = d['left'][i], d['top'][i]
            
            # Si está en la franja vertical de este item
            if y_top <= wy < y_bottom:
                
                # --- CLASIFICACIÓN POR COLUMNAS (Usando los sliders) ---
                
                # Descripción: Entre Cantidad y linea Descripcion
                if X_QTY_END < wx < X_DESC_END:
                    desc_words.append((wy, wx, word))
                    
                # UPC: Entre linea UPC inicio y linea Precio
                elif X_UPC_START < wx < X_PRICE_START:
                    if len(word) > 2: upc_words.append(word)
                    
                # Unitario: Entre linea Precio y fin (margen derecho)
                elif X_PRICE_START < wx < (w * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word): unit_words.append(word)
                
                # Total: Al final
                elif wx > (w * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word): total_words.append(word)

        # Reconstruir texto
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
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_file = st.file_uploader("Sube Factura (PDF)", type=["pdf"])

if uploaded_file is not None:
    
    # 1. Cargar Imagen para Previsualizar
    images = convert_from_bytes(uploaded_file.read(), dpi=300) # Alta calidad
    preview_img = images[0].copy()
    w, h = preview_img.size
    
    # 2. DIBUJAR LAS LÍNEAS DE CALIBRACIÓN
    draw = ImageDraw.Draw(preview_img)
    
    # Línea Cantidad (Azul)
    draw.line([(w*CFG['QTY_END'], 0), (w*CFG['QTY_END'], h)], fill="blue", width=5)
    
    # Línea Fin Descripción (Rojo)
    draw.line([(w*CFG['DESC_END'], 0), (w*CFG['DESC_END'], h)], fill="red", width=5)
    
    # Línea Inicio UPC (Verde)
    draw.line([(w*CFG['UPC_START'], 0), (w*CFG['UPC_START'], h)], fill="green", width=5)
    
    # Línea Inicio Precio (Naranja)
    draw.line([(w*CFG['PRICE_START'], 0), (w*CFG['PRICE_START'], h)], fill="orange", width=5)
    
    # Mostrar imagen calibrada
    st.image(preview_img, caption="Ajusta los sliders de la izquierda para alinear las columnas", use_column_width=True)
    
    st.divider()
    
    if st.button("🚀 Extraer Datos con esta Configuración"):
        with st.spinner("Extrayendo..."):
            try:
                # Texto completo para header (fallback seguro)
                full_text = pytesseract.image_to_string(images[0], lang='spa')
                
                # Extraer
                header = extract_header_robust(full_text)
                items = extract_items_dynamic(images[0], CFG)
                
                # Mostrar
                st.success(f"Factura detectada: {header.get('Factura')}")
                
                # Columnas Header
                c1, c2 = st.columns(2)
                c1.info(f"**Vendido A:**\n{header.get('Vendido A')}")
                c2.info(f"**Embarcado A:**\n{header.get('Embarcado A')}")
                
                if items:
                    df = pd.DataFrame(items)
                    st.dataframe(df, use_container_width=True)
                    
                    # Excel
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        pd.DataFrame([header]).to_excel(writer, sheet_name="General", index=False)
                        df.to_excel(writer, sheet_name="Items", index=False)
                        writer.sheets['Items'].set_column('B:B', 50)
                        
                    st.download_button("📥 Descargar Excel", buffer.getvalue(), "factura_calibrada.xlsx")
                else:
                    st.error("No se detectaron items. Mueve la línea AZUL (Cantidad) un poco a la derecha.")
                    
            except Exception as e:
                st.error(f"Error: {e}")
