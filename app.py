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

st.title("📄 Extractor Especializado: Regal Trading")



# --- VERIFICACIÓN DE SISTEMA ---

if not shutil.which("tesseract"):

    st.error("❌ Error: Tesseract no está instalado en el servidor.")

    st.stop()



# ==========================================

# 🧠 LÓGICA DE EXTRACCIÓN POR ZONAS (NUEVO)

# ==========================================



def clean_decimal(text):

    """Limpia símbolos de moneda y espacios para dejar solo números decimales"""

    if not text: return "0.00"

    # Quitar todo lo que no sea digito o punto

    clean = re.sub(r'[^\d.]', '', text)

    # Si tiene comas en vez de puntos, arreglar

    return clean if clean else "0.00"



def extract_items_by_coordinates(image):

    """

    Divide la imagen en columnas invisibles basadas en la posición X de cada palabra.

    Esta función es específica para el formato visual de Regal Trading.

    """

    # 1. Obtener datos con coordenadas (Left, Top, Width, Text)

    # --psm 6 asume un bloque de texto uniforme

    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')

    

    n_boxes = len(d['text'])

    img_width, img_height = image.size

    

    # Definimos los "Límites de las Columnas" basados en porcentajes del ancho de la página

    # Según tu imagen:

    # 0%  - 12% : Cantidad

    # 12% - 55% : Modelo / Descripción

    # 55% - 72% : País / UPC

    # 72% - 85% : Precio Unitario

    # 85% - 100%: Total

    

    LIM_QTY = img_width * 0.12

    LIM_DESC = img_width * 0.55

    LIM_UPC = img_width * 0.72

    LIM_PRICE = img_width * 0.88

    

    items = []

    

    # Variables temporales para construir el item actual

    current_item = {

        "qty": "", "desc": "", "upc": "", "unit": "", "total": "", "top_y": 0

    }

    

    # Rango vertical de seguridad (para no leer encabezados ni pies de página)

    # Solo leemos items que estén en el "cuerpo" de la factura

    start_reading = False

    

    # Agrupamos palabras por líneas visuales (Y-axis) con un margen de error de 10px

    lines = {} 

    

    for i in range(n_boxes):

        text = d['text'][i].strip()

        if not text: continue

        

        # Coordenadas

        x = d['left'][i]

        y = d['top'][i]

        w = d['width'][i]

        h = d['height'][i]

        

        # --- DETECTOR DE INICIO/FIN ---

        # Empezamos a leer items cuando pasamos los encabezados de la tabla

        if "QUANTITY" in text or "CANTIDAD" in text or "DESCRIPTION" in text:

            start_reading = True

            continue # Saltamos la palabra del encabezado

            

        # Dejamos de leer si llegamos a los totales o firmas

        if "SUBTOTAL" in text or "TOTAL" in text or "FIRMA" in text or "DUE DATE" in text:

            if y > img_height * 0.4: # Solo si está en la mitad inferior

                start_reading = False

        

        if not start_reading: continue

        

        # --- LÓGICA DE ASIGNACIÓN A COLUMNAS ---

        

        # 1. Detectar si es el INICIO de un nuevo item (La columna Cantidad es la clave)

        # Si el texto está a la izquierda (x < LIM_QTY) y es un número entero

        if x < LIM_QTY and re.match(r'^\d+$', text):

            # Si ya teníamos un item construyéndose, lo guardamos

            if current_item["qty"]:

                items.append(current_item)

            

            # Empezamos uno nuevo

            current_item = {

                "qty": text, 

                "desc": "", 

                "upc": "", 

                "unit": "", 

                "total": "",

                "top_y": y # Guardamos la altura para referencia

            }

            continue # Ya procesamos esta palabra

            

        # 2. Si no es cantidad, agregamos al item actual (si existe)

        if current_item["qty"]:

            # Verificamos que no esté demasiado lejos verticalmente (ej. más de 100px abajo es otro bloque)

            if y > current_item["top_y"] + 150: 

                continue 



            # Asignar a columna según posición X

            if LIM_QTY < x < LIM_DESC:

                current_item["desc"] += " " + text

            elif LIM_DESC < x < LIM_UPC:

                # Aquí suele estar CHN y el UPC. Filtramos basura.

                if len(text) > 3 or text == "CHN": 

                    current_item["upc"] += " " + text

            elif LIM_UPC < x < LIM_PRICE:

                # Precio unitario (ignoramos el símbolo $)

                if "$" not in text:

                    current_item["unit"] += text

            elif x > LIM_PRICE:

                # Total (ignoramos el símbolo $)

                if "$" not in text:

                    current_item["total"] += text



    # Agregar el último item pendiente

    if current_item["qty"]:

        items.append(current_item)

        

    # Limpieza final de espacios

    for item in items:

        for k in item:

            if isinstance(item[k], str):

                item[k] = item[k].strip()

                

    return items



def extract_header_data(full_text):

    """Extrae datos generales (Factura, Fecha) usando Regex simple"""

    data = {}

    # Factura

    inv = re.search(r'(?:#|No\.)\s*(\d{6})', full_text)

    data['Factura'] = inv.group(1) if inv else "No encontrada"

    

    # Fecha

    date = re.search(r'DATE/FECHA\s*[:.,]?\s*([A-Za-z]{3}\s\d{2},\s\d{4})', full_text)

    data['Fecha'] = date.group(1) if date else ""

    

    # Orden

    orden = re.search(r'ORDER/ORDEN\s*#\s*[:.,]?\s*(\d+)', full_text)

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

                # 1. Convertir a imagen

                images = convert_from_bytes(uploaded_file.read())

                

                # 2. Procesar primera página (usualmente ahí están los items)

                # Obtenemos texto crudo para cabecera y datos visuales para items

                full_text = pytesseract.image_to_string(images[0], lang='spa')

                

                # A. Datos Generales

                st.write("Leeyendo cabecera...")

                header_data = extract_header_data(full_text)

                

                # B. Items por Coordenadas

                st.write("Escaneando columnas invisibles...")

                items_data = extract_items_by_coordinates(images[0])

                

                status.update(label="¡Completado!", state="complete")

                

                # --- VISUALIZACIÓN ---

                st.subheader(f"Factura #{header_data['Factura']}")

                

                # Mostrar Cabecera

                col1, col2, col3 = st.columns(3)

                col1.metric("Fecha", header_data['Fecha'])

                col2.metric("Orden Compra", header_data['Orden'])

                col3.metric("Items Detectados", len(items_data))

                

                st.divider()

                

                # Mostrar Tabla

                if items_data:

                    df = pd.DataFrame(items_data)

                    # Renombrar columnas para que se vea bonito

                    df.columns = ["Cantidad", "Descripción / Modelo", "UPC / Origen", "Precio Unit.", "Total Línea", "Y-Pos"]

                    # Quitar columna técnica Y-Pos

                    df = df.drop(columns=["Y-Pos"])

                    

                    st.dataframe(df, use_container_width=True)

                    

                    # --- EXPORTAR EXCEL ---

                    buffer = io.BytesIO()

                    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:

                        # Hoja 1: Resumen

                        pd.DataFrame([header_data]).to_excel(writer, sheet_name="General", index=False)

                        # Hoja 2: Detalle

                        df.to_excel(writer, sheet_name="Items", index=False)

                        

                        # Formato bonito

                        workbook = writer.book

                        worksheet = writer.sheets['Items']

                        worksheet.set_column('B:B', 50) # Columna descripción ancha

                    

                    st.download_button(

                        "📥 Descargar Excel",

                        data=buffer.getvalue(),

                        file_name=f"Regal_{header_data['Factura']}.xlsx",

                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

                    )

                else:

                    st.warning("No se pudieron detectar items. Verifica que la imagen sea clara.")

                    st.text(full_text) # Debug

                    

            except Exception as e:

                st.error(f"Error técnico: {e}")
