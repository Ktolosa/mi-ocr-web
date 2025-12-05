import streamlit as st
import camelot
import pandas as pd
import io
import os
import tempfile

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor de Tablas (Camelot)", layout="wide")
st.title("📊 Extractor de Tablas SAC (Camelot)")

st.info("Esta herramienta usa 'Camelot', una librería especializada en detectar tablas dentro de PDFs.")

# --- BARRA LATERAL ---
with st.sidebar:
    st.header("⚙️ Configuración")
    
    # Selector de Modo de Camelot
    flavor = st.radio(
        "Tipo de Tabla:",
        ["Lattice (Tiene líneas/bordes)", "Stream (Solo espacios blancos)"],
        index=0
    )
    flavor_code = 'lattice' if 'Lattice' in flavor else 'stream'
    
    st.markdown("---")
    st.write("**Rango de Páginas:**")
    st.caption("El SAC es grande. Prueba con pocas páginas primero.")
    page_mode = st.radio("Selección:", ["Todas", "Rango"])
    
    pages_arg = 'all'
    if page_mode == "Rango":
        pages_arg = st.text_input("Ej: 1, 2-5, 10", "1-5")

# ==========================================
# 🧠 LÓGICA CAMELOT
# ==========================================

def process_pdf_camelot(file_path, pages, mode):
    """
    Ejecuta Camelot para extraer tablas.
    """
    try:
        # Camelot lee el archivo y busca tablas
        tables = camelot.read_pdf(file_path, pages=pages, flavor=mode, strip_text='\n')
        return tables
    except Exception as e:
        return str(e)

# ==========================================
# 🖥️ INTERFAZ
# ==========================================

uploaded_file = st.file_uploader("Sube el archivo SAC (PDF)", type=["pdf"])

if uploaded_file is not None:
    
    if st.button("🚀 Extraer Tablas"):
        
        with st.status("Procesando PDF (Esto puede tardar)...", expanded=True) as status:
            try:
                # 1. Guardar archivo temporalmente (Camelot necesita un archivo físico, no bytes)
                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp_file:
                    tmp_file.write(uploaded_file.read())
                    tmp_path = tmp_file.name
                
                st.write(f"Leyendo páginas: {pages_arg} en modo {flavor_code}...")
                
                # 2. Ejecutar Camelot
                camelot_tables = process_pdf_camelot(tmp_path, pages_arg, flavor_code)
                
                # Borrar archivo temporal
                os.remove(tmp_path)
                
                if isinstance(camelot_tables, str): # Si devolvió error
                    status.update(label="Error", state="error")
                    st.error(f"Error de Camelot: {camelot_tables}")
                    st.warning("Intenta cambiar el 'Tipo de Tabla' en la izquierda.")
                
                elif len(camelot_tables) > 0:
                    status.update(label="¡Tablas Encontradas!", state="complete")
                    st.success(f"✅ Se detectaron {len(camelot_tables)} tablas.")
                    
                    # 3. Unir todas las tablas en un solo DataFrame
                    all_dfs = []
                    
                    # Mostrar las primeras tablas como ejemplo
                    st.subheader("Vista Previa (Primeras 3 tablas):")
                    
                    for i, table in enumerate(camelot_tables):
                        df = table.df # Convertir a Pandas
                        
                        # Limpieza básica: Usar la primera fila como header si parece texto
                        # (Opcional, depende del SAC)
                        # df.columns = df.iloc[0] 
                        # df = df[1:]
                        
                        all_dfs.append(df)
                        
                        if i < 3:
                            with st.expander(f"Tabla {i+1} (Página {table.page})", expanded=True):
                                st.dataframe(df, use_container_width=True)
                                st.caption(f"Precisión de lectura: {table.accuracy:.2f}%")

                    # 4. Generar Excel Consolidado
                    if all_dfs:
                        # Concatenar todo (Cuidado: si las columnas cambian, esto puede desordenarse)
                        # Para el SAC, usualmente la estructura es constante.
                        df_final = pd.concat(all_dfs, ignore_index=True)
                        
                        buffer = io.BytesIO()
                        with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                            df_final.to_excel(writer, index=False, sheet_name="Tablas_SAC")
                            
                            # Ajustar columnas
                            worksheet = writer.sheets['Tablas_SAC']
                            worksheet.set_column('A:Z', 20)
                        
                        st.download_button(
                            label="📥 Descargar Excel con Tablas",
                            data=buffer.getvalue(),
                            file_name="Tablas_SAC_Extraidas.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                else:
                    status.update(label="Sin resultados", state="error")
                    st.warning("No se detectaron tablas. Prueba cambiando a modo 'Stream' en la barra lateral.")
                    
            except Exception as e:
                st.error(f"Error crítico: {e}")    if modo_app == "3. IA Gemini (Para casos difíciles)":
        api_key = st.text_input("🔑 Google API Key", type="password")
        st.caption("Obtenla gratis en Google AI Studio")

# ==============================================================================
# 🧩 MÓDULO 1: REGAL TRADING (Tesseract - Lógica V16 Probada)
# ==============================================================================
def clean_text_block(text):
    if not text: return ""
    return " ".join(text.split())

def clean_upc(text):
    if not text: return ""
    text = text.replace(" ", "").strip()
    if len(text) > 8 and text.startswith("A"): return "4" + text[1:]
    return text

def extract_money(text_list):
    for text in reversed(text_list):
        clean = text.replace('$', '').replace('S', '').strip()
        if re.search(r'\d+[.,]\d{2}', clean): return clean
    return ""

def extract_items_regal(image):
    d = pytesseract.image_to_data(image, output_type=Output.DICT, lang='spa', config='--psm 6')
    n_boxes = len(d['text'])
    w, h = image.size
    
    X_QTY_LIMIT = w * 0.14     
    X_DESC_START = w * 0.14
    X_DESC_END = w * 0.58
    X_UPC_END = w * 0.72
    X_PRICE_END = w * 0.88
    
    candidates = []
    for i in range(n_boxes):
        text = d['text'][i].strip()
        cx = d['left'][i]
        cy = d['top'][i]
        if cy < h*0.25 or cy > h*0.85: continue
        if cx < X_QTY_LIMIT and re.match(r'^[0-9.,]+$', text):
            if d['height'][i] > 8: candidates.append({'y': cy, 'qty': text})

    valid_anchors = []
    for cand in candidates:
        row_y = cand['y']
        has_price = False
        for i in range(n_boxes):
            word = d['text'][i].strip()
            wy, wx = d['top'][i], d['left'][i]
            if (row_y - 20) <= wy <= (row_y + 20) and wx > X_UPC_END:
                if re.search(r'\d+[.,]\d{2}', word) or '$' in word: has_price = True; break
        if has_price: valid_anchors.append(cand)
    
    if not valid_anchors and candidates: valid_anchors = candidates
    valid_anchors.sort(key=lambda k: k['y'])
    
    final_anchors = []
    if valid_anchors:
        final_anchors.append(valid_anchors[0])
        for anc in valid_anchors[1:]:
            if anc['y'] - final_anchors[-1]['y'] > 15: final_anchors.append(anc)

    items = []
    for idx, anchor in enumerate(final_anchors):
        y_top = anchor['y'] - 30 
        y_bottom = final_anchors[idx+1]['y'] - 5 if idx + 1 < len(final_anchors) else anchor['y'] + 150
        desc, upc, unit, total = [], [], [], []
        for i in range(n_boxes):
            word = d['text'][i].strip()
            if not word: continue
            bx, by = d['left'][i], d['top'][i]
            if y_top <= by < y_bottom:
                if X_DESC_START < bx < X_DESC_END: desc.append((by, bx, word))
                elif X_DESC_END <= bx < X_UPC_END: 
                    if len(word)>3 and word!="CHN": upc.append(clean_upc(word))
                elif X_UPC_END <= bx < X_PRICE_END: unit.append(word)
                elif bx >= X_PRICE_END: total.append(word)
        desc.sort(key=lambda k: (k[0], k[1]))
        full_desc = " ".join([t[2] for t in desc])
        items.append({
            "Cantidad": anchor['qty'], "Descripción": full_desc, "UPC": " ".join(upc),
            "Precio Unit.": extract_money(unit), "Total": extract_money(total)
        })
    return items

def extract_header_regal(full_text):
    data = {}
    inv = re.search(r'(?:#|No\.|297107)\s*(\d{6})', full_text)
    if not inv: inv = re.search(r'#\s*(\d{4,6})', full_text)
    data['Factura'] = inv.group(1) if inv else ""
    date = re.search(r'(?:DATE|FECHA)\s*[:.,]?\s*([A-Za-z]{3}\s+\d{1,2}[,.]?\s+\d{4})', full_text, re.IGNORECASE)
    data['Fecha'] = date.group(1) if date else ""
    orden = re.search(r'(?:ORDER|ORDEN).*?[:#]\s*(\d+)', full_text, re.IGNORECASE)
    data['Orden'] = orden.group(1) if orden else ""
    ref = re.search(r'(?:FILE|REF)\s*[:.,]?\s*([A-Z0-9-]+)', full_text, re.IGNORECASE)
    data['Ref'] = ref.group(1) if ref else ""
    sold = re.search(r'SOLD TO/VENDIDO A:(.*?)(?=SHIP TO|124829)', full_text, re.DOTALL | re.IGNORECASE)
    data['Vendido A'] = clean_text_block(sold.group(1)) if sold else ""
    ship = re.search(r'SHIP TO/EMBARCADO A:(.*?)(?=PAYMENT|DUE DATE|PAGE)', full_text, re.DOTALL | re.IGNORECASE)
    data['Embarcado A'] = clean_text_block(ship.group(1)) if ship else ""
    return data

def is_duplicate(image):
    w, h = image.size
    header = image.crop((0, 0, w, h * 0.35))
    txt = pytesseract.image_to_string(header, lang='spa')
    return bool(re.search(r'Duplicado', txt, re.IGNORECASE))

# ==============================================================================
# 🧩 MÓDULO 2: EXTRACTOR DUCA (Tesseract - Lógica V24 Probada)
# ==============================================================================
def extract_duca_header(full_text):
    header = {}
    ref = re.search(r'Referencia[\s\S]*?(\d{8,})', full_text)
    header['Referencia'] = ref.group(1) if ref else "ND"
    fecha = re.search(r'(\d{2}/\d{2}/\d{4})', full_text)
    header['Fecha'] = fecha.group(1) if fecha else "ND"
    decl = re.search(r'Nombre.*?Declarante.*?\n(.*?)\n', full_text, re.IGNORECASE)
    header['Declarante'] = decl.group(1).replace('"','').strip() if decl else ""
    return header

def extract_duca_items(full_text):
    items = []
    clean_txt = full_text.replace('"', ' ').replace("'", "")
    blocks = re.split(r'22\.\s*Item', clean_txt)
    
    for i, block in enumerate(blocks[1:]):
        try:
            item_data = {}
            desc_match = re.search(r'29\.\s*Descripci.*?Comercial(.*?)(?=30\.|Valor FOB)', block, re.DOTALL | re.IGNORECASE)
            if not desc_match or len(desc_match.group(1)) < 3:
                desc_match = re.search(r'\d{8}[\s\n]+([A-Za-z].*?)(?=\n)', block)
            desc_final = clean_text_block(desc_match.group(1)) if desc_match else "No detectada"
            fob_match = re.search(r'30\.\s*Valor.*?FOB.*?([\d,]+\.\d{2})', block, re.DOTALL | re.IGNORECASE)
            total_match = re.search(r'38\.\s*Total.*?([\d,]+\.\d{2})', block, re.DOTALL | re.IGNORECASE)
            if not total_match:
                all_prices = re.findall(r'([\d,]+\.\d{2})', block)
                total_val = all_prices[-1] if all_prices else "0.00"
            else:
                total_val = total_match.group(1)

            item_data['Item #'] = i + 1
            item_data['Descripción'] = desc_final
            item_data['Valor FOB'] = fob_match.group(1) if fob_match else "0.00"
            item_data['Total'] = total_val
            items.append(item_data)
        except: continue
    return items

# ==============================================================================
# 🧩 MÓDULO 3: IA GEMINI (COMODÍN INTELIGENTE)
# ==============================================================================
def process_with_gemini(image, key):
    genai.configure(api_key=key)
    # IMPORTANTE: Usamos un modelo compatible si el Flash falla o la librería es vieja
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
    except:
        model = genai.GenerativeModel('gemini-pro')
    
    prompt = """
    Analiza esta factura o documento aduanal y extrae datos en JSON.
    
    REGLAS:
    1. Si ves 'Duplicado' arriba, marca "is_duplicate": true.
    2. Cabecera: Factura, Fecha, Orden, Referencia, Cliente (Sold To), Envio (Ship To).
    3. Items: Extrae la tabla. 
       - Corrige OCR (ej: 'A' al inicio de UPC es '4').
       - Si la descripción invade la columna de código, corrígelo.
    
    JSON Output:
    {
        "is_duplicate": false,
        "header": {"invoice": "", "date": "", "order": "", "customer": ""},
        "items": [
            {"qty": "", "desc": "", "code": "", "unit_price": "", "total": ""}
        ]
    }
    """
    try:
        response = model.generate_content([prompt, image])
        clean_json = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)
    except Exception as e:
        return {"error": str(e)}

# ==========================================
# 🖥️ LÓGICA PRINCIPAL
# ==========================================

uploaded_files = st.file_uploader("Sube tus archivos PDF", type=["pdf"], accept_multiple_files=True)

if uploaded_files:
    
    # ------------------ MODO 1: REGAL (TESSERACT) ------------------
    if "Regal" in modo_app:
        if st.button("🚀 Extraer Regal"):
            all_data = []
            bar = st.progress(0)
            for idx, f in enumerate(uploaded_files):
                try:
                    images = convert_from_bytes(f.read(), dpi=300)
                    header = {}; file_items = []; pg_count = 0
                    for i, img in enumerate(images):
                        if is_duplicate(img): continue
                        if pg_count == 0:
                            txt = pytesseract.image_to_string(img, lang='spa')
                            header = extract_header_regal(txt)
                        items = extract_items_regal(img)
                        if items: file_items.extend(items)
                        pg_count += 1
                    
                    if file_items:
                        for it in file_items:
                            row = header.copy(); row.update(it); row['Archivo'] = f.name
                            all_data.append(row)
                except Exception as e: st.error(f"Error {f.name}: {e}")
                bar.progress((idx+1)/len(uploaded_files))
            
            if all_data:
                df = pd.DataFrame(all_data)
                cols = ['Archivo', 'Factura', 'Fecha', 'Orden', 'Ref', 'Vendido A', 'Embarcado A', 'Cantidad', 'Descripción', 'UPC', 'Precio Unit.', 'Total']
                final_cols = [c for c in cols if c in df.columns]
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df[final_cols].to_excel(writer, index=False)
                    writer.sheets['Sheet1'].set_column('H:H', 50)
                st.success("✅ Listo")
                st.download_button("📥 Excel Regal", buffer.getvalue(), "Regal_Report.xlsx")

    # ------------------ MODO 2: DUCA (TESSERACT) ------------------
    elif "DUCA" in modo_app:
        if st.button("🚢 Extraer DUCA"):
            all_duca = []
            bar = st.progress(0)
            for idx, f in enumerate(uploaded_files):
                try:
                    images = convert_from_bytes(f.read(), dpi=300)
                    header_duca = {}
                    for i, img in enumerate(images):
                        txt = pytesseract.image_to_string(img, lang='spa', config='--psm 4')
                        if i == 0: header_duca = extract_duca_header(txt)
                        items = extract_duca_items(txt)
                        for it in items:
                            row = header_duca.copy(); row.update(it); row['Archivo'] = f.name
                            all_duca.append(row)
                except Exception as e: st.error(f"Error {f.name}: {e}")
                bar.progress((idx+1)/len(uploaded_files))
            
            if all_duca:
                df = pd.DataFrame(all_duca)
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df.to_excel(writer, index=False)
                    writer.sheets['Sheet1'].set_column('F:F', 50)
                st.success("✅ DUCA Lista")
                st.download_button("📥 Excel DUCA", buffer.getvalue(), "DUCA_Report.xlsx")

    # ------------------ MODO 3: IA GEMINI ------------------
    elif "Gemini" in modo_app:
        if not api_key: st.warning("⚠️ Ingresa tu API Key en el menú lateral."); st.stop()
        
        if st.button("✨ Procesar con IA"):
            ai_data = []
            bar = st.progress(0)
            for idx, f in enumerate(uploaded_files):
                try:
                    images = convert_from_bytes(f.read(), dpi=200)
                    header_ai = {}
                    for i, img in enumerate(images):
                        res = process_with_gemini(img, api_key)
                        if "error" in res: st.error(res['error']); continue
                        if res.get("is_duplicate"): continue
                        
                        if not header_ai: header_ai = res.get("header", {})
                        
                        for item in res.get("items", []):
                            row = header_ai.copy(); row.update(item); row['Archivo'] = f.name
                            ai_data.append(row)
                        time.sleep(1)
                except Exception as e: st.error(f"Error {f.name}: {e}")
                bar.progress((idx+1)/len(uploaded_files))
                
            if ai_data:
                df = pd.DataFrame(ai_data)
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df.to_excel(writer, index=False)
                st.success("✅ IA Finalizada")
                st.download_button("📥 Excel IA", buffer.getvalue(), "IA_Report.xlsx")

