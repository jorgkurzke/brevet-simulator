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
        v_ms = max(v_kmh / 3.6, 0.1)

        dt = dist / v_ms
        new_time = times[-1] + dt

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
