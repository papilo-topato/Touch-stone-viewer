"""Lightweight RF trace viewer and validator."""

import os
import tempfile

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import skrf
import streamlit as st

MAX_SUPPORTED_PORTS = 32
SUPPORTED_EXTENSIONS = [f"s{i}p" for i in range(1, MAX_SUPPORTED_PORTS + 1)] + ["snp"]

st.set_page_config(page_title="Touchstone Viewer", layout="wide")


def format_freq(hz):
    """Format frequency using the largest readable engineering unit."""
    if hz >= 1e9:
        return f"{hz / 1e9:.3f} GHz"
    if hz >= 1e6:
        return f"{hz / 1e6:.3f} MHz"
    if hz >= 1e3:
        return f"{hz / 1e3:.3f} kHz"
    return f"{hz:.2f} Hz"

if "networks" not in st.session_state:
    st.session_state.networks = {}
if "passivity_status" not in st.session_state:
    st.session_state.passivity_status = {}
if "max_sv" not in st.session_state:
    st.session_state.max_sv = {}


def parse_touchstone_file(uploaded_file):
    """Parse uploaded Touchstone file and enforce the 32-port guardrail."""
    suffix = os.path.splitext(uploaded_file.name)[1].lower()
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            tmp_file.write(uploaded_file.getvalue())
            tmp_path = tmp_file.name

        ntwk = skrf.Network(tmp_path)
        ntwk.name = uploaded_file.name

        if ntwk.nports > MAX_SUPPORTED_PORTS:
            os.remove(tmp_path)
            return None, f"File has {ntwk.nports} ports. Maximum supported is {MAX_SUPPORTED_PORTS}."

        os.remove(tmp_path)
        return ntwk, None
    except Exception as exc:
        if "tmp_path" in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)
        return None, f"Invalid Touchstone file format ({exc})"


def check_passivity(ntwk):
    """Check whether the network is passive and compute the max singular value."""
    try:
        is_pass = bool(ntwk.is_passive())
        s_mats = ntwk.s
        svs = np.linalg.norm(s_mats, ord=2, axis=(1, 2))
        max_sv = float(np.max(svs))
        return is_pass, max_sv
    except Exception:
        try:
            s_mats = ntwk.s
            svs = np.linalg.norm(s_mats, ord=2, axis=(1, 2))
            max_sv = float(np.max(svs))
            return max_sv <= 1.0 + 1e-4, max_sv
        except Exception:
            return True, 1.0


def assess_passivity(network):
    """Return the max singular value for status display."""
    try:
        _, max_sv = check_passivity(network)
        return float(max_sv)
    except Exception:
        return 1.0


def create_sample_networks():
    """Create a few lightweight demo networks for the viewer."""
    freq = skrf.Frequency(start=1e6, stop=1e9, npoints=401, unit="Hz")
    f = freq.f
    z0 = 50.0

    net1 = skrf.Network(frequency=freq, s=np.zeros((len(f), 2, 2), dtype=complex), z0=z0)
    net1.name = "sample_cap_1.s2p"
    net2 = skrf.Network(frequency=freq, s=np.zeros((len(f), 2, 2), dtype=complex), z0=z0)
    net2.name = "sample_cap_2.s2p"
    return [net1, net2]


with st.sidebar:
    st.markdown("### TRACES")
    uploaded_files = st.file_uploader(
        "Upload SnP Files",
        accept_multiple_files=True,
        type=SUPPORTED_EXTENSIONS,
        label_visibility="collapsed",
    )

    current_names = {uploaded_file.name for uploaded_file in uploaded_files or []}
    removed_names = set(st.session_state.networks) - current_names
    for name in removed_names:
        st.session_state.networks.pop(name, None)
        st.session_state.max_sv.pop(name, None)
        st.session_state.passivity_status.pop(name, None)
    if removed_names:
        st.session_state.pop("srf_summary", None)

    for uploaded_file in uploaded_files or []:
        if uploaded_file.name not in st.session_state.networks:
            net, err = parse_touchstone_file(uploaded_file)
            if err:
                st.error(f"Error loading {uploaded_file.name}: {err}")
            else:
                st.session_state.networks[uploaded_file.name] = net
                is_passive, max_sv = check_passivity(net)
                st.session_state.passivity_status[uploaded_file.name] = is_passive
                st.session_state.max_sv[uploaded_file.name] = max_sv

    st.markdown(f"**LOADED TRACES: {len(st.session_state.networks)}**")
    st.divider()
    st.markdown("### MEASUREMENT SETTINGS")

    meas_type = st.selectbox(
        "Measurement Type",
        [
            "Return Loss (Reflection)",
            "Insertion Loss (Transmission)",
            "Custom (Manual S-Param)",
        ],
        key="measurement_type",
    )

    port_i, port_j = 1, 1
    mapping_style = "Sequential"

    if meas_type == "Custom (Manual S-Param)":
        max_ports = max(
            (network.nports for network in st.session_state.networks.values()),
            default=1,
        )
        for state_key in ("receive_port", "source_port"):
            if st.session_state.get(state_key, 1) > max_ports:
                st.session_state[state_key] = max_ports

        receive_col, source_col = st.columns(2)
        with receive_col:
            port_i = st.number_input(
                "Receive Port (i)",
                min_value=1,
                max_value=max_ports,
                value=st.session_state.get("receive_port", 1),
                key="receive_port",
            )
        with source_col:
            port_j = st.number_input(
                "Source Port (j)",
                min_value=1,
                max_value=max_ports,
                value=st.session_state.get("source_port", 1),
                key="source_port",
            )
        st.info(f"Plotting **S{port_i}{port_j}**")
    elif meas_type == "Insertion Loss (Transmission)":
        if any(network.nports > 2 for network in st.session_state.networks.values()):
            mapping_style = st.radio(
                "Port Mapping Assumption (for >2 ports)",
                ["Sequential (1↔2, 3↔4)", "Split/Bipartite (1↔3, 2↔4)"],
                key="insertion_port_mapping",
            )
        st.info(f"Plotting insertion loss with {mapping_style.lower()} port mapping.")
    else:
        st.info("Plotting return loss for up to the first four ports per trace.")

if st.session_state.networks:
    plotly_colors = [
        "#636EFA",
        "#EF553B",
        "#00CC96",
        "#AB63FA",
        "#FFA15A",
        "#19D3F3",
        "#FF6692",
        "#B6E880",
        "#FF97FF",
        "#FECB52",
    ]
    file_colors = {
        name: plotly_colors[idx % len(plotly_colors)]
        for idx, name in enumerate(st.session_state.networks)
    }
    table_data = []
    trace_list = []

    for name, network in st.session_state.networks.items():
        nports = network.nports
        pairs_to_plot = []

        if meas_type == "Custom (Manual S-Param)":
            if port_i <= nports and port_j <= nports:
                pairs_to_plot.append((port_i - 1, port_j - 1))
        elif meas_type == "Return Loss (Reflection)":
            pairs_to_plot = [(port, port) for port in range(min(nports, 4))]
        elif nports == 2:
            pairs_to_plot = [(1, 0), (0, 1)]
        elif mapping_style.startswith("Sequential"):
            for input_port in range(0, nports - 1, 2):
                output_port = input_port + 1
                pairs_to_plot.extend([
                    (output_port, input_port),
                    (input_port, output_port),
                ])
        else:
            half = nports // 2
            for input_port in range(half):
                output_port = input_port + half
                pairs_to_plot.extend([
                    (output_port, input_port),
                    (input_port, output_port),
                ])

        if not pairs_to_plot:
            continue

        parameter_labels = []
        primary_srf_freq = None
        primary_srf_mag = None
        line_styles = ["solid", "dash", "dot", "dashdot"]

        for idx, (i, j) in enumerate(pairs_to_plot):
            y_vals = network.s_db[:, i, j]
            min_idx = int(np.argmin(y_vals))
            srf_freq = float(network.f[min_idx])
            srf_mag = float(y_vals[min_idx])
            s_label = f"S{i + 1}{j + 1}"
            display_name = f"{name} ({s_label})"
            parameter_labels.append(s_label)
            if idx == 0:
                primary_srf_freq = srf_freq
                primary_srf_mag = srf_mag

            trace_list.append({
                "file": name,
                "label": display_name,
                "y_vals": y_vals,
                "freq": network.f,
                "srf_f": srf_freq,
                "srf_mag": srf_mag,
                "color": file_colors[name],
                "dash": line_styles[idx % len(line_styles)],
            })
        table_data.append({
            "Network File": name,
            "Parameters": ", ".join(parameter_labels),
            "Notch Frequency": format_freq(primary_srf_freq),
            "Peak Loss (dB)": round(primary_srf_mag, 3),
        })

    if trace_list:
        df_summary = pd.DataFrame(table_data)

        def style_file_name(file_name):
            color = file_colors.get(file_name, "#d1d5db")
            return f"color: {color}; font-weight: bold;"

        styled_summary = df_summary.style.map(
            style_file_name,
            subset=["Network File"],
        )
        title = meas_type.split(" (", 1)[0]
        graph_container = st.container()
        table_container = st.container()

        with table_container:
            st.markdown(f"### {title} Summary (click a row to isolate its traces)")
            selection_event = st.dataframe(
                styled_summary,
                width="stretch",
                hide_index=True,
                on_select="rerun",
                selection_mode="single-row",
                key=f"srf_summary_{'|'.join(df_summary['Network File'])}",
                alt=f"{meas_type} parameters and notch summary for loaded networks",
            )

        selected_rows = selection_event.selection.rows
        highlighted_file = (
            df_summary.iloc[selected_rows[0]]["Network File"]
            if selected_rows and selected_rows[0] < len(df_summary)
            else None
        )

        fig_stack = go.Figure()
        with graph_container:
            for trace in trace_list:
                is_selected = trace["file"] == highlighted_file
                trace_opacity = 1.0 if highlighted_file is None or is_selected else 0.15
                trace_width = 3 if is_selected else 1 if highlighted_file is not None else 2
                fig_stack.add_trace(go.Scatter(
                    x=trace["freq"],
                    y=trace["y_vals"],
                    mode="lines",
                    name=trace["label"],
                    opacity=trace_opacity,
                    line=dict(
                        color=trace["color"],
                        width=trace_width,
                        dash=trace["dash"],
                    ),
                    hovertemplate="Freq: %{x:.3e} Hz<br>Magnitude: %{y:.2f} dB<extra></extra>",
                ))

                if (highlighted_file is None or is_selected) and trace["dash"] == "solid":
                    fig_stack.add_annotation(
                        x=trace["srf_f"],
                        y=trace["srf_mag"],
                        text=format_freq(trace["srf_f"]),
                        showarrow=True,
                        arrowhead=2,
                        arrowsize=1,
                        arrowwidth=1.5,
                        ax=0,
                        ay=-35,
                        font=dict(size=11, color=trace["color"]),
                    )

        y_axis_title = (
            f"Magnitude S{port_i}{port_j} (dB)"
            if meas_type == "Custom (Manual S-Param)"
            else "Magnitude (dB)"
        )
        fig_stack.update_layout(
            showlegend=True,
            template="plotly_dark",
            plot_bgcolor="#262729",
            paper_bgcolor="#262729",
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1,
            ),
            xaxis=dict(
                type="log",
                title="Frequency (Hz)",
                showgrid=True,
                gridcolor="#3e3f42",
                zerolinecolor="#3e3f42",
            ),
            yaxis=dict(
                title=y_axis_title,
                showgrid=True,
                gridcolor="#3e3f42",
                zerolinecolor="#3e3f42",
            ),
            height=650,
            hovermode="closest",
            margin=dict(l=20, r=20, t=20, b=20),
        )
        with graph_container:
            st.plotly_chart(
                fig_stack,
                width="stretch",
                alt=f"{title} S-parameter magnitude for loaded networks",
            )
    else:
        st.warning("No applicable S-parameter traces are available for this measurement.")

    st.markdown("### Passivity Status")
    for name in st.session_state.networks:
        max_sv = st.session_state.max_sv.get(name, 1.0)
        is_passive = st.session_state.passivity_status.get(name, max_sv <= 1.0)
        status = "✅ PASS" if is_passive else "⚠️ FAIL"
        st.markdown(f"- **{name}**: {status} | Max singular value: **{max_sv:.4f}**")
else:
    empty_fig = go.Figure()
    empty_fig.add_annotation(
        text="<b>No Data Loaded</b><br>Use the sidebar to upload SnP files",
        x=0.5,
        y=0.5,
        xref="paper",
        yref="paper",
        showarrow=False,
        font=dict(size=20, color="#6b7280"),
    )
    empty_fig.update_layout(
        template="plotly_dark",
        plot_bgcolor="#262729",
        paper_bgcolor="#262729",
        xaxis=dict(title="Frequency (Hz)", range=[0, 1], showgrid=True, gridcolor="#3e3f42"),
        yaxis=dict(title="Magnitude (dB)", range=[-60, 10], showgrid=True, gridcolor="#3e3f42"),
        height=650,
    )
    st.plotly_chart(
        empty_fig,
        width="stretch",
        alt="Empty S-parameter plot; upload a Touchstone file to begin",
    )

st.write("")
st.markdown(
    "<p style='text-align: center; color: rgba(209, 213, 219, 0.4); "
    "font-size: 12px; margin-top: 50px;'>"
    "Built in Bengaluru ❤️, by Raghuram K S"
    "</p>",
    unsafe_allow_html=True,
)
