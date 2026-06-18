import sys
import os
import unittest

# Добавляем путь printer-worker в pythonpath
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orchestrator.snmp import parse_snmp_response, build_snmp_get_request


class TestSNMPParser(unittest.TestCase):
    def test_build_request(self):
        # Проверяем, что запрос строится без ошибок
        request = build_snmp_get_request("1.3.6.1.2.1.25.3.2.1.3.1", "public")
        self.assertIsInstance(request, bytes)
        self.assertGreater(len(request), 0)
        # Первые байты должны быть SEQUENCE (0x30)
        self.assertEqual(request[0], 0x30)

    def test_parse_simple_response(self):
        # Построим вручную простейший байтовый ответ SNMP GetResponse (0xa2)
        # Структура:
        # 0xa2 (GetResponse) - длина (short-form)
        #   Request ID: INTEGER (0x02), len=1, val=1
        #   Error Status: INTEGER (0x02), len=1, val=0
        #   Error Index: INTEGER (0x02), len=1, val=0
        #   Varbind List: SEQUENCE (0x30), len=20
        #     Varbind: SEQUENCE (0x30), len=18
        #       OID: OBJECT IDENTIFIER (0x06), len=8, val=...
        #       Value: OCTET STRING (0x04), len=6, val="Printer"

        oid_val = b"\x2b\x06\x01\x02\x01\x19\x03\x02"  # фиктивный OID
        oid_elem = b"\x06" + bytes([len(oid_val)]) + oid_val
        val_elem = b"\x04\x07Printer"

        varbind = b"\x30" + bytes([len(oid_elem) + len(val_elem)]) + oid_elem + val_elem
        varbind_list = b"\x30" + bytes([len(varbind)]) + varbind

        pdu_payload = b"\x02\x01\x01" + b"\x02\x01\x00" + b"\x02\x01\x00" + varbind_list
        pdu = b"\xa2" + bytes([len(pdu_payload)]) + pdu_payload

        # Полный пакет
        packet = (
            b"\x30" + bytes([len(pdu) + 10]) + b"\x02\x01\x01" + b"\x04\x06public" + pdu
        )

        res = parse_snmp_response(packet)
        self.assertEqual(res, "Printer")

    def test_parse_long_form_length_response(self):
        # Тестируем ситуацию, когда у нас есть длинная форма длины (long-form length) в элементах.
        # Например, Request ID имеет длинную форму длины или само строковое значение длинное.
        # Давайте сделаем длинный OCTET STRING (например, 130 байт).
        # Длина 130 кодируется как: 0x81 (1 байт длины) + 0x82 (значение 130)

        oid_val = b"\x2b\x06\x01\x02\x01\x19\x03\x02"
        oid_elem = b"\x06" + bytes([len(oid_val)]) + oid_val

        text_val = b"A" * 130
        val_elem = b"\x04\x81\x82" + text_val

        # Теперь соберем varbind. Его длина = len(oid_elem) + len(val_elem) = 10 + 133 = 143.
        # 143 в BER кодируется как: 0x81 (1 байт длины) + 0x8f (значение 143)
        varbind_len_bytes = b"\x81\x8f"
        varbind = b"\x30" + varbind_len_bytes + oid_elem + val_elem

        # varbind_list: длина 143 + 3 = 146.
        # 146 кодируется как: 0x81\x92
        varbind_list = b"\x30\x81\x92" + varbind

        # Request ID сделаем обычным, но обернем PDU.
        # pdu_payload: 3 + 3 + 3 + len(varbind_list) = 9 + 149 = 158.
        # 158 кодируется как: 0x81\x9e
        pdu_payload = b"\x02\x01\x01" + b"\x02\x01\x00" + b"\x02\x01\x00" + varbind_list
        pdu = b"\xa2\x81\x9e" + pdu_payload

        # Полный пакет
        packet = b"\x30\x81\xab" + b"\x02\x01\x01" + b"\x04\x06public" + pdu

        res = parse_snmp_response(packet)
        self.assertEqual(res, "A" * 130)

    def test_parse_long_form_request_id(self):
        # Проверяем, что skip_element корректно работает, если сам Request ID закодирован
        # с длинной формой длины (например, длина 4 байта).
        # Request ID: 0x02, длина 4 (или пусть даже закодированная длинным способом: 0x81\x04), значение: 0x12 0x34 0x56 0x78
        # По старому коду p += len_len пропустило бы только \x04, а 0x12 0x34 0x56 0x78 считалось бы следующим тегом!
        # С новым кодом это корректно пропустит и саму полезную нагрузку.

        oid_val = b"\x2b\x06\x01\x02\x01\x19\x03\x02"
        oid_elem = b"\x06" + bytes([len(oid_val)]) + oid_val
        val_elem = b"\x04\x07Printer"

        varbind = b"\x30" + bytes([len(oid_elem) + len(val_elem)]) + oid_elem + val_elem
        varbind_list = b"\x30" + bytes([len(varbind)]) + varbind

        # Request ID с длинной формой длины: 0x02 (INTEGER), 0x81 (длинная форма, 1 байт длины), 0x04 (длина=4), 0x12 0x34 0x56 0x78 (значение)
        req_id = b"\x02\x81\x04\x12\x34\x56\x78"
        pdu_payload = req_id + b"\x02\x01\x00" + b"\x02\x01\x00" + varbind_list
        pdu = b"\xa2" + bytes([len(pdu_payload)]) + pdu_payload

        packet = (
            b"\x30" + bytes([len(pdu) + 10]) + b"\x02\x01\x01" + b"\x04\x06public" + pdu
        )

        res = parse_snmp_response(packet)
        self.assertEqual(res, "Printer")

    def test_corrupted_response(self):
        # Проверка устойчивости к битым пакетам
        self.assertIsNone(parse_snmp_response(b""))
        self.assertIsNone(parse_snmp_response(b"\x30\x02\x01"))
        self.assertIsNone(
            parse_snmp_response(b"\x30\x0a\x02\x01\x01\x04\x06public\xa2\x00")
        )


if __name__ == "__main__":
    unittest.main()
