import math
from datetime import datetime, timedelta
from io import BytesIO

import streamlit as st
import folium
import gpxpy
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from streamlit.components.v1 import html
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# ---------------------------------------------------------
# Streamlit Grundkonfiguration
# ---------------------------------------------------------
st.set_page_config(page_title="Brevet-Simulator", layout="wide")
st.title("🚴‍♂️ Brevet-Simulator mit GPX, FTP, ACP & Wind (Robuste Version)")

# ---------------------------------------------------------
# Sidebar: Fahrer- und Leistungsparameter
# ---------------------------------------------------------
st.sidebar.header("Fahrerparameter")

ftp = st.sidebar.number_input("FTP (Watt)", min_value=100, max_value=500, value=250)
weight = st.sidebar.number_input("Fahrergewicht (kg)", min_value=40, max_value=120, value=75)
bike_weight = st.sidebar.number_input("Radgewicht (kg)", min_value=5, max_value=20, value=8)
cda = st.sidebar.number_input("CdA (m²)", min_value=0.20, max_value=0.40, value=0.30)
crr = st.sidebar.number_input("Rollwiderstand Crr", min_value=0.002, max_value=0.010, value=0.005)

st.sidebar.header("Leistungsprofile")

profile_flat = st.sidebar.slider("Leistungsfaktor Flach (% FTP)", 0.5, 1.2, 0.75)
profile_climb = st.sidebar.slider("Leistungsfaktor Berg (% FTP)", 0.5, 1.5, 0.90)
profile_down = st.sidebar.slider("Leistungsfaktor Abfahrt (% FTP)", 0.3, 1.0, 0.60)

st.sidebar.header("Windmodell")

wind_speed = st.sidebar.number_input("Windgeschwindigkeit (m/s, + = Gegenwind)", -10.0, 10.0, 0.0)
wind_direction = st.sidebar.slider("Windrichtung (°) – 0° = Norden", 0, 359, 0)

st.sidebar.header("ACP-Startzeit")

start_date = st.sidebar.date_input("Startdatum", value=datetime.today())
start_time = st.sidebar.time_input("Startzeit", value=datetime.now().time())
start_dt = datetime.combine(start_date, start_time)

# ---------------------------------------------------------
# Sidebar: Pausen & Kontrollpunkte
# ---------------------------------------------------------
st.sidebar.header("Pausenplanung")

pause_default = pd.DataFrame({
    "km": [50, 120, 200],
    "Dauer_min": [10, 20, 15]
})
pause_df = st.sidebar.data_editor(
    pause_default,
    num_rows="dynamic",
    key="pausen_editor"
)

st.sidebar.header("Kontrollpunkte (manuell)")

kontroll_default = pd.DataFrame({
    "km": [50, 120, 200],
    "Name": ["CP1", "CP2", "CP3"]
})
kontroll_df = st.sidebar.data_editor(
    kontroll_default,
    num_rows="dynamic",
    key="kontroll_editor"
)

# ---------------------------------------------------------
# GPX Upload
# ---------------------------------------------------------
uploaded_files = st.file_uploader(
    "GPX-Dateien hochladen (mehrere möglich)",
    type=["gpx"],
    accept_multiple_files=True
)

colors = ["red", "blue", "green", "purple", "orange", "black", "brown"]
# ---------------------------------------------------------
# Hauptlogik – WICHTIG: Diese Bedingung muss existieren!
# ---------------------------------------------------------

if uploaded_files and len(uploaded_files) > 0:

    st.write("DEBUG: Dateien erkannt:", [f.name for f in uploaded_files])

    track_stats = []
    m = None

    for idx, file in enumerate(uploaded_files):
        gpx = gpxpy.parse(file)
        color = colors[idx % len(colors)]

        all_points = []
        for track in gpx.tracks:
            for segment in track.segments:
                for p in segment.points:
                    all_points.append((p.latitude, p.longitude, p.elevation, p.time))

        if not all_points:
            st.warning(f"⚠️ Keine Punkte in {file.name}")
            continue

        df = compute_stats(all_points)
        df = apply_breaks(df, pause_df)
        controls = compute_controls(df, kontroll_df)

        track_stats.append((file.name, df, controls))

        if m is None:
            m = folium.Map(location=[df.lat.iloc[0], df.lon.iloc[0]], zoom_start=11)

        folium.PolyLine(
            df[["lat", "lon"]].values,
            color=color,
            weight=4,
            opacity=0.9,
            tooltip=file.name
        ).add_to(m)

        folium.Marker(
            [df.lat.iloc[0], df.lon.iloc[0]],
            popup=f"{file.name} Start"
        ).add_to(m)
        folium.Marker(
            [df.lat.iloc[-1], df.lon.iloc[-1]],
            popup=f"{file.name} Ziel"
        ).add_to(m)

    # Karte anzeigen
    st.subheader("🗺️ Karte")
    html(m._repr_html_(), height=600)

    # Restliche Auswertungen …
    # (dein bestehender Code bleibt unverändert)

else:
    st.info("Bitte eine oder mehrere GPX-Dateien hochladen.")


# ---------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------
def acp_control_time(km):
    segments = [
        (200, 34),
        (200, 32),
        (200, 30),
        (400, 28),
        (300, 26)
    ]
    remaining = km
    total_hours = 0
    for dist, vmax in segments:
        if remaining <= 0:
            break
        take = min(remaining, dist)
        total_hours += take / vmax
        remaining -= take
    return total_hours * 3600


def wind_effect(lat1, lon1, lat2, lon2, wind_speed, wind_dir_deg):
    dx = lon2 - lon1
    dy = lat2 - lat1
    if dx == 0 and dy == 0:
        return 0.0
    heading = math.degrees(math.atan2(dx, dy)) % 360
    relative_angle = abs(heading - wind_dir_deg)
    relative_angle = min(relative_angle, 360 - relative_angle)
    return wind_speed * math.cos(math.radians(relative_angle))


def speed_from_power(power, slope, mass, cda, crr, headwind):
    g = 9.81
    rho = 1.226
    theta = math.atan(slope)
    v = 5.0
    for _ in range(25):
        f_gravity = mass * g * math.sin(theta)
        f_roll = mass * g * crr
        v_rel = v + headwind
        f_aero = 0.5 * rho * cda * v_rel * v_rel
        f_total = f_gravity + f_roll + f_aero
        p_calc = f_total * v
        dpdv = f_total + v * (rho * cda * v_rel)
        v = v - (p_calc - power) / max(dpdv, 1e-3)
        v = max(v, 0.1)
    return v


def sanitize_gpx(df):
    """Entfernt NaN, Inf, doppelte Punkte, glättet Höhenmeter."""
    df = df.copy()

    # Höhenmeter glätten
    df["ele"] = df["ele"].replace([np.inf, -np.inf], np.nan)
    df["ele"] = df["ele"].interpolate().fillna(method="bfill").fillna(method="ffill")

    # Doppelte Punkte entfernen
    df = df.loc[~((df["lat"].diff() == 0) & (df["lon"].diff() == 0))]

    return df.reset_index(drop=True)


def compute_stats(points):
    df = pd.DataFrame(points, columns=["lat", "lon", "ele", "time"])
    df = sanitize_gpx(df)

    # Distanz robust
    df["dist"] = np.sqrt(
        (df["lat"].diff() * 111_320) ** 2 +
        (df["lon"].diff() * 40075_000 * np.cos(np.radians(df["lat"])) / 360) ** 2
    )
    df["dist"] = df["dist"].replace([np.inf, -np.inf], 0).fillna(0)
    df.loc[df["dist"] < 0.01, "dist"] = 0
    df["cum_dist"] = df["dist"].cumsum()

    # Steigung robust
    df["ele_diff"] = df["ele"].diff().fillna(0)
    df["slope"] = df["ele_diff"] / df["dist"].replace(0, np.nan)
    df["slope"] = df["slope"].replace([np.inf, -np.inf], 0).fillna(0)
    df["slope"] = df["slope"].clip(-0.3, 0.3)

    # Wind
    wind_components = [0.0]
    for i in range(1, len(df)):
        w = wind_effect(
            df.lat.iloc[i - 1], df.lon.iloc[i - 1],
            df.lat.iloc[i], df.lon.iloc[i],
            wind_speed, wind_direction
        )
        wind_components.append(w)
    df["wind_mps"] = wind_components

    # Geschwindigkeit
    mass = weight + bike_weight
    speeds = []
    for _, row in df.iterrows():
        slope = row["slope"]
        headwind = row["wind_mps"]
        if slope > 0.02:
            factor = profile_climb
        elif slope < -0.02:
            factor = profile_down
        else:
            factor = profile_flat
        power = ftp * factor
        v = speed_from_power(power, slope, mass, cda, crr, headwind)
        speeds.append(v)

    df["speed_mps"] = speeds
    df["speed_kmh"] = df["speed_mps"] * 3.6

    # Zeit robust
    df["time_s"] = df["dist"] / df["speed_mps"].replace(0, np.nan)
    df["time_s"] = df["time_s"].replace([np.inf, -np.inf], 0).fillna(0)
    df.loc[df["time_s"] < 0, "time_s"] = 0
    df["cum_time_s"] = df["time_s"].cumsum()

    # ACP
    df["acp_limit_s"] = df["cum_dist"].apply
