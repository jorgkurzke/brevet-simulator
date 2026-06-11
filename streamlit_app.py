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
# SIDEBAR – SIMULATION
# ---------------------------------------------------------
st.sidebar.header("⚙️ Simulationseinstellungen")

st.sidebar.subheader("Leistung")
ftp = st.sidebar.number_input("FTP (Watt)", min_value=100, max_value=400, value=220, step=5)
power_flat = st.sidebar.number_input("Leistung flach (W)", min_value=80, max_value=400, value=180)
power_climb = st.sidebar.number_input("Leistung bergauf (W)", min_value=80, max_value=400, value=220)
power_light_downhill = st.sidebar.number_input("Leistung leicht bergab (W)", min_value=0, max_value=250, value=80)
power_heavy_downhill = st.sidebar.number_input("Leistung stark bergab (W)", min_value=0, max_value=200, value=0)

st.sidebar.subheader("Aerodynamik")
c_dA = st.sidebar.number_input("CdA (m²)", min_value=0.15, max_value=0.40, value=0.28, step=0.01)
air_density = st.sidebar.number_input("Luftdichte ρ (kg/m³)", min_value=1.0, max_value=1.4, value=1.225, step=0.01)
wind_speed = st.sidebar.number_input("Windgeschwindigkeit (km/h)", min_value=0, max_value=80, value=10)
wind_angle = st.sidebar.slider("Windwinkel (°) – 0° Rückenwind, 180° Gegenwind", 0, 360, 180)

st.sidebar.subheader("Rollwiderstand")
c_rr = st.sidebar.number_input("Crr", min_value=0.002, max_value=0.01, value=0.004, step=0.001)

st.sidebar.subheader("Masse")
weight_rider = st.sidebar.number_input("Fahrergewicht (kg)", min_value=50, max_value=120, value=75)
weight_bike = st.sidebar.number_input("Radgewicht (kg)", min_value=6, max_value=20, value=10)
weight_total = weight_rider + weight_bike
st.sidebar.write(f"**Systemgewicht:** {weight_total:.1f} kg")

st.sidebar.subheader("Geschwindigkeitsgrenzen")
max_downhill_speed = st.sidebar.number_input("Maximale Abfahrtsgeschwindigkeit (km/h)", min_value=40, max_value=120, value=70)
min_speed = st.sidebar.number_input("Minimale Geschwindigkeit (km/h)", min_value=2, max_value=15, value=4)

st.sidebar.subheader("Gefälle‑Schwellen")
light_downhill_limit = st.sidebar.number_input("Leichtes Gefälle bis (%)", min_value=-10.0, max_value=0.0, value=-3.0)
heavy_downhill_limit = st.sidebar.number_input("Starkes Gefälle ab (%)", min_value=-20.0, max_value=-3.0, value=-6.0)

st.sidebar.header("⏱ ACP‑Start")
start_date = st.sidebar.date_input("Startdatum", datetime.now().date())
start_time = st.sidebar.time_input("Startzeit", datetime.now().time())
start_datetime = datetime.combine(start_date, start_time)


# ---------------------------------------------------------
# SIDEBAR – ZIELGESCHWINDIGKEITEN (FTP-kalibriert)
# ---------------------------------------------------------
st.sidebar.header("🎯 Zielgeschwindigkeiten pro Steigung")

# Basiswerte für 220 W
base_flat = 26
base_light_down = 32
base_down = 50
base_light_up = 20
base_med_up = 16
base_steep_up = 12
base_very_steep_up = 8

# FTP-Skalierung (sanft)
ftp_factor = (ftp / 220) ** 0.35

default_flat = round(base_flat * ftp_factor, 1)
default_light_down = round(base_light_down * ftp_factor, 1)
default_down = round(base_down * ftp_factor, 1)
default_light_up = round(base_light_up * ftp_factor, 1)
default_med_up = round(base_med_up * ftp_factor, 1)
default_steep_up = round(base_steep_up * ftp_factor, 1)
default_very_steep_up = round(base_very_steep_up * ftp_factor, 1)

target_speed_flat = st.sidebar.number_input("Flach (−1% bis +1%) (km/h)", 10.0, 45.0, default_flat)
target_speed_light_down = st.sidebar.number_input("Leicht bergab (−3% bis −1%) (km/h)", 10.0, 70.0, default_light_down)
target_speed_down = st.sidebar.number_input("Stark bergab (< −3%) (km/h)", 10.0, 120.0, default_down)

target_speed_light_up = st.sidebar.number_input("Leicht bergauf (1–3%) (km/h)", 5.0, 40.0, default_light_up)
target_speed_med_up = st.sidebar.number_input("Mäßig bergauf (3–6%) (km/h)", 5.0, 35.0, default_med_up)
target_speed_steep_up = st.sidebar.number_input("Stärker bergauf (6–10%) (km/h)", 3.0, 30.0, default_steep_up)
target_speed_very_steep_up = st.sidebar.number_input("Sehr steil (>10%) (km/h)", 2.0, 25.0, default_very_steep_up)


# ---------------------------------------------------------
# VISUALISIERUNG DER ZIELGESCHWINDIGKEITEN
# ---------------------------------------------------------
st.sidebar.subheader("📊 Zielgeschwindigkeiten Übersicht")

target_df = pd.DataFrame([
    {"Kategorie": "Stark bergab (< -3%)", "v_kmh": target_speed_down},
    {"Kategorie": "Leicht bergab (-3% bis -1%)", "v_kmh": target_speed_light_down},
    {"Kategorie": "Flach (-1% bis +1%)", "v_kmh": target_speed_flat},
    {"Kategorie": "Leicht bergauf (1–3%)", "v_kmh": target_speed_light_up},
    {"Kategorie": "Mäßig bergauf (3–6%)", "v_kmh": target_speed_med_up},
    {"Kategorie": "Stärker bergauf (6–10%)", "v_kmh": target_speed_steep_up},
    {"Kategorie": "Sehr steil (>10%)", "v_kmh": target_speed_very_steep_up},
])

target_chart = (
    alt.Chart(target_df)
    .mark_bar()
    .encode(
        x=alt.X("v_kmh:Q", title="Zielgeschwindigkeit (km/h)"),
        y=alt.Y("Kategorie:N", sort=None),
    )
    .properties(height=250)
)
st.sidebar.altair_chart(target_chart, use_container_width=True)


# ---------------------------------------------------------
# SIDEBAR – KONTROLLPUNKTE
# ---------------------------------------------------------
st.sidebar.header("📍 Kontrollpunkte")

if "control_points" not in st.session_state:
    st.session_state["control_points"] = []

if "new_cp_km" not in st.session_state:
    st.session_state["new_cp_km"] = 0.0
if "new_cp_name" not in st.session_state:
    st.session_state["new_cp_name"] = ""
if "new_cp_pause" not in st.session_state:
    st.session_state["new_cp_pause"] = 0

new_cp_km = st.sidebar.number_input(
    "KM für neuen Kontrollpunkt",
    min_value=0.0,
    step=1.0,
    value=st.session_state["new_cp_km"]
)

new_cp_name = st.sidebar.text_input(
    "Name des Kontrollpunkts",
    value=st.session_state["new_cp_name"]
)

new_cp_pause = st.sidebar.number_input(
    "Pause an Kontrollpunkt (Minuten)",
    min_value=0,
    max_value=240,
    value=st.session_state["new_cp_pause"]
)

if st.sidebar.button("Kontrollpunkt hinzufügen"):
    st.session_state["control_points"].append({
        "km": new_cp_km,
        "name": new_cp_name if new_cp_name else f"CP {len(st.session_state['control_points'])+1}",
        "pause_min": new_cp_pause
    })

    # Felder zurücksetzen
    st.session_state["new_cp_km"] = 0.0
    st.session_state["new_cp_name"] = ""
    st.session_state["new_cp_pause"] = 0

    st.experimental_rerun()


for cp in st.session_state["control_points"]:
    st.sidebar.write(f"• {cp['km']} km – {cp['name']} – Pause: {cp['pause_min']} min")


# ---------------------------------------------------------
# SIDEBAR – PAUSENPUNKTE
# ---------------------------------------------------------
st.sidebar.header("⏸ Pausenpunkte")

if "pauses" not in st.session_state:
    st.session_state["pauses"] = []

if "new_pause_km" not in st.session_state:
    st.session_state["new_pause_km"] = 0.0
if "new_pause_min" not in st.session_state:
    st.session_state["new_pause_min"] = 0

new_pause_km = st.sidebar.number_input(
    "KM für neue Pause",
    min_value=0.0,
    step=1.0,
    value=st.session_state["new_pause_km"]
)

new_pause_min = st.sidebar.number_input(
    "Pausendauer (Minuten)",
    min_value=0,
    max_value=240,
    value=st.session_state["new_pause_min"]
)

if st.sidebar.button("Pause hinzufügen"):
    st.session_state["pauses"].append({
        "km": new_pause_km,
        "pause_min": new_pause_min
    })

    st.session_state["new_pause_km"] = 0.0
    st.session_state["new_pause_min"] = 0

    st.experimental_rerun()


for p in st.session_state["pauses"]:
    st.sidebar.write(f"• Pause bei {p['km']} km – {p['pause_min']} min")


# ---------------------------------------------------------
# HELPERS
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


def parse_gpx(file) -> pd.DataFrame:
    tree = ET.parse(file)
    root = tree.getroot()
    ns = {"default": "http://www.topografix.com/GPX/1/1"}

    data = []
    for trkpt in root.findall(".//default:trkpt", ns):
        lat = float(trkpt.attrib.get("lat"))
        lon = float(trkpt.attrib.get("lon"))
        ele_el = trkpt.find("default:ele", ns)
        time_el = trkpt.find("default:time", ns)

        data.append({
            "lat": lat,
            "lon": lon,
            "elevation": float(ele_el.text) if ele_el is not None else None,
            "time": time_el.text if time_el is not None else None
        })

    return pd.DataFrame(data)


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def add_distance_and_gradient(df: pd.DataFrame) -> pd.DataFrame:
    distances = [0.0]
    for i in range(1, len(df)):
        d = haversine(
            df.iloc[i-1]["lat"], df.iloc[i-1]["lon"],
            df.iloc[i]["lat"], df.iloc[i]["lon"]
        )
        distances.append(distances[-1] + d)

    df["distance_m"] = distances
    df["km"] = df["distance_m"] / 1000

    if "elevation" in df and not df["elevation"].isna().all():
        df["delta_h"] = df["elevation"].diff()
        df["delta_m"] = df["distance_m"].diff().replace(0, 0.1)
        df["gradient_raw"] = (df["delta_h"] / df["delta_m"]) * 100
        df["gradient"] = df["gradient_raw"].clip(-20, 20).rolling(window=5, center=True, min_periods=1).mean()
    else:
        df["gradient"] = 0.0

    return df


# ---------------------------------------------------------
# ZIELGESCHWINDIGKEITS-MODELL (kategorienbasiert, FTP-sensitiv)
# ---------------------------------------------------------
def compute_segment_speed_category(
    gradient,
    ftp,
    target_speed_flat,
    target_speed_light_down,
    target_speed_down,
    target_speed_light_up,
    target_speed_med_up,
    target_speed_steep_up,
    target_speed_very_steep_up,
    max_downhill_speed,
    min_speed
):
    g = gradient

    if g < -3:
        base = target_speed_down
        regime = "stark bergab"
    elif -3 <= g < -1:
        base = target_speed_light_down
        regime = "leicht bergab"
    elif -1 <= g <= 1:
        base = target_speed_flat
        regime = "flach"
    elif 1 < g <= 3:
        base = target_speed_light_up
        regime = "leicht bergauf"
    elif 3 < g <= 6:
        base = target_speed_med_up
        regime = "mäßig bergauf"
    elif 6 < g <= 10:
        base = target_speed_steep_up
        regime = "stärker bergauf"
    else:
        base = target_speed_very_steep_up
        regime = "sehr steil bergauf"

    ftp_factor_local = (ftp / 220) ** 0.15
    v = base * ftp_factor_local

    if g < 0:
        v = min(v, max_downhill_speed)
    v = max(v, min_speed)

    return v, regime


# ---------------------------------------------------------
# TIME PROFILE (Simulation)
# ---------------------------------------------------------
def add_time_profile(df: pd.DataFrame) -> pd.DataFrame:
    times = [0.0]
    speeds_kmh = [0.0]
    regime_list = ["start"]

    for i in range(1, len(df)):
        grad = df.iloc[i]["gradient"]
        dist_m = df.iloc[i]["distance_m"] - df.iloc[i-1]["distance_m"]

        if dist_m <= 0:
            times.append(times[-1])
            speeds_kmh.append(speeds_kmh[-1])
            regime_list.append(regime_list[-1])
            continue

        v_kmh, regime = compute_segment_speed_category(
            grad,
            ftp,
            target_speed_flat,
            target_speed_light_down,
            target_speed_down,
            target_speed_light_up,
            target_speed_med_up,
            target_speed_steep_up,
            target_speed_very_steep_up,
            max_downhill_speed,
            min_speed
        )

        v_ms = max(0.1, v_kmh / 3.6)
        dt = dist_m / v_ms

        times.append(times[-1] + dt)
        speeds_kmh.append(v_kmh)
        regime_list.append(regime)

    df["speed_kmh"] = speeds_kmh
    df["time_s"] = times
    df["sim_time"] = [start_datetime + timedelta(seconds=t) for t in times]
    df["regime"] = regime_list

    return df


# ---------------------------------------------------------
# MAP
# ---------------------------------------------------------
def show_map(df: pd.DataFrame, control_points, pauses):
    if df.empty:
        st.warning("Keine GPS-Daten für die Karte.")
        return

    path = df.apply(lambda r: [r["lon"], r["lat"]], axis=1).tolist()
    start = path[0]
    end = path[-1]
    midpoint = (df["lat"].mean(), df["lon"].mean())

    layers = []

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

    layers.append(
        pdk.Layer(
            "ScatterplotLayer",
            data=[{"lon": start[0], "lat": start[1], "name": "Start", "pause_min": 0}],
            get_position="[lon, lat]",
            get_color=[0, 200, 0],
            get_radius=1200,
        )
    )

    layers.append(
        pdk.Layer(
            "ScatterplotLayer",
            data=[{"lon": end[0], "lat": end[1], "name": "Ziel", "pause_min": 0}],
            get_position="[lon, lat]",
            get_color=[0, 0, 0],
            get_radius=1200,
        )
    )

    cp_data = []
    for cp in control_points:
        try:
            target_km = float(cp["km"])
        except:
            continue
        nearest = df.iloc[(df["km"] - target_km).abs().argmin()]
        cp_data.append({
            "lon": nearest["lon"],
            "lat": nearest["lat"],
            "name": cp["name"],
            "pause_min": cp.get("pause_min", 0)
        })

    if cp_data:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=cp_data,
                get_position="[lon, lat]",
                get_color=[0, 100, 255],
                get_radius=1500,
            )
        )

    pause_data = []
    for p in pauses:
        try:
            target_km = float(p["km"])
        except:
            continue
        nearest = df.iloc[(df["km"] - target_km).abs().argmin()]
        pause_data.append({
            "lon": nearest["lon"],
            "lat": nearest["lat"],
            "name": "Pause",
            "pause_min": p.get("pause_min", 0)
        })

    if pause_data:
        layers.append(
            pdk.Layer(
                "ScatterplotLayer",
                data=pause_data,
                get_position="[lon, lat]",
                get_color=[255, 220, 0],
                get_radius=1500,
            )
        )

    view_state = pdk.ViewState(
        latitude=midpoint[0],
        longitude=midpoint[1],
        zoom=10,
        pitch=0,
    )

    st.pydeck_chart(
        pdk.Deck(
            layers=layers,
            initial_view_state=view_state,
            tooltip={
                "html": "<b>{name}</b><br/>Pause: {pause_min} min",
                "style": {"color": "white"}
            }
        )
    )


# ---------------------------------------------------------
# ELEVATION PROFILE
# ---------------------------------------------------------
def show_elevation_profile(df: pd.DataFrame):
    if "elevation" not in df or df["elevation"].isna().all():
        st.info("Keine Höhendaten in dieser GPX-Datei.")
        return

    df_plot = df.copy()

    df_plot["elevation_smooth"] = (
        df_plot["elevation"]
        .rolling(window=25, center=True, min_periods=1)
        .mean()
    )

    df_plot["gradient_smooth"] = (
        df_plot["gradient"]
        .rolling(window=25, center=True, min_periods=1)
        .mean()
    )

    def gradient_color(g):
        if g < 2:
            return "green"
        elif g < 5:
            return "yellow"
        elif g < 8:
            return "orange"
        else:
            return "red"

    df_plot["color"] = df_plot["gradient_smooth"].apply(gradient_color)

    chart = (
        alt.Chart(df_plot)
        .mark_bar()
        .encode(
            x=alt.X("km:Q", title="Distanz (km)"),
            y=alt.Y("elevation_smooth:Q", title="Höhe (m)"),
            color=alt.Color("color:N", scale=None, legend=None),
        )
        .properties(height=250)
    )

    st.altair_chart(chart, use_container_width=True)


# ---------------------------------------------------------
# SPEED CURVE VISUALIZATION
# ---------------------------------------------------------
def show_speed_curve(df: pd.DataFrame):
    if "speed_kmh" not in df:
        return

    base = alt.Chart(df).encode(x=alt.X("km:Q", title="Distanz (km)"))

    speed_line = base.mark_line(color="steelblue").encode(
        y=alt.Y("speed_kmh:Q", title="Geschwindigkeit (km/h)")
    )

    grad_line = base.mark_line(color="orange").encode(
        y=alt.Y("gradient:Q", title="Gradient (%)", axis=alt.Axis(titleColor="orange"))
    )

    chart = alt.layer(speed_line, grad_line).resolve_scale(y="independent").properties(height=250)
    st.altair_chart(chart, use_container_width=True)


# ---------------------------------------------------------
# PAUSEN IN ZEITRECHNUNG (Option 2)
# ---------------------------------------------------------
def apply_pauses(df: pd.DataFrame, control_points, pauses):
    total_pause_s = 0.0
    pause_events = set()
    df["sim_time_with_pauses"] = None

    for i in range(len(df)):
        km = df.iloc[i]["km"]
        base_time = df.iloc[i]["sim_time"]

        for cp in control_points:
            try:
                cp_km = float(cp["km"])
            except:
                continue
            if abs(km - cp_km) < 0.05:
                key = ("cp", round(cp_km, 2))
                if key not in pause_events:
                    pause_events.add(key)
                    total_pause_s += cp.get("pause_min", 0) * 60

        for p in pauses:
            try:
                p_km = float(p["km"])
            except:
                continue
            if abs(km - p_km) < 0.05:
                key = ("pause", round(p_km, 2))
                if key not in pause_events:
                    pause_events.add(key)
                    total_pause_s += p.get("pause_min", 0) * 60

        df.at[i, "sim_time_with_pauses"] = base_time + timedelta(seconds=total_pause_s)

    return df


# ---------------------------------------------------------
# SUMMARY TABLE (Start, CPs, Pausen, Ziel)
# ---------------------------------------------------------
def build_summary_table(df, control_points, pauses):
    points = []

    points.append({
        "km": 0.0,
        "name": "Start",
        "pause_min": 0
    })

    for cp in control_points:
        points.append({
            "km": float(cp["km"]),
            "name": cp["name"],
            "pause_min": cp.get("pause_min", 0)
        })

    for p in pauses:
        points.append({
            "km": float(p["km"]),
            "name": "Pause",
            "pause_min": p.get("pause_min", 0)
        })

    points.append({
        "km": float(df["km"].iloc[-1]),
        "name": "Ziel",
        "pause_min": 0
    })

    points = sorted(points, key=lambda x: x["km"])

    rows = []
    start_time_with_pauses = df["sim_time_with_pauses"].iloc[0]
    last_km = 0.0
    last_time = start_time_with_pauses
    last_elev = df["elevation"].iloc[0] if "elevation" in df else 0

    for p in points:
        nearest = df.iloc[(df["km"] - p["km"]).abs().argmin()]

        km_total = float(nearest["km"])
        km_diff = km_total - last_km

        time_total = nearest["sim_time_with_pauses"]
        time_diff = time_total - last_time

        elev_total = float(nearest["elevation"]) if "elevation" in df else 0.0
        elev_diff = elev_total - last_elev

        hours_total = (time_total - start_time_with_pauses).total_seconds() / 3600
        hours_diff = time_diff.total_seconds() / 3600

        avg_speed_total = km_total / hours_total if hours_total > 0 else 0
        avg_speed_diff = km_diff / hours_diff if hours_diff > 0 else 0

        rows.append({
            "Name": p["name"],
            "KM gesamt": round(km_total, 2),
            "KM seit letztem Punkt": round(km_diff, 2),
            "HM gesamt": round(elev_total, 0),
            "HM seit letztem Punkt": round(elev_diff, 0),
            "Zeit gesamt": time_total.strftime("%Y-%m-%d %H:%M:%S"),
            "Zeit seit letztem Punkt": str(time_diff),
            "Ø‑Speed gesamt (km/h)": round(avg_speed_total, 1),
            "Ø‑Speed Abschnitt (km/h)": round(avg_speed_diff, 1),
            "Pause (min)": p["pause_min"]
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
    st.success(f"{len(uploaded_files)} Datei(en) geladen")

    all_dfs = {}

    for file in uploaded_files:
        st.subheader(f"📍 {file.name}")

        df = parse_gpx(file)
        df = add_distance_and_gradient(df)
        df = add_time_profile(df)
        df = apply_pauses(df, st.session_state["control_points"], st.session_state["pauses"])

        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**Rohdaten & Simulation (Auszug)**")
            st.dataframe(
                df[["km", "elevation", "gradient", "speed_kmh", "sim_time", "sim_time_with_pauses"]].head(500)
            )

        with col2:
            st.subheader("🗺️ Karte")
            show_map(df, st.session_state["control_points"], st.session_state["pauses"])

        st.subheader("⛰️ Höhenprofil")
        show_elevation_profile(df)

        st.subheader("📈 Geschwindigkeitskurve")
        show_speed_curve(df)

        with st.expander("🔍 Regime je Abschnitt"):
            st.dataframe(df[["km", "gradient", "speed_kmh", "regime"]].head(500))

        st.subheader("📋 Kontroll‑ & Pausentabelle")
        summary_df = build_summary_table(
            df,
            st.session_state["control_points"],
            st.session_state["pauses"]
        )
        st.dataframe(summary_df)

        # Zusammenfassung als Excel exportieren
        summary_excel = BytesIO()
        with pd.ExcelWriter(summary_excel, engine="xlsxwriter") as writer:
            summary_df.to_excel(writer, sheet_name="Zusammenfassung", index=False)

        st.download_button(
            label="📥 Zusammenfassung als Excel",
            data=summary_excel.getvalue(),
            file_name=f"brevet_summary_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        # Zusammenfassung als PDF exportieren
        pdf_buffer = BytesIO()
        c = canvas.Canvas(pdf_buffer, pagesize=A4)
        width, height = A4

        text = c.beginText(40, height - 40)
        text.setFont("Helvetica", 9)

        header = "Brevet Zusammenfassung"
        text.textLine(header)
        text.textLine("")

        for i, row in summary_df.iterrows():
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

        finish_time = df.iloc[-1]["sim_time_with_pauses"]
        total_time = finish_time - start_datetime
        st.markdown(f"**Ankunftszeit (inkl. Pausen):** {finish_time}")
        st.markdown(f"**Gesamtzeit:** {total_time}")

        all_dfs[file.name] = df

    excel_bytes = export_to_excel(all_dfs)
    st.download_button(
        label="📥 Excel Export (alle Daten)",
        data=excel_bytes,
        file_name=f"brevet_export_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
else:
    st.info("Bitte eine oder mehrere GPX-Dateien hochladen.")








