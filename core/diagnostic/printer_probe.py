"""Network-based printer and MFU model identifier (PJL, SNMP, HTTP).

Ground-truth hardware identification bypassing applicant typos in ticket text.
Supports:
  1. RAW Port 9100: PJL INFO ID inquiry (< 50ms).
  2. SNMP UDP 161: sysDescr / hrDeviceDescr query (< 50ms).
  3. HTTP Port 80: Web UI <title> scraper (< 100ms).
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
from typing import Optional

logger = logging.getLogger("core.diagnostic.printer_probe")

PJL_PROBE_TIMEOUT: float = 1.0
HTTP_PROBE_TIMEOUT: float = 1.0
SNMP_PROBE_TIMEOUT: float = 1.0

# PJL query command
PJL_QUERY = b"\x1b%-12345X@PJL \r\n@PJL INFO ID\r\n\x1b%-12345X"

# Common known vendor prefixes
VENDOR_NAMES = ("HP", "Kyocera", "Canon", "Xerox", "Brother", "Pantum", "Samsung", "Ricoh", "Konica Minolta", "Epson", "Lexmark")


def clean_printer_model_name(raw_name: str) -> str:
    """Normalize extracted hardware printer model string."""
    if not raw_name:
        return ""
    cleaned = raw_name.replace('"', "").replace("'", "").strip()
    # Remove PJL response wrapping e.g. "@PJL INFO ID\r\n" or "ID="
    cleaned = re.sub(r"(?i)^@pjl\s+info\s+id\s*", "", cleaned)
    cleaned = re.sub(r"(?i)^id\s*=\s*", "", cleaned)
    # Strip HTML tags if from HTTP
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    # Strip common web console prefixes like "Command Center RX", "SyncThru Web Service"
    cleaned = re.sub(r"(?i)^command\s+center\s+rx\s*[-:|]?\s*", "", cleaned)
    cleaned = re.sub(r"(?i)^syncthru\s*[-:|]?\s*", "", cleaned)
    cleaned = re.sub(r"(?i)^embedded\s+web\s+server\s*[-:|]?\s*", "", cleaned)
    cleaned = cleaned.strip(" \t\r\n-–—:|")
    return cleaned


async def probe_pjl_model(host: str, timeout_sec: float = PJL_PROBE_TIMEOUT) -> Optional[str]:
    """Inquire printer model over RAW port 9100 using PJL INFO ID."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, 9100),
            timeout=timeout_sec,
        )
        writer.write(PJL_QUERY)
        await writer.drain()

        # Read response up to 512 bytes
        data = await asyncio.wait_for(reader.read(512), timeout=timeout_sec)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        if not data:
            return None

        text = data.decode("latin1", errors="ignore").strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines:
            if "ID=" in line.upper():
                m = re.search(r'ID\s*=\s*"([^"]+)"', line, re.IGNORECASE)
                if m:
                    return clean_printer_model_name(m.group(1))
                parts = line.split("=", 1)
                if len(parts) > 1 and parts[1].strip():
                    return clean_printer_model_name(parts[1])
            # If line starts with a known vendor
            for v in VENDOR_NAMES:
                if line.upper().startswith(v.upper()):
                    return clean_printer_model_name(line)
        return None
    except Exception as exc:
        logger.debug("PJL probe failed on %s: %s", host, exc)
        return None


async def probe_http_title(host: str, timeout_sec: float = HTTP_PROBE_TIMEOUT) -> Optional[str]:
    """Inquire printer model from HTTP web management console title."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, 80),
            timeout=timeout_sec,
        )
        req = f"GET / HTTP/1.0\r\nHost: {host}\r\nUser-Agent: IntraLink-Probe\r\nConnection: close\r\n\r\n"
        writer.write(req.encode("ascii"))
        await writer.drain()

        raw_data = await asyncio.wait_for(reader.read(4096), timeout=timeout_sec)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        text = raw_data.decode("utf-8", errors="ignore")
        title_match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        if title_match:
            raw_title = title_match.group(1).strip()
            cleaned = clean_printer_model_name(raw_title)
            if cleaned and any(v.lower() in cleaned.lower() for v in VENDOR_NAMES):
                return cleaned
        return None
    except Exception as exc:
        logger.debug("HTTP probe failed on %s: %s", host, exc)
        return None


def _build_snmp_get_packet(oid_parts: list[int], community: str = "public") -> bytes:
    """Construct minimal ASN.1 SNMP v1 GetRequest packet for given OID."""
    # Encode OID
    oid_bytes = bytearray([oid_parts[0] * 40 + oid_parts[1]])
    for part in oid_parts[2:]:
        if part < 128:
            oid_bytes.append(part)
        else:
            buf = []
            while part > 0:
                buf.append(part & 0x7F)
                part >>= 7
            for i in range(len(buf) - 1, 0, -1):
                oid_bytes.append(buf[i] | 0x80)
            oid_bytes.append(buf[0])

    oid_tlv = bytes([0x06, len(oid_bytes)]) + bytes(oid_bytes)
    varbind = oid_tlv + bytes([0x05, 0x00])  # NULL value
    varbind_seq = bytes([0x30, len(varbind)]) + varbind
    varbind_list = bytes([0x30, len(varbind_seq)]) + varbind_seq

    req_id = bytes([0x02, 0x01, 0x01])  # INTEGER 1
    err_status = bytes([0x02, 0x01, 0x00])  # INTEGER 0
    err_index = bytes([0x02, 0x01, 0x00])  # INTEGER 0
    pdu_payload = req_id + err_status + err_index + varbind_list
    pdu = bytes([0xA0, len(pdu_payload)]) + pdu_payload

    ver = bytes([0x02, 0x01, 0x00])  # SNMP v1 (version 0)
    comm_bytes = community.encode("ascii")
    comm = bytes([0x04, len(comm_bytes)]) + comm_bytes
    snmp_msg = bytes([0x30, len(ver + comm + pdu)]) + ver + comm + pdu
    return snmp_msg


def _parse_snmp_string_response(data: bytes) -> Optional[str]:
    """Extract string value from SNMP response payload."""
    try:
        # Search for OCTET STRING tag (0x04) in variable binding value
        pos = data.find(b"\x04")
        while pos != -1 and pos + 2 <= len(data):
            str_len = data[pos + 1]
            if str_len > 0 and pos + 2 + str_len <= len(data):
                candidate = data[pos + 2 : pos + 2 + str_len].decode("latin1", errors="ignore")
                if any(v.lower() in candidate.lower() for v in VENDOR_NAMES):
                    return clean_printer_model_name(candidate)
            pos = data.find(b"\x04", pos + 1)
        return None
    except Exception:
        return None


async def probe_snmp_model(host: str, timeout_sec: float = SNMP_PROBE_TIMEOUT) -> Optional[str]:
    """Inquire printer model over SNMP UDP 161 (sysDescr 1.3.6.1.2.1.1.1.0)."""
    loop = asyncio.get_running_loop()
    try:
        # sysDescr OID: 1.3.6.1.2.1.1.1.0
        sysdescr_oid = [1, 3, 6, 1, 2, 1, 1, 1, 0]
        packet = _build_snmp_get_packet(sysdescr_oid, community="public")

        def _sync_udp_exchange() -> Optional[bytes]:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(timeout_sec)
                sock.sendto(packet, (host, 161))
                resp, _ = sock.recvfrom(2048)
                return resp

        resp_data = await loop.run_in_executor(None, _sync_udp_exchange)
        if resp_data:
            return _parse_snmp_string_response(resp_data)
        return None
    except Exception as exc:
        logger.debug("SNMP probe failed on %s: %s", host, exc)
        return None


class PrinterNetworkIdentifier:
    """Asynchronous multi-channel network device identifier for printers and MFUs."""

    @classmethod
    async def identify(cls, host_or_ip: Optional[str], timeout_sec: float = 1.5) -> Optional[str]:
        """Perform concurrent discovery via PJL (9100), SNMP (161) and HTTP (80).

        Returns canonical hardware model string or None.
        """
        if not host_or_ip or not host_or_ip.strip():
            return None

        target = host_or_ip.strip()

        # Step 1: RAW Port 9100 PJL probe (primary, most accurate)
        pjl_res = await probe_pjl_model(target, timeout_sec=min(timeout_sec, 0.8))
        if pjl_res:
            logger.info("Printer %s identified via PJL: '%s'", target, pjl_res)
            return pjl_res

        # Step 2: Concurrent SNMP + HTTP fallback
        snmp_task = probe_snmp_model(target, timeout_sec=min(timeout_sec, 0.7))
        http_task = probe_http_title(target, timeout_sec=min(timeout_sec, 0.7))

        snmp_res, http_res = await asyncio.gather(snmp_task, http_task, return_exceptions=True)

        if isinstance(snmp_res, str) and snmp_res:
            logger.info("Printer %s identified via SNMP: '%s'", target, snmp_res)
            return snmp_res

        if isinstance(http_res, str) and http_res:
            logger.info("Printer %s identified via HTTP: '%s'", target, http_res)
            return http_res

        return None
