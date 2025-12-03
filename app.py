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
st.title("📄 Extractor Especializado: Regal Trading (V3)")

# --- VERIFICACIÓN DE SISTEMA ---
if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado en el servidor.")
    st.stop()

# ==========================================
# 🛠️ UTILIDADES
# ==========================================
def clean_text(text):
    return text.strip().replace('\n', ' ')

def safe_extract(pattern, text, group=1):
    try:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            return clean_text(match.group(group))
    except:
        pass
    return ""

# ==========================================
# 🧠 LÓGICA DE ENCABEZADO (REGEX MEJORADO)
# ==========================================
def extract_header_data_improved(full_text):
    data = {}
    
    # 1. FACTURA (Buscamos el número de 6 dígitos asociado a Commercial Invoice o el símbolo #)
    # Buscamos en todo el texto porque el # puede estar lejos del título
    inv_match = re.search(r'(?:COMMERCIAL INVOICE|FACTURA).*?#\s*(\d{6})', full_text, re.DOTALL)
    if not inv_match:
        # Intento secundario: buscar solo el bloque flotante # 297107
        inv_match = re.search(r'#\s*(\d{6})\s+[A-Z]', full_text)
    data['Factura'] = inv_match.group(1) if inv_match else ""

    # 2. FECHAS (Mejorado para capturar AGO/AUG)
    # Patrón: Mes (3 letras) + Dia + Coma? + Año
    date_pattern = r'([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})'
    
    data['Fecha Emisión'] = safe_extract(r'DATE/FECHA\s*[:.,-]?\s*' + date_pattern, full_text)
    data['Fecha Vencimiento'] = safe_extract(r'(?:DUE DATE|VENCIMIENTO)\s*[:.,-]?\s*' + date_pattern, full_text)

    # 3. DATOS LOGÍSTICOS (Derecha)
    data['Orden Compra'] = safe_extract(r'ORDER/ORDEN\s*#?\s*[:.,]?\s*(\d+)', full_text)
    data['Referencia'] = safe_extract(r'FILE/REF\s*[:.,]?\s*([A-Z0-9]+)', full_text)
    data['B/L'] = safe_extract(r'B/L#\s*[:.,]?\s*([A-Z0-9]+)', full_text)
    data['Incoterm'] = safe_extract(r'INCOTERM\s*[:.,]?\s*([A-Z]+)', full_text)

    # 4. DIRECCIONES (Usando delimitadores estrictos)
    # SOLD TO: Captura todo hasta que encuentre "SHIP TO" o una fecha o número de referencia
    sold_block = re.search(r'SOLD TO/VENDIDO A:(.*?)(?=SHIP TO|DATE/FECHA|124829)', full_text, re.DOTALL)
    data['Vendido A'] = clean_text(sold_block.group(1)) if sold_block else ""

    # SHIP TO: Captura todo hasta "PAYMENT TERMS" o "DATE"
    ship_block = re.search(r'SHIP TO/EMBARCADO A:(.*?)(?=PAYMENT|DATE/FECHA|PAGE)', full_text, re.DOTALL)
    data['Embarcado A'] = clean_text(ship_block.group(1)) if ship_block else ""

    return data

# ==========================================
# 🧠 LÓGICA DE ITEMS (ESTRATEGIA BLOQUES VERTICALES)
# ==========================================
def extract_items_advanced(image):
    # Obtenemos datos detallados con posición
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    width, height = image.size
    
    # 1. DEFINIR LÍMITES DE COLUMNAS (Basado en tu imagen)
    # Ajusta estos porcentajes si el PDF cambia mucho de tamaño
    X_QTY_LIMIT = width * 0.13    # Cantidad está en el primer 13%
    X_DESC_START = width * 0.13   # Descripción empieza después de cantidad
    X_DESC_END = width * 0.55     # Descripción termina al 55%
    X_PRICE_START = width * 0.70  # Precio unitario empieza por el 70%
    
    # 2. ENCONTRAR "ANCLAS" (Filas basadas en Cantidad)
    anchors = [] # Lista de tuplas (y_position, quantity_value)
    
    start_scanning = False
    stop_scanning = False
    
    for i in range(n_boxes):
        text = d['text'][i].strip()
        y = d['top'][i]
        x = d['left'][i]
        
        # Detectores de área segura
        if "QUANTITY" in text or "DESCRIPTION" in text: start_scanning = True
        if "SUBTOTAL" in text or "TOTAL" in text: 
             if y > height * 0.4: stop_scanning = True # Solo si está abajo
        
        if not start_scanning or stop_scanning: continue
        
        # SI ES UNA CANTIDAD (Número a la izquierda)
        if x < X_QTY_LIMIT and re.match(r'^\d+$', text):
            # Guardamos la posición Y y el valor
            anchors.append({'y': y, 'qty': text, 'index': i})

    # Si no hay items, salir
    if not anchors: return []

    # 3. EXTRAER DATOS POR BLOQUES
    # Definimos el "Piso" de cada fila como la Y del siguiente item
    items = []
    
    for idx, anchor in enumerate(anchors):
        # El techo es la Y del anchor actual (menos un margen por si el texto 'Modelo' está un pelín arriba)
        y_top = anchor['y'] - 10 
        
        # El piso es la Y del siguiente anchor, o el final de la tabla si es el último
        if idx + 1 < len(anchors):
            y_bottom = anchors[idx+1]['y'] - 5
        else:
            y_bottom = height * 0.80 # Hasta abajo (antes del footer)
            
        # Variables para concatenar texto de este bloque
        desc_parts = [] # Lista de tuplas (y, x, text) para ordenar lectura
        unit_price = ""
        total_price = ""
        upc = ""
        
        # Barrer TODAS las palabras de la página para ver cuáles caen en este bloque Y
        for i in range(n_boxes):
            word = d['text'][i].strip()
            if not word: continue
            
            wy = d['top'][i]
            wx = d['left'][i]
            
            # Chequeamos si la palabra cae dentro de la franja vertical de este item
            if y_top <= wy < y_bottom:
                
                # Clasificar por Columna X
                
                # COLUMNA DESCRIPCIÓN (captura Modelo + Descripcion)
                if X_DESC_START < wx < X_DESC_END:
                    desc_parts.append((wy, wx, word))
                    
                # COLUMNA UPC / PAIS
                elif X_DESC_END < wx < X_PRICE_START:
                    if len(word) > 3 or word == "CHN": # Filtro básico
                        upc += " " + word
                        
                # COLUMNA PRECIO UNITARIO
                elif X_PRICE_START < wx < (width * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word): # Solo si parece dinero
                        unit_price = word
                        
                # COLUMNA TOTAL
                elif wx > (width * 0.88):
                    if re.match(r'[\d,]+\.\d{2}', word):
                        total_price = word

        # 4. ORDENAR Y LIMPIAR DESCRIPCIÓN
        # Ordenamos primero por altura (Y) y luego por izquierda (X) para leer en orden natural
        desc_parts.sort(key=lambda k: (k[0], k[1]))
        full_desc = " ".join([p[2] for p in desc_parts])
        
        items.append({
            "Cantidad": anchor['qty'],
            "Descripción": full_desc.strip(),
            "UPC/Ref": upc.strip(),
            "Precio Unit.": unit_price,
            "Total": total_price
        })
        
    return items

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_file = st.file_uploader("Sube la Factura Regal (PDF)", type=["pdf"])

if uploaded_file is not None:
    if st.button("🚀 Extraer Datos (Modo Avanzado)"):
        
        with st.status("Procesando con IA...", expanded=True) as status:
            try:
                images = convert_from_bytes(uploaded_file.read())
                
                # 1. Encabezado (Texto Completo)
                full_text = pytesseract.image_to_string(images[0], lang='spa', config='--psm 4')
                header_data = extract_header_data_improved(full_text)
                
                # 2. Items (Análisis Espacial)
                items_data = extract_items_advanced(images[0])
                
                status.update(label="¡Extracción Finalizada!", state="complete")
                
                # --- MOSTRAR RESULTADOS ---
                st.subheader(f"Factura: {header_data.get('Factura', 'ND')}")
                
                # Métricas
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Fecha", header_data.get('Fecha Emisión', ''))
                c2.metric("Orden #", header_data.get('Orden Compra', ''))
                c3.metric("Ref", header_data.get('Referencia', ''))
                c4.metric("Incoterm", header_data.get('Incoterm', ''))

                # Direcciones
                with st.expander("📍 Ver Direcciones", expanded=False):
                    col_a, col_b = st.columns(2)
                    col_a.info(f"**Vendido A:**\n\n{header_data.get('Vendido A', '')}")
                    col_b.info(f"**Embarcado A:**\n\n{header_data.get('Embarcado A', '')}")

                st.divider()
                st.subheader("📦 Productos (Descripción Completa)")
                
                if items_data:
                    df = pd.DataFrame(items_data)
                    st.dataframe(df, use_container_width=True)
                    
                    # --- GENERAR EXCEL ---
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        # Hoja 1
                        df_head = pd.DataFrame(list(header_data.items()), columns=["Campo", "Valor"])
                        df_head.to_excel(writer, sheet_name="Datos Generales", index=False)
                        
                        # Hoja 2
                        df.to_excel(writer, sheet_name="Items", index=False)
                        
                        # Formato
                        workbook = writer.book
                        ws = writer.sheets['Items']
                        ws.set_column('B:B', 60) # Ancho columna descripción
                        format_wrap = workbook.add_format({'text_wrap': True})
                        ws.set_column('B:B', 60, format_wrap)
                        
                    st.download_button(
                        "📥 Descargar Excel",
                        data=buffer.getvalue(),
                        file_name=f"Regal_{header_data.get('Factura', 'export')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                else:
                    st.warning("No se encontraron items. Intenta escanear con mayor calidad.")
                    
            except Exception as e:
                st.error(f"Error técnico: {e}")
