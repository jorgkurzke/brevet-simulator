import math
import datetime as dt

import gpxpy
import gpxpy.geo
import numpy as np
import pandas as pd
import streamlit as st
from fpdf import FPDF

# -----------------------------------------------------
# Konstanten
# -----------------------------------------------------
g_const = 9.81
air_density = 1.226  # kg/m³


# -----------------------------------------------------
# GPX-PARSER
# -----------------------------------------------------
def parse_gpx(file) -> pd.DataFrame:
    gpx = gpxpy.parse(file)

    lats = []
    lons = []
    elevs = []
    dists = []

    total_dist = 0.0
    last_point = None

    for track in gpx.tracks:
        for segment in track.segments:
            if len(segment.points) == 0:
                continue

            for point in segment.points:
                lats.append(point.latitude)
                lons.append(point.longitude)
                elevs.append(point.elevation)

                if last_point is not None:
                    dx = gpxpy.geo.haversine_distance(
                        last_point.latitude,
                        last_point.longitude,
                        point.latitude,
                        point.longitude,
                    )
                    if dx is None or math.isnan(dx):
                        dx = 0.0
                    total_dist += dx

                dists.append(total_dist)
                last_point = point

    df = pd.DataFrame(
        {
            "lat": lats,
            "lon": lons,
            "elev": elevs,
            "distance_m": dists,
        }
    )

    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df["elev"] = pd.to_numeric(df["elev"], errors="coerce")
    df["distance_m"] = pd.to_numeric(df["distance_m"], errors="coerce")

    df["lat"].fillna(method="ffill", inplace=True)
    df["lon"].fillna(method="ffill", inplace=True)

    if df["elev"].isna().all():
        df["elev"] = 0.0
    df["elev"].fillna(method="ffill", inplace=True)
    df["elev"].fillna(method="bfill", inplace=True)

    df["distance_m"].fillna(method="ffill", inplace=True)
    df["distance_m"].fillna(0.0, inplace=True)

    df["gradient"] = 0.0
    for i in range(1, len(df)):
        dh = df["elev"].iloc[i] - df["elev"].iloc[i - 1]
        dx = df["distance_m"].iloc[i] - df["distance_m"].iloc[i - 1]
        if dx > 0:
            df.loc[df.index[i], "gradient"] = (dh / dx) * 100.0

    return df


# -----------------------------------------------------
# ACP-ZEITEN
# -----------------------------------------------------
def compute_acp_times(df: pd.DataFrame) -> pd.DataFrame:
    max_speeds = [
        (200, 34),
        (400, 32),
        (600, 30),
        (1000, 28),
        (1300, 26),
    ]

    min_speeds = [
        (200, 15),
        (400, 15),
        (600, 15),
        (1000, 11.428),
        (1300, 13.333),
    ]

    def acp_time(km, table):
        remaining = km
        total_hours = 0.0
        for limit, speed in table:
            if remaining <= 0:
                break
            segment = min(remaining, limit)
            total_hours += segment / speed
            remaining -= segment
        return total_hours * 3600.0

    rows = []
    for _, row in df.iterrows():
        km = row["distance_m"] / 1000.0
        open_t = acp_time(km, max_speeds)
        close_t = acp_time(km, min_speeds)
        rows.append({"km": km, "open_s": open_t, "close_s": close_t})

    return pd.DataFrame(rows)


# -----------------------------------------------------
# WIND
# -----------------------------------------------------
def wind_component_ms(wind_speed_kmh: float, wind_angle_deg: float) -> float:
    v = wind_speed_kmh / 3.6
    return v * math.cos(math.radians(wind_angle_deg))


# -----------------------------------------------------
# HYBRID-SPEEDMODELL (C2, vektorisiert)
# -----------------------------------------------------
def compute_speed_vectorized(
    gradients,
    target_speed_down,
    target_speed_light_down,
    target_speed_flat,
    target_speed_light_up,
    target_speed_med_up,
    target_speed_steep_up,
    target_speed_very_steep_up,
    watt_down,
    watt_flat,
    watt_up,
    weight_total,
    c_rr,
    c_dA,
    wind_speed,
    wind_angle,
    max_downhill_speed,
    min_speed,
):
    gradients = np.array(gradients)

    # Zielgeschwindigkeit nach Steigung
    v_target = np.where(
        gradients < -3,
        target_speed_down,
        np.where(
            gradients < -1,
            target_speed_light_down,
            np.where(
                gradients < 1,
                target_speed_flat,
                np.where(
                    gradients < 3,
                    target_speed_light_up,
                    np.where(
                        gradients < 6,
                        target_speed_med_up,
                        np.where(
                            gradients < 10,
                            target_speed_steep_up,
                            target_speed_very_steep_up,
                        ),
                    ),
                ),
            ),
        ),
    )

    # Leistung nach Steigung
    P = np.where(
        gradients < -1,
        watt_down,
        np.where(gradients <= 1, watt_flat, watt_up),
    )

    w = wind_component_ms(wind_speed, wind_angle)

    F_roll = weight_total * g_const * c_rr
    F_grav = weight_total * g_const * (gradients / 100.0)

    A = 0.5 * air_density * c_dA
    B = F_roll + F_grav

    # Näherung: zwei Regime
    v_est1 = P / np.maximum(B, 1e-6)
    v_est2 = (P / np.maximum(A, 1e-6)) ** (1.0 / 3.0)

    v_phys = np.where(v_est1 < 8.0, v_est1, v_est2)  # m/s
    v_phys = np.maximum(v_phys, min_speed / 3.6)
    v_phys_kmh = v_phys * 3.6

    # Hybrid C2: Physik dominiert, Zielgeschwindigkeit als Untergrenze (70 %)
    v_final = np.maximum(v_phys_kmh, v_target * 0.7)
    v_final = np.minimum(v_final, max_downhill_speed)

    return v_final


# -----------------------------------------------------
# ZEITPROFIL
# -----------------------------------------------------
def add_time_profile(
    df: pd.DataFrame,
    target_speed_down,
    target_speed_light_down,
    target_speed_flat,
    target_speed_light_up,
    target_speed_med_up,
    target_speed_steep_up,
    target_speed_very_steep_up,
    watt_down,
    watt_flat,
    watt_up,
    weight_total,
    c_rr,
    c_dA,
    wind_speed,
    wind_angle,
    max_downhill_speed,
    min_speed,
):
    speeds = compute_speed_vectorized(
        df["gradient"].values,
        target_speed_down,
        target_speed_light_down,
        target_speed_flat,
        target_speed_light_up,
        target_speed_med_up,
        target_speed_steep_up,
        target_speed_very_steep_up,
        watt_down,
        watt_flat,
        watt_up,
        weight_total,
        c_rr,
        c_dA,
        wind_speed,
        wind_angle,
        max_downhill_speed,
        min_speed,
    )

    df["speed_kmh"] = speeds

    dist_m = df["distance_m"].diff().fillna(0).values
    dist_km = dist_m / 1000.0

    hours = dist_km / np.maximum(speeds, 0.1)
    seconds = hours * 3600.0

    df["segment_seconds"] = seconds
    df["cum_seconds"] = df["segment_seconds"].cumsum()

    df_acp = compute_acp_times(df)

    return df, df_acp


# -----------------------------------------------------
# GLÄTTUNG
# -----------------------------------------------------
def smooth_speed(df: pd.DataFrame, window: int = 21) -> pd.DataFrame:
    if "speed_kmh" not in df.columns:
        return df
    df["speed_kmh_smooth"] = (
        df["speed_kmh"].rolling(window, center=True).mean().fillna(df["speed_kmh"])
    )
    return df


# -----------------------------------------------------
# PDF-EXPORT
# -----------------------------------------------------
def export_pdf(df_acp: pd.DataFrame, start_time: dt.datetime) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.add_font("DejaVu", "", "DejaVuSans.ttf", uni=True)
    pdf.set_font("DejaVu", size=10)

    pdf.cell(0, 10, "Brevet Simulation – ACP Kontrollzeiten", ln=True)

    for _, row in df_acp.iterrows():
        km = row["km"]
        open_s = row["open_s"]
        close_s = row["close_s"]

        open_t = start_time + dt.timedelta(seconds=open_s)
        close_t = start_time + dt.timedelta(seconds=close_s)

        line = f"KM {km:5.1f}: Open {open_t} – Close {close_t}"
        pdf.multi_cell(0, 8, line)

    return pdf.output(dest="S").encode("latin1", errors="ignore")


# -----------------------------------------------------
# STREAMLIT UI
# -----------------------------------------------------
st.set_page_config(page_title="Brevet Simulator", layout="wide")
st.title("Brevet Simulator")

st.sidebar.header("Fahrer & Rad")
weight_rider = st.sidebar.number_input("Fahrergewicht (kg)", 40.0, 120.0, 75.0)
weight_bike = st.sidebar.number_input("Radgewicht (kg)", 5.0, 20.0, 10.0)
weight_total = weight_rider + weight_bike

st.sidebar.header("Physik")
c_dA = st.sidebar.number_input("CdA (m²)", 0.15, 0.5, 0.28, step=0.01)
c_rr = st.sidebar.number_input("Crr", 0.002, 0.01, 0.004, step=0.0005)
wind_speed = st.sidebar.number_input("Wind (km/h)", 0.0, 60.0, 0.0)
wind_angle = st.sidebar.number_input("Windwinkel (°)", -180.0, 180.0, 0.0)
max_downhill_speed = st.sidebar.number_input("Max. Abfahrt (km/h)", 20.0, 100.0, 70.0)
min_speed = st.sidebar.number_input("Min. Geschwindigkeit (km/h)", 3.0, 15.0, 6.0)

st.sidebar.header("Zielgeschwindigkeiten")
target_speed_down = st.sidebar.number_input("Bergab (< -3%)", 30.0, 80.0, 50.0)
target_speed_light_down = st.sidebar.number_input("Leicht bergab (-3…-1%)", 25.0, 60.0, 40.0)
target_speed_flat = st.sidebar.number_input("Flach (-1…+1%)", 18.0, 40.0, 28.0)
target_speed_light_up = st.sidebar.number_input("Leicht bergauf (1…3%)", 15.0, 35.0, 24.0)
target_speed_med_up = st.sidebar.number_input("Mittel bergauf (3…6%)", 10.0, 30.0, 20.0)
target_speed_steep_up = st.sidebar.number_input("Steil bergauf (6…10%)", 6.0, 25.0, 15.0)
target_speed_very_steep_up = st.sidebar.number_input("Sehr steil (>10%)", 4.0, 20.0, 10.0)

st.sidebar.header("Leistung")
watt_flat = st.sidebar.number_input("Watt flach", 80, 400, 200)
watt_up = st.sidebar.number_input("Watt bergauf", 80, 450, 230)
watt_down = st.sidebar.number_input("Watt bergab", 0, 400, 150)

st.sidebar.header("Startzeit")
start_date = st.sidebar.date_input("Startdatum", dt.date.today())
start_time = st.sidebar.time_input("Startzeit", dt.time(6, 0))
start_dt = dt.datetime.combine(start_date, start_time)

st.sidebar.header("🛑 Kontrollpunkte")
num_controls = st.sidebar.number_input("Anzahl Kontrollpunkte", 0, 20, 0)
control_points = []
for i in range(num_controls):
    st.sidebar.subheader(f"Kontrollpunkt {i+1}")
    name = st.sidebar.text_input(f"Name KP {i+1}", key=f"cp_name_{i}")
    km = st.sidebar.number_input(
        f"Distanz (km) KP {i+1}", 0.0, 2000.0, 0.0, key=f"cp_km_{i}"
    )
    pause = st.sidebar.number_input(
        f"Pause (min) KP {i+1}", 0, 180, 0, key=f"cp_pause_{i}"
    )
    control_points.append({"name": name, "km": km, "pause": pause})

st.sidebar.header("⏸ Pausenpunkte")
num_pauses = st.sidebar.number_input("Anzahl Pausenpunkte", 0, 20, 0)
pause_points = []
for i in range(num_pauses):
    st.sidebar.subheader(f"Pausenpunkt {i+1}")
    name = st.sidebar.text_input(f"Name Pause {i+1}", key=f"pp_name_{i}")
    km = st.sidebar.number_input(
        f"Distanz (km) Pause {i+1}", 0.0, 2000.0, 0.0, key=f"pp_km_{i}"
    )
    pause = st.sidebar.number_input(
        f"Pause (min) Pause {i+1}", 0, 180, 0, key=f"pp_pause_{i}"
    )
    pause_points.append({"name": name, "km": km, "pause": pause})

uploaded_file = st.file_uploader("GPX-Datei hochladen", type=["gpx"])

if uploaded_file is not None:
    df = parse_gpx(uploaded_file)

    df, df_acp = add_time_profile(
        df,
        target_speed_down,
        target_speed_light_down,
        target_speed_flat,
        target_speed_light_up,
        target_speed_med_up,
        target_speed_steep_up,
        target_speed_very_steep_up,
        watt_down,
        watt_flat,
        watt_up,
        weight_total,
        c_rr,
        c_dA,
        wind_speed,
        wind_angle,
        max_downhill_speed,
        min_speed,
    )

    df = smooth_speed(df, window=21)

    st.subheader("Streckenprofil")
    st.line_chart(df[["distance_m", "elev"]].set_index("distance_m"))

    st.subheader("Geschwindigkeit")
    if "speed_kmh_smooth" in df.columns:
        st.line_chart(df[["distance_m", "speed_kmh_smooth"]].set_index("distance_m"))
    else:
        st.line_chart(df[["distance_m", "speed_kmh"]].set_index("distance_m"))

    st.subheader("ACP-Zeiten (Auszug)")
    df_acp_view = df_acp.copy()
    df_acp_view["open"] = df_acp_view["open_s"].apply(
        lambda s: start_dt + dt.timedelta(seconds=s)
    )
    df_acp_view["close"] = df_acp_view["close_s"].apply(
        lambda s: start_dt + dt.timedelta(seconds=s)
    )
    st.dataframe(df_acp_view[["km", "open", "close"]].head(50))

    pdf_bytes = export_pdf(df_acp_view, start_dt)
    st.download_button(
        "📄 ACP-Zeiten als PDF herunterladen",
        data=pdf_bytes,
        file_name="brevet_acp_zeiten.pdf",
        mime="application/pdf",
    )

else:
    st.info("Bitte eine GPX-Datei hochladen.")



