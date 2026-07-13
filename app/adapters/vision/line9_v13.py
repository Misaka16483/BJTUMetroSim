"""Authoritative Line 9 ordering/ranges transcribed from Vision Version 1.3."""

from __future__ import annotations

from dataclasses import dataclass


# The laboratory capture uses 92 signal bytes and 40 switch bytes.  The
# supplied Version 1.3 tables identify only 77/29 of them; the remaining wire
# slots are reserved until their field ordering is confirmed on site.
LINE9_WIRE_SIGNAL_COUNT = 92
LINE9_WIRE_SWITCH_COUNT = 40


@dataclass(frozen=True)
class ProtocolSignal:
    name: str
    protocol_id: str
    mileage_m: float
    direction: str


@dataclass(frozen=True)
class ProtocolSwitch:
    name: str
    protocol_id: str
    mileage_m: float


@dataclass(frozen=True)
class ProtocolEdge:
    edge_id: int
    section_id: str
    begin_m: float
    end_m: float


# Table 2 / Attachment 3 ordering. The receiver consumes states in this exact
# order; names are documentation only and are not included in the UDP payload.
LINE9_SIGNALS_V13: tuple[ProtocolSignal, ...] = tuple(
    ProtocolSignal(*item)
    for item in (
        ("Z0121", "0121", 30.0, "Reverse"),
        ("Z0120", "0120", 30.0, "Reverse"),
        ("X0103", "0103", 226.81, "Forward"),
        ("F1", "0151", 85.40987, "Reverse"),
        ("F2", "0152", 77.05, "Reverse"),
        ("S0106", "0106", 226.81, "Forward"),
        ("X0105", "0105", 307.5, "Reverse"),
        ("F5", "0107", 436.5, "Forward"),
        ("S0108", "0108", 307.5, "Reverse"),
        ("SC2", "0110", 436.5, "Forward"),
        ("X0109", "0109", 518.19, "Reverse"),
        ("X0118", "0118", 700.041, "Forward"),
        ("X0201", "0201", 1659.64, "Reverse"),
        ("X0301", "0301", 2448.177, "Reverse"),
        ("X0401", "0401", 3427.332, "Reverse"),
        ("X0403", "0403", 4261.672, "Reverse"),
        ("X0501", "0501", 5015.0146, "Reverse"),
        ("S0112", "0112", 518.19, "Reverse"),
        ("S0116", "0116", 705.22, "Forward"),
        ("S0202", "0202", 1784.02, "Forward"),
        ("S0302", "0302", 2572.11, "Forward"),
        ("S0402", "0402", 3552.82, "Forward"),
        ("S0404", "0404", 4303.62, "Forward"),
        ("S0503", "0503", 5010.334, "Reverse"),
        ("S0502", "0502", 5139.334, "Forward"),
        ("X0504", "0504", 5281.895, "Reverse"),
        ("X0506", "0506", 5410.895, "Forward"),
        ("X0510", "0510", 5589.655, "Reverse"),
        ("X0601", "0601", 6340.77, "Reverse"),
        ("X0603", "0603", 6856.37, "Reverse"),
        ("X0701", "0701", 8134.603, "Reverse"),
        ("X0703", "0703", 8263.603, "Forward"),
        ("S0508", "0508", 5889.124, "Forward"),
        ("S0602", "0602", 6464.774, "Forward"),
        ("S0604", "0604", 7162.374, "Forward"),
        ("S0704", "0704", 8114.704, "Reverse"),
        ("S0702", "0702", 8243.704, "Forward"),
        ("X0705", "0705", 8406.723, "Reverse"),
        ("S0710", "0710", 8576.184, "Forward"),
        ("X0706", "0706", 8694.00248, "Reverse"),
        ("X0714", "0714", 9343.80248, "Forward"),
        ("X0801", "0801", 9444.99248, "Reverse"),
        ("X0901", "0901", 10591.67848, "Reverse"),
        ("X0902", "0902", 10720.67848, "Forward"),
        ("X0910", "0910", 11004.60848, "Forward"),
        ("X0912", "0912", 11261.43848, "Reverse"),
        ("X1001", "1001", 11993.37933, "Reverse"),
        ("X1003", "1003", 12820.90949, "Reverse"),
        ("X1101", "1101", 13903.17949, "Reverse"),
        ("X1102", "1102", 14032.18649, "Forward"),
        ("X0707", "0707", 8443.19792, "Reverse"),
        ("X0708", "0708", 8574.81792, "Forward"),
        ("S0711", "0711", 8639.564, "Reverse"),
        ("S0712", "0712", 8887.154, "Forward"),
        ("S0713", "0713", 8987.154, "Reverse"),
        ("S0802", "0802", 9552.844, "Forward"),
        ("S0903", "0903", 10594.614, "Reverse"),
        ("S0904", "0904", 10723.614, "Forward"),
        ("S0911", "0911", 11007.544, "Forward"),
        ("S0913", "0913", 11801.374, "Forward"),
        ("S1002", "1002", 12122.57, "Forward"),
        ("S1003", "1004", 13032.1, "Forward"),
        ("S1103", "1103", 13905.78014, "Reverse"),
        ("S1104", "1104", 14034.78014, "Forward"),
        ("X1105", "1105", 14095.13649, "Reverse"),
        ("X1201", "1201", 14948.01649, "Reverse"),
        ("X1301", "1301", 16038.97149, "Reverse"),
        ("X1302", "1302", 16167.97149, "Forward"),
        ("X1106", "1106", 14189.15854, "Reverse"),
        ("X1107", "1107", 14320.05023, "Forward"),
        ("S1108", "1108", 14407.14, "Reverse"),
        ("S1109", "1109", 14654.73, "Forward"),
        ("S1202", "1202", 15078.41, "Forward"),
        ("S1303", "1303", 16045.52, "Reverse"),
        ("S1304", "1304", 16174.52, "Forward"),
        ("X1305", "1305", 16277.76149, "Reverse"),
        ("S1316", "1316", 16282.21, "Reverse"),
    )
)


# Table 3 ordering. Mileages come from the edge attachment and are used only
# to match the legacy names to current electronic-map switch identifiers.
LINE9_SWITCHES_V13: tuple[ProtocolSwitch, ...] = tuple(
    ProtocolSwitch(*item)
    for item in (
        ("P_01A", "0101", 215.49),
        ("P_02A", "0102", 70.05),
        ("P_03A", "0103", 232.81),
        ("P_04A", "0104", 215.49),
        ("P_05A", "0105", 300.19),
        ("P_06A", "0106", 232.81),
        ("P_07A", "0107", 443.81),
        ("P_08A", "0108", 300.19),
        ("P_09A", "0109", 511.19),
        ("P_10A", "0110", 443.81),
        ("P_12A", "0112", 511.19),
        ("SW0501", "0501", 5274.8396),
        ("SW0502", "0502", 5146.644),
        ("SW0701", "0701", 8346.083),
        ("SW0702", "0702", 8352.184),
        ("SW0703", "0703", 8687.02908),
        ("SW0704", "0704", 8436.19792),
        ("SW0708", "0708", 8581.81792),
        ("SW0710", "0710", 8617.40792),
        ("SW0712", "0712", 8632.564),
        ("SW1101", "1101", 14039.49649),
        ("SW1102", "1102", 14119.63),
        ("SW1104", "1104", 14176.15854),
        ("SW1106", "1106", 14327.05088),
        ("SW1108", "1108", 14400.14),
        ("SW1301", "1301", 16203.38149),
        ("SW1302", "1302", 16207.82966),
        ("SW1303", "1303", 16270.76149),
        ("SW1304", "1304", 16268.92262),
    )
)


# The station-to-station engine runs on these two mainline chains. Position in
# the packet is always measured from the edge's BeginKm, even for reverse runs.
UP_MAINLINE_EDGES: tuple[ProtocolEdge, ...] = tuple(
    ProtocolEdge(*item)
    for item in (
        (11, "01050107", 300.19, 443.81),
        (14, "01070109", 443.81, 511.19),
        (17, "01090501", 511.19, 5274.8396),
        (21, "05010701", 5274.8396, 8346.083),
        (24, "07010703", 8346.083, 8687.02908),
        (28, "07031101", 8687.02908, 14039.49649),
        (36, "11011301", 14039.49649, 16203.38149),
        (43, "13011303", 16203.38149, 16270.76149),
        (47, "13032F01", 16270.76149, 16484.57149),
    )
)

DOWN_MAINLINE_EDGES: tuple[ProtocolEdge, ...] = tuple(
    ProtocolEdge(*item)
    for item in (
        (16, "01080110", 300.19, 443.81),
        (19, "01100112", 443.81, 511.19),
        (20, "01120502", 511.19, 5146.644),
        (23, "05020702", 5146.644, 8352.184),
        (27, "07020712", 8352.184, 8632.564),
        (34, "07121102", 8632.564, 14119.63),
        (38, "11021108", 14119.63, 14400.14),
        (42, "11081302", 14400.14, 16207.82966),
        (46, "13021304", 16207.82966, 16275.20966),
        (48, "13042F02", 16275.20966, 16489.02),
    )
)
