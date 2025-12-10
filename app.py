# ==========================================
# 🧠 DEFINICIÓN DE PROMPTS POR TIPO
# ==========================================
PROMPTS_POR_TIPO = {
    "Factura Internacional (Regal - Con Filtro)": """
        Actúa como un experto en comercio exterior. Analiza la imagen de esta factura.
        
        REGLA CRÍTICA DE FILTRADO:
        1. Busca si dice "Original" o "Duplicado".
        2. SI DICE "Duplicado": Devuelve JSON vacío {}.
        3. SI DICE "Original": Extrae los datos.

        ESTRUCTURA JSON:
        {
            "tipo_documento": "Original",
            "numero_factura": "Invoice #",
            "fecha": "Date",
            "proveedor": "Vendor Name",
            "cliente": "Sold To Name",
            "items": [
                {
                    "codigo": "Model/UPC",
                    "descripcion": "Description",
                    "cantidad": 0,
                    "precio_unitario": 0.00,
                    "total_linea": 0.00
                }
            ],
            "total_factura": 0.00
        }
    """,
    
    "Factura Goodyear (Sin Filtro - Todo)": """
        Actúa como experto digitador para facturas de Goodyear.
        Analiza la imagen. Esta factura NO tiene duplicados, procesa todas las páginas que tengan tablas de items.

        INSTRUCCIONES ESPECÍFICAS:
        1. Mapea las columnas de Goodyear así:
           - "Code" -> codigo
           - "Description" -> descripcion
           - "Qty" -> cantidad
           - "Unit Value" -> precio_unitario
           - "Total Value" -> total_linea
        2. IMPORTANTE: Para pasar el filtro de seguridad del sistema, incluye SIEMPRE "tipo_documento": "Original" en tu respuesta JSON.
        3. Ignora los números de página (ej: 'Page 1 of 4').
        4. Si la descripción corta líneas, únelas.

        ESTRUCTURA JSON REQUERIDA:
        {
            "tipo_documento": "Original",  <-- ESTO ES OBLIGATORIO
            "numero_factura": "Extraer de 'INVOICE NUMBER' (ej: 300098911)",
            "fecha": "Extraer de 'DATE'",
            "proveedor": "Goodyear International Corporation",
            "cliente": "Extraer de 'SOLD TO'",
            "items": [
                {
                    "codigo": "...",
                    "descripcion": "...",
                    "cantidad": 0,
                    "precio_unitario": 0.00,
                    "total_linea": 0.00
                }
            ],
            "total_factura": 0.00
        }
    """
}
