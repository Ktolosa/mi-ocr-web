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
st.title("📄 Extractor Regal Trading (V9: Definitiva)")

# --- VERIFICACIÓN DE SISTEMA ---
if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado en el servidor.")
    st.stop()

# ==========================================
# 🛠️ UTILIDADES
# ==========================================
def clean_text_block(text):
    """Limpia bloques de texto eliminando saltos de línea innecesarios."""
    if not text: return ""
    return " ".join(text.split())

# ==========================================
# 🕵️‍♂️ DETECTOR DE DUPLICADOS (RESTAURADO)
# ==========================================
def is_duplicate_page(image):
    """
    Verifica si la página es un duplicado leyendo la cabecera.
    """
    w, h = image.size
    # Recortamos solo la parte superior para buscar rápido
    header_crop = image.crop((0, 0, w, h * 0.35))
    text = pytesseract.image_to_string(header_crop, lang='spa')
    
    # Buscamos "Duplicado" ignorando mayúsculas/minúsculas
    if re.search(r'Duplicado', text, re.IGNORECASE):
        return True
    return False

# ==========================================
# 🧠 LÓGICA DE ITEMS (V9: CLUSTERING + DESBORDAMIENTO INTELIGENTE)
# ==========================================
def extract_items_v9(image):
    # 1. Obtener datos
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    # --- ZONAS DE COLUMNAS ---
    # Ajustadas para permitir que la descripción empiece antes y termine después
    X_QTY_LIMIT = w * 0.14     # Cantidad (Hasta el 14% para atrapar números movidos)
    X_DESC_START = w * 0.12    # Descripción empieza en el 12%
    X_DESC_END = w * 0.55      # Límite teórico Descripción
    X_UPC_END = w * 0.72       # Límite UPC
    X_PRICE_START = w * 0.72   
    X_PRICE_END = w * 0.88
    
    # PASO 1: ENCONTRAR ANCLAS (CANTIDADES)
    anchors = []
    min_y = h * 0.25 
    max_y = h * 0.85
    
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        
        if cy < min_y or cy > max_y: continue
        
        # Busca números en la columna izquierda
        # Usamos regex estricto para evitar basura
        if cx < X_QTY_LIMIT and re.match(r'^[\d,.]+$', text):
            if len(text) >= 1: 
                anchors.append({'y': cy, 'qty': text})

    if not anchors: return []

    # Filtrar anclas muy cercanas (duplicados de OCR)
    anchors.sort(key=lambda k: k['y'])
    unique_anchors = []
    if anchors:
        unique_anchors.append(anchors[0])
        for anc in anchors[1:]:
            # Si hay más de 15px de diferencia, es una nueva fila
            if anc['y'] - unique_anchors[-1]['y'] > 15:
                unique_anchors.append(anc)
    anchors = unique_anchors

    # PASO 2: CLASIFICAR TEXTO POR FILAS
    items = []
    
    for idx, anchor in enumerate(anchors):
        # TECHO: Miramos 30 pixeles ARRIBA para capturar el modelo ("TCL...")
        row_top = anchor['y'] - 30 
        
        # PISO: Hasta la siguiente fila
        if idx + 1 < len(anchors):
            row_bottom = anchors[idx+1]['y'] - 5
        else:
            row_bottom = anchor['y'] + 150 
            
        desc_parts = []
        upc_parts = []
        unit_parts = []
        total_parts = []
        
        for i in range(n_boxes):
            word = d['text'][i].strip()
            if not word: continue
            
            bx = d['left'][i]
            by = d['top'][i]
            
            # Si la palabra cae en la franja vertical de este item
            if row_top <= by < row_bottom:
                
                # --- LÓGICA HORIZONTAL INTELIGENTE ---
                
                # 1. ZONA DESCRIPCIÓN PURA
                if X_DESC_START < bx < X_DESC_END:
                    desc_parts.append((by, bx, word))
                
                # 2. ZONA UPC (AQUÍ ESTÁ EL TRUCO DEL PDF 5284)
                elif X_DESC_END <= bx < X_UPC_END:
                    # Analizamos: ¿Parece un UPC o es texto desbordado?
                    is_real_upc = (
                        re.match(r'^\d+$', word) or  # Son solo números
                        word == "CHN" or             # Es el país
                        len(word) < 3                # Es basura corta
                    )
                    
                    if is_real_upc:
                        if len(word) > 2: upc_parts.append(word)
                    else:
                        # ¡AJÁ! Es texto largo (ej: "FFC-SLS...") que invadió la columna.
                        # Lo mandamos a la descripción.
                        desc_parts.append((by, bx, word))
                        
                # 3. ZONA PRECIO
                elif X_UPC_END <= bx < X_PRICE_END:
                    if re.match(r'[\d,.]+', word): unit_parts.append(word)
                        
                # 4. ZONA TOTAL
                elif bx >= X_PRICE_END:
                    if re.match(r'[\d,.]+', word): total_parts.append(word)

        # Ordenar descripción para lectura natural (Arriba->Abajo, Izq->Der)
        desc_parts.sort(key=lambda k: (k[0], k[1]))
        full_desc = " ".join([t[2] for t in desc_parts])
        
        # Precios: Tomar el último valor encontrado
        u_price = unit_parts[-1] if unit_parts else ""
        t_price = total_parts[-1] if total_parts else ""
        
        items.append({
            "Cantidad": anchor['qty'],
            "Descripción": full_desc,
            "UPC": " ".join(upc_parts),
            "Precio Unit.": u_price,
            "Total": t_price
        })
        
    return items

# ==========================================
# 🧠 LÓGICA DE CABECERA (ROBUSTA)
# ==========================================
def extract_header_data(full_text):
    data = {}
    
    # FACTURA
    inv = re.search(r'(?:#|No\.|297107)\s*(\d{6})', full_text)
    if not inv: inv = re.search(r'#\s*(\d{4,6})', full_text)
    data['Factura'] = inv.group(1) if inv else ""

    # FECHA
    date = re.search(r'(?:DATE|FECHA)\s*[:.,]?\s*([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})', full_text, re.IGNORECASE)
    data['Fecha'] = date.group(1) if date else ""

    # ORDEN
    orden = re.search(r'(?:ORDER|ORDEN).*?[:#]\s*(\d+)', full_text, re.IGNORECASE)
    data['Orden'] = orden.group(1) if orden else ""

    # REF
    ref = re.search(r'(?:FILE|REF)\s*[:.,]?\s*([A-Z0-9-]+)', full_text, re.IGNORECASE)
    data['Ref'] = ref.group(1) if ref else ""
    
    # B/L
    bl = re.search(r'B/L#\s*[:.,]?\s*([A-Z0-9]+)', full_text, re.IGNORECASE)
    data['BL'] = bl.group(1) if bl else ""
    
    # INCOTERM
    incoterm = re.search(r'INCOTERM\s*[:.,]?\s*([A-Z]+)', full_text, re.IGNORECASE)
    data['Incoterm'] = incoterm.group(1) if incoterm else ""

    # DIRECCIONES
    sold = re.search(r'SOLD TO/VENDIDO A:(.*?)(?=SHIP TO|124829|\d{2}/\d{2})', full_text, re.DOTALL | re.IGNORECASE)
    data['Vendido A'] = clean_text_block(sold.group(1)) if sold else ""

    ship = re.search(r'SHIP TO/EMBARCADO A:(.*?)(?=PAYMENT|DUE DATE|PAGE)', full_text, re.DOTALL | re.IGNORECASE)
    data['Embarcado A'] = clean_text_block(ship.group(1)) if ship else ""
    
    return data

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_files = st.file_uploader("Sube tus Facturas Regal (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    if st.button("🚀 Extraer Datos"):
        
        all_data_export = []
        progress_bar = st.progress(0)
        
        for idx, uploaded_file in enumerate(uploaded_files):
            with st.expander(f"📄 Procesando: {uploaded_file.name}", expanded=True):
                try:
                    # 1. Convertir
                    images = convert_from_bytes(uploaded_file.read(), dpi=300)
                    
                    file_items = []
                    header = {}
                    pages_processed = 0
                    
                    # 2. Recorrer páginas
                    for i, img in enumerate(images):
                        
                        # VALIDAR DUPLICADO
                        if is_duplicate_page(img):
                            st.warning(f"⚠️ Página {i+1}: Detectada como 'Duplicado' -> Omitida.")
                            continue 
                        
                        st.info(f"✅ Página {i+1}: Original -> Procesando...")
                        
                        # Extraer Header (solo primera página válida)
                        if pages_processed == 0:
                            txt_head = pytesseract.image_to_string(img, lang='spa')
                            header = extract_header_data(txt_head)
                        
                        # Extraer Items (con lógica mejorada V9)
                        page_items = extract_items_v9(img)
                        file_items.extend(page_items)
                        
                        pages_processed += 1
                    
                    # --- RESULTADOS ---
                    if header:
                        c1, c2, c3 = st.columns(3)
                        c1.success(f"Factura: {header.get('Factura', 'ND')}")
                        c2.metric("Orden", header.get('Orden', 'ND'))
                        c3.metric("Items Totales", len(file_items))
                    
                    if file_items:
                        df = pd.DataFrame(file_items)
                        # Ordenar columnas visuales
                        cols_view = ["Cantidad", "Descripción", "UPC", "Precio Unit.", "Total"]
                        st.dataframe(df[cols_view], use_container_width=True)
                        
                        # Guardar datos
                        for it in file_items:
                            row = header.copy()
                            row.update(it)
                            row['Archivo'] = uploaded_file.name
                            all_data_export.append(row)
                    else:
                        if pages_processed > 0:
                            st.warning("No se encontraron items en las páginas originales.")
                        
                except Exception as e:
                    st.error(f"Error en {uploaded_file.name}: {e}")
            
            progress_bar.progress((idx + 1) / len(uploaded_files))

        # --- EXCEL CONSOLIDADO ---
        if all_data_export:
            df_final = pd.DataFrame(all_data_export)
            
            # Ordenar columnas
            cols_order = ['Archivo', 'Factura', 'Fecha', 'Orden', 'Ref', 'BL', 'Incoterm', 
                          'Vendido A', 'Embarcado A', 
                          'Cantidad', 'Descripción', 'UPC', 'Precio Unit.', 'Total']
            
            final_cols = [c for c in cols_order if c in df_final.columns]
            df_final = df_final[final_cols]
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_final.to_excel(writer, sheet_name="Consolidado", index=False)
                ws = writer.sheets['Consolidado']
                ws.set_column('A:I', 15)
                ws.set_column('J:J', 10)
                ws.set_column('K:K', 60) # Descripción ancha
                
            st.success("✅ ¡Extracción completada con éxito!")
            st.download_button("📥 Descargar Reporte Excel", buffer.getvalue(), "Reporte_Regal_Master.xlsx")
