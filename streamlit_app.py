# B.7.20 – Brevet Simulator (ultrasicher, in Blöcken)

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
import plotly.graph_objects as go
import branca.colormap as cm

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
        "lat": pd.to_numeric(lats, errors="coerce"),
        "lon": pd.to_numeric(lons, errors="coerce"),
        "elev": pd.to_numeric(elevs, errors="coerce"),
        "distance_m": pd.to_numeric(dists, errors="coerce"),
    })

    df["lat"] = df["lat"].ffill()
    df["lon"] = df["lon"].ffill()
    df["elev"] = df["elev"].ffill().bfill()
    df["distance_m"] = df["distance_m"].ffill().fillna(0.0)

    dh = df["elev"].diff().fillna(0)
    dx = df["distance_m"].diff().fillna(1)
    df["gradient"] = (dh / dx) * 100

    return df

# -----------------------------------------------------
# DOWNSAMPLING (10× schneller)
# -----------------------------------------------------
def downsample(df, n=1500):
    if len(df) <= n:
        return df
    idx = np.linspace(0, len(df) - 1, n).astype(int)
    return df.iloc[idx].reset_index(drop=True)
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
# -----------------------------------------------------
# FTP-basiertes Speed-Modell (C4-Hybrid)
# -----------------------------------------------------
# -----------------------------------------------------
# HYBRID C5: Zielgeschwindigkeit + FTP-Physik
# -----------------------------------------------------
# -----------------------------------------------------
# STEIGUNG / GRADIENT BERECHNEN (%)
# -----------------------------------------------------
def compute_gradient(df):
    elev = df["elev"].values
    dist = df["distance_m"].values

    grad = np.zeros(len(df))
    for i in range(1, len(df)):
        dh = elev[i] - elev[i-1]
        dx = dist[i] - dist[i-1]
        if dx > 0:
            grad[i] = (dh / dx) * 100
        else:
            grad[i] = 0
    return grad


# -----------------------------------------------------
# HAVERSINE (Meter)
# -----------------------------------------------------
def haversine(lat1, lon1, lat2, lon2):
    R = 6371000  # Erdradius in Metern
    phi1 = np.radians(lat1)
    phi2 = np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlambda = np.radians(lon2 - lon1)

    a = np.sin(dphi/2)**2 + np.cos(phi1)*np.cos(phi2)*np.sin(dlambda/2)**2
    return 2 * R * np.arcsin(np.sqrt(a))
# -----------------------------------------------------
# DISTANZ BERECHNEN (Meter)
# -----------------------------------------------------
def compute_distance(df):
    dist = [0.0]
    for i in range(1, len(df)):
        lat1, lon1 = df.loc[i-1, "lat"], df.loc[i-1, "lon"]
        lat2, lon2 = df.loc[i, "lat"], df.loc[i, "lon"]
        d = haversine(lat1, lon1, lat2, lon2)
        dist.append(dist[-1] + d)
    return np.array(dist)
# -----------------------------------------------------
# GESCHWINDIGKEIT BERECHNEN (Hybrid: Physik + Zieltempo)
# -----------------------------------------------------
def compute_speed(df, params):

    weight = params["weight"]
    cda = params["cda"]
    crr = params["crr"]
    wind = params["wind"]
    wind_ang = params["wind_ang"]
    max_down = params["max_down"]
    min_spd = params["min_spd"]

    # Zielgeschwindigkeiten
    spd_down  = params["spd_down"]
    spd_ldown = params["spd_ldown"]
    spd_flat  = params["spd_flat"]
    spd_lup   = params["spd_lup"]
    spd_mup   = params["spd_mup"]
    spd_sup   = params["spd_sup"]
    spd_vs_up = params["spd_vs_up"]

    hybrid = params["hybrid_factor"]

    g = df["gradient"].values / 100.0  # z.B. 0.05 = 5%

    # Zieltempo nach Steigung
    v_target = np.select(
        [
            g < -0.03,
            g < -0.01,
            g < 0.01,
            g < 0.03,
            g < 0.06,
            g < 0.10
        ],
        [
            spd_down,
            spd_ldown,
            spd_flat,
            spd_lup,
            spd_mup,
            spd_sup,
        ],
        default=spd_vs_up
    )

    # Physikalische Geschwindigkeit (vereinfachtes Modell)
    v_phys = np.zeros(len(df))
    for i in range(len(df)):
        slope = g[i]
        if slope < -0.01:
            v = max_down
        elif slope < 0.01:
            v = spd_flat
        else:
            v = max(min_spd, spd_flat - slope * 200)
        v_phys[i] = v

    # Hybrid
    v_final = np.maximum(v_phys, v_target * hybrid)

    return v_final

    
def add_time_profile(df, params):
    df["speed_kmh"] = compute_speed(df, params)

    dist_km = df["distance_m"].diff().fillna(0) / 1000
    hours = dist_km / np.maximum(df["speed_kmh"], 0.1)
    df["segment_seconds"] = hours * 3600
    df["cum_seconds"] = df["segment_seconds"].cumsum()

    df_acp = compute_acp_times(df)
    return df, df_acp


# -----------------------------------------------------
# ZUSAMMENFASSUNG ERZEUGEN
# -----------------------------------------------------
def build_summary(df, control_points, pause_points, start_dt, df_acp):

    # Sicherstellen, dass df die km-Spalte hat
    if "km" not in df.columns:
        st.error("Fehler: DataFrame hat keine 'km'-Spalte. Reihenfolge der Verarbeitung prüfen!")
        st.stop()
    
    # Ungültige Punkte entfernen
    control_points = [cp for cp in control_points if cp["km"] is not None]
    pause_points = [pp for pp in pause_points if pp["km"] is not None]

    rows = []

    last_km = 0
    last_time = 0
    last_elev = df["elev"].iloc[0]

    cum_t = 0  # Gesamtzeit in Sekunden

    # Kontrollpunkte + Pausenpunkte zusammenführen
    all_points = []

    for cp in control_points:
        all_points.append({
            "km": cp["km"],
            "name": cp["name"],
            "type": "Kontrollpunkt",
            "pause": 0
        })

    for pp in pause_points:
        all_points.append({
            "km": pp["km"],
            "name": pp["name"],
            "type": "Pause",
            "pause": pp["pause"]
        })

    # Sortieren nach Kilometer
    all_points = sorted(all_points, key=lambda x: x["km"])

    # Startpunkt hinzufügen
    all_points.insert(0, {
        "km": 0,
        "name": "Start",
        "type": "Start",
        "pause": 0
    })

    for p in all_points:

        # Index im DF finden
        idx = df.index[df["km"] >= p["km"]].min()

        km = df.loc[idx, "km"]
        elev = df.loc[idx, "elev"]

        # Abschnittswerte
        seg_km = km - last_km
        seg_hm = max(0, elev - last_elev)

        # Zeit seit letztem Punkt
        seg_t = df.loc[idx, "time_s"] - last_time
        cum_t += seg_t

        # Pause addieren
        if p["pause"] > 0:
            cum_t += p["pause"] * 60

        # Formatierte Zeiten
        seg_time_str = str(dt.timedelta(seconds=int(seg_t)))[:-3]
        cum_time_str = str(dt.timedelta(seconds=int(cum_t)))[:-3]

        # Durchschnittsgeschwindigkeiten
        avg_seg = int(round((seg_km / (seg_t / 3600)) if seg_t > 0 else 0))
        avg_total = int(round((km / (cum_t / 3600)) if cum_t > 0 else 0))

        rows.append({
            "Typ": p["type"],
            "Name": p["name"],
            "KM": int(round(km)),
            "KM Abschnitt": int(round(seg_km)),
            "HM Abschnitt": int(round(seg_hm)),
            "HM gesamt": int(round(elev - df["elev"].iloc[0])),
            "Zeit Abschnitt": seg_time_str,
            "Zeit gesamt": cum_time_str,
            "Ø Abschnitt": avg_seg,
            "Ø gesamt": avg_total,
            "Pause (min)": p["pause"]
        })

        # Update für nächsten Abschnitt
        last_km = km
        last_time = df.loc[idx, "time_s"]
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
# PDF EXPORT
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
# FOLIUM MAP (stabil, B.7.20)
# -----------------------------------------------------
def build_map(df, control_points, pause_points):
    # Falls Gradient überall gleich ist → Colormap‑Fix
    vmin = float(df["gradient"].min())
    vmax = float(df["gradient"].max())
    if vmin == vmax:
        vmax = vmin + 0.01

    m = folium.Map(
        location=[df["lat"].iloc[0], df["lon"].iloc[0]],
        zoom_start=12
    )

    colormap = cm.LinearColormap(
        colors=["green", "yellow", "orange", "red"],
        vmin=vmin,
        vmax=vmax
    )

    # Route
    folium.PolyLine(
        df[["lat", "lon"]].values,
        color="blue",
        weight=4,
        opacity=0.8
    ).add_to(m)

    colormap.add_to(m)

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

    # Kontrollpunkte
    for cp in control_points:
        idx = (df["distance_m"] / 1000 - cp["km"]).abs().idxmin()
        folium.Marker(
            [df["lat"].iloc[idx], df["lon"].iloc[idx]],
            popup=f"KP: {cp['name']}",
            icon=folium.Icon(color="blue")
        ).add_to(m)

    # Pausenpunkte
    for pp in pause_points:
        idx = (df["distance_m"] / 1000 - pp["km"]).abs().idxmin()
        folium.Marker(
            [df["lat"].iloc[idx], df["lon"].iloc[idx]],
            popup=f"Pause: {pp['name']}",
            icon=folium.Icon(color="orange")
        ).add_to(m)

    return m


# -----------------------------------------------------
# HÖHENPROFIL (Plotly)
# -----------------------------------------------------
def plot_elevation(df):
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["distance_m"] / 1000,
        y=df["elev"],
        mode="lines",
        line=dict(color="firebrick", width=2)
    ))
    fig.update_layout(
        title="Höhenprofil",
        xaxis_title="Kilometer",
        yaxis_title="Höhe (m)",
        height=300,
        margin=dict(l=40, r=20, t=40, b=40)
    )
    return fig
# -----------------------------------------------------
# SIDEBAR – Fahrer & Rad
# -----------------------------------------------------
st.sidebar.header("Fahrer & Rad")
weight = st.sidebar.number_input("Gesamtgewicht (kg)", 50.0, 150.0, 85.0)

# -----------------------------------------------------
# SIDEBAR – Physik
# -----------------------------------------------------
st.sidebar.header("Physik")
cda = st.sidebar.number_input("CdA", 0.15, 0.5, 0.28)
crr = st.sidebar.number_input("Crr", 0.002, 0.01, 0.004)
wind = st.sidebar.number_input("Wind (km/h)", 0.0, 60.0, 0.0)
wind_ang = st.sidebar.number_input("Windwinkel (°)", -180.0, 180.0, 0.0)
max_down = st.sidebar.number_input("Max. Abfahrt (km/h)", 20.0, 100.0, 70.0)
min_spd = st.sidebar.number_input("Min. Geschwindigkeit (km/h)", 3.0, 15.0, 6.0)

# -----------------------------------------------------
# SIDEBAR – Leistung / FTP
# -----------------------------------------------------
st.sidebar.header("Leistung / FTP")

ftp = st.sidebar.number_input("FTP (Watt)", 100, 450, 250)

# Auto-Leistung aus FTP
st.sidebar.caption("Leistung automatisch aus FTP berechnet:")

w_flat = int(ftp * 0.75)
w_up   = int(ftp * 0.90)
w_down = int(ftp * 0.50)

st.sidebar.write(f"Flach: {w_flat} W")
st.sidebar.write(f"Bergauf: {w_up} W")
st.sidebar.write(f"Bergab: {w_down} W")

# -----------------------------------------------------
# SIDEBAR – Zielgeschwindigkeiten
# -----------------------------------------------------
st.sidebar.header("Zielgeschwindigkeiten (automatisch aus FTP, aber überschreibbar)")

# Auto-Vorschläge aus FTP
auto_flat  = round(ftp * 0.11)   # ~ FTP * 0.11 → 28 km/h bei FTP=250
auto_lup   = round(ftp * 0.09)
auto_mup   = round(ftp * 0.075)
auto_sup   = round(ftp * 0.055)
auto_vs_up = round(ftp * 0.04)
auto_ldown = round(ftp * 0.13)
auto_down  = round(ftp * 0.15)

spd_down  = st.sidebar.number_input("Bergab", 10.0, 90.0, float(auto_down))
spd_ldown = st.sidebar.number_input("Leicht bergab", 10.0, 70.0, float(auto_ldown))
spd_flat  = st.sidebar.number_input("Flach", 10.0, 50.0, float(auto_flat))
spd_lup   = st.sidebar.number_input("Leicht bergauf", 5.0, 40.0, float(auto_lup))
spd_mup   = st.sidebar.number_input("Mittel bergauf", 5.0, 35.0, float(auto_mup))
spd_sup   = st.sidebar.number_input("Steil bergauf", 3.0, 30.0, float(auto_sup))
spd_vs_up = st.sidebar.number_input("Sehr steil", 2.0, 25.0, float(auto_vs_up))


# Hybrid-Faktor (0.5 = Physik dominiert, 1.0 = Zieltempo dominiert)
hybrid_factor = st.sidebar.slider(
    "Hybrid-Faktor (Zieltempo vs. Physik)",
    0.5, 1.2, 0.85, 0.01
)


# -----------------------------------------------------
# SIDEBAR – Startzeit
# -----------------------------------------------------
st.sidebar.header("Startzeit")
start_date = st.sidebar.date_input("Datum", dt.date.today())
start_time = st.sidebar.time_input("Zeit", dt.time(6, 0))
start_dt = dt.datetime.combine(start_date, start_time)

# -----------------------------------------------------
# SIDEBAR – Kontrollpunkte
# -----------------------------------------------------
st.sidebar.header("Kontrollpunkte")
n_cp = st.sidebar.number_input("Anzahl KP", 0, 20, 0)
control_points = []
for i in range(n_cp):
    name = st.sidebar.text_input(f"KP {i+1} Name", key=f"cpn{i}")
    km = st.sidebar.number_input(f"KP {i+1} km", 0.0, 2000.0, 0.0, key=f"cpk{i}")
    pause = st.sidebar.number_input(f"KP {i+1} Pause (min)", 0, 180, 0, key=f"cpp{i}")
    control_points.append({"name": name, "km": km, "pause": pause})

# -----------------------------------------------------
# SIDEBAR – Pausenpunkte
# -----------------------------------------------------
st.sidebar.header("Pausenpunkte")
n_pp = st.sidebar.number_input("Anzahl Pausen", 0, 20, 0)
pause_points = []
for i in range(n_pp):
    name = st.sidebar.text_input(f"Pause {i+1} Name", key=f"ppn{i}")
    km = st.sidebar.number_input(f"Pause {i+1} km", 0.0, 2000.0, 0.0, key=f"ppk{i}")
    pause = st.sidebar.number_input(f"Pause {i+1} Dauer (min)", 0, 180, 0, key=f"ppp{i}")
    pause_points.append({"name": name, "km": km, "pause": pause})
# -----------------------------------------------------
# GPX UPLOAD + PARAMETER-BUNDLE + ZEITPROFIL
# -----------------------------------------------------

uploaded = st.file_uploader("GPX-Datei hochladen", type=["gpx"])

if uploaded:
    # GPX einlesen
    df_raw = parse_gpx(uploaded)
    
    # Downsampling
    df = downsample(df_raw, 1500)
    
    # Distanz berechnen
    df["distance_m"] = compute_distance(df)
    df["km"] = df["distance_m"] / 1000
    
    # Gradient berechnen
    df["gradient"] = compute_gradient(df)
    df["gradient"] = df["gradient"].replace([np.inf, -np.inf], np.nan).fillna(0).clip(-30, 30)
    
    # Geschwindigkeit berechnen
    df["speed_kmh"] = compute_speed(df, params)
    
    # Zeitprofil berechnen
    df["time_s"] = compute_time(df["speed_kmh"], df["km"])
    df["cum_seconds"] = df["time_s"].cumsum()
    
    # ACP-Zeiten berechnen (falls benötigt)
    df, df_acp = add_time_profile(df, params)
    
    # Gesamtzeit
    total_seconds = df["cum_seconds"].iloc[-1]
    total_time = dt.timedelta(seconds=int(total_seconds))
    st.metric("Gesamtzeit", str(total_time))

    st.metric("Gesamtzeit", str(total_time))
# -----------------------------------------------------
# HÖHENPROFIL + KARTE
# -----------------------------------------------------

    # Höhenprofil anzeigen
    st.subheader("Höhenprofil")
    st.plotly_chart(plot_elevation(df), use_container_width=True)

    # Karte anzeigen
    st.subheader("Karte")
    m = build_map(df, control_points, pause_points)
    st_folium(m, width=900, height=600)
# -----------------------------------------------------
# ZUSAMMENFASSUNG
# -----------------------------------------------------

    st.subheader("Zusammenfassung")

    df_sum = build_summary(df, control_points, pause_points, start_dt, df_acp)

    # Anzeige
    st.dataframe(df_sum, use_container_width=True)
# -----------------------------------------------------
# EXPORT (Excel + PDF)
# -----------------------------------------------------

    st.subheader("Export")

    col1, col2 = st.columns(2)

    with col1:
        if st.button("Excel exportieren"):
            data = export_excel(df_sum)
            st.download_button(
                "Download Excel",
                data=data,
                file_name="brevet.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

    with col2:
        if st.button("PDF exportieren"):
            data = export_pdf(df_sum)
            st.download_button(
                "Download PDF",
                data=data,
                file_name="brevet.pdf",
                mime="application/pdf"
            )
# -----------------------------------------------------
# FINALE ABSCHLUSSLOGIK
# -----------------------------------------------------

else:
    st.info("Bitte eine GPX-Datei hochladen, um die Simulation zu starten.")

