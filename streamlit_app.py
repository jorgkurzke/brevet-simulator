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
# PAUSEN & KONTROLLPUNKTE
# ---------------------------------------------------------
st.sidebar.header("⏱ Pausen & Kontrollpunkte")

if "pauses" not in st.session_state:
    st.session_state["pauses"] = []

if "controls" not in st.session_state:
    st.session_state["controls"] = []

st.sidebar.subheader("Pause hinzufügen")
pause_dist = st.sidebar.number_input("Pausenpunkt bei km", min_value=0.0, value=0.0, step=1.0)
pause_minutes = st.sidebar.number_input("Pausendauer (min)", min_value=0, max_value=180, value=0)
if st.sidebar.button("Pause hinzufügen"):
    st.session_state["pauses"].append({"km": pause_dist, "pause_min": pause_minutes})

st.sidebar.subheader("Kontrollpunkt hinzufügen")
control_dist = st.sidebar.number_input("Kontrollpunkt bei km", min_value=0.0, value=0.0, step=1.0)
control_pause = st.sidebar.number_input("Pause am Kontrollpunkt (min)", min_value=0, max_value=180, value=0)
if st.sidebar.button("Kontrollpunkt hinzufügen"):
    st.session_state["controls"].append({"km": control_dist, "pause_min": control_pause})

st.sidebar.write("### Pausenpunkte")
st.sidebar.write(st.session_state["pauses"])
st.sidebar.write("### Kontrollpunkte")
st.sidebar.write(st.session_state["controls"])


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
# ACP ÖFFNUNGS- UND SCHLIEßZEITEN
# ---------------------------------------------------------
def acp_open_close(dist_km):
    vmax = 34.0
    vmin = 15.0
    open_h = dist_km / vmax
    close_h = dist_km / vmin
    return timedelta(hours=open_h), timedelta(hours=close_h)


# ---------------------------------------------------------
# REALISTISCHES SPEED-MODELL MIT PHYSIK, WIND & FTP
# ---------------------------------------------------------
def compute_speed(gradient: float) -> float:
    base = base_speed_from_gradient(gradient)
    P = segment_power(gradient)
    w_eff = wind_component_ms(wind_speed, wind_angle)

    v = max(base / 3.6, min_speed / 3.6)

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
    v_kmh = max(v_kmh, min_speed)
    if gradient < 0:
        v_kmh = min(v_kmh, max_downhill_speed)
    return v_kmh


# ---------------------------------------------------------
# ZEITPROFIL MIT PAUSEN & ACP
# ---------------------------------------------------------
def add_time_profile(df: pd.DataFrame):
    times = [0.0]
    speeds = [min_speed]

    pauses = st.session_state["pauses"]
    controls = st.session_state["controls"]

    for i in range(1, len(df)):
        dist = df.distance_m.iloc[i] - df.distance_m.iloc[i - 1]

        if dist <= 0:
            times.append(times[-1])
            speeds.append(speeds[-1])
            continue

        g = df.gradient.iloc[i]
        v_kmh = compute_speed(g)
        v_ms = v_kmh / 3.6

        dt = dist / v_ms
        new_time = times[-1] + dt

        km_now = df.distance_m.iloc[i] / 1000.0

        for p in pauses:
            if abs(km_now - p["km"]) < 0.05:
                new_time += p["pause_min"] * 60

        for c in controls:
            if abs(km_now - c["km"]) < 0.05:
                new_time += c["pause_min"] * 60

        times.append(new_time)
        speeds.append(v_kmh)

    df["speed_kmh"] = speeds
    df["time_s"] = times
    df["sim_time"] = [start_datetime + timedelta(seconds=t) for t in times]

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
# GESCHWINDIGKEITSKURVE GLÄTTEN
# ---------------------------------------------------------
def smooth_speed(df, window=9):
    df["speed_kmh_smooth"] = df["speed_kmh"].rolling(window, center=True).mean()
    df["speed_kmh_smooth"].fillna(df["speed_kmh"], inplace=True)
    return df


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
# MAIN LOGIC
# ---------------------------------------------------------
if uploaded_file is None:
    st.info("Bitte eine GPX-Datei hochladen, um die Simulation zu starten.")
else:
    df = parse_gpx(uploaded_file)
    df, df_acp = add_time_profile(df)
    df = smooth_speed(df)

    st.subheader("Streckenprofil & Simulation")
    col1, col2 = st.columns(2)

    with col1:
        st.line_chart(df.set_index("distance_m")[["elev"]], height=250)

    with col2:
        st.line_chart(df.set_index("distance_m")[["speed_kmh_smooth"]], height=250)

    st.subheader("🗺 Karte")
    st_folium(show_map(df), width=900, height=500)

    st.subheader("Zeitprofil")
    st.write(df[["distance_m", "elev", "gradient", "speed_kmh", "speed_kmh_smooth", "sim_time"]])

    st.subheader("ACP Kontrollzeiten")
    st.write(df_acp)

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

    st.download_button(
        label="📥 Excel exportieren",
        data=excel_buffer.getvalue(),
        file_name="brevet_simulation.xlsx",
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
        file_name="brevet_simulation.pdf",
        mime="application/pdf"
    )

# ---------------------------------------------------------
# VISUALISIERUNG – KARTE
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
# ---------------------------------------------------------
# VISUALISIERUNG – HÖHENPROFIL
# ---------------------------------------------------------
def show_elevation_profile(df: pd.DataFrame):
    if "elevation" not in df or df["elevation"].isna().all():
        st.info("Keine Höhendaten in dieser GPX-Datei.")
        return

    df_plot = df.copy()
    df_plot["elevation_smooth"] = df_plot["elevation"].rolling(window=25, center=True, min_periods=1).mean()
    df_plot["gradient_smooth"] = df_plot["gradient"].rolling(window=25, center=True, min_periods=1).mean()

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
# VISUALISIERUNG – SPEED
# ---------------------------------------------------------
def show_speed(df):
    chart = (
        alt.Chart(df)
        .mark_line(color="green")
        .encode(x="km", y="speed_kmh")
        .properties(height=250)
    )
    st.altair_chart(chart, use_container_width=True)
# ---------------------------------------------------------
# ZUSAMMENFASSUNG – Version B.2 (10 Spalten)
# ---------------------------------------------------------
def build_summary(df):

    def fmt_km(value):
        return int(round(value))

    def fmt_hhmm(td):
        total = int(td.total_seconds())
        h = total // 3600
        m = (total % 3600) // 60
        return f"{h:02d}:{m:02d}"

    def fmt_speed(km, td):
        hours = td.total_seconds() / 3600
        if hours <= 0:
            return 0
        return round(km / hours)

    points = [{"km": 0.0, "name": "Start", "pause_min": 0}]
    points += st.session_state["control_points"]
    points += [{"km": p["km"], "name": "Pause", "pause_min": p["pause_min"]} for p in st.session_state["pauses"]]
    points.append({"km": df.km.iloc[-1], "name": "Ziel", "pause_min": 0})

    points = sorted(points, key=lambda x: x["km"])

    rows = []
    last_km = 0
    last_time = df.sim_time_with_pauses.iloc[0]
    start_time = df.sim_time_with_pauses.iloc[0]
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
            "KM gesamt": fmt_km(km_total),
            "KM Abschnitt": fmt_km(km_diff),
            "HM gesamt": int(round(elev_total)) if pd.notna(elev_total) else 0,
            "HM Abschnitt": int(round(elev_diff)) if pd.notna(elev_diff) else 0,
            "Ankunftszeit": time_total.strftime("%d.%m.%Y %H:%M"),
            "Zeit gesamt": fmt_hhmm(time_total - start_time),
            "Zeit Abschnitt": fmt_hhmm(time_diff),
            "Ø‑km/h gesamt": fmt_speed(km_total, time_total - start_time),
            "Ø‑km/h Abschnitt": fmt_speed(km_diff, time_diff),
            "Pause (min)": p["pause_min"],
        })

        last_km = km_total
        last_time = time_total
        last_elev = elev_total

    return pd.DataFrame(rows)
# ---------------------------------------------------------
# EXCEL EXPORT
# ---------------------------------------------------------
def export_summary_excel(summary_df):
    excel_buffer = BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
        summary_df.to_excel(writer, sheet_name="Zusammenfassung", index=False)
    return excel_buffer.getvalue()
# ---------------------------------------------------------
# PDF EXPORT – KOMPAKT (A1, 10 SPALTEN)
# ---------------------------------------------------------
def export_summary_pdf(summary_df):
    pdf_buffer = BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=A4)
    width, height = A4

    # Layout
    x_positions = [20, 70, 120, 160, 200, 240, 280, 320, 360, 400, 450]
    headers = [
        "Name", "KM ges.", "KM Abs.", "HM ges.", "HM Abs.", "Ankunftszeit",
        "Zeit ges.", "Zeit Abs.", "Ø km/h g.", "Ø km/h A.", "Pause"
    ]

    y = height - 30
    c.setFont("Helvetica-Bold", 10)
    c.drawString(20, y, "Brevet Zusammenfassung")
    y -= 20

    # Header
    c.setFont("Helvetica-Bold", 7)
    for x, h in zip(x_positions, headers):
        c.drawString(x, y, h)

    y -= 8
    c.line(15, y, width - 15, y)
    y -= 10

    # Rows
    c.setFont("Helvetica", 7)

    for _, row in summary_df.iterrows():
        values = [
            row["Name"],
            row["KM gesamt"],
            row["KM Abschnitt"],
            row["HM gesamt"],
            row["HM Abschnitt"],
            row["Ankunftszeit"],
            row["Zeit gesamt"],
            row["Zeit Abschnitt"],
            row["Ø‑km/h gesamt"],
            row["Ø‑km/h Abschnitt"],
            row["Pause (min)"],
        ]

        for x, v in zip(x_positions, values):
            c.drawString(x, y, str(v))

        y -= 10

        if y < 40:
            c.showPage()
            y = height - 40
            c.setFont("Helvetica", 7)

    c.save()
    return pdf_buffer.getvalue()
# ---------------------------------------------------------
# GPX UPLOAD + HAUPTAUSGABE
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

        df = parse_gpx(file)
        df = add_distance_and_gradient(df)
        df = add_time_profile(df)
        df = apply_pauses(df)

        # Karte
        st.subheader("🗺️ Karte")
        show_map(df)

        # Höhenprofil
        st.subheader("⛰️ Höhenprofil")
        show_elevation_profile(df)

        # Speed
        st.subheader("📈 Geschwindigkeitskurve")
        show_speed(df)

        # Summary
        st.subheader("📋 Kontroll‑ & Pausentabelle")
        summary_df = build_summary(df)
        st.dataframe(summary_df)

        # Excel Export
        st.download_button(
            "📥 Excel Export",
            export_summary_excel(summary_df),
            file_name=f"brevet_summary_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

        # PDF Export
        st.download_button(
            "📄 PDF Export (kompakt)",
            export_summary_pdf(summary_df),
            file_name=f"brevet_summary_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            mime="application/pdf"
        )

        # Ankunftszeit
        finish_time = df.sim_time_with_pauses.iloc[-1]
        total_time = finish_time - start_datetime

        st.markdown(f"**Ankunftszeit (inkl. Pausen):** {finish_time.strftime('%d.%m.%Y %H:%M')}")

        total_hours = int(total_time.total_seconds() // 3600)
        total_minutes = int((total_time.total_seconds() % 3600) // 60)
        st.markdown(f"**Gesamtzeit:** {total_hours:02d}:{total_minutes:02d} Std")

        all_dfs[file.name] = df
else:
    st.info("Bitte eine oder mehrere GPX-Dateien hochladen.")
# ---------------------------------------------------------
# CLOUD‑STABILER RERUN (OHNE experimental_rerun)
# ---------------------------------------------------------
if st.session_state.get("trigger_rerun", False):
    st.session_state["trigger_rerun"] = False
    st.session_state["__rerun_placeholder"] = datetime.now().timestamp()
