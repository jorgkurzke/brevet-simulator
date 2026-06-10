import math
from datetime import datetime, timedelta
from io import BytesIO

import streamlit as st
import pandas as pd
import xlsxwriter
import io

# ---------------------------------------------------------
# Streamlit Grundkonfiguration
# ---------------------------------------------------------
st.set_page_config(
    page_title="Brevet GPX Analyzer & Simulator",
    page_icon="🚴",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("Brevet GPX Analyzer & Simulator")

# ---------------------------------------------------------
# SIDEBAR – Eingaben für Simulation
# ---------------------------------------------------------
st.sidebar.header("⚙️ Simulationseinstellungen")

# FTP
ftp = st.sidebar.number_input(
    "FTP (Watt)",
    min_value=100,
    max_value=400,
    value=220,
    step=5
)

# Leistungsprofile
st.sidebar.subheader("Leistungsprofile")
power_flat = st.sidebar.number_input("Flach (Watt)",  min_value=80, max_value=400, value=180)
power_climb = st.sidebar.number_input("Berg (Watt)",  min_value=80, max_value=400, value=200)
power_down = st.sidebar.number_input("Abfahrt (Watt)", min_value=50, max_value=400, value=120)

# Physikalisches Modell
st.sidebar.subheader("Physikalisches Modell")
c_rr = st.sidebar.number_input("Rollwiderstand Crr", min_value=0.002, max_value=0.01, value=0.004, step=0.001)
c_dA = st.sidebar.number_input("Luftwiderstand CdA", min_value=0.15, max_value=0.40, value=0.28, step=0.01)
weight = st.sidebar.number_input("Systemgewicht (kg)", min_value=60, max_value=120, value=85)

# Windmodell
st.sidebar.subheader("Windmodell")
wind_speed = st.sidebar.number_input("Windstärke (km/h)", min_value=0, max_value=80, value=10)
wind_dir = st.sidebar.slider("Windrichtung (°)", min_value=0, max_value=360, value=180)

# ACP-Regeln
st.sidebar.header("⏱ ACP‑Regeln")
start_time = st.sidebar.time_input("Startzeit")
start_date = st.sidebar.date_input("Startdatum", datetime.now().date())

# Pausen
st.sidebar.header("☕ Pausen")
pause_count = st.sidebar.number_input("Anzahl Pausen", min_value=0, max_value=20, value=2)

pauses = []
for i in range(pause_count):
    st.sidebar.subheader(f"Pause {i+1}")
    km = st.sidebar.number_input(f"km‑Marke Pause {i+1}", min_value=0, max_value=2000, value=50*(i+1))
    duration = st.sidebar.number_input(f"Dauer Pause {i+1} (min)", min_value=1, max_value=120, value=10)
    pauses.append({"km": km, "duration": duration})

# Kontrollpunkte
st.sidebar.header("📍 Kontrollpunkte")
cp_count = st.sidebar.number_input("Anzahl Kontrollpunkte", min_value=0, max_value=20, value=3)

control_points = []
for i in range(cp_count):
    st.sidebar.subheader(f"Kontrollpunkt {i+1}")
    km = st.sidebar.number_input(f"km‑Marke KP {i+1}", min_value=0, max_value=2000, value=50*(i+1))
    name = st.sidebar.text_input(f"Name KP {i+1}", value=f"Kontrolle {i+1}")
    control_points.append({"km": km, "name": name})

# ---------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------
def safe_sheet_name(name: str) -> str:
    invalid_chars = ['\\', '/', '*', '?', ':', '[', ']']
    for ch in invalid_chars:
        name = name.replace(ch, '_')
    return name[:31]

def export_to_excel(dfs: dict) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        for sheet_name, df in dfs.items():
            df.to_excel(writer, sheet_name=safe_sheet_name(sheet_name), index=False)
    return output.getvalue()

def export_to_pdf(html_content: str) -> bytes:
    try:
        import pdfkit
        return pdfkit.from_string(html_content, False)
    except Exception:
        return html_content.encode("utf-8")

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
# Hauptbereich – GPX Upload & Analyse
# ---------------------------------------------------------
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

        df = parse_gpx(file
