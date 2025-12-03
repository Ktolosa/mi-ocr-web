import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil
import re

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor Regal V10", layout="wide")
st.title("📄 Extractor Regal Trading (Precisión V10)")

# --- VERIFICACIÓN DE SISTEMA ---
if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# ==========================================
# 🛠️ UTILIDADES DE LIMPIEZA
# ==========================================
def clean_text_block(text):
    """Limpia bloques de texto (direcciones)"""
    if not text: return ""
    return " ".join(text.split())

def extract_money(text_list):
    """
    Busca un precio válido en una lista de palabras.
    Ej: ['$','6.25'] -> '6.25'
    Ej: ['6,210.00'] -> '6,210.00'
    """
    for text in reversed(text_list): # Preferimos el último número encontrado (suele ser el más limpio)
        # Limpiamos simbolos de moneda
        clean = text.replace('$', '').replace('S', '').strip()
        # Regex para dinero: digitos, punto/coma, dos decimales
        if re.search(r'\d+[.,]\d{2}', clean):
            return clean
    return ""

# ==========================================
# 🧠 LÓGICA DE ITEMS (V10: ANCLAS ESTRICTAS + ZONAS)
# ==========================================
def extract_items_v10(image):
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    # --- ZONAS DE COLUMNAS (Ajustadas a tus capturas) ---
    X_QTY_LIMIT = w * 0.14     # Cantidad (Hasta el 14%)
    X_DESC_START = w * 0.14    # Descripción empieza donde termina Cantidad
    X_DESC_END = w * 0.58      # Fin Descripción / Inicio UPC
    X_UPC_END = w * 0.73       # Fin UPC / Inicio Precio
    X_PRICE_END = w * 0.88     # Fin Precio / Inicio Total
    
    # --- PASO 1: DETECTAR FILAS (ANCLAS) ---
    anchors = []
    min_y = h * 0.25  # Ignorar cabecera
    max_y = h * 0.85  # Ignorar pie
    
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        
        if cy < min_y or cy > max_y: continue
        
        # FILTRO ESTRICTO PARA CANTIDAD:
        # 1. Debe estar a la izquierda (0-14%)
        # 2. Debe ser un número (permitimos puntos/comas para miles: 1,200 o 1.200)
        # 3. NO debe tener letras (evita capturar "16CM" o modelos)
        if cx < X_QTY_LIMIT:
            # Regex: Solo dígitos, puntos o comas. Nada de letras.
            if re.match(r'^[0-9.,]+$', text) and len(text) > 0:
                # Filtro extra: altura del texto > 8px (evita puntos sueltos)
                if d['height'][i] > 8:
                    anchors.append({'y': cy, 'qty': text})

    if not anchors: return []

    # Filtrar anclas muy cercanas (duplicados de OCR)
    anchors.sort(key=lambda k: k['y'])
    unique_anchors = []
    if anchors:
        unique_anchors.append(anchors[0])
        for anc in anchors[1:]:
            # Si hay más de 10px de diferencia vertical, es una nueva fila
            if anc['y'] - unique_anchors[-1]['y'] > 10:
                unique_anchors.append(anc)
    anchors = unique_anchors

    # --- PASO 2: CLASIFICAR TEXTO EN CADA FILA ---
    items = []
    
    for idx, anchor in enumerate(anchors):
        # TECHO: Miramos 30px arriba para capturar el modelo (que está encima de la línea)
        row_top = anchor['y'] - 30 
        
        # PISO: Hasta la siguiente cantidad o un margen fijo
        if idx + 1 < len(anchors):
            row_bottom = anchors[idx+1]['y'] - 5
        else:
            row_bottom = anchor['y'] + 150 # Margen para última fila
            
        desc_tokens = []
        upc_tokens = []
        unit_tokens = []
        total_tokens = []
        
        for i in range(n_boxes):
            word = d['text'][i].strip()
            if not word: continue
            
            bx = d['left'][i]
            by = d['top'][i]
            
            # Si la palabra cae en la franja vertical de este item
            if row_top <= by < row_bottom:
                
                # --- CLASIFICACIÓN HORIZONTAL ---
                
                # 1. DESCRIPCIÓN
                if X_DESC_START < bx < X_DESC_END:
                    desc_tokens.append((by, bx, word))
                
                # 2. UPC
                elif X_DESC_END < bx < X_UPC_END:
                    # Filtro UPC: ignorar "CHN" o guiones sueltos
                    if len(word) > 3 and word != "CHN": 
                        upc_tokens.append(word)
                        
                # 3. PRECIO UNITARIO
                elif X_UPC_END < bx < X_PRICE_END:
                    unit_tokens.append(word)
                        
                # 4. TOTAL
                elif bx > X_PRICE_END:
                    total_tokens.append(word)

        # Ordenar descripción (Arriba->Abajo, Izq->Der)
        desc_tokens.sort(key=lambda k: (k[0], k[1]))
        full_desc = " ".join([t[2] for t in desc_tokens])
        
        # Extraer precios limpios
        final_unit = extract_money(unit_tokens)
        final_total = extract_money(total_tokens)
        
        items.append({
            "Cantidad": anchor['qty'],
            "Descripción": full_desc,
            "UPC": " ".join(upc_tokens),
            "Precio Unit.": final_unit,
            "Total": final_total
        })
        
    return items

# ==========================================
# 🧠 LÓGICA DE CABECERA (ROBUSTA)
# ==========================================
def extract_header_data(full_text):
    data = {}
    
    # FACTURA (Busca # y 6 dígitos)
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
# 🕵️‍♂️ DETECTOR DE DUPLICADOS
# ==========================================
def is_duplicate_page(image):
    w, h = image.size
    header_crop = image.crop((0, 0, w, h * 0.35))
    text = pytesseract.image_to_string(header_crop, lang='spa')
    return bool(re.search(r'Duplicado', text, re.IGNORECASE))

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
                    # 1. Convertir (Alta Calidad)
                    images = convert_from_bytes(uploaded_file.read(), dpi=300)
                    
                    file_items = []
                    header = {}
                    pages_processed = 0
                    
                    # 2. Recorrer páginas
                    for i, img in enumerate(images):
                        
                        # DETECTOR DE DUPLICADOS
                        if is_duplicate_page(img):
                            st.warning(f"⚠️ Página {i+1}: 'Duplicado' detectado -> Omitida.")
                            continue 
                        
                        st.success(f"✅ Página {i+1}: Original -> Procesando")
                        
                        # Extraer Header (solo primera página válida)
                        if pages_processed == 0:
                            txt_full = pytesseract.image_to_string(img, lang='spa')
                            header = extract_header_data(txt_full)
                        
                        # Extraer Items
                        page_items = extract_items_v10(img)
                        file_items.extend(page_items)
                        
                        pages_processed += 1
                    
                    # --- RESULTADOS ---
                    if header:
                        c1, c2, c3 = st.columns(3)
                        c1.info(f"Factura: {header.get('Factura')}")
                        c2.info(f"Orden: {header.get('Orden')}")
                        c3.metric("Items", len(file_items))
                    
                    if file_items:
                        df = pd.DataFrame(file_items)
                        st.dataframe(df, use_container_width=True)
                        
                        # Guardar para Excel
                        for it in file_items:
                            row = header.copy()
                            row.update(it)
                            row['Archivo'] = uploaded_file.name
                            all_data_export.append(row)
                    else:
                        if pages_processed > 0:
                            st.error("No se encontraron items. Verifica la calidad.")
                        
                except Exception as e:
                    st.error(f"Error en {uploaded_file.name}: {e}")
            
            progress_bar.progress((idx + 1) / len(uploaded_files))

        # --- EXCEL FINAL ---
        if all_data_export:
            df_final = pd.DataFrame(all_data_export)
            
            cols_order = ['Archivo', 'Factura', 'Fecha', 'Orden', 'Ref', 'BL', 'Incoterm', 
                          'Vendido A', 'Embarcado A', 
                          'Cantidad', 'Descripción', 'UPC', 'Precio Unit.', 'Total']
            
            final_cols = [c for c in cols_order if c in df_final.columns]
            df_final = df_final[final_cols]
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_final.to_excel(writer, sheet_name="Consolidado", index=False)
                ws = writer.sheets['Consolidado']
                ws.set_column('J:J', 60) # Descripción ancha
                
            st.success("✅ ¡Proceso finalizado!")
            st.download_button("📥 Descargar Reporte Excel", buffer.getvalue(), "Reporte_Regal_V10.xlsx")
