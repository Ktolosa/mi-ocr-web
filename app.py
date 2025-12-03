import streamlit as st
import pytesseract
from pytesseract import Output
from pdf2image import convert_from_bytes
import pandas as pd
import io
import shutil
import re

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor Regal Final", layout="wide")
st.title("📄 Extractor Regal Trading (Versión Restaurada)")

# --- VERIFICACIÓN ---
if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado.")
    st.stop()

# ==========================================
# 🧠 LÓGICA DE ITEMS (RESTAURADA A LA VERSIÓN QUE FUNCIONABA)
# ==========================================
def extract_items_restored(image):
    # 1. Obtener datos
    # Usamos psm 6 para leer filas coherentes
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    # --- MÁRGENES AMPLIOS (TOLERANCIA ALTA) ---
    # Cantidad: Buscamos hasta el 15% para asegurar que atrapamos el número aunque se mueva
    X_QTY_LIMIT = w * 0.15    
    
    # Descripción: Empieza MUY a la izquierda (10%) para atrapar "TCL"
    X_DESC_START = w * 0.10 
    X_DESC_END = w * 0.58
    
    X_UPC_START = w * 0.60
    X_PRICE_START = w * 0.74
    
    # 2. ENCONTRAR FILAS (ANCLAS)
    anchors = []
    
    # Ignoramos encabezado y pie de página
    start_y = h * 0.30 
    end_y = h * 0.85
    
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        
        # Filtro vertical básico
        if cy < start_y or cy > end_y: continue
        
        # --- AQUÍ ESTABA LA CLAVE ---
        # Usamos re.search en lugar de re.match.
        # re.search encuentra "234" incluso si Tesseract lee ".234" o "234_"
        # Además, el límite X_QTY_LIMIT es generoso (15%)
        if cx < X_QTY_LIMIT and re.search(r'\d+', text):
            # Filtro anti-ruido: ignorar cosas muy pequeñas (menos de 10px de altura)
            if d['height'][i] > 8: 
                anchors.append({'y': cy, 'qty': text})

    if not anchors: return []

    # 3. EXTRAER TEXTO (MIRANDO ARRIBA)
    items = []
    
    for idx, anchor in enumerate(anchors):
        # Miramos 35 pixeles ARRIBA para atrapar la línea superior de la descripción
        y_top = anchor['y'] - 35
        
        # El piso es la siguiente fila
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
            
            # Si cae dentro de la franja vertical de este producto
            if y_top <= wy < y_bottom:
                
                # Clasificar por columna
                if X_DESC_START < wx < X_DESC_END:
                    desc_parts.append((wy, wx, word)) # Guardamos Y, X para ordenar
                    
                elif X_UPC_START < wx < X_PRICE_START:
                    if len(word) > 2: upc_parts.append(word)
                    
                elif X_PRICE_START < wx < (w * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word): unit_parts.append(word)
                    
                elif wx > (w * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word): total_parts.append(word)

        # Ordenar descripción: Primero por altura (Y), luego izquierda (X)
        desc_parts.sort(key=lambda k: (k[0], k[1]))
        full_desc = " ".join([w[2] for w in desc_parts])
        
        items.append({
            "Cantidad": anchor['qty'],
            "Descripción": full_desc,
            "UPC": " ".join(upc_parts),
            "Precio": unit_parts[0] if unit_parts else "",
            "Total": total_parts[0] if total_parts else ""
        })
        
    return items

# ==========================================
# 🧠 LÓGICA DE ENCABEZADO (ROBUSTA - LA QUE SÍ FUNCIONABA)
# ==========================================
def clean_text(text):
    return " ".join(text.split())

def extract_header_robust(full_text):
    data = {}
    
    # FACTURA
    inv = re.search(r'(?:#|No\.|297107)\s*(\d{6})', full_text)
    if not inv: inv = re.search(r'#\s*(\d{6})', full_text)
    data['Factura'] = inv.group(1) if inv else ""

    # FECHA
    date = re.search(r'(?:DATE|FECHA)\s*[:.,]?\s*([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})', full_text, re.IGNORECASE)
    data['Fecha'] = date.group(1) if date else ""

    # ORDEN
    orden = re.search(r'(?:ORDER|ORDEN)\s*#?\s*[:.,]?\s*(\d+)', full_text, re.IGNORECASE)
    data['Orden'] = orden.group(1) if orden else ""

    # REF
    ref = re.search(r'(?:FILE|REF)\s*[:.,]?\s*([A-Z0-9]+)', full_text, re.IGNORECASE)
    data['Ref'] = ref.group(1) if ref else ""

    # DIRECCIONES
    sold = re.search(r'SOLD TO/VENDIDO A:(.*?)(?=SHIP TO|124829|\d{2}/\d{2})', full_text, re.DOTALL)
    data['Vendido A'] = clean_text(sold.group(1)) if sold else ""

    ship = re.search(r'SHIP TO/EMBARCADO A:(.*?)(?=PAYMENT|DUE DATE|PAGE)', full_text, re.DOTALL)
    data['Embarcado A'] = clean_text(ship.group(1)) if ship else ""
    
    return data

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_file = st.file_uploader("Sube Factura (PDF)", type=["pdf"])

if uploaded_file is not None:
    if st.button("🚀 Extraer Datos"):
        
        with st.status("Procesando...", expanded=True) as status:
            try:
                # 1. Convertir a imagen (Alta Calidad)
                images = convert_from_bytes(uploaded_file.read(), dpi=300)
                target_img = images[0]
                
                # 2. Header (Texto completo)
                full_text = pytesseract.image_to_string(target_img, lang='spa')
                header_data = extract_header_robust(full_text)
                
                # 3. Items (Lógica Restaurada)
                items_data = extract_items_restored(target_img)
                
                status.update(label="¡Completado!", state="complete")
                
                # --- VISUALIZACIÓN ---
                st.subheader(f"Factura: {header_data['Factura']}")
                
                c1, c2, c3 = st.columns(3)
                c1.metric("Fecha", header_data['Fecha'])
                c2.metric("Orden", header_data['Orden'])
                c3.metric("Ref", header_data['Ref'])
                
                with st.expander("📍 Ver Direcciones", expanded=True):
                    d1, d2 = st.columns(2)
                    d1.info(f"**Vendido A:**\n{header_data['Vendido A']}")
                    d2.info(f"**Embarcado A:**\n{header_data['Embarcado A']}")

                st.divider()
                
                if items_data:
                    df = pd.DataFrame(items_data)
                    st.dataframe(df, use_container_width=True)
                    
                    # Excel
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        pd.DataFrame([header_data]).to_excel(writer, sheet_name="General", index=False)
                        df.to_excel(writer, sheet_name="Items", index=False)
                        
                        # Formato Ancho
                        workbook = writer.book
                        worksheet = writer.sheets['Items']
                        worksheet.set_column('B:B', 70) 
                        format_wrap = workbook.add_format({'text_wrap': True})
                        worksheet.set_column('B:B', 70, format_wrap)
                    
                    st.download_button(
                        "📥 Descargar Excel",
                        data=buffer.getvalue(),
                        file_name=f"Regal_{header_data['Factura']}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                else:
                    st.warning("No se detectaron items.")
                    
            except Exception as e:
                st.error(f"Error técnico: {e}")
