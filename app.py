import streamlit as st
import camelot
import pandas as pd
import io
import os
import tempfile
import shutil
import re

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="SAC Cleaner V2", layout="wide")
st.title("📊 Extractor SAC (Solo Datos Limpios)")

# Verificar Dependencias
if not shutil.which("gs"):
    st.error("❌ Error Crítico: Ghostscript no está instalado.")
    st.stop()

# --- BARRA LATERAL ---
with st.sidebar:
    st.header("⚙️ Configuración")
    st.info("Este modo usa 'Camelot Lattice' para detectar cuadrículas y elimina todo lo que no sea dato.")
    
    # Rango de páginas (Vital para el SAC)
    pages_input = st.text_input("Rango de páginas (Ej: 10-20)", "10-15")
    
    st.divider()
    st.write("Estructura de Salida:")
    st.code("Col 1: CÓDIGO\nCol 2: DESCRIPCIÓN\nCol 3: DAI %")

# ==========================================
# 🧠 LÓGICA DE LIMPIEZA INTELIGENTE
# ==========================================

def clean_sac_data(df):
    """
    Recibe un DataFrame crudo de Camelot y lo limpia agresivamente.
    """
    # 1. SELECCIÓN DE COLUMNAS
    # El SAC suele tener: Código | Descripción | DAI | ISC | Otros...
    # Nos quedamos estrictamente con las primeras 3 columnas (0, 1, 2)
    if df.shape[1] < 3:
        return None # Tabla inservible o mal detectada
    
    df = df.iloc[:, 0:3] 
    
    # Renombrar para estandarizar
    df.columns = ["CODIGO", "DESCRIPCION", "DAI"]
    
    # 2. LIMPIEZA DE FILAS (El paso más importante)
    # Convertimos a string para poder buscar palabras clave
    df["CODIGO"] = df["CODIGO"].astype(str)
    
    # Definimos palabras "prohibidas" que indican que la fila NO es un dato
    # (Encabezados de tabla, Títulos de Capítulo, Notas al pie que quedaron dentro)
    bad_words = [
        "CÓDIGO", "CODIGO", "SAC", "DESCRIPCIÓN", "DESCRIPCION", 
        "TASA", "DAI", "DERECHOS", "CAPÍTULO", "SECCIÓN", "NOTAS"
    ]
    
    # Regex para encontrar cualquiera de esas palabras (ignorando mayúsculas/minúsculas)
    pattern = '|'.join(bad_words)
    
    # Filtro 1: Eliminar filas donde la columna CODIGO tenga esas palabras
    df = df[~df["CODIGO"].str.contains(pattern, case=False, na=False)]
    
    # Filtro 2: Eliminar filas donde la DESCRIPCIÓN sea igual al CÓDIGO (error común de merge)
    df = df[df["CODIGO"] != df["DESCRIPCION"]]
    
    # Filtro 3: Eliminar filas totalmente vacías
    df = df.dropna(how='all')
    
    # 3. LIMPIEZA DE TEXTO
    # Quitar saltos de línea internos (\n) que rompen el Excel
    df = df.replace(r'\n', ' ', regex=True)
    
    return df

def process_sac_pdf(file_bytes, pages):
    # Guardar temporalmente
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    
    try:
        # Ejecutar Camelot en modo Lattice (Red)
        # Esto ignora automáticamente el texto que está FUERA de las tablas (títulos de página)
        tables = camelot.read_pdf(tmp_path, pages=pages, flavor='lattice', strip_text='\n')
        
        if len(tables) == 0:
            return None, "No se encontraron tablas."
            
        master_df = pd.DataFrame()
        
        # Iterar y consolidar
        for table in tables:
            df = table.df
            
            # Limpiar esta tabla específica
            clean_df = clean_sac_data(df)
            
            if clean_df is not None and not clean_df.empty:
                master_df = pd.concat([master_df, clean_df], ignore_index=True)
                
        return master_df, None
        
    except Exception as e:
        return None, str(e)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_file = st.file_uploader("Cargar SAC (PDF)", type=["pdf"])

if uploaded_file is not None:
    if st.button("🚀 Extraer y Limpiar"):
        
        with st.status("Analizando estructura del documento...", expanded=True) as status:
            
            # Procesar
            df_final, error = process_sac_pdf(uploaded_file.read(), pages_input)
            
            if error:
                status.update(label="Error", state="error")
                st.error(f"Error técnico: {error}")
            
            elif df_final is not None and not df_final.empty:
                status.update(label="¡Extracción Finalizada!", state="complete")
                
                rows_count = len(df_final)
                st.success(f"✅ Se han extraído y unificado **{rows_count}** filas de productos.")
                
                # Vista Previa
                st.write("### Vista Previa de Datos Limpios:")
                st.dataframe(df_final.head(10), use_container_width=True)
                
                # Generar Excel
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df_final.to_excel(writer, index=False, sheet_name="SAC_Consolidado")
                    
                    # Formato estético
                    workbook = writer.book
                    worksheet = writer.sheets['SAC_Consolidado']
                    
                    # Estilo de cabecera
                    header_fmt = workbook.add_format({'bold': True, 'bg_color': '#D9E1F2', 'border': 1})
                    worksheet.set_row(0, None, header_fmt)
                    
                    # Anchos
                    worksheet.set_column('A:A', 15) # Código
                    worksheet.set_column('B:B', 70) # Descripción (Muy ancha)
                    worksheet.set_column('C:C', 10) # DAI
                    
                    # Ajuste de texto
                    wrap_fmt = workbook.add_format({'text_wrap': True, 'valign': 'top'})
                    worksheet.set_column('B:B', 70, wrap_fmt)

                st.download_button(
                    label="📥 Descargar Excel Unificado",
                    data=buffer.getvalue(),
                    file_name="SAC_Limpio.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                status.update(label="Vacío", state="error")
                st.warning("Se detectaron tablas pero estaban vacías después de la limpieza. Revisa el rango de páginas.")
