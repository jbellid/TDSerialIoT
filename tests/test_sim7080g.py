import pytest

from tdserial_iot.sim7080g import (
    DEFAULT_SIGNAL_QUALITY_THRESHOLDS,
    FULL_DIAGNOSTIC_KEYS,
    MONITOR_KEYS,
    SIM7080G_COMMANDS,
    classify_signal_quality,
    classify_nbiot_signal,
    normalize_signal_technology,
    parse_command_response,
    parse_registration,
    parse_sim_state,
    resolve_signal_quality_thresholds,
)


def test_sim7080_diagnostic_command_sets_are_complete_and_monitor_is_limited():
    assert set(FULL_DIAGNOSTIC_KEYS) == set(SIM7080G_COMMANDS)
    assert MONITOR_KEYS == ("registration", "csq", "cpsi", "attach")


def test_sim_state_values_are_interpreted_without_unlocking():
    assert parse_sim_state([b"+CPIN: READY\r\n"]) == "SIM preparada"
    assert parse_sim_state([b"+CPIN: SIM PIN\r\n"]) == "PIN requerido"
    assert parse_sim_state([b"+CPIN: SIM PUK\r\n"]) == "PUK requerido"
    assert parse_sim_state([b"+CPIN: NOT INSERTED\r\n"]) == "SIM no disponible"
    assert parse_sim_state([b"ERROR\r\n"]) == "Error de lectura"
    assert parse_sim_state([]) == "N/D"


def test_cereg_uses_registration_state_not_first_parameter_and_parses_extended_fields():
    assert parse_registration([b"+CEREG: 0,5\r\n"]) == {
        "registration": "Registrado en roaming",
        "roaming": "Sí",
    }
    extended = parse_registration(
        [b'+CEREG: 2,1,"1234","00ABCDEF",9\r\n']
    )
    assert extended == {
        "registration": "Registrado en red local",
        "roaming": "No",
        "cell": "00ABCDEF",
        "access_technology": "LTE NB-S1 (IoT)",
    }
    assert parse_registration([b"+CEREG: 0,3\r\n"])["registration"] == "Registro rechazado"


def test_operator_keeps_unknown_commercial_identity_unidentified():
    result = parse_command_response("operator", [b'+COPS: 0,2,"46011",9\r\n'])
    assert result == {
        "operator": "46011",
        "operator_code": "460-11",
        "access_technology": "LTE NB-S1 (IoT)",
    }


def test_sim7080_lte_cpsi_uses_documented_fields_and_calculates_sinr_from_rssnr():
    result = parse_command_response(
        "cpsi",
        [
            b"+CPSI: LTE NB-IOT,Online,460-11,0x5AE1,187212754,82,"
            b"EUTRAN-BAND5,2506,0,0,-7,-115,-110,13\r\n"
        ],
    )
    assert result == {
        "technology": "LTE NB-IOT",
        "cpsi_state": "Online",
        "band": "LTE banda 5 (EARFCN 2506)",
        "cell": "187212754",
        "cpsi_mcc_mnc": "460-11",
        "rssi_measured": "-110 dBm",
        "rsrp": "-115 dBm",
        "rsrq": "-7 dB",
        "rssnr": "13",
        "sinr": "6 dB (calculado desde RSSNR 13)",
    }


def test_cat_m1_cpsi_is_detected_and_uses_same_documented_rssnr_conversion_once():
    result = parse_command_response(
        "cpsi",
        [
            b"+CPSI: LTE CAT-M1,Online,310-260,0x1234,987654321,42,"
            b"EUTRAN-BAND2,900,0,0,-10,-100,-95,13\r\n"
        ],
    )
    assert result["technology"] == "LTE CAT-M1"
    assert result["rsrp"] == "-100 dBm"
    assert result["rsrq"] == "-10 dB"
    assert result["rssnr"] == "13"
    assert result["sinr"] == "6 dB (calculado desde RSSNR 13)"

    classified = classify_signal_quality(
        result["rsrp"],
        result["rsrq"],
        result["sinr"],
        "Registrado en red local",
        result["technology"],
    )
    assert classified["sinr_quality"] == "Aceptable"
    assert classified["signal_quality"].startswith("Aceptable")


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("LTE NB-IOT", "NB-IoT"),
        ("LTE CAT-M1", "LTE-M"),
        ("No Service", None),
        ("LTE", None),
    ],
)
def test_cpsi_system_mode_maps_to_rat_or_remains_unknown(reported, expected):
    assert normalize_signal_technology(reported) == expected


def test_rat_signal_thresholds_are_independent_and_configurable():
    configured = {
        "NB-IoT": {"rsrp": [-80, -90, -105]},
        "LTE-M": {"rsrp": [-95, -105, -115]},
    }
    thresholds = resolve_signal_quality_thresholds(configured)
    assert thresholds["NB-IoT"]["rsrp"] == (-80, -90, -105)
    assert thresholds["LTE-M"]["rsrp"] == (-95, -105, -115)
    assert (
        classify_signal_quality(
            "-90 dBm", "N/D", "N/D", "Registrado en red local",
            "LTE NB-IOT", thresholds,
        )["rsrp_quality"]
        == "Buena"
    )
    assert (
        classify_signal_quality(
            "-90 dBm", "N/D", "N/D", "Registrado en red local",
            "LTE CAT-M1", thresholds,
        )["rsrp_quality"]
        == "Excelente"
    )
    assert DEFAULT_SIGNAL_QUALITY_THRESHOLDS["NB-IoT"] == (
        DEFAULT_SIGNAL_QUALITY_THRESHOLDS["LTE-M"]
    )
    assert DEFAULT_SIGNAL_QUALITY_THRESHOLDS["NB-IoT"] is not (
        DEFAULT_SIGNAL_QUALITY_THRESHOLDS["LTE-M"]
    )


def test_cpsi_missing_or_non_lte_signal_fields_are_unavailable_not_zero():
    incomplete = parse_command_response("cpsi", [b"+CPSI: LTE NB-IOT,Online\r\n"])
    assert incomplete["rsrp"] == "N/D"
    assert incomplete["rsrq"] == "N/D"
    non_lte = parse_command_response("cpsi", [b"+CPSI: No Service\r\n"])
    assert non_lte["technology"] == "No Service"
    assert non_lte["rsrp"] == "N/D"


def test_csq_99_is_unavailable_and_valid_rssi_is_explicitly_estimated():
    assert parse_command_response("csq", [b"+CSQ: 99,99\r\n"]) == {
        "csq": "N/D",
        "rssi": "N/D",
        "coverage": "N/D",
    }
    result = parse_command_response("csq", [b"+CSQ: 18,99\r\n"])
    assert result["csq"] == "18"
    assert result["rssi"] == "≈ -77 dBm (estimado desde CSQ)"
    assert result["coverage"] == "Aceptable (estimado desde CSQ)"


def test_nbiot_signal_quality_uses_worst_ordinal_and_reports_partial_metrics():
    result = classify_nbiot_signal(
        "-90 dBm",
        "-12 dB",
        "16 dB (calculado desde RSSNR 18)",
        "Registrado en red local",
    )
    assert result == {
        "nbiot_quality": "Aceptable (todas las métricas disponibles)",
        "rsrp_quality": "Buena",
        "rsrq_quality": "Aceptable",
        "sinr_quality": "Buena",
    }

    partial = classify_nbiot_signal(
        "-90 dBm", "N/D", "N/D", "Registrado en roaming"
    )
    assert partial == {
        "nbiot_quality": "Buena (diagnóstico parcial; N/D: RSRQ, SINR)",
        "rsrp_quality": "Buena",
        "rsrq_quality": "Desconocida",
        "sinr_quality": "Desconocida",
    }
    no_service = classify_nbiot_signal(
        "-75 dBm", "-5 dB", "25 dB", "No registrado"
    )
    assert no_service["nbiot_quality"] == (
        "Sin servicio (todas las métricas disponibles)"
    )


def test_data_contexts_keep_cid_and_application_contexts_separate():
    contexts = parse_command_response(
        "contexts",
        [
            b'+CGDCONT: 1,"IP","iot.1nce.net","0.0.0.0",0,0\r\n',
            b"OK\r\n",
        ],
    )
    pdp = parse_command_response("pdp", [b"+CGACT: 1,1\r\n"])
    app = parse_command_response(
        "app_context",
        [b'+CNACT: 0,1,"10.0.0.1"\r\n', b'+CNACT: 1,0,"0.0.0.0"\r\n'],
    )
    ip = parse_command_response("ip", [b"+CGPADDR: 1,10.0.0.1\r\n"])
    assert contexts == {
        "pdp": "CID 1: IP",
        "apn": "CID 1: iot.1nce.net",
    }
    assert pdp["pdp_state"] == "CID 1: activo"
    assert "Contexto de aplicación 0: activo" in app["app_context"]
    assert ip["ip"] == "10.0.0.1"


def test_socket_status_never_opens_or_closes_connections():
    assert parse_command_response("sockets", [b"OK\r\n"]) == {
        "sockets": "Sin conexiones activas"
    }
    assert parse_command_response(
        "sockets", [b"+CASTATE: 0,1\r\n", b"+CASTATE: 1,0\r\n"]
    ) == {"sockets": "Socket 0: estado 1"}
