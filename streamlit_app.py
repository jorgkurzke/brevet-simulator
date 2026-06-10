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
    df = df.copy()

    # --- LAT/LON robust bereinigen ---
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df = df.dropna(subset=["lat", "lon"])

    # --- Höhenwerte robust bereinigen ---
    df["ele"] = pd.to_numeric(df["ele"], errors="coerce")
    df["ele"] = df["ele"].replace([np.inf, -np.inf], np.nan)

    # Wenn weniger als 3 Punkte → keine Interpolation
    if len(df) < 3:
        df["ele"] = df["ele"].fillna(method="ffill").fillna(method="bfill").fillna(0)
    else:
        if df["ele"].isna().all():
            df["ele"] = 0
        else:
            # NumPy-Interpolation (um Pandas-Bug zu vermeiden)
            ele = df["ele"].to_numpy()
            mask = np.isnan(ele)
            if mask.any():
                ele[mask] = np.interp(
                    np.flatnonzero(mask),
                    np.flatnonzero(~mask),
                    ele[~mask]
                )
            df["ele"] = ele

    # --- ZEIT KOMPLETT NEU ERZEUGEN ---
    df["time"] = pd.date_range(
        start=datetime.now(),
        periods=len(df),
        freq=pd.Timedelta(seconds=1)
    )

    # --- Doppelte Punkte entfernen ---
    df = df.loc[~((df["lat"].diff() == 0) & (df["lon"].diff() == 0))]

    return df.reset_index(drop=True)



def compute_stats(points):
    df = pd.DataFrame(points, columns=["lat", "lon", "ele", "time"])
    df = sanitize_gpx(df)

    df["dist"] = np.sqrt(
        (df["lat"].diff() * 111_320) ** 2 +
        (df["lon"].diff() * 40075_000 * np.cos(np.radians(df["lat"])) / 360) ** 2
    )
    df["dist"] = df["dist"].replace([np.inf, -np.inf], 0).fillna(0)
    df.loc[df["dist"] < 0.01, "dist"] = 0
    df["cum_dist"] = df["dist"].cumsum()

    df["ele_diff"] = df["ele"].diff().fillna(0)
    df["slope"] = df["ele_diff"] / df["dist"].replace(0, np.nan)
    df["slope"] = df["slope"].replace([np.inf, -np.inf], 0).fillna(0)
    df["slope"] = df["slope"].clip(-0.3, 0.3)

    wind_components = [0.0]
    for i in range(1, len(df)):
        w = wind_effect(
            df.lat.iloc[i - 1], df.lon.iloc[i - 1],
            df.lat.iloc[i], df.lon.iloc[i],
            wind_speed, wind_direction
        )
        wind_components.append(w)
    df["wind_mps"] = wind_components

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

    df["time_s"] = df["dist"] / df["speed_mps"].replace(0, np.nan)
    df["time_s"] = df["time_s"].replace([np.inf, -np.inf], 0).fillna(0)
    df.loc[df["time_s"] < 0, "time_s"] = 0
    df["cum_time_s"] = df["time_s"].cumsum()

    df["acp_limit_s"] = df["cum_dist"].apply(lambda x: acp_control_time(x / 1000))

    df["sim_clock"] = df["cum_time_s"].apply(lambda s: start_dt + timedelta(seconds=s))
    df["acp_deadline"] = df["acp_limit_s"].apply(lambda s: start_dt + timedelta(seconds=s))

    return df


def apply_breaks(df, pause_df):
    df = df.copy()
    df["break_s"] = 0
    for _, row in pause_df.iterrows():
        km_break = row["km"]
        dur_s = row["Dauer_min"] * 60
        idx = df.index[df["cum_dist"] >= km_break * 1000]
        if len(idx) > 0:
            df.loc[idx[0]:, "break_s"] += dur_s
    df["cum_break_s"] = df["break_s"].cumsum()
    df["cum_time_with_break_s"] = df["cum_time_s"] + df["cum_break_s"]
    return df


def compute_controls(df, kontroll_df):
    controls = []
    for _, row in kontroll_df.iterrows():
        km = row["km"]
        name = row["Name"]
        idx = df.index[df["cum_dist"] >= km * 1000]
        if len(idx) == 0:
            continue
        i = idx[0]
        controls.append({
            "km": km,
            "Name": name,
            "sim_time_s": df.loc[i, "cum_time_with_break_s"],
            "acp_limit_s": df.loc[i, "acp_limit_s"]
        })
    return pd.DataFrame(controls)

# ---------------------------------------------------------
# Hauptlogik
# ---------------------------------------------------------
if uploaded_files and len(uploaded_files) > 0:

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

    st.subheader("🗺️ Karte")
    html(m._repr_html_(), height=600)

    st.subheader("📊 Tracks, Profile & ACP-Check")

    for name, df, controls in track_stats:
        st.markdown(f"### {name}")

        total_dist = df["cum_dist"].iloc[-1] / 1000
        total_up = df.ele.diff().clip(lower=0).sum()
        total_down = -df.ele.diff().clip(upper=0).sum()
        total_time_h = df["cum_time_with_break_s"].iloc[-1] / 3600
        acp_total_h = df["acp_limit_s"].iloc[-1] / 3600

        st.write(f"**Distanz:** {total_dist:.1f} km")
        st.write(f"**Höhenmeter bergauf:** {total_up:.0f} m")
        st.write(f"**Höhenmeter bergab:** {total_down:.0f} m")
        st.write(f"**Gesamtzeit inkl. Pausen:** {total_time_h:.2f} h")
        st.write(f"**ACP-Zeitlimit:** {acp_total_h:.2f} h")

        if total_time_h <= acp_total_h:
            st.success("✔️ Brevet liegt innerhalb des ACP-Zeitlimits.")
        else:
            st.error("❌ Brevet liegt außerhalb des ACP-Zeitlimits.")

        fig, ax = plt.subplots(figsize=(8, 3))
        ax.plot(df["cum_dist"] / 1000, df["ele"], color="black")
        ax.set_xlabel("Distanz (km)")
        ax.set_ylabel("Höhe (m)")
        ax.set_title(f"Höhenprofil – {name}")
        st.pyplot(fig)

        if not controls.empty:
            controls["sim_clock"] = controls["sim_time_s"].apply(
                lambda s: start_dt + timedelta(seconds=s)
            )
            controls["acp_deadline"] = controls["acp_limit_s"].apply(
                lambda s: start_dt + timedelta(seconds=s)
            )
            st.write("**Kontrollpunkte:**")
            st.dataframe(controls)

    with BytesIO() as buffer:
        with pd.ExcelWriter(buffer, engine="xlsxwriter") as writer:
            for name, df, controls in track_stats:
                df.to_excel(writer, sheet_name=(name[:31] + "_Track"), index=False)
                if not controls.empty:
                    controls.to_excel(writer, sheet_name=(name[:31] + "_CP"), index=False)
        buffer.seek(0)
        st.download_button(
            "📥 Excel exportieren",
            data=buffer,
            file_name="brevet_simulation.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    pdf_buffer = BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=A4)
    width, height = A4

    for name, df, controls in track_stats:
        c.setFont("Helvetica-Bold", 14)
        c.drawString(40, height - 40, f"Track: {name}")

        total_dist = df["cum_dist"].iloc[-1] / 1000
        total_time_h = df["cum_time_with_break_s"].iloc[-1] / 3600
        acp_total_h = df["acp_limit_s"].iloc[-1] / 3600

        c.setFont("Helvetica", 11)
        c.drawString(40, height - 70, f"Distanz: {total_dist:.1f} km")
        c.drawString(40, height - 90, f"Zeit inkl. Pausen: {total_time_h:.2f} h")
        c.drawString(40, height - 110, f"ACP-Zeitlimit: {acp_total_h:.2f} h")

        y = height - 140
        c.setFont("Helvetica-Bold", 11)
        c.drawString(40, y, "Kontrollpunkte:")
        y -= 20
        c.setFont("Helvetica", 10)
        if not controls.empty:
            for _, row in controls.iterrows():
                line = (
                    f"{row['Name']} @ {row['km']} km – "
                    f"Sim: {start_dt + timedelta(seconds=row['sim_time_s'])} – "
                    f"ACP: {start_dt + timedelta(seconds=row['acp_limit_s'])}"
                )
                c.drawString(40, y, line)
                y -= 15
                if y < 60:
                    c.showPage()
                    y = height - 60
        c.showPage()

    c.save()
    pdf_buffer.seek(0)

    st.download_button(
        "📥 PDF exportieren",
        data=pdf_buffer,
        file_name="brevet_simulation.pdf",
        mime="application/pdf"
    )

else:
    st.info("Bitte eine oder mehrere GPX-Dateien hochladen.")
