import streamlit as st
import camelot
import pandas as pd
import io
import os
import tempfile
import shutil
import gc  # Garbage Collector para liberar RAM
from pypdf import PdfReader # Para contar páginas rápido

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor SAC V5 (Automático)", layout="wide")
st.title("📊 Extractor SAC (Procesamiento por Lotes)")

if not shutil.which("gs"):
    st.error("❌ Error Crítico: Ghostscript no está instalado. Revisa packages.txt")
    st.stop()

# --- BARRA LATERAL ---
with st.sidebar:
    st.header("⚙️ Configuración")
    st.info("Este modo divide el PDF automáticamente para evitar colapsos de memoria.")
    
    # Tamaño del lote (Batch size)
    batch_size = st.slider("Páginas por lote:", min_value=10, max_value=50, value=20, 
                           help="Menos páginas = Menos RAM usada, pero más lento.")

# ==========================================
# 🧠 LÓGICA DE LIMPIEZA
# ==========================================
def clean_sac_data(df):
    """Limpia la tabla cruda"""
    if df.shape[1] < 3: return None
    
    # Quedarse con las 3 primeras columnas
    df = df.iloc[:, 0:3]
    df.columns = ["CODIGO", "DESCRIPCION", "DAI"]
    
    # Filtro de basura
    df["CODIGO"] = df["CODIGO"].astype(str)
    bad_words = ["CÓDIGO", "CODIGO", "SAC", "DESCRIPCIÓN", "DAI", "CAPÍTULO", "NOTAS", "SECCIÓN"]
    pattern = '|'.join(bad_words)
    
    # Eliminar filas de encabezado repetido
    df = df[~df["CODIGO"].str.contains(pattern, case=False, na=False)]
    df = df[df["CODIGO"] != df["DESCRIPCION"]]
    df = df.dropna(how='all')
    
    # Limpiar texto
    df = df.replace(r'\n', ' ', regex=True)
    return df

# ==========================================
# 🚜 MOTOR DE LOTES (BATCH ENGINE)
# ==========================================
def process_full_document(file_bytes, batch_size):
    # 1. Guardar archivo temporal
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        # 2. Contar páginas totales
        reader = PdfReader(tmp_path)
        total_pages = len(reader.pages)
        
        st.info(f"📄 Documento detectado: {total_pages} páginas. Iniciando procesamiento en lotes de {batch_size}...")
        
        # Barra de progreso
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        all_dataframes = []
        
        # 3. BUCLE DE PROCESAMIENTO
        # Itera desde la pág 1 hasta la última, saltando de 20 en 20
        for i, start_page in enumerate(range(1, total_pages + 1, batch_size)):
            
            # Calcular fin del lote (ej: 1-20, 21-40...)
            end_page = min(start_page + batch_size - 1, total_pages)
            pages_arg = f"{start_page}-{end_page}"
            
            status_text.text(f"⏳ Procesando lote {i+1}: Páginas {pages_arg}...")
            
            try:
                # CAMELOT: Lee solo este pedacito
                tables = camelot.read_pdf(tmp_path, pages=pages_arg, flavor='lattice', strip_text='\n')
                
                # Limpiar y acumular
                for t in tables:
                    clean = clean_sac_data(t.df)
                    if clean is not None and not clean.empty:
                        all_dataframes.append(clean)
                
            except Exception as e:
                st.warning(f"⚠️ Error menor en páginas {pages_arg}: {e}")
            
            # 4. LIMPIEZA DE MEMORIA (CRÍTICO)
            # Borramos las tablas de Camelot de la memoria RAM
            del tables
            gc.collect() # Forzamos al sistema a liberar espacio
            
            # Actualizar barra
            progress_bar.progress(min(end_page / total_pages, 1.0))

        status_text.text("✅ Procesamiento finalizado. Unificando datos...")
        
        # 5. CONSOLIDACIÓN FINAL
        if all_dataframes:
            master_df = pd.concat(all_dataframes, ignore_index=True)
            return master_df
        else:
            return None

    except Exception as e:
        st.error(f"Error fatal: {e}")
        return None
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

# ==========================================
# 🖥️ INTERFAZ PRINCIPAL
# ==========================================

uploaded_file = st.file_uploader("Sube el archivo SAC Completo (PDF)", type=["pdf"])

if uploaded_file is not None:
    
    if st.button("🚀 Procesar Todo el Documento"):
        
        # Ejecutar motor
        df_result = process_full_document(uploaded_file.read(), batch_size)
        
        if df_result is not None and not df_result.empty:
            st.balloons()
            st.success(f"✅ ¡Éxito! Se extrajeron {len(df_result)} filas en total.")
            
            # Vista Previa (Primeras filas)
            st.write("### Muestra de Datos:")
            st.dataframe(df_result.head(100), use_container_width=True)
            
            # EXCEL
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                df_result.to_excel(writer, index=False, sheet_name="SAC_Completo")
                
                # Formato
                workbook = writer.book
                ws = writer.sheets['SAC_Completo']
                header_fmt = workbook.add_format({'bold': True, 'bg_color': '#2C3E50', 'font_color': 'white'})
                ws.set_row(0, None, header_fmt)
                
                ws.set_column('A:A', 15) # Código
                ws.set_column('B:B', 80) # Desc
                ws.set_column('C:C', 10) # DAI
                
                wrap = workbook.add_format({'text_wrap': True, 'valign': 'top'})
                ws.set_column('A:C', None, wrap)
            
            st.download_button(
                label="📥 Descargar SAC Completo (.xlsx)",
                data=buffer.getvalue(),
                file_name="SAC_Maestro_Completo.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            st.error("No se pudieron extraer datos. Puede que el PDF no tenga tablas legibles o esté encriptado.")
