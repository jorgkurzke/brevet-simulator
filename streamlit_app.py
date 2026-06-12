import io
import math
from datetime import datetime, timedelta

import gpxpy
import gpxpy.gpx
import pandas as pd
import numpy as np
import streamlit as st
import folium
from streamlit_folium import st_folium
from fpdf import FPDF

# ---------------------------------------------------------
# GRUNDKONFIGURATION
# ---------------------------------------------------------
st.set_page_config(page_title="Brevet Simulator", layout="wide")

st.title("Brevet Simulator – B.7.1")

# ---------------------------------------------------------
# SIDEBAR – BASISPARAMETER
# ---------------------------------------------------------
st.sidebar.header("⚙ Basisparameter")

start_datetime = st.sidebar.datetime_input(
    "Startzeit",
    value=datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
)

weight_rider = st.sidebar.number_input("Fahrergewicht [kg]", 50.0, 120.0, 75.0, 1.0)
weight_bike = st.sidebar.number_input("Radgewicht [kg]", 5.0, 20.0, 10.0, 0.5)
weight_total = weight_rider + weight_bike

ftp = st.sidebar.number_input("FTP [W]", 100, 500, 250, 5)
c_dA = st.sidebar.number_input("c_dA [m²]", 0.15, 0.5, 0.28, 0.01)
c_rr = st.sidebar.number_input("Rollwiderstand Crr", 0.001, 0.01, 0.004, 0.0005)

wind_speed = st.sidebar.number_input("Windgeschwindigkeit [km/h]", -50.0, 50.0, 0.0, 1.0)
wind_angle = st.sidebar.number_input("Windwinkel [°] (0 = Gegenwind, 180 = Rückenwind)", 0.0, 360.0, 0.0, 5.0)

min_speed = st.sidebar.number_input("Minimale Geschwindigkeit [km/h]", 3.0, 25.0, 8.0, 0.5)
max_downhill_speed = st.sidebar.number_input("Max. Abfahrtsgeschwindigkeit [km/h]", 20.0, 120.0, 70.0, 1.0)

debug_flag = st.sidebar.checkbox("Debug-Panel anzeigen", value=False)

air_density = 1.226
g_const = 9.81
# ---------------------------------------------------------
# LEISTUNGSPROFILE & ZIELGESCHWINDIGKEITEN
# ---------------------------------------------------------
st.sidebar.header("🚴 Leistungsprofile")

# Wattvorgaben
watt_flat = st.sidebar.number_input("Watt flach [W]", 50, 500, 200, 5)
watt_up = st.sidebar.number_input("Watt bergauf [W]", 50, 500, 240, 5)
watt_down = st.sidebar.number_input("Watt bergab [W]", 50, 500, 160, 5)

st.sidebar.header("🎯 Zielgeschwindigkeiten nach Steigung")

target_speed_down = st.sidebar.number_input("Abfahrt (< -3%) [km/h]", 10.0, 120.0, 55.0, 1.0)
target_speed_light_down = st.sidebar.number_input("Leicht bergab (-3% bis -1%) [km/h]", 10.0, 80.0, 40.0, 1.0)
target_speed_flat = st.sidebar.number_input("Flach (-1% bis +1%) [km/h]", 10.0, 60.0, 30.0, 1.0)
target_speed_light_up = st.sidebar.number_input("Leicht bergauf (1% bis 3%) [km/h]", 5.0, 40.0, 22.0, 1.0)
target_speed_med_up = st.sidebar.number_input("Mittel bergauf (3% bis 6%) [km/h]", 5.0, 30.0, 16.0, 1.0)
target_speed_steep_up = st.sidebar.number_input("Steil bergauf (6% bis 10%) [km/h]", 3.0, 20.0, 12.0, 1.0)
target_speed_very_steep_up = st.sidebar.number_input("Sehr steil (> 10%) [km/h]", 2.0, 15.0, 8.0, 1.0)

# ---------------------------------------------------------
# GPX-UPLOAD
# ---------------------------------------------------------
uploaded_file = st.sidebar.file_uploader("GPX-Datei hochladen", type=["gpx"])


# ---------------------------------------------------------
# GPX PARSEN
# ---------------------------------------------------------
def safe_series(col) -> pd.Series:
    """
    Nimmt irgendetwas (Liste, Object-Spalte, Mixed-Typen),
    gibt garantiert eine float-Series ohne NaNs zurück.
    """
    s = pd.Series(col)

    # alles in float konvertieren, Unfug -> NaN
    s = pd.to_numeric(s, errors="coerce")

    # wenn alles NaN -> einfach 0
    if s.isna().all():
        return s.fillna(0.0)

    # normaler Weg: vorwärts + rückwärts füllen
    try:
        s = s.ffill().bfill()
    except TypeError:
        # falls die Pandas-Version rumzickt
        s = s.fillna(0.0)

    return s.astype(float)

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

            # Segment hat Punkte?
            if len(segment.points) == 0:
                continue

            for point in segment.points:

                # Koordinaten
                lats.append(point.latitude)
                lons.append(point.longitude)
                elevs.append(point.elevation)

                # Distanz
                if last_point is not None:
                    dx = gpxpy.geo.haversine_distance(
                        last_point.latitude,
                        last_point.longitude,
                        point.latitude,
                        point.longitude
                    )
                    if dx is None or math.isnan(dx):
                        dx = 0.0
                    total_dist += dx

                dists.append(total_dist)
                last_point = point

            # WICHTIG:
            # last_point NICHT zurücksetzen!
            # Sonst entstehen Distanzsprünge zwischen Segmenten

    # DataFrame erzeugen
    # DataFrame erzeugen
    df = pd.DataFrame({
        "lat": lats,
        "lon": lons,
        "elev": elevs,
        "distance_m": dists
    })

    # Spalten robust normalisieren
    df["lat"] = safe_series(df["lat"])
    df["lon"] = safe_series(df["lon"])
    df["elev"] = safe_series(df["elev"])
    df["distance_m"] = safe_series(df["distance_m"])

    # Gradient berechnen
    df["gradient"] = 0.0
    for i in range(1, len(df)):
        dh = df["elev"].iloc[i] - df["elev"].iloc[i - 1]
        dx = df["distance_m"].iloc[i] - df["distance_m"].iloc[i - 1]
        if dx > 0:
            df.loc[df.index[i], "gradient"] = (dh / dx) * 100.0

    return df



# ---------------------------------------------------------
# PHYSIK-MODELL BASIS
# ---------------------------------------------------------
def base_speed_from_gradient(gradient: float) -> float:
    if gradient < -8:
        return max_downhill_speed
    elif gradient < 0:
        return max_downhill_speed - (abs(gradient) / 8.0) * (max_downhill_speed - 25.0)
    elif gradient < 8:
        return 25.0 - (gradient / 8.0) * 10.0
    else:
        return 15.0


def wind_component_ms(wind_speed_kmh: float, wind_angle_deg: float) -> float:
    # 0° = Gegenwind, 180° = Rückenwind
    ws_ms = wind_speed_kmh / 3.6
    angle_rad = math.radians(wind_angle_deg)
    return -ws_ms * math.cos(angle_rad)


def segment_power(gradient: float) -> float:
    if gradient < -1:
        return watt_down
    elif gradient <= 1:
        return watt_flat
    else:
        return watt_up

# ---------------------------------------------------------
# REALISTISCHES SPEED-MODELL MIT PHYSIK, WIND & FTP
# ---------------------------------------------------------
def compute_speed_vectorized(gradients):
    gradients = np.array(gradients)

    # 1) Zielgeschwindigkeit nach Steigung
    v_target = np.where(
        gradients < -3, target_speed_down,
        np.where(
            gradients < -1, target_speed_light_down,
            np.where(
                gradients < 1, target_speed_flat,
                np.where(
                    gradients < 3, target_speed_light_up,
                    np.where(
                        gradients < 6, target_speed_med_up,
                        np.where(
                            gradients < 10, target_speed_steep_up,
                            target_speed_very_steep_up
                        )
                    )
                )
            )
        )
    )

    # 2) Leistung nach Steigung
    P = np.where(
        gradients < -1, watt_down,
        np.where(
            gradients <= 1, watt_flat,
            watt_up
        )
    )

    # 3) Physikalische Geschwindigkeit (analytisch)
    #    v = P / (F_roll + F_grav + F_aero)
    #    Wir lösen das ohne Iteration:
    #    v_rel = v + wind
    #    Näherung: aero dominiert → quadratische Lösung

    w = wind_component_ms(wind_speed, wind_angle)

    # Kräfte
    F_roll = weight_total * g_const * c_rr
    F_grav = weight_total * g_const * (gradients / 100.0)

    # Quadratische Lösung für Luftwiderstand:
    # P = (F_roll + F_grav) * v + 0.5 * rho * CdA * (v + w)^3
    # Näherung: wir ignorieren w im Kubikterm für Geschwindigkeitsschätzung
    A = 0.5 * air_density * c_dA
    B = F_roll + F_grav

    # Näherungslösung: v ≈ (P / B) für kleine v, sonst (P/A)^(1/3)
    v_phys = np.where(
        P / np.maximum(B, 1e-6) < 8,
        P / np.maximum(B, 1e-6),
        (P / A) ** (1/3)
    )

    v_phys *= 3.6  # m/s → km/h

    # 4) Hybrid-Regel C2
    v_final = np.maximum(v_phys, v_target * 0.7)
    v_final = np.minimum(v_final, max_downhill_speed)

    return v_final




# ---------------------------------------------------------
# DEBUG-PANEL (F_aero, F_roll, F_grav)
# ---------------------------------------------------------
def build_debug(df: pd.DataFrame) -> pd.DataFrame:
    w_eff = wind_component_ms(wind_speed, wind_angle)
    v_ms = df["speed_kmh"] / 3.6
    v_rel = v_ms + w_eff

    F_aero = 0.5 * air_density * c_dA * v_rel * abs(v_rel)
    F_roll = weight_total * g_const * c_rr
    F_grav = weight_total * g_const * (df["gradient"] / 100.0)
    F_total = F_aero + F_roll + F_grav

    dbg = pd.DataFrame({
        "distance_km": df["distance_m"] / 1000.0,
        "speed_kmh": df["speed_kmh"],
        "F_aero": F_aero,
        "F_roll": F_roll,
        "F_grav": F_grav,
        "F_total": F_total,
    })

    return dbg


# ---------------------------------------------------------
# PAUSEN & KONTROLLPUNKTE (mit km-0-Fix)
# ---------------------------------------------------------
st.sidebar.header("⏱ Pausen & Kontrollpunkte")

if "pauses" not in st.session_state:
    st.session_state["pauses"] = []

if "controls" not in st.session_state:
    st.session_state["controls"] = []

# --- Pause hinzufügen ---
st.sidebar.subheader("Pause hinzufügen")
pause_dist = st.sidebar.number_input("Pausenpunkt bei km", min_value=0.0, value=0.0, step=1.0)
pause_minutes = st.sidebar.number_input("Pausendauer (min)", min_value=0, max_value=180, value=0)

if st.sidebar.button("Pause hinzufügen"):
    if pause_dist == 0:
        st.sidebar.warning("❗ Der Startpunkt (km 0) kann keine Pause haben.")
    else:
        st.session_state["pauses"].append({"km": pause_dist, "pause_min": pause_minutes})

# --- Kontrollpunkt hinzufügen ---
st.sidebar.subheader("Kontrollpunkt hinzufügen")
control_dist = st.sidebar.number_input("Kontrollpunkt bei km", min_value=0.0, value=0.0, step=1.0)
control_pause = st.sidebar.number_input("Pause am Kontrollpunkt (min)", min_value=0, max_value=180, value=0)

# Name Kontroll- und Pausenpunkt
st.sidebar.header("🛑 Kontrollpunkte")

num_controls = st.sidebar.number_input("Anzahl Kontrollpunkte", 0, 20, 0)

control_points = []
for i in range(num_controls):
    st.sidebar.subheader(f"Kontrollpunkt {i+1}")
    name = st.sidebar.text_input(f"Name KP {i+1}", key=f"cp_name_{i}")
    km = st.sidebar.number_input(f"Distanz (km) KP {i+1}", 0.0, 2000.0, 0.0, key=f"cp_km_{i}")
    pause = st.sidebar.number_input(f"Pause (min) KP {i+1}", 0, 180, 0, key=f"cp_pause_{i}")
    control_points.append({"name": name, "km": km, "pause": pause})


st.sidebar.header("⏸ Pausenpunkte")

num_pauses = st.sidebar.number_input("Anzahl Pausenpunkte", 0, 20, 0)

pause_points = []
for i in range(num_pauses):
    st.sidebar.subheader(f"Pausenpunkt {i+1}")
    name = st.sidebar.text_input(f"Name Pause {i+1}", key=f"pp_name_{i}")
    km = st.sidebar.number_input(f"Distanz (km) Pause {i+1}", 0.0, 2000.0, 0.0, key=f"pp_km_{i}")
    pause = st.sidebar.number_input(f"Pause (min) Pause {i+1}", 0, 180, 0, key=f"pp_pause_{i}")
    pause_points.append({"name": name, "km": km, "pause": pause})


if st.sidebar.button("Kontrollpunkt hinzufügen"):
    if control_dist == 0:
        st.sidebar.warning("❗ Der Startpunkt (km 0) kann kein Kontrollpunkt sein.")
    else:
        st.session_state["controls"].append({"km": control_dist, "pause_min": control_pause})

st.sidebar.write("### Pausenpunkte")
st.sidebar.write(st.session_state["pauses"])

st.sidebar.write("### Kontrollpunkte")
st.sidebar.write(st.session_state["controls"])
# ---------------------------------------------------------
# ACP ÖFFNUNGS- UND SCHLIEßZEITEN
# ---------------------------------------------------------
def acp_open_close(dist_km):
    vmax = 34.0
    vmin = 15.0

    open_h = dist_km / vmax
    close_h = dist_km / vmin

    return timedelta(hours=open_h), timedelta(hours=close_h)


# ---------------------------------------------------------
# ZEITPROFIL MIT STABILEN ZEITEN & km-0-FIX
# ---------------------------------------------------------
def add_time_profile(df):
    # Geschwindigkeiten vektorisieren
    speeds = compute_speed_vectorized(df["gradient"].values)  # km/h

    # Zeit pro Segment
    dist_m = df["distance_m"].diff().fillna(0).values  # Meter
    dist_km = dist_m / 1000.0

    hours = dist_km / np.maximum(speeds, 0.1)
    seconds = hours * 3600

    df["segment_seconds"] = seconds
    df["cum_seconds"] = df["segment_seconds"].cumsum()

    # ACP‑Zeiten
    df_acp = compute_acp_times(df)

    return df, df_acp



# ---------------------------------------------------------
# ZUSAMMENFASSUNGSTABELLE (mit km-0-Fix)
# ---------------------------------------------------------
def build_summary(df: pd.DataFrame, df_acp: pd.DataFrame):
    summary_rows = []

    summary_rows.append({
        "Punkt": "Start",
        "km": 0.0,
        "Ankunft": start_datetime,
        "Pause (min)": 0,
        "ACP Open": "-",
        "ACP Close": "-",
        "Abschnitt km": 0.0,
        "Abschnitt Zeit": "0:00",
        "Abschnitt Ø km/h": "-",
        "Gesamtzeit": "0:00",
        "Gesamt Ø km/h": "-"
    })

    all_points = []

    for p in st.session_state["pauses"]:
        if p["km"] > 0:
            all_points.append({"km": p["km"], "pause": p["pause_min"], "type": "Pause"})

    for c in st.session_state["controls"]:
        if c["km"] > 0:
            all_points.append({"km": c["km"], "pause": c["pause_min"], "type": "Kontrolle"})

    all_points = sorted(all_points, key=lambda x: x["km"])

    last_km = 0.0
    last_time = start_datetime

    for p in all_points:
        km = p["km"]

        row = df.iloc[(df.distance_m/1000 - km).abs().argmin()]

        arrival = row.sim_time
        pause_min = p["pause"]

        section_km = km - last_km
        section_time = arrival - last_time
        section_h = section_time.total_seconds() / 3600.0
        section_speed = section_km / section_h if section_h > 0 else 0

        acp_row = df_acp[df_acp["km"] == km]
        if len(acp_row) > 0:
            acp_open = acp_row.iloc[0]["open"]
            acp_close = acp_row.iloc[0]["close"]
        else:
            acp_open = "-"
            acp_close = "-"

        summary_rows.append({
            "Punkt": p["type"],
            "km": km,
            "Ankunft": arrival,
            "Pause (min)": pause_min,
            "ACP Open": acp_open,
            "ACP Close": acp_close,
            "Abschnitt km": round(section_km, 1),
            "Abschnitt Zeit": str(section_time),
            "Abschnitt Ø km/h": round(section_speed, 1),
            "Gesamtzeit": str(arrival - start_datetime),
            "Gesamt Ø km/h": round(km / ((arrival - start_datetime).total_seconds()/3600), 1) if km > 0 else "-"
        })

        last_km = km
        last_time = arrival + timedelta(minutes=pause_min)

    return pd.DataFrame(summary_rows)
# ---------------------------------------------------------
# KARTE MIT GROßEN MARKERN
# ---------------------------------------------------------
def show_map(df: pd.DataFrame):
    m = folium.Map(location=[df.lat.mean(), df.lon.mean()], zoom_start=10)

    folium.PolyLine(
        df[["lat", "lon"]].values,
        color="red",
        weight=4,
        opacity=0.8
    ).add_to(m)

    for c in st.session_state["controls"]:
        row = df.iloc[(df.distance_m / 1000.0 - c["km"]).abs().argmin()]
        folium.CircleMarker(
            location=[row.lat, row.lon],
            radius=12,
            color="blue",
            fill=True,
            fill_color="blue",
            tooltip=f"Kontrollpunkt {c['km']} km – Pause {c['pause_min']} min"
        ).add_to(m)

    for p in st.session_state["pauses"]:
        row = df.iloc[(df.distance_m / 1000.0 - p["km"]).abs().argmin()]
        folium.CircleMarker(
            location=[row.lat, row.lon],
            radius=12,
            color="yellow",
            fill=True,
            fill_color="yellow",
            tooltip=f"Pause {p['pause_min']} min"
        ).add_to(m)

    return m


# ---------------------------------------------------------
# INTERAKTIVE KARTENANIMATION (Playhead)
# ---------------------------------------------------------
def animate_map(df: pd.DataFrame):
    st.subheader("🎬 Kartenanimation")

    max_km = df["distance_m"].iloc[-1] / 1000.0

    pos_km = st.slider(
        "Position auf der Strecke [km]",
        0.0,
        float(max_km),
        0.0,
        0.5
    )

    row = df.iloc[(df["distance_m"]/1000.0 - pos_km).abs().argmin()]

    m = folium.Map(location=[row.lat, row.lon], zoom_start=12)

    folium.PolyLine(
        df[["lat", "lon"]].values,
        color="red",
        weight=4,
        opacity=0.6
    ).add_to(m)

    folium.CircleMarker(
        location=[row.lat, row.lon],
        radius=10,
        color="green",
        fill=True,
        fill_color="green",
        tooltip=(
            f"km {pos_km:.1f} | "
            f"v={row['speed_kmh_smooth']:.1f} km/h | "
            f"{row['sim_time']}"
        )
    ).add_to(m)

    st_folium(m, width=900, height=500)


# ---------------------------------------------------------
# GESCHWINDIGKEITSKURVE GLÄTTEN
# ---------------------------------------------------------
def smooth_speed(df, window=9):
    df["speed_kmh_smooth"] = df["speed_kmh"].rolling(window, center=True).mean()
    df["speed_kmh_smooth"].fillna(df["speed_kmh"], inplace=True)
    return df
# ---------------------------------------------------------
# MAIN LOGIC – B.7.1
# ---------------------------------------------------------
if uploaded_file is None:
    st.info("Bitte eine GPX-Datei hochladen, um die Simulation zu starten.")
else:
    df = parse_gpx(uploaded_file)
    df, df_acp = add_time_profile(df)
    df = smooth_speed(df)

    summary_df = build_summary(df, df_acp)

    st.subheader("📈 Streckenprofil & Simulation")
    col1, col2 = st.columns(2)

    with col1:
        st.line_chart(df.set_index("distance_m")[["elev"]], height=250)

    with col2:
        st.line_chart(df.set_index("distance_m")[["speed_kmh_smooth"]], height=250)

    st.subheader("🗺 Karte")
    st_folium(show_map(df), width=900, height=500)

    animate_map(df)

    st.subheader("⏱ Zeitprofil")
    st.write(df[["distance_m", "elev", "gradient", "speed_kmh", "speed_kmh_smooth", "sim_time"]])

    st.subheader("📘 ACP Kontrollzeiten")
    st.write(df_acp)

    st.subheader("📒 Zusammenfassung")
    st.write(summary_df)

    if debug_flag:
        st.subheader("🔍 Debug-Panel – Kräfte")
        dbg = build_debug(df)
        st.dataframe(dbg.head(200))

    total_time = df["time_s"].iloc[-1] / 3600.0
    st.success(f"Gesamtzeit: {total_time:.2f} Stunden")

    # -----------------------------------------------------
    # EXCEL EXPORT
    # -----------------------------------------------------
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
        df.to_excel(writer, sheet_name="Simulation", index=False)
        df_acp.to_excel(writer, sheet_name="ACP", index=False)
        summary_df.to_excel(writer, sheet_name="Zusammenfassung", index=False)

    st.download_button(
        label="📥 Excel exportieren",
        data=excel_buffer.getvalue(),
        file_name="brevet_simulation_b7_1.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # -----------------------------------------------------
    # PDF EXPORT
    # -----------------------------------------------------
    # -----------------------------------------------------
    # PDF EXPORT (UTF-8 fähig)
    # -----------------------------------------------------
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    
    # WICHTIG: UTF-8 Schrift laden
    pdf.add_font("DejaVu", "", "DejaVuSans.ttf", uni=True)


    pdf.set_font("DejaVu", size=10)
    
    pdf.cell(0, 10, "Brevet Simulation – ACP Kontrollzeiten", ln=True)
    
    for idx, row in df_acp.iterrows():
        line = f"KM {row['km']}: Open {row['open']} – Close {row['close']}"
        pdf.multi_cell(0, 8, line)
    
    pdf_buffer = pdf.output(dest="S").encode("utf-8")
    
    st.download_button(
        label="📄 PDF exportieren",
        data=pdf_buffer,
        file_name="brevet_simulation.pdf",
        mime="application/pdf"
    )


