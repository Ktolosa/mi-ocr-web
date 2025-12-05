import streamlit as st
import camelot
import pandas as pd
import io
import os
import tempfile
import shutil

# --- CONFIGURACIÓN ---
st.set_page_config(page_title="Extractor SAC (Camelot)", layout="wide")
st.title("📊 Extractor de Tablas SAC (Camelot)")

# Verificar Ghostscript (Vital para Camelot)
if not shutil.which("gs"):
    st.error("❌ Error: Ghostscript no está instalado. Revisa packages.txt")
    st.stop()

# --- BARRA LATERAL ---
with st.sidebar:
    st.header("⚙️ Configuración")
    
    # El SAC suele tener líneas, así que 'lattice' es mejor por defecto
    flavor = st.radio("Método de Detección:", ["Lattice (Líneas)", "Stream (Espacios)"], index=0)
    flavor_code = 'lattice' if 'Lattice' in flavor else 'stream'
    
    st.info("El SAC es un archivo pesado. Procesa por rangos de páginas para no saturar la memoria.")
    pages_input = st.text_input("Páginas a leer (Ej: 10-20, 50, all)", "10-15")

# ==========================================
# 🧠 MOTOR CAMELOT
# ==========================================
def extract_tables_camelot(file_bytes, pages, mode):
    # Guardar temporalmente porque Camelot necesita archivo físico
    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name
    
    try:
        # Ejecutar extracción
        tables = camelot.read_pdf(tmp_path, pages=pages, flavor=mode, strip_text='\n')
        return tables, tmp_path
    except Exception as e:
        return None, str(e)

# ==========================================
# 🖥️ INTERFAZ
# ==========================================
uploaded_file = st.file_uploader("Sube el archivo SAC (PDF)", type=["pdf"])

if uploaded_file is not None:
    if st.button("🚀 Extraer Tablas"):
        
        with st.status("Procesando PDF (Esto puede tardar)...", expanded=True) as status:
            # Extracción
            tables, error_path = extract_tables_camelot(uploaded_file.read(), pages_input, flavor_code)
            
            # Limpieza archivo temporal
            if isinstance(tables, str): # Si hubo error antes de retornar
                pass 
            elif error_path and os.path.exists(error_path):
                os.remove(error_path)

            if tables is None or isinstance(tables, str):
                status.update(label="Error", state="error")
                st.error(f"Error técnico: {error_path}")
            
            elif len(tables) > 0:
                status.update(label="¡Completado!", state="complete")
                st.success(f"✅ Se encontraron {len(tables)} tablas en las páginas {pages_input}.")
                
                # Consolidar en un solo Excel
                all_dfs = []
                buffer = io.BytesIO()
                
                with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
                    # Hoja Consolidada
                    row_pointer = 0
                    
                    for i, table in enumerate(tables):
                        df = table.df
                        all_dfs.append(df)
                        
                        # Escribir cada tabla en su propia hoja también (opcional)
                        sheet_name = f"Tabla_{i+1}_Pag{table.page}"
                        df.to_excel(writer, sheet_name=sheet_name, index=False, header=False)
                    
                    # Crear hoja maestra unida
                    if all_dfs:
                        master_df = pd.concat(all_dfs, ignore_index=True)
                        master_df.to_excel(writer, sheet_name="CONSOLIDADO", index=False, header=False)
                
                # Mostrar vista previa de la primera tabla detectada
                st.subheader("Vista Previa (Tabla 1):")
                st.dataframe(tables[0].df, use_container_width=True)
                
                st.download_button(
                    "📥 Descargar Excel SAC",
                    data=buffer.getvalue(),
                    file_name="SAC_Tablas.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            else:
                status.update(label="Sin resultados", state="error")
                st.warning("No se encontraron tablas. Prueba cambiando el método a 'Stream' o ajusta el rango de páginas.")
