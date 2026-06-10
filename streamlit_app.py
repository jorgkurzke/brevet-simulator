# ---------------------------------------------------------
# IMPORTS
# ---------------------------------------------------------
import math
from datetime import datetime, timedelta
from io import BytesIO

import streamlit as st
import pandas as pd
import xlsxwriter
import pydeck as pdk
import altair as alt
import xml.etree.ElementTree as ET


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

ftp = st.sidebar.number_input("FTP (Watt)", min_value=100, max_value=400, value=220, step=5)

st.sidebar.subheader("Leistungsprofile")
power_flat = st.sidebar.number_input("Leistung flach (W)", min_value=80, max_value=400, value=180)
power_climb = st.sidebar.number_input("Leistung bergauf (W)", min_value=80, max_value=400, value=220)

st.sidebar.subheader("Physikalisches Modell")
c_rr = st.sidebar.number_input("Rollwiderstand Crr", min_value=0.002, max_value=0.01, value=0.004, step=0.001)
c_dA = st.sidebar.number_input("Luftwiderstand CdA", min_value=0.15, max_value=0.40, value=0.28, step=0.01)
weight = st.sidebar.number_input("Systemgewicht (kg)", min_value=60, max_value=120, value=85)

st.sidebar.subheader("Windmodell")
wind_speed = st.sidebar.number_input("Windstärke (km/h)", min_value=0, max_value=80, value=10)
wind_dir = st.sidebar.slider("Windrichtung (°)", min_value=0, max_value=360, value=180)

st.sidebar.subheader("Maximale Abfahrtsgeschwindigkeit")
max_downhill_speed = st.sidebar.number_input(
    "Max-Speed bergab (km/h)",
    min_value=40,
    max_value=120,
    value=70,
    step=1
)

st.sidebar.header("⏱ ACP‑Start")
start_date = st.sidebar.date_input("Startdatum", datetime.now().date())
start_time = st.sidebar.time_input("Startzeit", datetime.now().time())
start_datetime = datetime.combine(start_date, start_time)


# ---------------------------------------------------------
# SIDEBAR – KONTROLLPUNKTE
# ---------------------------------------------------------
st.sidebar.header("📍 Kontrollpunkte")

if "control_points" not in st.session_state:
    st.session_state["control_points"] = []

new_cp_km = st.sidebar.number_input("KM für neuen Kontrollpunkt", min_value=0.0, step=1.0)
new_cp_name = st.sidebar.text_input("Name des Kontrollpunkts")
new_cp_pause = st.sidebar.number_input("Pause an Kontrollpunkt (Minuten)", min_value=0, max_value=240, value=0)

if st.sidebar.button("Kontrollpunkt hinzufügen"):
    st.session_state["control_points"].append({
        "km": new_cp_km,
        "name": new_cp_name if new_cp_name else f"CP {len(st.session_state['control_points'])+1}",
        "pause_min": new_cp_pause
    })

for i, cp in enumerate(st.session_state["control_points"]):
    st.sidebar.write(f"• {cp['km']} km – {cp['name']} – Pause: {cp['pause_min']} min")


# ---------------------------------------------------------
# SIDEBAR – PAUSENPUNKTE
# ---------------------------------------------------------
st.sidebar.header("⏸ Pausenpunkte")

if "pauses" not in st.session_state:
    st.session_state["pauses"] = []

new_pause_km = st.sidebar.number_input("KM für neue Pause", min_value=0.0, step=1.0)
new_pause_min = st.sidebar.number_input("Pausendauer (Minuten)", min_value=0, max_value=240, value=0)

if st.sidebar.button("Pause hinzufügen"):
    st.session_state["pauses"].append({
        "km": new_pause_km,
        "pause_min": new_pause_min
    })

for i, p in enumerate(st.session_state["pauses"]):
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
        df["gradient"] = (df["delta_h"] / df["delta_m"]) * 100
        df["gradient"] = df["gradient"].clip(-20, 20)
    else:
        df["gradient"] = 0.0

    return df


# ---------------------------------------------------------
# PHYSICS – SPEED MODEL
# ---------------------------------------------------------
def compute_segment_speed(gradient, wind_speed_kmh, c_rr, c_dA, weight_kg, power_flat, power_climb, max_downhill_speed_kmh):
    g = 9.81
    rho = 1.225
    m = weight_kg

    # wind: simple model – assume headwind if gradient >= 0, tailwind if < 0
    wind_ms = wind_speed_kmh / 3.6
    # base power
    if gradient > 1.0:
        P = power_climb
    elif gradient < -1.0:
        # bergab: Leistung ignorieren, nur Gravitation – Widerstände
        P = None
    else:
        P = power_flat

    # if downhill and P is None: solve v from forces balance
    if P is None:
        # approximate equilibrium speed: m*g*sin(theta) = F_roll + F_aero
        theta = math.atan(gradient / 100.0)
        # iterate simple
        v = 10.0  # m/s initial
        for _ in range(20):
            F_grav = m * g * math.sin(theta)
            F_roll = m * g * c_rr * math.cos(theta)
            F_aero = 0.5 * rho * c_dA * (v + wind_ms)**2
            net = F_grav - F_roll - F_aero
            v = max(0.1, v + 0.2 * net)  # simple relaxation
        v_kmh = v * 3.6
        return min(v_kmh, max_downhill_speed_kmh)

    # otherwise: use power balance
    # iterate to find v such that P ≈ v * (F_roll + F_grav + F_aero)
    v = 5.0  # m/s initial
    theta = math.atan(gradient / 100.0)
    for _ in range(20):
        F_roll = m * g * c_rr * math.cos(theta)
        F_grav = m * g * math.sin(theta)
        F_aero = 0.5 * rho * c_dA * (v + wind_ms)**2
        denom = F_roll + F_grav + F_aero
        if denom <= 0:
            v = 0.1
            break
        v = max(0.1, P / denom)
    v_kmh = v * 3.6
    # bergab limit
    if gradient < -1.0:
        v_kmh = min(v_kmh, max_downhill_speed_kmh)
    return v_kmh


def add_time_profile(df: pd.DataFrame,
                     c_rr, c_dA, weight,
                     power_flat, power_climb,
                     wind_speed, max_downhill_speed):
    times = [0.0]  # seconds from start
    speeds_kmh = [0.0]

    for i in range(1, len(df)):
        grad = df.iloc[i]["gradient"]
        dist_m = df.iloc[i]["distance_m"] - df.iloc[i-1]["distance_m"]
        if dist_m <= 0:
            times.append(times[-1])
            speeds_kmh.append(speeds_kmh[-1])
            continue

        v_kmh = compute_segment_speed(
            grad,
            wind_speed,
            c_rr,
            c_dA,
            weight,
            power_flat,
            power_climb,
            max_downhill_speed
        )
        v_ms = max(0.1, v_kmh / 3.6)
        dt = dist_m / v_ms
        times.append(times[-1] + dt)
        speeds_kmh.append(v_kmh)

    df["speed_kmh"] = speeds_kmh
    df["time_s"] = times
    df["sim_time"] = [start_datetime + timedelta(seconds=t) for t in times]
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

    # Track
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

    # Start
    layers.append(
        pdk.Layer(
            "ScatterplotLayer",
            data=[{"lon": start[0], "lat": start[1], "name": "Start", "pause_min": 0}],
            get_position="[lon, lat]",
            get_color=[0, 200, 0],
            get_radius=200,
        )
    )

    # Ziel
    layers.append(
        pdk.Layer(
            "ScatterplotLayer",
            data=[{"lon": end[0], "lat": end[1], "name": "Ziel", "pause_min": 0}],
            get_position="[lon, lat]",
            get_color=[0, 0, 0],
            get_radius=200,
        )
    )

    # Kontrollpunkte
    cp_data = []
    for cp in control_points:
        if "km" not in cp:
            continue
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
                get_radius=250,
            )
        )

    # Pausenpunkte
    pause_data = []
    for p in pauses:
        if "km" not in p:
            continue
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
                get_radius=250,
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
    df_plot["gradient_smooth"] = df_plot["gradient"].rolling(window=15, center=True, min_periods=1).mean()

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
            y=alt.Y("elevation:Q", title="Höhe (m)"),
            color=alt.Color("color:N", scale=None, legend=None),
        )
        .properties(height=250)
    )

    st.altair_chart(chart, use_container_width=True)


# ---------------------------------------------------------
# PAUSEN IN ZEITRECHNUNG (Option 2)
# ---------------------------------------------------------
def apply_pauses(df: pd.DataFrame, control_points, pauses):
    # wir berechnen eine zusätzliche Spalte "sim_time_with_pauses"
    total_pause_s = 0.0
    pause_events = []

    for i in range(len(df)):
        km = df.iloc[i]["km"]
        base_time = df.iloc[i]["sim_time"]

        # check CPs
        for cp in control_points:
            try:
                cp_km = float(cp["km"])
            except:
                continue
            if abs(km - cp_km) < 0.05:  # innerhalb 50 m
                # wenn Pause hier noch nicht gezählt wurde:
                key = ("cp", cp_km)
                if key not in pause_events:
                    pause_events.append(key)
                    total_pause_s += cp.get("pause_min", 0) * 60

        # check Pausenpunkte
        for p in pauses:
            try:
                p_km = float(p["km"])
            except:
                continue
            if abs(km - p_km) < 0.05:
                key = ("pause", p_km)
                if key not in pause_events:
                    pause_events.append(key)
                    total_pause_s += p.get("pause_min", 0) * 60

        df.at[i, "sim_time_with_pauses"] = base_time + timedelta(seconds=total_pause_s)

    return df


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
        df = add_time_profile(
            df,
            c_rr=c_rr,
            c_dA=c_dA,
            weight=weight,
            power_flat=power_flat,
            power_climb=power_climb,
            wind_speed=wind_speed,
            max_downhill_speed=max_downhill_speed
        )
        df = apply_pauses(df, st.session_state["control_points"], st.session_state["pauses"])

        st.markdown("**Rohdaten & Simulation**")
        st.dataframe(df[["lat", "lon", "km", "elevation", "gradient", "speed_kmh", "sim_time", "sim_time_with_pauses"]])

        st.subheader("🗺️ Karte")
        show_map(df, st.session_state["control_points"], st.session_state["pauses"])

        st.subheader("⛰️ Höhenprofil")
        show_elevation_profile(df)

        # Gesamtzeit
        finish_time = df.iloc[-1]["sim_time_with_pauses"]
        total_time = finish_time - start_datetime
        st.markdown(f"**Ankunftszeit (inkl. Pausen):** {finish_time}")
        st.markdown(f"**Gesamtzeit:** {total_time}")

        all_dfs[file.name] = df

    excel_bytes = export_to_excel(all_dfs)
    st.download_button(
        label="📥 Excel Export",
        data=excel_bytes,
        file_name=f"brevet_export_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
else:
    st.info("Bitte eine oder mehrere GPX-Dateien hochladen.")


