# ---------------------------------------------------------
# IMPORTS
# ---------------------------------------------------------
import math
from datetime import datetime, timedelta
from io import BytesIO

import streamlit as st
import pandas as pd
import xlsxwriter
import io
import pydeck as pdk
import altair as alt
import xml.etree.ElementTree as ET


# ---------------------------------------------------------
# STREAMLIT KONFIGURATION
# ---------------------------------------------------------
st.set_page_config(
    page_title="Brevet GPX Analyzer & Simulator",
    page_icon="🚴",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("Brevet GPX Analyzer & Simulator")


# ---------------------------------------------------------
# SIDEBAR – SIMULATIONSEINSTELLUNGEN
# ---------------------------------------------------------
st.sidebar.header("⚙️ Simulationseinstellungen")

# FTP
ftp = st.sidebar.number_input("FTP (Watt)", min_value=100, max_value=400, value=220, step=5)

# Leistungsprofile
st.sidebar.subheader("Leistungsprofile")
power_flat = st.sidebar.number_input("Flach (Watt)", min_value=80, max_value=400, value=180)
power_climb = st.sidebar.number_input("Berg (Watt)", min_value=80, max_value=400, value=200)
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
# HILFSFUNKTIONEN
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
# KARTE
# ---------------------------------------------------------
def show_map(df: pd.DataFrame, control_points, pauses):
    if df.empty:
        st.warning("Keine GPS-Daten für die Karte.")
        return

    # GPX-Track als Liste von Koordinaten
    path = df.apply(lambda r: [r["lon"], r["lat"]], axis=1).tolist()

    # Start- und Endpunkt
    start = path[0]
    end = path[-1]

    midpoint = (df["lat"].mean(), df["lon"].mean())

    layers = []

    # ---------------------------------------------------------
    # GPX-Track (rote Linie)
    # ---------------------------------------------------------
    layers.append(
        pdk.Layer(
            "PathLayer",
            data=[{"path": path}],
            get_path="path",
            get_color=[255, 0, 0],
            width_scale=2,
            width_min_pixels=2,
        )
    )

    # ---------------------------------------------------------
    # Startpunkt (grün)
    # ---------------------------------------------------------
    layers.append(
        pdk.Layer(
            "ScatterplotLayer",
            data=[{"lon": start[0], "lat": start[1]}],
            get_position="[lon, lat]",
            get_color=[0, 200, 0],
            get_radius=80,
        )
    )

    # ---------------------------------------------------------
    # Endpunkt (schwarz)
    # ---------------------------------------------------------
    layers.append(
        pdk.Layer(
            "ScatterplotLayer",
            data=[{"lon": end[0], "lat": end[1]}],
            get_position="[lon, lat]",
            get_color=[0, 0, 0],
            get_radius=80,
        )
    )

    # ---------------------------------------------------------
    # Kontrollpunkte (blau)
    # ---------------------------------------------------------
    cp_data = []
    for cp in control_points:
        # Nächster GPX-Punkt anhand km-Marke
        idx = min(int(cp["km"]), len(df) - 1)
        cp_data.append({
            "lon": df.iloc[idx]["lon"],
            "lat": df.iloc[idx]["lat"],
            "name": cp["name"]
        })

    if cp_data:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=cp_data,
                get_position="[lon, lat]",
                get_color=[0, 100, 255],
                get_radius=90,
            )
        )

    # ---------------------------------------------------------
    # Pausenpunkte (gelb)
    # ---------------------------------------------------------
    pause_data = []
    for p in pauses:
        idx = min(int(p["km"]), len(df) - 1)
        pause_data.append({
            "lon": df.iloc[idx]["lon"],
            "lat": df.iloc[idx]["lat"],
        })

    if pause_data:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=pause_data,
                get_position="[lon, lat]",
                get_color=[255, 220, 0],
                get_radius=90,
            )
        )

    # ---------------------------------------------------------
    # Karte rendern
    # ---------------------------------------------------------
    view_state = pdk.ViewState(
        latitude=midpoint[0],
        longitude=midpoint[1],
        zoom=10,
        pitch=0,
    )

    st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=view_state))


# ---------------------------------------------------------
# HÖHENPROFIL
# ---------------------------------------------------------
def show_elevation_profile(df: pd.DataFrame):
    if "elevation" not in df or df["elevation"].isna().all():
        st.info("Keine Höhendaten in dieser GPX-Datei.")
        return

    df["km"] = df.index / 1000

    chart = (
        alt.Chart(df)
        .mark_line(color="green")
        .encode(
            x=alt.X("km:Q", title="Distanz (km)"),
            y=alt.Y("elevation:Q", title="Höhe (m)")
        )
        .properties(height=250)
    )

    st.altair_chart(chart, use_container_width=True)


# ---------------------------------------------------------
# HAUPTBEREICH – GPX UPLOAD & ANALYSE
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

        df = parse_gpx(file)
        st.dataframe(df)

        st.subheader("🗺️ Karte")
        show_map(df)

        st.subheader("⛰️ Höhenprofil")
        show_elevation_profile(df)

        all_dfs[file.name] = df
        html_report += f"<h2>{file.name}</h2>"
        html_report += df.to_html(index=False)

    # Excel Export
    excel_bytes = export_to_excel(all_dfs)
    st.download_button(
        label="📥 Excel Export",
        data=excel_bytes,
        file_name=f"brevet_export_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # PDF Export
    pdf_bytes = export_to_pdf(html_report)
    st.download_button(
        label="📄 PDF Export",
        data=pdf_bytes,
        file_name=f"brevet_report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
        mime="application/pdf"
    )

else:
    st.info("Bitte eine oder mehrere GPX-Dateien hochladen.")


