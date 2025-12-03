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
st.title("📄 Extractor Regal Trading (Multi-Items Robusto)")

# --- VERIFICACIÓN DE SISTEMA ---
if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado en el servidor.")
    st.stop()

# ==========================================
# 🛠️ UTILIDADES
# ==========================================
def clean_text_block(text):
    """Limpia saltos de línea para direcciones"""
    return " ".join(text.split())

# ==========================================
# 🧠 LÓGICA DE ITEMS: CLUSTERING VERTICAL (LA SOLUCIÓN)
# ==========================================
def extract_items_clustering(image):
    # 1. Obtener datos crudos
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    # --- ZONAS DE COLUMNAS (Ajustadas a tus imágenes) ---
    X_QTY_LIMIT = w * 0.12     # Cantidad (0-12%)
    X_DESC_START = w * 0.12    # Descripción (12-58%)
    X_DESC_END = w * 0.58
    X_UPC_START = w * 0.58     # UPC (58-72%)
    X_UPC_END = w * 0.72
    X_PRICE_START = w * 0.72   # Precio (72-88%)
    X_PRICE_END = w * 0.88
                               # Total (>88%)

    # --- PASO 1: ENCONTRAR LAS "ANCLAS" (CANTIDADES) ---
    # Buscamos todos los números que están en la columna izquierda
    anchors = []
    
    # Ignorar encabezado y pie de página para no confundir
    min_y = h * 0.25 
    max_y = h * 0.85
    
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        
        # Filtros de posición y contenido
        if cy < min_y or cy > max_y: continue
        
        # Si está a la izquierda y parece un número (ej: 234, 1,500, 1.200)
        # El regex permite puntos y comas
        if cx < X_QTY_LIMIT and re.match(r'^[\d,.]+$', text):
            # Filtro de ruido: ignorar cosas muy pequeñas o símbolos sueltos
            if len(text) >= 1: 
                anchors.append({'y': cy, 'qty': text})

    # Si no hay anclas, no hay tabla
    if not anchors: return []

    # Ordenar anclas por altura (de arriba a abajo)
    anchors.sort(key=lambda k: k['y'])
    
    # Filtrar anclas duplicadas (a veces el OCR detecta el mismo número dos veces muy cerca)
    unique_anchors = []
    if anchors:
        unique_anchors.append(anchors[0])
        for anc in anchors[1:]:
            # Si la distancia con el anterior es mayor a 10px, es una nueva fila
            if anc['y'] - unique_anchors[-1]['y'] > 15:
                unique_anchors.append(anc)
    
    anchors = unique_anchors

    # --- PASO 2: DEFINIR FRONTERAS Y CLASIFICAR TEXTO ---
    items = []
    
    for idx, anchor in enumerate(anchors):
        # Definir el TECHO y el PISO de esta fila específica
        # Techo: Un poco arriba de la cantidad para capturar negritas superiores
        row_top = anchor['y'] - 20 
        
        # Piso: Donde empieza la siguiente cantidad (o un margen fijo si es la última)
        if idx + 1 < len(anchors):
            row_bottom = anchors[idx+1]['y'] - 5
        else:
            row_bottom = anchor['y'] + 150 # Margen para la última fila
            
        # Contenedores para esta fila
        desc_tokens = []
        upc_tokens = []
        unit_tokens = []
        total_tokens = []
        
        # RECORRER TODOS LOS TEXTOS DE NUEVO
        for i in range(n_boxes):
            text = d['text'][i].strip()
            if not text: continue
            
            bx = d['left'][i]
            by = d['top'][i]
            
            # Chequear si este texto pertenece verticalmente a esta fila
            if row_top <= by < row_bottom:
                
                # Clasificar por columna Horizontal
                if X_DESC_START < bx < X_DESC_END:
                    desc_tokens.append((by, bx, text)) # Guardamos pos para ordenar
                    
                elif X_DESC_END < bx < X_UPC_END:
                    # Filtro UPC: ignorar "CHN" o basura corta
                    if len(text) > 3 and text != "CHN":
                        upc_tokens.append(text)
                        
                elif X_UPC_END < bx < X_PRICE_END:
                    # Filtro Precio
                    if re.match(r'[\d,.]+', text):
                        unit_tokens.append(text)
                        
                elif bx > X_PRICE_END:
                    # Filtro Total
                    if re.match(r'[\d,.]+', text):
                        total_tokens.append(text)

        # Ordenar descripción para lectura natural (Arriba->Abajo, Izq->Der)
        desc_tokens.sort(key=lambda k: (k[0], k[1]))
        full_desc = " ".join([t[2] for t in desc_tokens])
        
        # Limpieza final de precios (tomar el último encontrado que suele ser el correcto si hay basura)
        u_price = unit_tokens[-1] if unit_tokens else ""
        t_price = total_tokens[-1] if total_tokens else ""
        
        items.append({
            "Cantidad": anchor['qty'],
            "Descripción": full_desc,
            "UPC": " ".join(upc_tokens),
            "Precio Unit.": u_price,
            "Total": t_price
        })
        
    return items

# ==========================================
# 🧠 LÓGICA DE CABECERA (MEJORADA CON DOTALL)
# ==========================================
def extract_header_data(full_text):
    data = {}
    
    # FACTURA (Busca # y números)
    inv = re.search(r'(?:#|No\.|297107)\s*(\d{6})', full_text)
    if not inv: inv = re.search(r'#\s*(\d{6})', full_text)
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

    # DIRECCIONES (Bloques completos)
    sold = re.search(r'SOLD TO/VENDIDO A:(.*?)(?=SHIP TO|124829|\d{2}/\d{2})', full_text, re.DOTALL)
    data['Vendido A'] = clean_text_block(sold.group(1)) if sold else ""

    ship = re.search(r'SHIP TO/EMBARCADO A:(.*?)(?=PAYMENT|DUE DATE|PAGE)', full_text, re.DOTALL)
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
            with st.expander(f"📄 Resultado: {uploaded_file.name}", expanded=True):
                try:
                    # 1. Convertir (Alta Calidad)
                    images = convert_from_bytes(uploaded_file.read(), dpi=300)
                    
                    # 2. Header (Solo página 1)
                    txt_page1 = pytesseract.image_to_string(images[0], lang='spa')
                    header = extract_header_data(txt_page1)
                    
                    # 3. Items (Todas las páginas)
                    file_items = []
                    for img in images:
                        page_items = extract_items_clustering(img)
                        file_items.extend(page_items)
                    
                    # Mostrar
                    c1, c2, c3, c4 = st.columns(4)
                    c1.success(f"Factura: {header['Factura']}")
                    c2.info(f"Orden: {header['Orden']}")
                    c3.metric("Fecha", header['Fecha'])
                    c4.metric("Items", len(file_items))
                    
                    if file_items:
                        df = pd.DataFrame(file_items)
                        st.dataframe(df, use_container_width=True)
                        
                        # Preparar para Excel Consolidado
                        for it in file_items:
                            row = header.copy()
                            row.update(it)
                            all_data_export.append(row)
                    else:
                        st.warning("No se encontraron items en este archivo.")
                        
                except Exception as e:
                    st.error(f"Error procesando {uploaded_file.name}: {e}")
            
            progress_bar.progress((idx + 1) / len(uploaded_files))

        # --- EXPORTAR TODO ---
        if all_data_export:
            df_final = pd.DataFrame(all_data_export)
            
            # Ordenar columnas
            cols = ['Factura', 'Fecha', 'Orden', 'Ref', 'BL', 'Incoterm', 
                    'Vendido A', 'Embarcado A', 
                    'Cantidad', 'Descripción', 'UPC', 'Precio Unit.', 'Total']
            
            # Asegurar que existan (por si acaso faltó algún dato)
            final_cols = [c for c in cols if c in df_final.columns]
            df_final = df_final[final_cols]
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_final.to_excel(writer, sheet_name="Consolidado", index=False)
                # Formato
                ws = writer.sheets['Consolidado']
                ws.set_column('A:H', 15)
                ws.set_column('J:J', 60) # Descripción ancha
                
            st.success("✅ ¡Procesamiento Terminado!")
            st.download_button("📥 Descargar Reporte Excel", buffer.getvalue(), "Reporte_Regal.xlsx")
