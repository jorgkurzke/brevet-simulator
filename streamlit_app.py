import math
from datetime import datetime, timedelta
from io import BytesIO

import streamlit as st
import pandas as pd
import io
from datetime import datetime
import xlsxwriter

# ---------------------------------------------------------
# Hilfsfunktion: Sichere Sheet-Namen für Excel
# ---------------------------------------------------------
def safe_sheet_name(name: str) -> str:
    invalid_chars = ['\\', '/', '*', '?', ':', '[', ']']
    for ch in invalid_chars:
        name = name.replace(ch, '_')
    return name[:31]

# ---------------------------------------------------------
# Excel-Export
# ---------------------------------------------------------
def export_to_excel(dfs: dict) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        for sheet_name, df in dfs.items():
            df.to_excel(writer, sheet_name=safe_sheet_name(sheet_name), index=False)
    return output.getvalue()

# ---------------------------------------------------------
# PDF-Export (HTML → PDF)
# ---------------------------------------------------------
def export_to_pdf(html_content: str) -> bytes:
    try:
        import pdfkit
        return pdfkit.from_string(html_content, False)
    except Exception:
        return html_content.encode("utf-8")

# ---------------------------------------------------------
# GPX-Datei einlesen
# ---------------------------------------------------------
def parse_gpx(file) -> pd.DataFrame:
    import xml.etree.ElementTree as ET

    tree = ET.parse(file)
    root = tree.getroot()

    ns = {"default": "http://www.topografix.com/GPX/1/1"}

    data = []
    for trkpt in root.findall(".//default:trkpt", ns):
        lat = trkpt.attrib.get("lat")
        lon = trkpt.attrib.get("lon")
        ele = trkpt.find("default:ele", ns)
        time = trkpt.find("default:time", ns)

        data.append({
            "lat": float(lat),
            "lon": float(lon),
            "elevation": float(ele.text) if ele is not None else None,
            "time": time.text if time is not None else None
        })

    return pd.DataFrame(data)

# ---------------------------------------------------------
# Streamlit UI
# ---------------------------------------------------------
st.title("Brevet GPX Analyzer & Planner")

uploaded_files = st.file_uploader(
    "GPX-Dateien hochladen",
    type=["gpx"],
    accept_multiple_files=True
)

if uploaded_files:
    st.success(f"{len(uploaded_files)} Datei(en) geladen")

    all_dfs = {}
    html_report = "<h1>Brevet Analyse Report</h1>"

    for file in uploaded_files:
        st.subheader(f"📍 {file.name}")

        df = parse_gpx(file)
        st.dataframe(df)

        all_dfs[file.name] = df

        html_report += f"<h2>{file.name}</h2>"
        html_report += df.to_html(index=False)

    # ---------------------------------------------------------
    # Excel Export Button
    # ---------------------------------------------------------
    excel_bytes = export_to_excel(all_dfs)
    st.download_button(
        label="📥 Excel Export",
        data=excel_bytes,
        file_name=f"brevet_export_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # ---------------------------------------------------------
    # PDF Export Button
    # ---------------------------------------------------------
    pdf_bytes = export_to_pdf(html_report)
    st.download_button(
        label="📄 PDF Export",
        data=pdf_bytes,
        file_name=f"brevet_report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
        mime="application/pdf"
    )

else:
    st.info("Bitte eine oder mehrere GPX-Dateien hochladen.")
