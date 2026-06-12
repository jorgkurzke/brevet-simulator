# B.7.16 – Brevet Simulator (komplett, sauber, ohne Edge-Müll)
# Features:
# - GPX-Parser (robust)
# - Vektorisiertes Speed-Modell (Hybrid C2)
# - Zeitprofil + ACP-Zeiten
# - Folium-Karte mit Höhen-Farbverlauf
# - Zusammenfassung (Variante B – vollständig)
# - Export: Excel + PDF (UTF‑8, DejaVuSans.ttf)
# - Performance: Caching, keine Loops

import math
import datetime as dt
import numpy as np
import pandas as pd
import streamlit as st
import gpxpy
import gpxpy.geo
from fpdf import FPDF
import folium
from streamlit_folium import st_folium

G = 9.81
AIR = 1.226


# -----------------------------------------------------
# GPX PARSER (ohne Cache)
# -----------------------------------------------------
def parse_gpx(file):
    gpx = gpxpy.parse(file)

    lats, lons, elevs, dists = [], [], [], []
    total = 0.0
    last = None

    for track in gpx.tracks:
        for seg in track.segments:
            for p in seg.points:
                lats.append(p.latitude)
                lons.append(p.longitude)
                elevs.append(p.elevation)

                if last:
                    dx = gpxpy.geo.haversine_distance(
                        last.latitude, last.longitude, p.latitude, p.longitude
                    )
                    if dx is None or math.isnan(dx):
                        dx = 0.0
                    total += dx

                dists.append(total)
                last = p

    df = pd.DataFrame({
        "lat": pd.to_numeric(lats, errors="coerce").ffill(),
        "lon": pd.to_numeric(lons, errors="coerce").ffill(),
        "elev": pd.to_numeric(elevs, errors="coerce").ffill().bfill(),
        "distance_m": pd.to_numeric(dists, errors="coerce").ffill().fillna(0.0)
    })

    dh = df["elev"].diff().fillna(0)
    dx = df["distance_m"].diff().fillna(1)
    df["gradient"] = (dh / dx) * 100

    return df

# -----------------------------------------------------
# ACP TIMES
# -----------------------------------------------------
def compute_acp_times(df):
    max_s = [(200, 34), (400, 32), (600, 30), (1000, 28), (1300, 26)]
    min_s = [(200, 15), (400, 15), (600, 15), (1000, 11.428), (1300, 13.333)]

    def acp(km, table):
        rem = km
        h = 0
        for lim, sp in table:
            if rem <= 0:
                break
            seg = min(rem, lim)
            h += seg / sp
            rem -= seg
        return h * 3600

    rows = []
    for _, r in df.iterrows():
        km = r["distance_m"] / 1000
        rows.append({
            "km": km,
            "open_s": acp(km, max_s),
            "close_s": acp(km, min_s)
        })
    return pd.DataFrame(rows)


# -----------------------------------------------------
# WIND
# -----------------------------------------------------
def wind_component(w, ang):
    return (w / 3.6) * math.cos(math.radians(ang))


# -----------------------------------------------------
# SPEED MODEL (Hybrid C2, vektorisiert)
# -----------------------------------------------------
def compute_speed(df, params):
    g = df["gradient"].values

    # Zielgeschwindigkeit
    v_t = np.select(
        [
            g < -3,
            g < -1,
            g < 1,
            g < 3,
            g < 6,
            g < 10
        ],
        [
            params["spd_down"],
            params["spd_ldown"],
            params["spd_flat"],
            params["spd_lup"],
            params["spd_mup"],
            params["spd_sup"]
        ],
        default=params["spd_vs_up"]
    )

    # Leistung
    P = np.select(
        [g < -1, g <= 1],
        [params["w_down"], params["w_flat"]],
        default=params["w_up"]
    )

    w = wind_component(params["wind"], params["wind_ang"])
    F_roll = params["weight"] * G * params["crr"]
    F_grav = params["weight"] * G * (g / 100)
    A = 0.5 * AIR * params["cda"]
    B = F_roll + F_grav

    v1 = P / np.maximum(B, 1e-6)
    v2 = (P / np.maximum(A, 1e-6)) ** (1/3)

    v = np.where(v1 < 8, v1, v2)
    v = np.maximum(v, params["min_spd"] / 3.6)
    v = v * 3.6

    v = np.maximum(v, v_t * 0.7)
    v = np.minimum(v, params["max_down"])

    return v


# -----------------------------------------------------
# TIME PROFILE
# -----------------------------------------------------
def add_time_profile(df, params):
    df["speed_kmh"] = compute_speed(df, params)

    dist_km = df["distance_m"].diff().fillna(0) / 1000
    hours = dist_km / np.maximum(df["speed_kmh"], 0.1)
    df["segment_seconds"] = hours * 3600
    df["cum_seconds"] = df["segment_seconds"].cumsum()

    df_acp = compute_acp_times(df)
    return df, df_acp


# -----------------------------------------------------
# SUMMARY TABLE
# -----------------------------------------------------
def build_summary(df, control_points, pause_points, start_dt, df_acp):
    # Punkte zusammenführen
    pts = []

    # Start
    pts.append({
        "km": 0.0,
        "name": "Start",
        "type": "Start",
        "pause": 0
    })

    # KP + Pausen
    for cp in control_points:
        pts.append({
            "km": cp["km"],
            "name": cp["name"],
            "type": "Kontrollpunkt",
            "pause": cp["pause"]
        })

    for pp in pause_points:
        pts.append({
            "km": pp["km"],
            "name": pp["name"],
            "type": "Pause",
            "pause": pp["pause"]
        })

    # Ziel
    pts.append({
        "km": df["distance_m"].iloc[-1] / 1000,
        "name": "Ziel",
        "type": "Ziel",
        "pause": 0
    })

    # Sortieren nach km
    pts = sorted(pts, key=lambda x: x["km"])

    rows = []
    last_km = 0
    last_time = 0
    last_elev = df["elev"].iloc[0]

    for p in pts:
        km = p["km"]
        idx = (df["distance_m"] / 1000 - km).abs().idxmin()

        elev = df["elev"].iloc[idx]
        cum_t = df["cum_seconds"].iloc[idx]

        seg_km = km - last_km
        seg_t = cum_t - last_time
        seg_hm = elev - last_elev

        rows.append({
            "Typ": p["type"],
            "Name": p["name"],
            "KM": km,
            "KM Abschnitt": seg_km,
            "HM Abschnitt": seg_hm,
            "HM gesamt": elev - df["elev"].iloc[0],
            "Zeit Abschnitt": dt.timedelta(seconds=int(seg_t)),
            "Zeit gesamt": dt.timedelta(seconds=int(cum_t)),
            "Ø Abschnitt": (seg_km / (seg_t / 3600)) if seg_t > 0 else 0,
            "Ø gesamt": (km / (cum_t / 3600)) if cum_t > 0 else 0,
            "Pause (min)": p["pause"],
            "ACP Open": df_acp.loc[idx, "open_s"],
            "ACP Close": df_acp.loc[idx, "close_s"]
        })

        last_km = km
        last_time = cum_t
        last_elev = elev

    return pd.DataFrame(rows)


# -----------------------------------------------------
# EXCEL EXPORT
# -----------------------------------------------------
def export_excel(df):
    import io
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as w:
        df.to_excel(w, index=False, sheet_name="Zusammenfassung")
    return buf.getvalue()


# -----------------------------------------------------
# PDF EXPORT (UTF‑8)
# -----------------------------------------------------
def export_pdf(df):
    pdf = FPDF()
    pdf.add_page()
    pdf.add_font("DejaVu", "", "DejaVuSans.ttf", uni=True)
    pdf.set_font("DejaVu", size=9)

    pdf.cell(0, 10, "Brevet Zusammenfassung", ln=True)

    for _, r in df.iterrows():
        line = (
            f"{r['Typ']} – {r['Name']} – KM {r['KM']:.1f} – "
            f"Zeit gesamt {r['Zeit gesamt']}"
        )
        pdf.multi_cell(0, 6, line)

    return pdf.output(dest="S").encode("utf-8")


# -----------------------------------------------------
# FOLIUM MAP (Höhen-Farbverlauf)
# -----------------------------------------------------
def color_for_gradient(g):
    if g < -2:
        return "#00aa00"  # grün
    if g < 2:
        return "#88cc00"  # gelbgrün
    if g < 6:
        return "#ffcc00"  # gelb
    if g < 10:
        return "#ff8800"  # orange
    return "#ff0000"      # rot


def build_map(df, control_points, pause_points):
    m = folium.Map(location=[df["lat"].iloc[0], df["lon"].iloc[0]], zoom_start=12)

    # Track segmentweise
    for i in range(len(df)-1):
        p1 = (df["lat"].iloc[i], df["lon"].iloc[i])
        p2 = (df["lat"].iloc[i+1], df["lon"].iloc[i+1])
        g = df["gradient"].iloc[i]
        folium.PolyLine([p1, p2], color=color_for_gradient(g), weight=4).add_to(m)

    # Start
    folium.Marker(
        [df["lat"].iloc[0], df["lon"].iloc[0]],
        popup="Start",
        icon=folium.Icon(color="green")
    ).add_to(m)

    # Ziel
    folium.Marker(
        [df["lat"].iloc[-1], df["lon"].iloc[-1]],
        popup="Ziel",
        icon=folium.Icon(color="red")
    ).add_to(m)

    # KP
    for cp in control_points:
        idx = (df["distance_m"]/1000 - cp["km"]).abs().idxmin()
        folium.Marker(
            [df["lat"].iloc[idx], df["lon"].iloc[idx]],
            popup=f"KP: {cp['name']}",
            icon=folium.Icon(color="blue")
        ).add_to(m)

    # Pausen
    for pp in pause_points:
        idx = (df["distance_m"]/1000 - pp["km"]).abs().idxmin()
        folium.Marker(
            [df["lat"].iloc[idx], df["lon"].iloc[idx]],
            popup=f"Pause: {pp['name']}",
            icon=folium.Icon(color="orange")
        ).add_to(m)

    return m


# -----------------------------------------------------
# STREAMLIT UI
# -----------------------------------------------------
st.set_page_config(page_title="Brevet Simulator", layout="wide")
st.title("Brevet Simulator B.7.16")

# Sidebar
st.sidebar.header("Fahrer & Rad")
weight = st.sidebar.number_input("Gesamtgewicht (kg)", 50.0, 150.0, 85.0)

st.sidebar.header("Physik")
cda = st.sidebar.number_input("CdA", 0.15, 0.5, 0.28)
crr = st.sidebar.number_input("Crr", 0.002, 0.01, 0.004)
wind = st.sidebar.number_input("Wind (km/h)", 0.0, 60.0, 0.0)
wind_ang = st.sidebar.number_input("Windwinkel (°)", -180.0, 180.0, 0.0)
max_down = st.sidebar.number_input("Max. Abfahrt (km/h)", 20.0, 100.0, 70.0)
min_spd = st.sidebar.number_input("Min. Geschwindigkeit (km/h)", 3.0, 15.0, 6.0)

st.sidebar.header("Zielgeschwindigkeiten")
spd_down = st.sidebar.number_input("Bergab", 20.0, 80.0, 50.0)
spd_ldown = st.sidebar.number_input("Leicht bergab", 20.0, 60.0, 40.0)
spd_flat = st.sidebar.number_input("Flach", 15.0, 40.0, 28.0)
spd_lup = st.sidebar.number_input("Leicht bergauf", 10.0, 35.0, 24.0)
spd_mup = st.sidebar.number_input("Mittel bergauf", 8.0, 30.0, 20.0)
spd_sup = st.sidebar.number_input("Steil bergauf", 5.0, 25.0, 15.0)
spd_vs_up = st.sidebar.number_input("Sehr steil", 3.0, 20.0, 10.0)

st.sidebar.header("Leistung")
w_flat = st.sidebar.number_input("Watt flach", 80, 400, 200)
w_up = st.sidebar.number_input("Watt bergauf", 80, 450, 230)
w_down = st.sidebar.number_input("Watt bergab", 0, 400, 150)

st.sidebar.header("Startzeit")
start_date = st.sidebar.date_input("Datum", dt.date.today())
start_time = st.sidebar.time_input("Zeit", dt.time(6, 0))
start_dt = dt.datetime.combine(start_date, start_time)

st.sidebar.header("Kontrollpunkte")
n_cp = st.sidebar.number_input("Anzahl KP", 0, 20, 0)
control_points = []
for i in range(n_cp):
    name = st.sidebar.text_input(f"KP {i+1} Name", key=f"cpn{i}")
    km = st.sidebar.number_input(f"KP {i+1} km", 0.0, 2000.0, 0.0, key=f"cpk{i}")
    pause = st.sidebar.number_input(f"KP {i+1} Pause (min)", 0, 180, 0, key=f"cpp{i}")
    control_points.append({"name": name, "km": km, "pause": pause})

st.sidebar.header("Pausenpunkte")
n_pp = st.sidebar.number_input("Anzahl Pausen", 0, 20, 0)
pause_points = []
for i in range(n_pp):
    name = st.sidebar.text_input(f"Pause {i+1} Name", key=f"ppn{i}")
    km = st.sidebar.number_input(f"Pause {i+1} km", 0.0, 2000.0, 0.0, key=f"ppk{i}")
    pause = st.sidebar.number_input(f"Pause {i+1} Dauer (min)", 0, 180, 0, key=f"ppp{i}")
    pause_points.append({"name": name, "km": km, "pause": pause})

uploaded = st.file_uploader("GPX-Datei hochladen", type=["gpx"])

if uploaded:
    df = parse_gpx(uploaded)

    params = {
        "weight": weight,
        "cda": cda,
        "crr": crr,
        "wind": wind,
        "wind_ang": wind_ang,
        "max_down": max_down,
        "min_spd": min_spd,
        "spd_down": spd_down,
        "spd_ldown": spd_ldown,
        "spd_flat": spd_flat,
        "spd_lup": spd_lup,
        "spd_mup": spd_mup,
        "spd_sup": spd_sup,
        "spd_vs_up": spd_vs_up,
        "w_flat": w_flat,
        "w_up": w_up,
        "w_down": w_down
    }

    df, df_acp = add_time_profile(df, params)

    st.subheader("Karte")
    m = build_map(df, control_points, pause_points)
    st_folium(m, width=900, height=600)

    st.subheader("Zusammenfassung")
    summary = build_summary(df, control_points, pause_points, start_dt, df_acp)
    st.dataframe(summary)

    st.download_button(
        "📄 Zusammenfassung als Excel",
        data=export_excel(summary),
        file_name="brevet_zusammenfassung.xlsx"
    )

    st.download_button(
        "📄 Zusammenfassung als PDF",
        data=export_pdf(summary),
        file_name="brevet_zusammenfassung.pdf"
    )

else:
    st.info("Bitte GPX-Datei hochladen.")





