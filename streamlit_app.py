import math
from datetime import datetime, timedelta
from io import BytesIO

import streamlit as st
import pandas as pd
import xlsxwriter
import pydeck as pdk
import altair as alt
import xml.etree.ElementTree as ET
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


# ---------------------------------------------------------
# STREAMLIT CONFIG
# ---------------------------------------------------------
st.set_page_config(
    page_title="Brevet GPX Analyzer & Simulator",
    page_icon="🚴",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.title("Brevet GPX Analyzer & Simulator")


# ---------------------------------------------------------
# INITIAL SESSION STATE
# ---------------------------------------------------------
DEFAULTS = {
    "control_points": [],
    "pauses": [],
    "new_cp_km": 0.0,
    "new_cp_name": "",
    "new_cp_pause": 0,
    "new_pause_km": 0.0,
    "new_pause_min": 0,
}

for key, value in DEFAULTS.items():
    st.session_state.setdefault(key, value)


# ---------------------------------------------------------
# SIDEBAR – SIMULATION
# ---------------------------------------------------------
st.sidebar.header("⚙️ Simulationseinstellungen")

ftp = st.sidebar.number_input("FTP (Watt)", 100, 400, 220, 5)
c_dA = st.sidebar.number_input("CdA (m²)", 0.15, 0.40, 0.28, 0.01)
air_density = st.sidebar.number_input("Luftdichte ρ (kg/m³)", 1.0, 1.4, 1.225, 0.01)
wind_speed = st.sidebar.number_input("Windgeschwindigkeit (km/h)", 0, 80, 10)
wind_angle = st.sidebar.slider("Windwinkel (°)", 0, 360, 180)
c_rr = st.sidebar.number_input("Crr", 0.002, 0.01, 0.004, 0.001)

weight_rider = st.sidebar.number_input("Fahrergewicht (kg)", 50, 120, 75)
weight_bike = st.sidebar.number_input("Radgewicht (kg)", 6, 20, 10)
weight_total = weight_rider + weight_bike
st.sidebar.write(f"**Systemgewicht:** {weight_total:.1f} kg")

max_downhill_speed = st.sidebar.number_input("Maximale Abfahrtsgeschwindigkeit (km/h)", 40, 120, 70)
min_speed = st.sidebar.number_input("Minimale Geschwindigkeit (km/h)", 2, 15, 4)

start_date = st.sidebar.date_input("Startdatum", datetime.now().date())
start_time = st.sidebar.time_input("Startzeit", datetime.now().time())
start_datetime = datetime.combine(start_date, start_time)


# ---------------------------------------------------------
# ZIELGESCHWINDIGKEITEN
# ---------------------------------------------------------
st.sidebar.header("🎯 Zielgeschwindigkeiten pro Steigung")

base_speeds = {
    "flat": 26,
    "light_down": 32,
    "down": 50,
    "light_up": 20,
    "med_up": 16,
    "steep_up": 12,
    "very_steep_up": 8,
}

ftp_factor = (ftp / 220) ** 0.35

target_speed_flat = st.sidebar.number_input("Flach (−1% bis +1%)", 10.0, 45.0, round(base_speeds["flat"] * ftp_factor, 1))
target_speed_light_down = st.sidebar.number_input("Leicht bergab (−3% bis −1%)", 10.0, 70.0, round(base_speeds["light_down"] * ftp_factor, 1))
target_speed_down = st.sidebar.number_input("Stark bergab (< −3%)", 10.0, 120.0, round(base_speeds["down"] * ftp_factor, 1))
target_speed_light_up = st.sidebar.number_input("Leicht bergauf (1–3%)", 5.0, 40.0, round(base_speeds["light_up"] * ftp_factor, 1))
target_speed_med_up = st.sidebar.number_input("Mäßig bergauf (3–6%)", 5.0, 35.0, round(base_speeds["med_up"] * ftp_factor, 1))
target_speed_steep_up = st.sidebar.number_input("Stärker bergauf (6–10%)", 3.0, 30.0, round(base_speeds["steep_up"] * ftp_factor, 1))
target_speed_very_steep_up = st.sidebar.number_input("Sehr steil (>10%)", 2.0, 25.0, round(base_speeds["very_steep_up"] * ftp_factor, 1))
# ---------------------------------------------------------
# KONTROLLPUNKTE – VERZÖGERTER RERUN
# ---------------------------------------------------------
st.sidebar.header("📍 Kontrollpunkte")

new_cp_km = st.sidebar.number_input(
    "KM für neuen Kontrollpunkt",
    min_value=0.0,
    max_value=2000.0,
    value=st.session_state["new_cp_km"],
)

new_cp_name = st.sidebar.text_input(
    "Name des Kontrollpunkts",
    value=st.session_state["new_cp_name"],
)

new_cp_pause = st.sidebar.number_input(
    "Pause (Minuten)",
    min_value=0,
    max_value=240,
    value=st.session_state["new_cp_pause"],
)

if st.sidebar.button("Kontrollpunkt hinzufügen"):
    st.session_state["pending_add_cp"] = {
        "km": new_cp_km,
        "name": new_cp_name,
        "pause": new_cp_pause,
    }
    st.session_state["trigger_rerun"] = True


# ---------------------------------------------------------
# PAUSEN – VERZÖGERTER RERUN
# ---------------------------------------------------------
st.sidebar.header("⏸ Pausenpunkte")

new_pause_km = st.sidebar.number_input(
    "KM für neue Pause",
    min_value=0.0,
    max_value=2000.0,
    value=st.session_state["new_pause_km"],
)

new_pause_min = st.sidebar.number_input(
    "Pausendauer (Minuten)",
    min_value=0,
    max_value=240,
    value=st.session_state["new_pause_min"],
)

if st.sidebar.button("Pause hinzufügen"):
    st.session_state["pending_add_pause"] = {
        "km": new_pause_km,
        "pause": new_pause_min,
    }
    st.session_state["trigger_rerun"] = True


# ---------------------------------------------------------
# VERARBEITUNG DER PENDING EVENTS
# ---------------------------------------------------------
if "pending_add_cp" in st.session_state:
    cp = st.session_state["pending_add_cp"]

    st.session_state["control_points"].append({
        "km": cp["km"],
        "name": cp["name"] or f"CP {len(st.session_state['control_points'])+1}",
        "pause_min": cp["pause"],
    })

    st.session_state["new_cp_km"] = 0.0
    st.session_state["new_cp_name"] = ""
    st.session_state["new_cp_pause"] = 0

    del st.session_state["pending_add_cp"]


if "pending_add_pause" in st.session_state:
    p = st.session_state["pending_add_pause"]

    st.session_state["pauses"].append({
        "km": p["km"],
        "pause_min": p["pause"],
    })

    st.session_state["new_pause_km"] = 0.0
    st.session_state["new_pause_min"] = 0

    del st.session_state["pending_add_pause"]


# ---------------------------------------------------------
# ANZEIGE DER PUNKTE
# ---------------------------------------------------------
st.sidebar.subheader("Kontrollpunkte")
for cp in st.session_state["control_points"]:
    st.sidebar.write(f"• {cp['km']} km – {cp['name']} – {cp['pause_min']} min")

st.sidebar.subheader("Pausenpunkte")
for p in st.session_state["pauses"]:
    st.sidebar.write(f"• Pause bei {p['km']} km – {p['pause_min']} min")


# ---------------------------------------------------------
# HELPERS
# ---------------------------------------------------------
def safe_sheet_name(name: str) -> str:
    for ch in ['\\', '/', '*', '?', ':', '[', ']']:
        name = name.replace(ch, '_')
    return name[:31]


def parse_gpx(file) -> pd.DataFrame:
    tree = ET.parse(file)
    root = tree.getroot()
    ns = {"default": "http://www.topografix.com/GPX/1/1"}

    data = []
    for trkpt in root.findall(".//default:trkpt", ns):
        lat = float(trkpt.attrib["lat"])
        lon = float(trkpt.attrib["lon"])
        ele = trkpt.find("default:ele", ns)
        time = trkpt.find("default:time", ns)
        data.append({
            "lat": lat,
            "lon": lon,
            "elevation": float(ele.text) if ele is not None else None,
            "time": time.text if time is not None else None,
        })

    return pd.DataFrame(data)


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = map(math.radians, [lat1, lat2])
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def add_distance_and_gradient(df):
    distances = [0.0]
    for i in range(1, len(df)):
        d = haversine(df.lat[i-1], df.lon[i-1], df.lat[i], df.lon[i])
        distances.append(distances[-1] + d)

    df["distance_m"] = distances
    df["km"] = df["distance_m"] / 1000

    if df["elevation"].notna().any():
        df["delta_h"] = df["elevation"].diff()
        df["delta_m"] = df["distance_m"].diff().replace(0, 0.1)
        df["gradient_raw"] = (df["delta_h"] / df["delta_m"]) * 100
        df["gradient"] = df["gradient_raw"].clip(-20, 20).rolling(5, center=True, min_periods=1).mean()
    else:
        df["gradient"] = 0.0

    return df
# ---------------------------------------------------------
# SPEED MODEL
# ---------------------------------------------------------
def compute_speed(gradient):
    g = gradient

    if g < -3:
        base = target_speed_down
    elif -3 <= g < -1:
        base = target_speed_light_down
    elif -1 <= g <= 1:
        base = target_speed_flat
    elif 1 < g <= 3:
        base = target_speed_light_up
    elif 3 < g <= 6:
        base = target_speed_med_up
    elif 6 < g <= 10:
        base = target_speed_steep_up
    else:
        base = target_speed_very_steep_up

    # FTP‑Skalierung
    v = base * (ftp / 220) ** 0.15

    # Abfahrtslimit
    if g < 0:
        v = min(v, max_downhill_speed)

    # Mindestgeschwindigkeit
    return max(v, min_speed)


def add_time_profile(df):
    times = [0.0]
    speeds = [0.0]

    for i in range(1, len(df)):
        dist = df.distance_m[i] - df.distance_m[i-1]
        if dist <= 0:
            times.append(times[-1])
            speeds.append(speeds[-1])
            continue

        v_kmh = compute_speed(df.gradient[i])
        v_ms = v_kmh / 3.6
        dt = dist / v_ms

        times.append(times[-1] + dt)
        speeds.append(v_kmh)

    df["speed_kmh"] = speeds
    df["time_s"] = times
    df["sim_time"] = [start_datetime + timedelta(seconds=t) for t in times]
    return df


# ---------------------------------------------------------
# PAUSE LOGIC
# ---------------------------------------------------------
def apply_pauses(df):
    total_pause = 0
    pause_events = set()
    df["sim_time_with_pauses"] = None

    for i in range(len(df)):
        km = df.km[i]
        base_time = df.sim_time[i]

        # Kontrollpunkte
        for cp in st.session_state["control_points"]:
            if abs(km - cp["km"]) < 0.05:
                key = ("cp", cp["km"])
                if key not in pause_events:
                    pause_events.add(key)
                    total_pause += cp["pause_min"] * 60

        # Pausenpunkte
        for p in st.session_state["pauses"]:
            if abs(km - p["km"]) < 0.05:
                key = ("pause", p["km"])
                if key not in pause_events:
                    pause_events.add(key)
                    total_pause += p["pause_min"] * 60

        df.at[i, "sim_time_with_pauses"] = base_time + timedelta(seconds=total_pause)

    return df


# ---------------------------------------------------------
# VISUALS
# ---------------------------------------------------------
def show_map(df):
    if df.empty:
        st.warning("Keine GPS-Daten.")
        return

    path = df.apply(lambda r: [r.lon, r.lat], axis=1).tolist()
    midpoint = (df.lat.mean(), df.lon.mean())

    layers = [
        pdk.Layer(
            "PathLayer",
            data=[{"path": path}],
            get_path="path",
            get_color=[255, 0, 0],
            width_scale=2,
            width_min_pixels=2,
        )
    ]

    def add_points(points, color):
        data = []
        for p in points:
            nearest = df.iloc[(df.km - p["km"]).abs().argmin()]
            data.append({
                "lon": nearest.lon,
                "lat": nearest.lat,
                "name": p.get("name", "Pause"),
                "pause_min": p["pause_min"],
            })
        return pdk.Layer(
            "ScatterplotLayer",
            data=data,
            get_position="[lon, lat]",
            get_color=color,
            get_radius=1500,
        )

    layers.append(add_points(st.session_state["control_points"], [0, 100, 255]))
    layers.append(add_points(st.session_state["pauses"], [255, 220, 0]))

    st.pydeck_chart(
        pdk.Deck(
            layers=layers,
            initial_view_state=pdk.ViewState(
                latitude=midpoint[0],
                longitude=midpoint[1],
                zoom=10
            ),
            tooltip={"html": "<b>{name}</b><br/>Pause: {pause_min} min"},
        )
    )


def show_elevation(df):
    if df.elevation.isna().all():
        st.info("Keine Höhendaten.")
        return

    df_plot = df.copy()
    df_plot["elevation_smooth"] = df_plot.elevation.rolling(25, center=True, min_periods=1).mean()

    chart = (
        alt.Chart(df_plot)
        .mark_line(color="steelblue")
        .encode(x="km", y="elevation_smooth")
        .properties(height=250)
    )
    st.altair_chart(chart, use_container_width=True)


def show_speed(df):
    chart = (
        alt.Chart(df)
        .mark_line(color="green")
        .encode(x="km", y="speed_kmh")
        .properties(height=250)
    )
    st.altair_chart(chart, use_container_width=True)


# ---------------------------------------------------------
# SUMMARY TABLE
# ---------------------------------------------------------
def build_summary(df):
    points = [{"km": 0.0, "name": "Start", "pause_min": 0}]
    points += st.session_state["control_points"]
    points += [{"km": p["km"], "name": "Pause", "pause_min": p["pause_min"]} for p in st.session_state["pauses"]]
    points.append({"km": df.km.iloc[-1], "name": "Ziel", "pause_min": 0})

    points = sorted(points, key=lambda x: x["km"])

    rows = []
    last_km = 0
    last_time = df.sim_time_with_pauses.iloc[0]
    last_elev = df.elevation.iloc[0]

    for p in points:
        nearest = df.iloc[(df.km - p["km"]).abs().argmin()]
        km_total = nearest.km
        km_diff = km_total - last_km
        time_total = nearest.sim_time_with_pauses
        time_diff = time_total - last_time
        elev_total = nearest.elevation
        elev_diff = elev_total - last_elev

        rows.append({
            "Name": p["name"],
            "KM gesamt": round(km_total, 2),
            "KM Abschnitt": round(km_diff, 2),
            "HM gesamt": round(elev_total, 0),
            "HM Abschnitt": round(elev_diff, 0),
            "Zeit gesamt": time_total.strftime("%Y-%m-%d %H:%M:%S"),
            "Zeit Abschnitt": str(time_diff),
            "Pause (min)": p["pause_min"],
        })

        last_km = km_total
        last_time = time_total
        last_elev = elev_total

    return pd.DataFrame(rows)
# ---------------------------------------------------------
# MAIN – GPX UPLOAD
# ---------------------------------------------------------
uploaded_files = st.file_uploader(
    "GPX-Dateien hochladen",
    type=["gpx"],
    accept_multiple_files=True
)

if uploaded_files:
    all_dfs = {}

    for file in uploaded_files:
        st.subheader(f"📍 {file.name}")

        # GPX einlesen
        df = parse_gpx(file)
        df = add_distance_and_gradient(df)
        df = add_time_profile(df)
        df = apply_pauses(df)

        # -----------------------------
        # DATENANZEIGE
        # -----------------------------
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**Rohdaten & Simulation (Auszug)**")
            st.dataframe(
                df[[
                    "km",
                    "elevation",
                    "gradient",
                    "speed_kmh",
                    "sim_time",
                    "sim_time_with_pauses"
                ]].head(500)
            )

        with col2:
            st.subheader("🗺️ Karte")
            show_map(df)

        # -----------------------------
        # HÖHENPROFIL
        # -----------------------------
        st.subheader("⛰️ Höhenprofil")
        show_elevation(df)

        # -----------------------------
        # GESCHWINDIGKEITSKURVE
        # -----------------------------
        st.subheader("📈 Geschwindigkeitskurve")
        show_speed(df)

        # -----------------------------
        # REGIME-TABELLE
        # -----------------------------
        with st.expander("🔍 Regime je Abschnitt"):
            st.dataframe(
                df[["km", "gradient", "speed_kmh"]].head(500)
            )

        # -----------------------------
        # ZUSAMMENFASSUNG
        # -----------------------------
        st.subheader("📋 Kontroll‑ & Pausentabelle")
        summary_df = build_summary(df)
        st.dataframe(summary_df)

        # -----------------------------
        # EXCEL EXPORT
        # -----------------------------
        summary_excel = BytesIO()
        with pd.ExcelWriter(summary_excel, engine="xlsxwriter") as writer:
            summary_df.to_excel(writer, sheet_name="Zusammenfassung", index=False)

        st.download_button(
            label="📥 Zusammenfassung als Excel",
            data=summary_excel.getvalue(),
            file_name=f"brevet_summary_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        # -----------------------------
        # PDF EXPORT
        # -----------------------------
        pdf_buffer = BytesIO()
        c = canvas.Canvas(pdf_buffer, pagesize=A4)
        width, height = A4

        text = c.beginText(40, height - 40)
        text.setFont("Helvetica", 9)

        text.textLine("Brevet Zusammenfassung")
        text.textLine("")

        for _, row in summary_df.iterrows():
            line = ", ".join(f"{col}: {row[col]}" for col in summary_df.columns)
            text.textLine(line)
            text.textLine("")

            if text.getY() < 60:
                c.drawText(text)
                c.showPage()
                text = c.beginText(40, height - 40)
                text.setFont("Helvetica", 9)

        c.drawText(text)
        c.showPage()
        c.save()

        st.download_button(
            label="📄 Zusammenfassung als PDF",
            data=pdf_buffer.getvalue(),
            file_name=f"brevet_summary_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            mime="application/pdf"
        )

        # -----------------------------
        # ANKUNFTSZEIT
        # -----------------------------
        finish_time = df.sim_time_with_pauses.iloc[-1]
        total_time = finish_time - start_datetime

        st.markdown(f"**Ankunftszeit (inkl. Pausen):** {finish_time}")
        st.markdown(f"**Gesamtzeit:** {total_time}")

        all_dfs[file.name] = df

    # -----------------------------------------------------
    # GESAMT-EXCEL EXPORT
    # -----------------------------------------------------
    excel_all = BytesIO()
    with pd.ExcelWriter(excel_all, engine="xlsxwriter") as writer:
        for name, df in all_dfs.items():
            sheet = safe_sheet_name(name)
            df.to_excel(writer, sheet_name=sheet, index=False)

    st.download_button(
        label="📥 Excel Export (alle Daten)",
        data=excel_all.getvalue(),
        file_name=f"brevet_export_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

else:
    st.info("Bitte eine oder mehrere GPX-Dateien hochladen.")
# ---------------------------------------------------------
# RERUN-FLAG AUSWERTEN (MUSS GANZ UNTEN STEHEN)
# ---------------------------------------------------------
if st.session_state.get("trigger_rerun", False):
    st.session_state["trigger_rerun"] = False
    st.session_state["__rerun_placeholder"] = datetime.now().timestamp()















