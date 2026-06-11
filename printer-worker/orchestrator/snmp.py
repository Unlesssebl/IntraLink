import socket
import asyncio
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# OIDs for printer model discovery
OID_HR_DEVICE_DESCR = "1.3.6.1.2.1.25.3.2.1.3.1"
OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"


def build_snmp_get_request(oid: str, community: str = "public") -> bytes:
    parts = [int(x) for x in oid.split(".")]
    oid_bytes = bytearray()
    oid_bytes.append(parts[0] * 40 + parts[1])
    for part in parts[2:]:
        if part < 128:
            oid_bytes.append(part)
        else:
            temp = []
            while part > 0:
                temp.append(part & 0x7F)
                part >>= 7
            temp.reverse()
            for i in range(len(temp) - 1):
                oid_bytes.append(temp[i] | 0x80)
            oid_bytes.append(temp[-1])

    oid_encoded = bytes([0x06, len(oid_bytes)]) + bytes(oid_bytes)
    varbind = bytes([0x30, len(oid_encoded) + 2]) + oid_encoded + b"\x05\x00"
    varbind_list = bytes([0x30, len(varbind)]) + varbind
    pdu_payload = b"\x02\x01\x01\x02\x01\x00\x02\x01\x00" + varbind_list
    pdu = bytes([0xA0, len(pdu_payload)]) + pdu_payload
    comm_bytes = community.encode("utf-8")
    community_encoded = bytes([0x04, len(comm_bytes)]) + comm_bytes
    version_encoded = b"\x02\x01\x01"
    msg_payload = version_encoded + community_encoded + pdu
    msg = bytes([0x30, len(msg_payload)]) + msg_payload
    return msg


def parse_snmp_response(data: bytes) -> Optional[str]:
    try:
        idx = data.find(b"\xa2")
        if idx == -1:
            return None

        pos = idx + 1
        pdu_len = data[pos]
        pos += 1
        if pdu_len & 0x80:
            pos += pdu_len & 0x7F

        def skip_element(data: bytes, p: int) -> int:
            if p >= len(data):
                return p
            p += 1
            length = data[p]
            p += 1
            if length & 0x80:
                len_len = length & 0x7F
                payload_len = 0
                for _ in range(len_len):
                    payload_len = (payload_len << 8) | data[p]
                    p += 1
                p += payload_len
            else:
                p += length
            return p

        pos = skip_element(data, pos)  # Request ID
        pos = skip_element(data, pos)  # Error Status
        pos = skip_element(data, pos)  # Error Index

        if pos >= len(data) or data[pos] != 0x30:
            return None
        pos += 1
        seq_len = data[pos]
        pos += 1
        if seq_len & 0x80:
            pos += seq_len & 0x7F

        if pos >= len(data) or data[pos] != 0x30:
            return None
        pos += 1
        seq_len = data[pos]
        pos += 1
        if seq_len & 0x80:
            pos += seq_len & 0x7F

        if pos >= len(data) or data[pos] != 0x06:
            return None
        pos = skip_element(data, pos)  # OID

        if pos >= len(data):
            return None
        val_tag = data[pos]
        pos += 1
        val_len = data[pos]
        pos += 1
        if val_len & 0x80:
            len_len = val_len & 0x7F
            val_len_val = 0
            for i in range(len_len):
                val_len_val = (val_len_val << 8) | data[pos]
                pos += 1
            val_len = val_len_val

        if val_tag == 0x04:  # Octet String
            return data[pos : pos + val_len].decode("utf-8", errors="ignore")
        return None
    except Exception as e:
        logger.debug("parse_snmp_response error: %s | hex=%s", e, data.hex()[:80])
        return None


def resolve_hostname_sync(hostname: str) -> Optional[str]:
    # Check if already an IP
    if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", hostname):
        return hostname
    try:
        return socket.gethostbyname(hostname)
    except Exception:
        return None


async def resolve_hostname(hostname: str, timeout: float = 1.0) -> Optional[str]:
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(resolve_hostname_sync, hostname), timeout=timeout
        )
    except Exception:
        return None


def query_snmp_oid_sync(
    ip: str, oid: str, port: int = 161, community: str = "public", timeout: float = 1.5
) -> Optional[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    request_data = build_snmp_get_request(oid, community)
    try:
        sock.sendto(request_data, (ip, port))
        data, addr = sock.recvfrom(4096)
        return parse_snmp_response(data)
    except socket.timeout:
        return None
    except Exception as e:
        logger.debug("SNMP sync query error for %s: %s", ip, e)
        return None
    finally:
        sock.close()


async def query_snmp_oid(
    ip: str, oid: str, port: int = 161, community: str = "public", timeout: float = 1.5
) -> Optional[str]:
    return await asyncio.to_thread(
        query_snmp_oid_sync, ip, oid, port, community, timeout
    )


async def probe_printer_model(
    ip_or_host: str, community: str = "public", timeout: float = 1.5
) -> Optional[str]:
    """
    Пробует получить модель сетевого принтера по IP адресу или имени хоста с помощью SNMP.
    Сначала запрашивает OID_HR_DEVICE_DESCR, затем OID_SYS_DESCR.
    """
    logger.info("Попытка SNMP-автоопределения модели принтера на %s...", ip_or_host)

    # 1. Быстрое разрешение хоста с таймаутом
    ip = await resolve_hostname(ip_or_host, timeout=10.0)
    if not ip:
        logger.warning(
            "SNMP: Не удалось разрешить имя хоста '%s' или превышен таймаут", ip_or_host
        )
        return None

    # 2. Опрос OID
    model = await query_snmp_oid(
        ip, OID_HR_DEVICE_DESCR, community=community, timeout=timeout
    )
    if model:
        model = model.strip()
        logger.info("SNMP: hrDeviceDescr вернул '%s' для %s", model, ip_or_host)
        return model

    model = await query_snmp_oid(
        ip, OID_SYS_DESCR, community=community, timeout=timeout
    )
    if model:
        model = model.strip()
        logger.info("SNMP: sysDescr вернул '%s' для %s", model, ip_or_host)
        return model

    logger.warning(
        "SNMP-автоопределение модели принтера на %s не дало результатов", ip_or_host
    )
    return None
