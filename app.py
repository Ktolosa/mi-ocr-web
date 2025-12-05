import streamlit as st
import camelot
import pandas as pd
import io
import os
import tempfile
import shutil
import gc
from pypdf import PdfReader

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor SAC V6 (Disco)", layout="wide")
st.title("📊 Extractor SAC (Modo Seguro de Memoria)")

if not shutil.which("gs"):
    st.error("❌ Error Crítico: Ghostscript no está instalado. Revisa packages.txt")
    st.stop()

# --- BARRA LATERAL ---
with st.sidebar:
    st.header("⚙️ Configuración")
    st.info("Este modo escribe en disco paso a paso para no saturar la memoria RAM.")
    
    # Lote pequeño para seguridad
    batch_size = st.slider("Páginas por lote:", 5, 50, 10)

# ==========================================
# 🧠 LÓGICA DE LIMPIEZA
# ==========================================
def clean_sac_data(df):
    if df.shape[1] < 3: return None
    
    # Quedarse con las 3 primeras columnas
    df = df.iloc[:, 0:3]
    df.columns = ["CODIGO", "DESCRIPCION", "DAI"]
    
    # Filtro de basura
    df["CODIGO"] = df["CODIGO"].astype(str)
    bad_words = ["CÓDIGO", "CODIGO", "SAC", "DESCRIPCIÓN", "DAI", "CAPÍTULO", "NOTAS", "SECCIÓN"]
    pattern = '|'.join(bad_words)
    
    df = df[~df["CODIGO"].str.contains(pattern, case=False, na=False)]
    df = df[df["CODIGO"] != df["DESCRIPCION"]]
    df = df.dropna(how='all')
    df = df.replace(r'\n', ' ', regex=True)
    return df

# ==========================================
# 🚜 MOTOR DE ESCRITURA EN DISCO (V6)
# ==========================================
def process_massive_pdf(file_bytes, batch_size):
    # Archivos temporales
    temp_pdf = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    temp_csv = tempfile.NamedTemporaryFile(delete=False, suffix=".csv")
    
    temp_pdf.write(file_bytes)
    temp_pdf.close() # Cerramos escritura, mantenemos path
    
    pdf_path = temp_pdf.name
    csv_path = temp_csv.name
    temp_csv.close() # Cerramos para que pandas pueda escribir

    try:
        reader = PdfReader(pdf_path)
        total_pages = len(reader.pages)
        
        status_bar = st.progress(0)
        status_text = st.empty()
        
        total_rows_extracted = 0
        first_batch = True
        
        # BUCLE POR LOTES
        for i, start_page in enumerate(range(1, total_pages + 1, batch_size)):
            end_page = min(start_page + batch_size - 1, total_pages)
            pages_arg = f"{start_page}-{end_page}"
            
            status_text.write(f"⏳ Procesando páginas {pages_arg} de {total_pages}...")
            
            try:
                # 1. Extraer (Solo este pedacito)
                tables = camelot.read_pdf(pdf_path, pages=pages_arg, flavor='lattice', strip_text='\n')
                
                # 2. Limpiar y consolidar lote en memoria RAM pequeña
                batch_df = pd.DataFrame()
                for t in tables:
                    clean = clean_sac_data(t.df)
                    if clean is not None and not clean.empty:
                        batch_df = pd.concat([batch_df, clean], ignore_index=True)
                
                # 3. ESCRIBIR EN DISCO INMEDIATAMENTE (Append Mode)
                if not batch_df.empty:
                    # Si es el primer lote, escribimos encabezados. Si no, solo datos.
                    batch_df.to_csv(csv_path, mode='a', header=first_batch, index=False, encoding='utf-8-sig')
                    total_rows_extracted += len(batch_df)
                    first_batch = False
                
                # 4. LIBERAR MEMORIA AGRESIVAMENTE
                del tables
                del batch_df
                gc.collect()
                
            except Exception as e:
                # Si falla una pagina, seguimos con la siguiente
                print(f"Error en lote {pages_arg}: {e}")
            
            # Actualizar barra
            status_bar.progress(min(end_page / total_pages, 1.0))

        status_text.success("✅ Extracción finalizada. Generando archivos de descarga...")
        
        return csv_path, total_rows_extracted

    except Exception as e:
        st.error(f"Error fatal: {e}")
        return None, 0
    finally:
        if os.path.exists(pdf_path): os.remove(pdf_path)

# ==========================================
# 🖥️ INTERFAZ
# ==========================================

uploaded_file = st.file_uploader("Sube el SAC Completo (PDF)", type=["pdf"])

if uploaded_file is not None:
    if st.button("🚀 Procesar Documento Gigante"):
        
        csv_path, rows = process_massive_pdf(uploaded_file.read(), batch_size)
        
        if rows > 0:
            st.balloons()
            st.success(f"✅ Se han extraído {rows} filas de datos.")
            
            # Opción 1: Descargar CSV (Súper rápido y seguro)
            with open(csv_path, "rb") as f:
                csv_bytes = f.read()
                
            st.download_button(
                "📥 Descargar CSV (Recomendado - Más ligero)",
                data=csv_bytes,
                file_name="SAC_Completo.csv",
                mime="text/csv"
            )
            
            # Opción 2: Intentar Convertir a Excel (Puede tardar)
            st.write("---")
            st.info("Generando Excel... (Si esto falla, usa el botón de CSV de arriba)")
            
            try:
                # Leemos el CSV del disco para pasarlo a Excel
                # Chunksize ayuda a no saturar memoria al leer para convertir
                df_final = pd.read_csv(csv_path)
                
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    df_final.to_excel(writer, index=False, sheet_name="SAC")
                    ws = writer.sheets['SAC']
                    ws.set_column('B:B', 80)
                
                st.download_button(
                    "📥 Descargar Excel (.xlsx)",
                    data=buffer.getvalue(),
                    file_name="SAC_Completo.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            except Exception as e:
                st.error(f"El Excel es demasiado grande para generarlo aquí. Por favor descarga el CSV. Error: {e}")
            
            # Limpieza final
            if os.path.exists(csv_path): os.remove(csv_path)
            
        else:
            st.warning("No se encontraron datos o hubo un error en la lectura.")
