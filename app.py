import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil
import re

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor Regal V11", layout="wide")
st.title("📄 Extractor Regal Trading (V11: Accesorios + Duplicados)")

if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# ==========================================
# 🛠️ UTILIDADES
# ==========================================
def clean_text(text):
    """Limpia saltos de línea y espacios dobles"""
    return " ".join(text.split())

# ==========================================
# 🕵️‍♂️ DETECTOR DE DUPLICADOS (ACTIVO)
# ==========================================
def is_duplicate_page(image):
    """
    Verifica si la página es un duplicado leyendo la cabecera.
    """
    w, h = image.size
    # Recortar cabecera (35% superior)
    header_crop = image.crop((0, 0, w, h * 0.35))
    text = pytesseract.image_to_string(header_crop, lang='spa')
    
    # Buscar palabra clave "Duplicado"
    if re.search(r'Duplicado', text, re.IGNORECASE):
        return True
    return False

# ==========================================
# 🧠 LÓGICA DE ITEMS (V11: DESBORDAMIENTO INTELIGENTE)
# ==========================================
def extract_items_v11(image):
    # 1. Obtener datos con coordenadas
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    # --- ZONAS DE COLUMNAS (Ajustadas para Accesorios) ---
    X_QTY_LIMIT = w * 0.15     # Cantidad (Hasta el 15% para atrapar números movidos)
    X_DESC_START = w * 0.10    # Descripción empieza antes (10%)
    X_DESC_END = w * 0.58      # Fin teórico de Descripción
    X_UPC_END = w * 0.74       # Fin de zona UPC
    X_PRICE_START = w * 0.74   # Inicio Precio
    X_PRICE_END = w * 0.88     # Fin Precio
    
    # 2. ENCONTRAR ANCLAS (FILAS)
    anchors = []
    min_y = h * 0.25 
    max_y = h * 0.85
    
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        
        if cy < min_y or cy > max_y: continue
        
        # Detectar número de cantidad
        if cx < X_QTY_LIMIT and re.match(r'^[\d,.]+$', text):
            # Filtro: debe tener cierta altura (evitar puntos)
            if d['height'][i] > 6: 
                anchors.append({'y': cy, 'qty': text})

    if not anchors: return []

    # Filtrar anclas duplicadas
    anchors.sort(key=lambda k: k['y'])
    unique_anchors = [anchors[0]]
    for anc in anchors[1:]:
        if anc['y'] - unique_anchors[-1]['y'] > 15:
            unique_anchors.append(anc)
    anchors = unique_anchors

    # 3. EXTRAER DATOS (CON LOGICA DE DESBORDAMIENTO)
    items = []
    
    for idx, anchor in enumerate(anchors):
        # TECHO: 35px arriba para capturar texto superior
        y_top = anchor['y'] - 35 
        
        # PISO: Hasta la siguiente fila
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
            
            bx = d['left'][i]
            by = d['top'][i]
            
            # Si cae en la franja vertical
            if y_top <= by < y_bottom:
                
                # --- CLASIFICACIÓN HORIZONTAL INTELIGENTE ---
                
                # 1. ZONA DESCRIPCIÓN
                if X_DESC_START < bx < X_DESC_END:
                    desc_parts.append((by, bx, word))
                
                # 2. ZONA UPC (AQUÍ ESTÁ LA SOLUCIÓN)
                elif X_DESC_END <= bx < X_UPC_END:
                    # ¿Es un UPC real o es texto desbordado?
                    is_real_upc = (
                        re.match(r'^[\d-]+$', word) or  # Solo números y guiones
                        word == "CHN"                   # Es el país
                    )
                    
                    if is_real_upc and len(word) > 3:
                        upc_parts.append(word)
                    else:
                        # ¡ES TEXTO DESBORDADO! (ej: "FFC-SLS...") -> Mover a descripción
                        desc_parts.append((by, bx, word))
                        
                # 3. ZONA PRECIO
                elif X_UPC_END <= bx < X_PRICE_END:
                    if re.match(r'[\d,.]+', word): unit_parts.append(word)
                        
                # 4. ZONA TOTAL
                elif bx >= X_PRICE_END:
                    if re.match(r'[\d,.]+', word): total_parts.append(word)

        # Ordenar descripción (Arriba->Abajo, Izq->Der)
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
# 🧠 LÓGICA DE CABECERA (GLOBAL)
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
    data['Vendido A'] = clean_text(sold.group(1)) if sold else ""

    ship = re.search(r'SHIP TO/EMBARCADO A:(.*?)(?=PAYMENT|DUE DATE|PAGE)', full_text, re.DOTALL | re.IGNORECASE)
    data['Embarcado A'] = clean_text(ship.group(1)) if ship else ""
    
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
                        
                        # --- VALIDACIÓN DE DUPLICADO ---
                        if is_duplicate_page(img):
                            st.warning(f"⚠️ Página {i+1}: 'Duplicado' detectado -> Omitida.")
                            continue 
                        
                        st.success(f"✅ Página {i+1}: Original -> Procesando")
                        
                        # Extraer Header (solo primera página válida)
                        if pages_processed == 0:
                            txt_full = pytesseract.image_to_string(img, lang='spa')
                            header = extract_header_data(txt_full)
                        
                        # Extraer Items (V11)
                        page_items = extract_items_v11(img)
                        file_items.extend(page_items)
                        
                        pages_processed += 1
                    
                    # --- RESULTADOS ---
                    if header:
                        c1, c2, c3 = st.columns(3)
                        c1.info(f"Factura: {header.get('Factura', 'ND')}")
                        c2.metric("Orden", header.get('Orden', 'ND'))
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
                ws.set_column('J:J', 70) # Descripción muy ancha para que se lea bien
                
            st.success("✅ ¡Proceso finalizado!")
            st.download_button("📥 Descargar Reporte Excel", buffer.getvalue(), "Reporte_Regal_V11.xlsx")
