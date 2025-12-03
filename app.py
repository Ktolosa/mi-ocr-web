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
st.title("📄 Extractor Especializado: Regal Trading (V. Final)")

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
    # Reemplaza saltos de línea por espacios y quita espacios dobles
    return " ".join(text.split())

# ==========================================
# 🧠 LÓGICA DE ITEMS (TU VERSIÓN PRESERVADA)
# ==========================================
def extract_items_by_coordinates(image):
    """
    Divide la imagen en columnas invisibles basadas en la posición X.
    (Código original conservado como solicitaste)
    """
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    img_width, img_height = image.size
    
    LIM_QTY = img_width * 0.12
    LIM_DESC = img_width * 0.55
    LIM_UPC = img_width * 0.72
    LIM_PRICE = img_width * 0.88
    
    items = []
    current_item = {"qty": "", "desc": "", "upc": "", "unit": "", "total": "", "top_y": 0}
    start_reading = False
    
    for i in range(n_boxes):
        text = d['text'][i].strip()
        if not text: continue
        
        x = d['left'][i]
        y = d['top'][i]
        
        # Detectores de inicio/fin
        if "QUANTITY" in text or "CANTIDAD" in text or "DESCRIPTION" in text:
            start_reading = True
            continue 
        if "SUBTOTAL" in text or "TOTAL" in text or "FIRMA" in text or "DUE DATE" in text:
            if y > img_height * 0.4: start_reading = False
        
        if not start_reading: continue
        
        # 1. Detectar inicio de item (Cantidad a la izquierda)
        if x < LIM_QTY and re.match(r'^\d+$', text):
            if current_item["qty"]:
                items.append(current_item)
            current_item = {
                "qty": text, "desc": "", "upc": "", "unit": "", "total": "", "top_y": y
            }
            continue 
            
        # 2. Agregar datos al item actual
        if current_item["qty"]:
            if y > current_item["top_y"] + 150: continue 

            if LIM_QTY < x < LIM_DESC:
                current_item["desc"] += " " + text
            elif LIM_DESC < x < LIM_UPC:
                if len(text) > 3 or text == "CHN": current_item["upc"] += " " + text
            elif LIM_UPC < x < LIM_PRICE:
                if "$" not in text: current_item["unit"] += text
            elif x > LIM_PRICE:
                if "$" not in text: current_item["total"] += text

    if current_item["qty"]:
        items.append(current_item)
        
    for item in items:
        for k in item:
            if isinstance(item[k], str): item[k] = item[k].strip()
                
    return items

# ==========================================
# 🧠 LÓGICA DE CABECERA (MEJORADA Y ROBUSTA)
# ==========================================
def extract_header_data(full_text):
    """
    Extrae datos de la cabecera usando Regex Multilínea (DOTALL).
    Captura direcciones completas y datos dispersos.
    """
    data = {}
    
    # 1. FACTURA (Busca # seguido de 6 dígitos)
    # Busca tanto "COMMERCIAL INVOICE ... #" como solo "# 123456"
    inv = re.search(r'(?:#|No\.|297107)\s*(\d{6})', full_text)
    if not inv: 
        inv = re.search(r'#\s*(\d{6})', full_text)
    data['Factura'] = inv.group(1) if inv else ""

    # 2. FECHA DE EMISIÓN
    # Soporta: AUG 07, 2025 | JUN 30, 2025
    date = re.search(r'(?:DATE|FECHA).*?:\s*([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})', full_text, re.IGNORECASE)
    data['Fecha'] = date.group(1) if date else ""

    # 3. ORDEN DE COMPRA
    # Busca "ORDER" u "ORDEN" seguido de : o # y luego números
    orden = re.search(r'(?:ORDER|ORDEN).*?[:#]\s*(\d+)', full_text, re.IGNORECASE)
    data['Orden'] = orden.group(1) if orden else ""

    # 4. REFERENCIA / FILE
    # Busca FILE/REF : XXXXX
    ref = re.search(r'(?:FILE|REF).*?:\s*([A-Z0-9-]+)', full_text, re.IGNORECASE)
    data['Ref'] = ref.group(1) if ref else ""

    # 5. BILL OF LADING (B/L)
    bl = re.search(r'B/L#\s*[:.,]?\s*([A-Z0-9]+)', full_text, re.IGNORECASE)
    data['BL'] = bl.group(1) if bl else ""

    # 6. INCOTERM
    incoterm = re.search(r'INCOTERM\s*[:.,]?\s*([A-Z]+)', full_text, re.IGNORECASE)
    data['Incoterm'] = incoterm.group(1) if incoterm else ""

    # 7. FECHA DE VENCIMIENTO
    due_date = re.search(r'(?:DUE DATE|VENCIMIENTO).*?([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})', full_text, re.IGNORECASE)
    data['Vencimiento'] = due_date.group(1) if due_date else ""

    # --- DIRECCIONES (LÓGICA DE BLOQUES) ---
    
    # VENDIDO A: Captura todo desde "VENDIDO A:" hasta que encuentra "SHIP TO" o un código postal/teléfono
    # Usamos re.DOTALL para que el punto (.) capture también los saltos de línea
    sold_block = re.search(r'(?:SOLD TO|VENDIDO A)\s*:?\s*(.*?)(?=\n\s*(?:SHIP TO|EMBARCADO|124829))', full_text, re.DOTALL | re.IGNORECASE)
    data['Vendido A'] = clean_text_block(sold_block.group(1)) if sold_block else ""

    # EMBARCADO A: Captura todo desde "EMBARCADO A:" hasta "DATE", "PAYMENT" o fin de bloque
    ship_block = re.search(r'(?:SHIP TO|EMBARCADO A)\s*:?\s*(.*?)(?=\n\s*(?:DATE|FECHA|PAYMENT|CONDICION))', full_text, re.DOTALL | re.IGNORECASE)
    data['Embarcado A'] = clean_text_block(ship_block.group(1)) if ship_block else ""
    
    return data

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_files = st.file_uploader("Sube tus Facturas Regal (PDF)", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    if st.button("🚀 Extraer Datos"):
        
        all_invoices_data = [] # Para el Excel consolidado si subes varios
        
        # Barra de progreso general
        progress_bar = st.progress(0)
        
        for idx, uploaded_file in enumerate(uploaded_files):
            with st.expander(f"📄 Procesando: {uploaded_file.name}", expanded=True):
                try:
                    # 1. Convertir PDF a imágenes (Alta calidad para leer letras pequeñas)
                    images = convert_from_bytes(uploaded_file.read(), dpi=300)
                    
                    # 2. Extraer CABECERA (Usamos la primera página completa)
                    full_text_page1 = pytesseract.image_to_string(images[0], lang='spa')
                    header = extract_header_data(full_text_page1)
                    
                    # 3. Extraer ITEMS (Recorremos TODAS las páginas)
                    all_items = []
                    for img in images:
                        page_items = extract_items_by_coordinates(img)
                        all_items.extend(page_items)
                    
                    # --- MOSTRAR RESULTADOS INDIVIDUALES ---
                    c1, c2, c3, c4 = st.columns(4)
                    c1.success(f"Factura: {header['Factura']}")
                    c2.info(f"Orden: {header['Orden']}")
                    c3.metric("Fecha", header['Fecha'])
                    c4.metric("Items", len(all_items))
                    
                    # Direcciones
                    st.caption(f"📍 **Cliente:** {header['Vendido A']}")
                    
                    # Tabla de Items
                    if all_items:
                        df = pd.DataFrame(all_items)
                        df.columns = ["Cantidad", "Descripción", "UPC", "Unitario", "Total", "Pos"]
                        st.dataframe(df.drop(columns=["Pos"]), use_container_width=True)
                        
                        # Guardar para Excel final
                        # Agregamos los datos del header a cada fila del item para que sea una tabla plana
                        for item in all_items:
                            row = header.copy()
                            row.update(item)
                            del row["Pos"] # No necesitamos la posición Y en el excel
                            all_invoices_data.append(row)
                    else:
                        st.warning("No se detectaron items en este archivo.")
                        
                except Exception as e:
                    st.error(f"Error en {uploaded_file.name}: {e}")
            
            # Actualizar barra
            progress_bar.progress((idx + 1) / len(uploaded_files))

        # --- EXCEL FINAL (CONSOLIDADO) ---
        if all_invoices_data:
            df_final = pd.DataFrame(all_invoices_data)
            
            # Reordenar columnas para que se vea profesional
            cols_order = ['Factura', 'Fecha', 'Orden', 'Ref', 'BL', 'Incoterm', 'Vencimiento', 
                          'Vendido A', 'Embarcado A', 'qty', 'desc', 'upc', 'unit', 'total']
            
            # Ajustar nombres de columnas si no coinciden exactamente con las claves internas
            # Normalizamos nombres para el usuario final
            rename_map = {
                'qty': 'Cantidad', 'desc': 'Descripción', 'upc': 'UPC/Ref', 
                'unit': 'Precio Unit.', 'total': 'Total Línea'
            }
            df_final.rename(columns=rename_map, inplace=True)
            
            # Generar Excel en memoria
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_final.to_excel(writer, sheet_name="Reporte Consolidado", index=False)
                
                # Ajuste de anchos de columna
                worksheet = writer.sheets['Reporte Consolidado']
                worksheet.set_column('A:G', 15) # Datos generales
                worksheet.set_column('H:I', 40) # Direcciones
                worksheet.set_column('K:K', 60) # Descripción del producto
                
            st.success("✅ ¡Procesamiento completado!")
            st.download_button(
                label="📥 Descargar Excel Consolidado",
                data=buffer.getvalue(),
                file_name="Reporte_Facturas_Regal.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
