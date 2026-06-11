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
# INITIAL SESSION STATE
# ---------------------------------------------------------
if "control_points" not in st.session_state:
    st.session_state["control_points"] = []

if "pauses" not in st.session_state:
    st.session_state["pauses"] = []

# Eingabefelder
for key, default in [
    ("new_cp_km", 0.0),
    ("new_cp_name", ""),
    ("new_cp_pause", 0),
    ("new_pause_km", 0.0),
    ("new_pause_min", 0),
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ---------------------------------------------------------
# SIDEBAR – SIMULATION
# ---------------------------------------------------------
st.sidebar.header("⚙️ Simulationseinstellungen")

st.sidebar.subheader("Leistung")
ftp = st.sidebar.number_input("FTP (Watt)", min_value=100, max_value=400, value=220, step=5)

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

st.sidebar.header("⏱ ACP‑Start")
start_date = st.sidebar.date_input("Startdatum", datetime.now().date())
start_time = st.sidebar.time_input("Startzeit", datetime.now().time())
start_datetime = datetime.combine(start_date, start_time)


# ---------------------------------------------------------
# ZIELGESCHWINDIGKEITEN
# ---------------------------------------------------------
st.sidebar.header("🎯 Zielgeschwindigkeiten pro Steigung")

base_flat = 26
base_light_down = 32
base_down = 50
base_light_up = 20
base_med_up = 16
base_steep_up = 12
base_very_steep_up = 8

ftp_factor = (ftp / 220) ** 0.35

target_speed_flat = st.sidebar.number_input("Flach (−1% bis +1%) (km/h)", 10.0, 45.0, round(base_flat * ftp_factor, 1))
target_speed_light_down = st.sidebar.number_input("Leicht bergab (−3% bis −1%) (km/h)", 10.0, 70.0, round(base_light_down * ftp_factor, 1))
target_speed_down = st.sidebar.number_input("Stark bergab (< −3%) (km/h)", 10.0, 120.0, round(base_down * ftp_factor, 1))

target_speed_light_up = st.sidebar.number_input("Leicht bergauf (1–3%) (km/h)", 5.0, 40.0, round(base_light_up * ftp_factor, 1))
target_speed_med_up = st.sidebar.number_input("Mäßig bergauf (3–6%) (km/h)", 5.0, 35.0, round(base_med_up * ftp_factor, 1))
target_speed_steep_up = st.sidebar.number_input("Stärker bergauf (6–10%) (km/h)", 3.0, 30.0, round(base_steep_up * ftp_factor, 1))
target_speed_very_steep_up = st.sidebar.number_input("Sehr steil (>10%) (km/h)", 2.0, 25.0, round(base_very_steep_up * ftp_factor, 1))


# ---------------------------------------------------------
# KONTROLLPUNKTE – VERZÖGERTER RERUN-FIX
# ---------------------------------------------------------
st.sidebar.header("📍 Kontrollpunkte")

new_cp_km = st.sidebar.number_input("KM für neuen Kontrollpunkt", min_value=0.0, step=1.0, value=st.session_state["new_cp_km"])
new_cp_name = st.sidebar.text_input("Name des Kontrollpunkts", value=st.session_state["new_cp_name"])
new_cp_pause = st.sidebar.number_input("Pause an Kontrollpunkt (Minuten)", min_value=0, max_value=240, value=st.session_state["new_cp_pause"])

if st.sidebar.button("Kontrollpunkt hinzufügen"):
    st.session_state["pending_add_cp"] = {
        "km": new_cp_km,
        "name": new_cp_name,
        "pause": new_cp_pause
    }
    st.session_state["trigger_rerun"] = True


# ---------------------------------------------------------
# PAUSENPUNKTE – VERZÖGERTER RERUN-FIX
# ---------------------------------------------------------
st.sidebar.header("⏸ Pausenpunkte")

new_pause_km = st.sidebar.number_input("KM für neue Pause", min_value=0.0, step=1.0, value=st.session_state["new_pause_km"])
new_pause_min = st.sidebar.number_input("Pausendauer (Minuten)", min_value=0, max_value=240, value=st.session_state["new_pause_min"])

if st.sidebar.button("Pause hinzufügen"):
    st.session_state["pending_add_pause"] = {
        "km": new_pause_km,
        "pause": new_pause_min
    }
    st.session_state["trigger_rerun"] = True


# ---------------------------------------------------------
# VERARBEITUNG DER PENDING EVENTS (NACH DEM BUTTON)
# ---------------------------------------------------------
if "pending_add_cp" in st.session_state:
    cp = st.session_state["pending_add_cp"]

    st.session_state["control_points"].append({
        "km": cp["km"],
        "name": cp["name"] if cp["name"] else f"CP {len(st.session_state['control_points'])+1}",
        "pause_min": cp["pause"]
    })

    st.session_state["new_cp_km"] = 0.0
    st.session_state["new_cp_name"] = ""
    st.session_state["new_cp_pause"] = 0

    del st.session_state["pending_add_cp"]

if "pending_add_pause" in st.session_state:
    p = st.session_state["pending_add_pause"]

    st.session_state["pauses"].append({
        "km": p["km"],
        "pause_min": p["pause"]
    })

    st.session_state["new_pause_km"] = 0.0
    st.session_state["new_pause_min"] = 0

    del st.session_state["pending_add_pause"]


# ---------------------------------------------------------
# ANZEIGE DER PUNKTE
# ---------------------------------------------------------
for cp in st.session_state["control_points"]:
    st.sidebar.write(f"• {cp['km']} km – {cp['name']} – Pause: {cp['pause_min']} min")

for p in st.session_state["pauses"]:
    st.sidebar.write(f"• Pause bei {p['km']} km – {p['pause_min']} min")


# ---------------------------------------------------------
# (REST DES GPX‑CODES UNVERÄNDERT)
# ---------------------------------------------------------
# … dein kompletter GPX‑Parser, Höhenprofil, Karte, Export usw.
# Ich lasse ihn hier weg, weil er unverändert bleibt.
# Du kannst ihn 1:1 aus deiner Version übernehmen.


# ---------------------------------------------------------
# RERUN-FLAG (MUSS GANZ UNTEN STEHEN)
# ---------------------------------------------------------
if st.session_state.get("trigger_rerun", False):
    st.session_state["trigger_rerun"] = False
    st.experimental_rerun()












