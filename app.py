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
st.title("📄 Extractor Regal Trading (Con Filtro de Duplicados)")

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
# 🧠 LÓGICA DE ITEMS (V7 - CLUSTERING VERTICAL)
# ==========================================
def extract_items_clustering(image):
    """
    Esta es la lógica que te funcionó bien para separar items sin revolverlos.
    """
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    # --- ZONAS DE COLUMNAS ---
    X_QTY_LIMIT = w * 0.12     
    X_DESC_START = w * 0.12    
    X_DESC_END = w * 0.58
    X_UPC_START = w * 0.58     
    X_UPC_END = w * 0.72
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
        if cx < X_QTY_LIMIT and re.match(r'^[\d,.]+$', text):
            if len(text) >= 1: 
                anchors.append({'y': cy, 'qty': text})

    if not anchors: return []

    # Ordenar y filtrar duplicados cercanos
    anchors.sort(key=lambda k: k['y'])
    unique_anchors = []
    if anchors:
        unique_anchors.append(anchors[0])
        for anc in anchors[1:]:
            if anc['y'] - unique_anchors[-1]['y'] > 15:
                unique_anchors.append(anc)
    anchors = unique_anchors

    # PASO 2: CLASIFICAR TEXTO POR FILAS
    items = []
    
    for idx, anchor in enumerate(anchors):
        row_top = anchor['y'] - 20 
        
        if idx + 1 < len(anchors):
            row_bottom = anchors[idx+1]['y'] - 5
        else:
            row_bottom = anchor['y'] + 150 
            
        desc_tokens = []
        upc_tokens = []
        unit_tokens = []
        total_tokens = []
        
        for i in range(n_boxes):
            text = d['text'][i].strip()
            if not text: continue
            
            bx = d['left'][i]
            by = d['top'][i]
            
            if row_top <= by < row_bottom:
                if X_DESC_START < bx < X_DESC_END:
                    desc_tokens.append((by, bx, text))
                elif X_DESC_END < bx < X_UPC_END:
                    if len(text) > 3 and text != "CHN": upc_tokens.append(text)
                elif X_UPC_END < bx < X_PRICE_END:
                    if re.match(r'[\d,.]+', text): unit_tokens.append(text)
                elif bx > X_PRICE_END:
                    if re.match(r'[\d,.]+', text): total_tokens.append(text)

        desc_tokens.sort(key=lambda k: (k[0], k[1]))
        full_desc = " ".join([t[2] for t in desc_tokens])
        
        # Precios: Tomar el último valor encontrado (suele ser el más limpio)
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
# 🧠 LÓGICA DE CABECERA (CON DOTALL)
# ==========================================
def extract_header_data(full_text):
    data = {}
    
    # Factura
    inv = re.search(r'(?:#|No\.|297107)\s*(\d{6})', full_text)
    if not inv: inv = re.search(r'#\s*(\d{6})', full_text)
    data['Factura'] = inv.group(1) if inv else ""

    # Fecha
    date = re.search(r'(?:DATE|FECHA)\s*[:.,]?\s*([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})', full_text, re.IGNORECASE)
    data['Fecha'] = date.group(1) if date else ""

    # Orden
    orden = re.search(r'(?:ORDER|ORDEN).*?[:#]\s*(\d+)', full_text, re.IGNORECASE)
    data['Orden'] = orden.group(1) if orden else ""

    # Ref
    ref = re.search(r'(?:FILE|REF)\s*[:.,]?\s*([A-Z0-9-]+)', full_text, re.IGNORECASE)
    data['Ref'] = ref.group(1) if ref else ""
    
    # B/L
    bl = re.search(r'B/L#\s*[:.,]?\s*([A-Z0-9]+)', full_text, re.IGNORECASE)
    data['BL'] = bl.group(1) if bl else ""
    
    # Incoterm
    incoterm = re.search(r'INCOTERM\s*[:.,]?\s*([A-Z]+)', full_text, re.IGNORECASE)
    data['Incoterm'] = incoterm.group(1) if incoterm else ""

    # Direcciones
    sold = re.search(r'SOLD TO/VENDIDO A:(.*?)(?=SHIP TO|124829|\d{2}/\d{2})', full_text, re.DOTALL | re.IGNORECASE)
    data['Vendido A'] = clean_text_block(sold.group(1)) if sold else ""

    ship = re.search(r'SHIP TO/EMBARCADO A:(.*?)(?=PAYMENT|DUE DATE|PAGE)', full_text, re.DOTALL | re.IGNORECASE)
    data['Embarcado A'] = clean_text_block(ship.group(1)) if ship else ""
    
    return data

# ==========================================
# 🕵️‍♂️ DETECTOR DE DUPLICADOS
# ==========================================
def is_duplicate_page(image):
    """
    Verifica si la página tiene la marca 'Duplicado' en la cabecera.
    Recorta solo el 35% superior para buscar rápido.
    """
    w, h = image.size
    # Recortar cabecera (Parte superior)
    header_crop = image.crop((0, 0, w, h * 0.35))
    
    # Leer texto
    text = pytesseract.image_to_string(header_crop, lang='spa')
    
    # Buscar palabra clave
    if re.search(r'Duplicado', text, re.IGNORECASE):
        return True
    return False

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_files = st.file_uploader("Sube tus Facturas Regal (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    if st.button("🚀 Extraer Datos (Omitiendo Duplicados)"):
        
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
                    
                    # 2. Iterar sobre cada página
                    for i, img in enumerate(images):
                        
                        # --- VALIDACIÓN DE DUPLICADO ---
                        if is_duplicate_page(img):
                            st.warning(f"⚠️ Página {i+1} omitida (Detectado como 'Duplicado')")
                            continue # Salta a la siguiente página del bucle
                        
                        # Si es válida, procesamos
                        st.info(f"✅ Procesando Página {i+1} (Original)")
                        
                        # Extraemos texto completo solo si es la primera página válida (para sacar el header)
                        if pages_processed == 0:
                            full_text = pytesseract.image_to_string(img, lang='spa')
                            header = extract_header_data(full_text)
                        
                        # Extraemos items
                        page_items = extract_items_clustering(img)
                        file_items.extend(page_items)
                        
                        pages_processed += 1
                    
                    # --- RESULTADOS DEL ARCHIVO ---
                    if header:
                        c1, c2, c3, c4 = st.columns(4)
                        c1.success(f"Factura: {header.get('Factura', 'ND')}")
                        c2.metric("Orden", header.get('Orden', 'ND'))
                        c3.metric("Fecha", header.get('Fecha', 'ND'))
                        c4.metric("Items Extraídos", len(file_items))
                    
                    if file_items:
                        df = pd.DataFrame(file_items)
                        st.dataframe(df, use_container_width=True)
                        
                        # Guardar para el consolidado
                        for it in file_items:
                            row = header.copy()
                            row.update(it)
                            row['Archivo Origen'] = uploaded_file.name
                            all_data_export.append(row)
                    else:
                        if pages_processed > 0:
                            st.warning("No se encontraron items en las páginas originales.")
                        else:
                            st.error("Todas las páginas eran duplicados o no se pudieron leer.")
                        
                except Exception as e:
                    st.error(f"Error procesando {uploaded_file.name}: {e}")
            
            progress_bar.progress((idx + 1) / len(uploaded_files))

        # --- EXCEL FINAL ---
        if all_data_export:
            df_final = pd.DataFrame(all_data_export)
            
            # Ordenar columnas
            cols = ['Archivo Origen', 'Factura', 'Fecha', 'Orden', 'Ref', 'BL', 'Incoterm', 
                    'Vendido A', 'Embarcado A', 
                    'Cantidad', 'Descripción', 'UPC', 'Precio Unit.', 'Total']
            
            final_cols = [c for c in cols if c in df_final.columns]
            df_final = df_final[final_cols]
            
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_final.to_excel(writer, sheet_name="Consolidado", index=False)
                ws = writer.sheets['Consolidado']
                ws.set_column('A:I', 15)
                ws.set_column('K:K', 60) 
                
            st.success("✅ ¡Todo Listo! Se han filtrado las páginas duplicadas.")
            st.download_button("📥 Descargar Reporte Limpio", buffer.getvalue(), "Reporte_Regal_Sin_Duplicados.xlsx")
