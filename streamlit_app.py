import math
import io
from datetime import datetime, timedelta

import gpxpy
import gpxpy.gpx
import numpy as np
import pandas as pd
import streamlit as st
import folium
from streamlit_folium import st_folium
from fpdf import FPDF


# ---------------------------------------------------------
# APP CONFIG
# ---------------------------------------------------------
st.set_page_config(page_title="Brevet Simulator B.6", layout="wide")
st.title("🚴 Brevet Simulator B.6 – Physik, Wind, FTP, Pausen & Kontrollen")


# ---------------------------------------------------------
# SIDEBAR – BREVET & SIMULATION
# ---------------------------------------------------------
st.sidebar.markdown("## ⚡ Schnellzugriff")
st.sidebar.markdown("- **FTP** beeinflusst Segmentleistung")
st.sidebar.markdown("- **Wind** wirkt physikalisch")
st.sidebar.markdown("- **Pausen** beim Erreichen addiert")
st.sidebar.markdown("---")

if "start_date" not in st.session_state:
    st.session_state["start_date"] = datetime.now().date()

if "start_time" not in st.session_state:
    st.session_state["start_time"] = datetime.now().time()

st.sidebar.subheader("Brevet Daten")
start_date = st.sidebar.date_input("Startdatum", st.session_state["start_date"])
start_time = st.sidebar.time_input("Startzeit", st.session_state["start_time"])
st.session_state["start_date"] = start_date
st.session_state["start_time"] = start_time
start_datetime = datetime.combine(
    st.session_state["start_date"],
    st.session_state["start_time"]
)

st.sidebar.header("⚙️ Simulationseinstellungen")

ftp = st.sidebar.number_input("FTP [W]", min_value=100, max_value=400, value=220, step=10)

st.sidebar.subheader("Leistungsprofile (Segmentleistung)")
power_flat = st.sidebar.number_input("Flach (Watt)",  min_value=80, max_value=400, value=180)
power_climb = st.sidebar.number_input("Berg (Watt)",  min_value=80, max_value=400, value=220)
power_down = st.sidebar.number_input("Abfahrt (Watt)", min_value=50, max_value=400, value=140)

min_speed = st.sidebar.number_input("Mindestgeschwindigkeit [km/h]", min_value=5.0, max_value=25.0, value=8.0, step=0.5)
max_downhill_speed = st.sidebar.number_input("Max. Abfahrtsgeschwindigkeit [km/h]", min_value=30.0, max_value=90.0, value=60.0, step=1.0)

st.sidebar.header("🎯 Zielgeschwindigkeiten pro Steigung (Basis)")
target_speed_down = st.sidebar.number_input("Abfahrt (< -3%) [km/h]", 20.0, 80.0, 40.0, 1.0)
target_speed_light_down = st.sidebar.number_input("Leicht bergab (-3 bis -1%) [km/h]", 20.0, 60.0, 32.0, 1.0)
target_speed_flat = st.sidebar.number_input("Flach (-1 bis +1%) [km/h]", 15.0, 40.0, 28.0, 1.0)
target_speed_light_up = st.sidebar.number_input("Leicht bergauf (1 bis 3%) [km/h]", 10.0, 35.0, 24.0, 1.0)
target_speed_med_up = st.sidebar.number_input("Mittel bergauf (3 bis 6%) [km/h]", 8.0, 30.0, 20.0, 1.0)
target_speed_steep_up = st.sidebar.number_input("Steil bergauf (6 bis 10%) [km/h]", 6.0, 25.0, 16.0, 1.0)
target_speed_very_steep_up = st.sidebar.number_input("Sehr steil (> 10%) [km/h]", 5.0, 20.0, 12.0, 1.0)

st.sidebar.subheader("Rad Daten")
weight_rider = st.sidebar.number_input("Fahrergewicht (kg)", 50, 120, 75)
weight_bike = st.sidebar.number_input("Radgewicht (kg)", 6, 20, 10)
weight_total = weight_rider + weight_bike
st.sidebar.write(f"**Systemgewicht:** {weight_total:.1f} kg")

st.sidebar.subheader("Physikalisches Modell")
c_dA = st.sidebar.number_input("CdA (m²)", 0.15, 0.40, 0.28, 0.01)
c_rr = st.sidebar.number_input("Crr", 0.002, 0.01, 0.004, 0.001)
air_density = st.sidebar.number_input("Luftdichte ρ (kg/m³)", 1.0, 1.4, 1.225, 0.01)

st.sidebar.subheader("Wetter Modell")
wind_speed = st.sidebar.number_input("Windgeschwindigkeit (km/h)", 0, 80, 10)
wind_angle = st.sidebar.slider("Windrichtung: 0° = Gegenwind, 180° = Rückenwind", 0, 360, 180)

debug_flag = st.sidebar.checkbox("Debug-Panel anzeigen", False)
# ---------------------------------------------------------
# GPX UPLOAD & PARSE
# ---------------------------------------------------------
st.header("📁 GPX-Datei hochladen")
uploaded_file = st.file_uploader("GPX-Datei wählen", type=["gpx"])

def parse_gpx(file) -> pd.DataFrame:
    gpx = gpxpy.parse(file)
    points = []
    for track in gpx.tracks:
        for segment in track.segments:
            for p in segment.points:
                points.append((p.latitude, p.longitude, p.elevation, p.time))

    df = pd.DataFrame(points, columns=["lat", "lon", "elev", "time"])

    df["distance_m"] = 0.0
    df["gradient"] = 0.0

    for i in range(1, len(df)):
        lat1, lon1, ele1 = df.loc[i - 1, ["lat", "lon", "elev"]]
        lat2, lon2, ele2 = df.loc[i, ["lat", "lon", "elev"]]

        R = 6371000
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)

        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        d_horiz = R * c

        d_vert = ele2 - ele1
        dist = math.sqrt(d_horiz ** 2 + d_vert ** 2)

        df.at[i, "distance_m"] = df.at[i - 1, "distance_m"] + dist

        if d_horiz > 0:
            df.at[i, "gradient"] = (d_vert / d_horiz) * 100.0
        else:
            df.at[i, "gradient"] = 0.0

    return df


# ---------------------------------------------------------
# PHYSIK-HILFSFUNKTIONEN
# ---------------------------------------------------------
g_const = 9.81

def segment_power(gradient: float) -> float:
    if gradient < -1.0:
        base = power_down
    elif gradient > 2.0:
        base = power_climb
    else:
        base = power_flat
    return base * (ftp / 220.0) ** 0.3

def base_speed_from_gradient(gradient: float) -> float:
    g = gradient
    if g < -3:
        return target_speed_down
    elif -3 <= g < -1:
        return target_speed_light_down
    elif -1 <= g <= 1:
        return target_speed_flat
    elif 1 < g <= 3:
        return target_speed_light_up
    elif 3 < g <= 6:
        return target_speed_med_up
    elif 6 < g <= 10:
        return target_speed_steep_up
    else:
        return target_speed_very_steep_up

def wind_component_ms(wind_speed_kmh: float, wind_angle_deg: float) -> float:
    w = wind_speed_kmh / 3.6
    angle = math.radians(wind_angle_deg)
    return w * math.cos(angle)
# ---------------------------------------------------------
# REALISTISCHES SPEED-MODELL MIT PHYSIK, WIND & FTP
# ---------------------------------------------------------
def compute_speed(gradient: float) -> float:
    base = base_speed_from_gradient(gradient)
    P = segment_power(gradient)
    w_eff = wind_component_ms(wind_speed, wind_angle)

    # Startwert
    v = max(base / 3.6, min_speed / 3.6)

    # Iterative Lösung: P = F_total * v
    for _ in range(12):
        v_rel = v + w_eff

        F_aero = 0.5 * air_density * c_dA * v_rel * abs(v_rel)
        F_roll = weight_total * g_const * c_rr
        F_grav = weight_total * g_const * (gradient / 100.0)

        F_total = F_aero + F_roll + F_grav

        if F_total <= 0:
            break

        v = P / F_total

    v_kmh = v * 3.6

    # Grenzen
    v_kmh = max(v_kmh, min_speed)
    if gradient < 0:
        v_kmh = min(v_kmh, max_downhill_speed)

    return v_kmh


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

if st.sidebar.button("Kontrollpunkt hinzufügen"):
    if control_dist == 0:
        st.sidebar.warning("❗ Der Startpunkt (km 0) kann kein Kontrollpunkt sein.")
    else:
        st.session_state["controls"].append({"km": control_dist, "pause_min": control_pause})

# Anzeigen
st.sidebar.write("### Pausenpunkte")
st.sidebar.write(st.session_state["pauses"])

st.sidebar.write("### Kontrollpunkte")
st.sidebar.write(st.session_state["controls"])
# ---------------------------------------------------------
# ZEITPROFIL MIT PAUSEN, KONTROLLEN & km-0-FIX
# ---------------------------------------------------------
def add_time_profile(df: pd.DataFrame):
    times = [0.0]
    speeds = [min_speed]

    pauses = st.session_state["pauses"]
    controls = st.session_state["controls"]

    # km-0-Fix: Alle ungültigen Punkte entfernen
    pauses = [p for p in pauses if p["km"] > 0]
    controls = [c for c in controls if c["km"] > 0]

    for i in range(1, len(df)):
        dist = df.distance_m.iloc[i] - df.distance_m.iloc[i - 1]

        # Schutz: GPX-Duplikate oder Null-Distanz
        if dist <= 0:
            times.append(times[-1])
            speeds.append(speeds[-1])
            continue

        g = df.gradient.iloc[i]
        v_kmh = compute_speed(g)
        # Geschwindigkeit in m/s
v_ms = v_kmh / 3.6

# Schutz: Geschwindigkeit darf niemals 0 oder negativ sein
if v_ms <= 0 or math.isnan(v_ms) or math.isinf(v_ms):
    v_ms = min_speed / 3.6

# Zeitdifferenz
dt = dist / v_ms

# Schutz: dt darf niemals negativ oder unendlich sein
if dt < 0 or math.isnan(dt) or math.isinf(dt):
    dt = 0

new_time = times[-1] + dt

# Schutz: Zeit darf niemals rückwärts laufen
if new_time < times[-1]:
    new_time = times[-1]


        km_now = df.distance_m.iloc[i] / 1000.0

        # --- Pausenpunkte ---
        for p in pauses:
            if abs(km_now - p["km"]) < 0.05:
                new_time += p["pause_min"] * 60

        # --- Kontrollpunkte ---
        for c in controls:
            if abs(km_now - c["km"]) < 0.05:
                new_time += c["pause_min"] * 60

        # Schutz: Negative Zeiten verhindern
        new_time = max(new_time, times[-1])

        times.append(new_time)
        speeds.append(v_kmh)

    df["speed_kmh"] = speeds
    df["time_s"] = times
    df["sim_time"] = [start_datetime + timedelta(seconds=float(t)) for t in times]

    # ACP Zeiten
    acp_list = []
    for c in controls:
        open_t, close_t = acp_open_close(c["km"])
        acp_list.append({
            "km": c["km"],
            "open": start_datetime + open_t,
            "close": start_datetime + close_t
        })

    df_acp = pd.DataFrame(acp_list)

    return df, df_acp
# ---------------------------------------------------------
# ZUSAMMENFASSUNGSTABELLE (mit km-0-Fix)
# ---------------------------------------------------------
def build_summary(df: pd.DataFrame, df_acp: pd.DataFrame):
    summary_rows = []

    # Startpunkt
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

    # Pausen + Kontrollpunkte sortieren
    all_points = []

    for p in st.session_state["pauses"]:
        if p["km"] > 0:   # km-0-Fix
            all_points.append({"km": p["km"], "pause": p["pause_min"], "type": "Pause"})

    for c in st.session_state["controls"]:
        if c["km"] > 0:   # km-0-Fix
            all_points.append({"km": c["km"], "pause": c["pause_min"], "type": "Kontrolle"})

    all_points = sorted(all_points, key=lambda x: x["km"])

    last_km = 0.0
    last_time = start_datetime

    for p in all_points:
        km = p["km"]

        # Nächsten GPX-Punkt finden
        row = df.iloc[(df.distance_m/1000 - km).abs().argmin()]

        arrival = row.sim_time
        pause_min = p["pause"]

        # Abschnittsdaten
        section_km = km - last_km
        section_time = arrival - last_time
        section_h = section_time.total_seconds() / 3600.0
        section_speed = section_km / section_h if section_h > 0 else 0

        # ACP Zeiten
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

        # Pause einrechnen
        last_km = km
        last_time = arrival + timedelta(minutes=pause_min)

    return pd.DataFrame(summary_rows)
# ---------------------------------------------------------
# INTERAKTIVE KARTENANIMATION (Playhead)
# ---------------------------------------------------------
def animate_map(df: pd.DataFrame):
    st.subheader("🎬 Kartenanimation")

    # Maximale Distanz in km
    max_km = df["distance_m"].iloc[-1] / 1000.0

    # Slider für Animation
    pos_km = st.slider(
        "Position auf der Strecke [km]",
        0.0,
        float(max_km),
        0.0,
        0.5
    )

    # Nächsten GPX-Punkt finden
    row = df.iloc[(df["distance_m"]/1000.0 - pos_km).abs().argmin()]

    # Karte erzeugen
    m = folium.Map(location=[row.lat, row.lon], zoom_start=12)

    # GPX-Linie
    folium.PolyLine(
        df[["lat", "lon"]].values,
        color="red",
        weight=4,
        opacity=0.6
    ).add_to(m)

    # Aktuelle Position
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
# MAIN LOGIC – B.7 (mit km-0-Fix & Animation)
# ---------------------------------------------------------
if uploaded_file is None:
    st.info("Bitte eine GPX-Datei hochladen, um die Simulation zu starten.")
else:
    # GPX laden + Simulation
    df = parse_gpx(uploaded_file)
    df, df_acp = add_time_profile(df)
    df = smooth_speed(df)

    # Zusammenfassungstabelle
    summary_df = build_summary(df, df_acp)

    # Charts
    st.subheader("📈 Streckenprofil & Simulation")
    col1, col2 = st.columns(2)

    with col1:
        st.line_chart(df.set_index("distance_m")[["elev"]], height=250)

    with col2:
        st.line_chart(df.set_index("distance_m")[["speed_kmh_smooth"]], height=250)

    # Karte
    st.subheader("🗺 Karte")
    st_folium(show_map(df), width=900, height=500)

    # Animation
    animate_map(df)

    # Zeitprofil
    st.subheader("⏱ Zeitprofil")
    st.write(df[["distance_m", "elev", "gradient", "speed_kmh", "speed_kmh_smooth", "sim_time"]])

    # ACP Zeiten
    st.subheader("📘 ACP Kontrollzeiten")
    st.write(df_acp)

    # Zusammenfassung
    st.subheader("📒 Zusammenfassung")
    st.write(summary_df)

    # Debug Panel
    if debug_flag:
        st.subheader("🔍 Debug-Panel – Kräfte")
        dbg = build_debug(df)
        st.dataframe(dbg.head(200))

    # Gesamtzeit
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
        file_name="brevet_simulation_b7.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # -----------------------------------------------------
    # PDF EXPORT
    # -----------------------------------------------------
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=10)
    pdf.cell(0, 10, "Brevet Simulation – ACP Kontrollzeiten", ln=True)

    for idx, row in df_acp.iterrows():
        pdf.cell(
            0,
            8,
            f"KM {row['km']}: Open {row['open']} – Close {row['close']}",
            ln=True
        )

    pdf_buffer = pdf.output(dest="S").encode("latin1")

    st.download_button(
        label="📄 PDF exportieren",
        data=pdf_buffer,
        file_name="brevet_simulation_b7.pdf",
        mime="application/pdf"
    )
