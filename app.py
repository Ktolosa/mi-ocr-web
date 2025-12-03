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
st.title("📄 Extractor Especializado: Regal Trading (Multi-Formato)")

# --- VERIFICACIÓN DE SISTEMA ---
if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado en el servidor.")
    st.stop()

# ==========================================
# 🧠 LÓGICA DE EXTRACCIÓN (AJUSTADA PARA ACCESORIOS)
# ==========================================

def clean_decimal(text):
    """Limpia símbolos de moneda y espacios"""
    if not text: return "0.00"
    clean = re.sub(r'[^\d.]', '', text)
    return clean if clean else "0.00"

def extract_items_by_coordinates(image):
    """
    Lógica mejorada: Detecta cantidades desplazadas y descripciones largas
    que invaden la columna UPC.
    """
    # 1. Obtener datos (Usamos psm 6 para bloques de texto)
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    img_width, img_height = image.size
    
    # --- AJUSTE DE LÍMITES (CRUCIAL PARA PDF 5284) ---
    # Aumentamos el límite de cantidad al 14% (antes 12%) para atrapar números movidos
    LIM_QTY = img_width * 0.14
    
    # La descripción empieza antes (12%)
    LIM_DESC = img_width * 0.55
    LIM_UPC = img_width * 0.72
    LIM_PRICE = img_width * 0.88
    
    items = []
    
    current_item = {
        "qty": "", "desc": "", "upc": "", "unit": "", "total": "", "top_y": 0
    }
    
    start_reading = False
    
    for i in range(n_boxes):
        text = d['text'][i].strip()
        if not text: continue
        
        x = d['left'][i]
        y = d['top'][i]
        
        # --- DETECTOR DE INICIO/FIN ---
        if "QUANTITY" in text or "CANTIDAD" in text or "DESCRIPTION" in text:
            start_reading = True
            continue 
            
        # Fin de tabla (Totales, Firmas, Notas)
        if "SUBTOTAL" in text or "TOTAL" in text or "FIRMA" in text or "DUE DATE" in text:
            if y > img_height * 0.4: 
                start_reading = False
        
        if not start_reading: continue
        
        # --- LÓGICA DE ASIGNACIÓN INTELIGENTE ---
        
        # 1. DETECTAR NUEVO ITEM (Cantidad)
        # Usamos LIM_QTY expandido (14%) y regex estricto de números
        if x < LIM_QTY and re.match(r'^\d+$', text):
            # Guardar anterior
            if current_item["qty"]:
                items.append(current_item)
            
            # Nuevo item
            current_item = {
                "qty": text, "desc": "", "upc": "", "unit": "", "total": "", "top_y": y
            }
            continue 
            
        # 2. AGREGAR DATOS AL ITEM ACTUAL
        if current_item["qty"]:
            # Filtro vertical: Si el texto está muy lejos (>150px) abajo, lo ignoramos
            # Esto evita mezclar basura del pie de página
            if y > current_item["top_y"] + 150: 
                continue 

            # -- COLUMNA DESCRIPCIÓN --
            if LIM_QTY < x < LIM_DESC:
                current_item["desc"] += " " + text
                
            # -- COLUMNA UPC (CON LÓGICA DE DESBORDAMIENTO) --
            elif LIM_DESC < x < LIM_UPC:
                # AQUÍ ESTÁ EL TRUCO PARA EL PDF 5284:
                # Si el texto en la columna UPC parece un código técnico (tiene letras, barras, guiones)
                # y NO es "CHN" ni solo números, entonces es parte de la DESCRIPCIÓN larga.
                
                is_pure_upc = re.match(r'^\d+$', text) or text == "CHN"
                
                if is_pure_upc:
                    if len(text) > 3 or text == "CHN": # Filtro de ruido
                        current_item["upc"] += " " + text
                else:
                    # Es parte de la descripción que se salió de su columna
                    current_item["desc"] += " " + text
                    
            # -- COLUMNA PRECIO --
            elif LIM_UPC < x < LIM_PRICE:
                if "$" not in text:
                    current_item["unit"] += text
                    
            # -- COLUMNA TOTAL --
            elif x > LIM_PRICE:
                if "$" not in text:
                    current_item["total"] += text

    # Guardar último item
    if current_item["qty"]:
        items.append(current_item)
        
    # Limpieza final
    for item in items:
        for k in item:
            if isinstance(item[k], str): item[k] = item[k].strip()
                
    return items

def extract_header_data(full_text):
    """Extrae datos generales (Factura, Fecha) usando Regex simple"""
    data = {}
    
    # Factura
    inv = re.search(r'(?:#|No\.)\s*(\d{6})', full_text)
    if not inv: inv = re.search(r'(?:#|No\.)\s*(\d{4})', full_text) # Soporte para 4 digitos (5284)
    data['Factura'] = inv.group(1) if inv else "No encontrada"
    
    # Fecha
    date = re.search(r'(?:DATE|FECHA)\s*[:.,]?\s*([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})', full_text, re.IGNORECASE)
    data['Fecha'] = date.group(1) if date else ""
    
    # Orden
    orden = re.search(r'(?:ORDER|ORDEN)\s*#?\s*[:.,]?\s*(\d+)', full_text, re.IGNORECASE)
    data['Orden'] = orden.group(1) if orden else ""
    
    return data

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_file = st.file_uploader("Sube la Factura Regal (PDF)", type=["pdf"])

if uploaded_file is not None:
    if st.button("🚀 Extraer Datos"):
        
        with st.status("Analizando estructura visual...", expanded=True) as status:
            try:
                # 1. Convertir a imagen (Alta calidad DPI 300 para leer textos pequeños)
                images = convert_from_bytes(uploaded_file.read(), dpi=300)
                
                # 2. Header (Solo página 1)
                full_text = pytesseract.image_to_string(images[0], lang='spa')
                st.write("Leyendo encabezado...")
                header_data = extract_header_data(full_text)
                
                # 3. Items (Todas las páginas)
                st.write("Escaneando items (Modo Elástico)...")
                all_items = []
                for img in images:
                    items = extract_items_by_coordinates(img)
                    all_items.extend(items)
                
                status.update(label="¡Completado!", state="complete")
                
                # --- VISUALIZACIÓN ---
                st.subheader(f"Factura #{header_data['Factura']}")
                
                col1, col2, col3 = st.columns(3)
                col1.metric("Fecha", header_data['Fecha'])
                col2.metric("Orden", header_data['Orden'])
                col3.metric("Total Items", len(all_items))
                
                st.divider()
                
                if all_items:
                    df = pd.DataFrame(all_items)
                    # Columnas amigables
                    df.columns = ["Cantidad", "Descripción / Modelo", "UPC", "Precio Unit.", "Total Línea", "Y-Pos"]
                    st.dataframe(df.drop(columns=["Y-Pos"]), use_container_width=True)
                    
                    # --- EXPORTAR EXCEL ---
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        pd.DataFrame([header_data]).to_excel(writer, sheet_name="General", index=False)
                        df.drop(columns=["Y-Pos"]).to_excel(writer, sheet_name="Items", index=False)
                        
                        # Formato
                        workbook = writer.book
                        worksheet = writer.sheets['Items']
                        worksheet.set_column('B:B', 60) # Columna descripción ancha
                        format_wrap = workbook.add_format({'text_wrap': True})
                        worksheet.set_column('B:B', 60, format_wrap)
                    
                    st.download_button(
                        "📥 Descargar Excel",
                        data=buffer.getvalue(),
                        file_name=f"Regal_{header_data['Factura']}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                else:
                    st.warning("No se pudieron detectar items. Verifica la calidad del PDF.")
                    
            except Exception as e:
                st.error(f"Error técnico: {e}")
