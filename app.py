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
st.title("📄 Extractor Especializado: Regal Trading (V2 Mejorada)")

# --- VERIFICACIÓN DE SISTEMA ---
if not shutil.which("tesseract"):
    st.error("❌ Error: Tesseract no está instalado en el servidor.")
    st.stop()

# ==========================================
# 🛠️ UTILIDADES
# ==========================================

def clean_text(text):
    """Limpia espacios dobles y saltos de línea"""
    return " ".join(text.split())

# ==========================================
# 🧠 LÓGICA DE EXTRACCIÓN POR ZONAS MEJORADA
# ==========================================

def extract_items_by_coordinates(image):
    """
    Divide la imagen en columnas invisibles basadas en la posición X de cada palabra.
    Mejorada para capturar descripciones multilínea.
    """
    # 1. Obtener datos con coordenadas
    # Usamos psm 6 (bloque de texto) para mantener el orden
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    
    n_boxes = len(d['text'])
    img_width, img_height = image.size
    
    # --- DEFINICIÓN DE COLUMNAS (Ajustadas para Regal Trading) ---
    # 0%  - 13% : Cantidad
    # 13% - 58% : Descripción / Modelo (Aumentado para que quepa todo)
    # 58% - 74% : UPC / País
    # 74% - 88% : Precio Unitario
    # 88% - 100%: Total
    
    LIM_QTY = img_width * 0.13
    LIM_DESC = img_width * 0.58
    LIM_UPC = img_width * 0.74
    LIM_PRICE = img_width * 0.88
    
    items = []
    
    # Estado actual
    current_item = {
        "qty": "", "desc": "", "upc": "", "unit": "", "total": "", "top_y": 0
    }
    
    start_reading = False
    
    for i in range(n_boxes):
        text = d['text'][i].strip()
        if not text: continue
        
        # Coordenadas
        x = d['left'][i]
        y = d['top'][i]
        
        # --- DETECTOR DE INICIO/FIN ---
        if "QUANTITY" in text or "DESCRIPTION" in text:
            start_reading = True
            continue 
            
        # Dejamos de leer si llegamos al pie de página (Totales, Firmas, Fechas inferiores)
        if "SUBTOTAL" in text or "TOTAL" in text or "FIRMA" in text or "DUE DATE" in text:
            if y > img_height * 0.4: 
                start_reading = False
        
        if not start_reading: continue
        
        # --- LÓGICA DE ASIGNACIÓN ---
        
        # 1. DETECTAR NUEVO ITEM (Número entero a la izquierda absoluta)
        # Regex mejorado: Solo dígitos, sin puntos ni letras (ej: "234")
        if x < LIM_QTY and re.match(r'^\d+$', text):
            # Guardar el item anterior si existe
            if current_item["qty"]:
                # Limpieza final antes de guardar
                current_item["desc"] = clean_text(current_item["desc"])
                items.append(current_item)
            
            # Iniciar nuevo item
            current_item = {
                "qty": text, 
                "desc": "", 
                "upc": "", 
                "unit": "", 
                "total": "",
                "top_y": y 
            }
            continue 
            
        # 2. AGREGAR DATOS AL ITEM ACTUAL
        if current_item["qty"]:
            # Tolerancia vertical: Aceptamos texto hasta 150px más abajo del número de cantidad
            # Esto permite capturar descripciones de 2 o 3 líneas
            if y > current_item["top_y"] + 150: 
                continue 

            # Asignar a columna según posición X
            
            # COLUMNA DESCRIPCIÓN
            if LIM_QTY < x < LIM_DESC:
                current_item["desc"] += " " + text
                
            # COLUMNA UPC / PAIS
            elif LIM_DESC < x < LIM_UPC:
                # Filtro: Ignorar palabras muy cortas (ruido) a menos que sea CHN
                if len(text) > 3 or text == "CHN": 
                    current_item["upc"] += " " + text
                    
            # COLUMNA PRECIO UNITARIO
            elif LIM_UPC < x < LIM_PRICE:
                if "$" not in text: # Ignoramos símbolo moneda
                    current_item["unit"] += text
                    
            # COLUMNA TOTAL
            elif x > LIM_PRICE:
                if "$" not in text:
                    current_item["total"] += text

    # Guardar el último item
    if current_item["qty"]:
        current_item["desc"] = clean_text(current_item["desc"])
        items.append(current_item)
                
    return items

def extract_header_data(full_text):
    """Extrae datos generales con patrones más flexibles"""
    data = {}
    
    # Factura (Busca # o No. seguido de dígitos)
    inv = re.search(r'(?:#|No\.|297107)\s*(\d{6})', full_text)
    if not inv: 
        # Intento secundario: buscar bloque numérico aislado
        inv = re.search(r'#\s*(\d{6})', full_text)
    data['Factura'] = inv.group(1) if inv else ""
    
    # Fecha (Soporta formatos tipo AUG 07, 2025 o 07-08-2025)
    date = re.search(r'(?:DATE|FECHA)\s*[:.,]?\s*([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})', full_text, re.IGNORECASE)
    data['Fecha'] = date.group(1) if date else ""
    
    # Orden
    orden = re.search(r'(?:ORDER|ORDEN)\s*#?\s*[:.,]?\s*(\d+)', full_text, re.IGNORECASE)
    data['Orden'] = orden.group(1) if orden else ""
    
    # Referencia
    ref = re.search(r'(?:FILE|REF)\s*[:.,]?\s*([A-Z0-9]+)', full_text, re.IGNORECASE)
    data['Ref'] = ref.group(1) if ref else ""
    
    return data

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_file = st.file_uploader("Sube la Factura Regal (PDF)", type=["pdf"])

if uploaded_file is not None:
    if st.button("🚀 Extraer Datos"):
        
        with st.status("Analizando documento...", expanded=True) as status:
            try:
                # 1. Convertir a imagen (DPI aumentado para mejor lectura)
                images = convert_from_bytes(uploaded_file.read(), dpi=200)
                target_img = images[0]
                
                # 2. Texto completo para cabecera
                full_text = pytesseract.image_to_string(target_img, lang='spa')
                
                # A. Datos Generales
                st.write("Leyendo encabezado...")
                header_data = extract_header_data(full_text)
                
                # B. Items por Coordenadas
                st.write("Escaneando tabla de productos...")
                items_data = extract_items_by_coordinates(target_img)
                
                status.update(label="¡Completado!", state="complete")
                
                # --- VISUALIZACIÓN ---
                st.subheader(f"Factura #{header_data['Factura']}")
                
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Fecha", header_data['Fecha'])
                col2.metric("Orden", header_data['Orden'])
                col3.metric("Ref", header_data['Ref'])
                col4.metric("Items", len(items_data))
                
                st.divider()
                
                # Mostrar Tabla
                if items_data:
                    df = pd.DataFrame(items_data)
                    df.columns = ["Cantidad", "Descripción / Modelo", "UPC / País", "Precio Unit.", "Total Línea", "Y-Pos"]
                    # Eliminamos columna técnica Y-Pos para la vista
                    df_show = df.drop(columns=["Y-Pos"])
                    
                    st.dataframe(df_show, use_container_width=True)
                    
                    # --- EXPORTAR EXCEL ---
                    buffer = io.BytesIO()
                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                        # Hoja 1: Resumen
                        pd.DataFrame([header_data]).to_excel(writer, sheet_name="General", index=False)
                        # Hoja 2: Detalle
                        df_show.to_excel(writer, sheet_name="Items", index=False)
                        
                        # Formato bonito
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
                    st.warning("No se pudieron detectar items. Intenta escanear con más calidad.")
                    
            except Exception as e:
                st.error(f"Error técnico: {e}")
