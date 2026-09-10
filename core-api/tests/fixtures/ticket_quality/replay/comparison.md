# Сравнение решений сценариев заявок (Baseline vs Candidate)

- **Baseline:** `decisions.json` (99 решений)
- **Candidate:** `decisions-after.json` (99 решений)

## Сводные метрики

| Метрика | Исходный Baseline | Candidate Replay | Дельта |
|---|---:|---:|---:|
| Всего решений | 99 | 99 | 0 |
| Точность на подтверждённой выборке | 0/4 | 4/4 | +4 |
| Запрещённые редиректы | 2 | 0 | -2 |
| Запрещённые допуски | 0 | 0 | +0 |
| Ошибочные уточнения устройства | 2 | 0 | -2 |
| Идентичные решения (0 diff) | - | 56/99 | - |
| Решения с различиями | - | 43/99 | - |

## Подтверждённые регрессии (4 контрольных случая)

| Заявка | Ожидалось | Baseline ошибки | Candidate ошибки | Статус |
|---|---|---|---|---|
| 139762 | pc_performance, network_diagnostics | scenario_mismatch, forbidden_redirect | — | ✅ Исправлено |
| 140408 | peripheral_setup | scenario_mismatch, wrong_device_clarification | — | ✅ Исправлено |
| 140511 | peripheral_diagnostics | scenario_mismatch, wrong_device_clarification | — | ✅ Исправлено |
| 140686 | os_reinstallation | scenario_mismatch, forbidden_redirect | — | ✅ Исправлено |

## Различия в неразмеченной выборке (needs_review)

> [!NOTE]
> Данные различия отражают семантические изменения логики или ограничения реконструкции входа (например, отсутствие сырого XML Data). Они не оцениваются автоматически как ошибка или успех до экспертной разметки.

| Заявка | Область различий | Baseline | Candidate |
|---|---|---|---|
| 138308 | `facts.pc_name` | `{'source': 'comment', 'source_ref': 'comment:sha256:5176db970a3dfbc0f191a6a54a5ff43e:pc_name', 'state': 'valid', 'value': 'NTEMW1000'}` | `{'source': 'comment', 'source_ref': 'comment:sha256:870f370b34adc70c5224af2a4f9a805b:pc_name', 'state': 'valid', 'value': 'NTEMW1000'}` |
| 138383 | `facts.pc_name` | `{'source': 'comment', 'source_ref': 'comment:sha256:6548d415be3243dd814f9430990a27bc:pc_name', 'state': 'valid', 'value': 'NTEMW1001'}` | `{'source': 'comment', 'source_ref': 'comment:sha256:92166ff17c4e8f1e68c911868a6b8007:pc_name', 'state': 'valid', 'value': 'NTEMW1001'}` |
| 138420 | `scenario_key` | `consultation` | `peripheral_setup` |
| 138420 | `outcome.kind` | `resolution` | `clarification` |
| 138420 | `outcome.outcome_key` | `in_work_standard` | `peripheral_clarify` |
| 138420 | `outcome.missing_fields` | `None` | `['pc_name']` |
| 138420 | `outcome.invalid_fields` | `None` | `[]` |
| 138420 | `policy.status_id` | `27` | `35` |
| 138420 | `policy.status_name` | `В работе` | `Требует уточнения` |
| 138420 | `gates.blocked_reasons` | `[]` | `['missing_fact:pc_name']` |
| 138470 | `facts.pc_name` | `{'source': 'structured_field', 'source_ref': 'field:1112', 'state': 'valid', 'value': 'KMK1002'}` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:pc_name', 'state': 'valid', 'value': 'KMK1002'}` |
| 138470 | `facts.printer_address` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:printer_address', 'state': 'valid', 'value': 'kmkp1003'}` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:printer_address', 'state': 'valid', 'value': 'kmkp1002'}` |
| 138509 | `scenario_key` | `consultation` | `pc_performance` |
| 138640 | `scenario_key` | `consultation` | `peripheral_setup` |
| 138640 | `outcome.kind` | `resolution` | `clarification` |
| 138640 | `outcome.outcome_key` | `in_work_standard` | `peripheral_clarify` |
| 138640 | `outcome.missing_fields` | `None` | `['pc_name']` |
| 138640 | `outcome.invalid_fields` | `None` | `[]` |
| 138640 | `policy.status_id` | `27` | `35` |
| 138640 | `policy.status_name` | `В работе` | `Требует уточнения` |
| 138640 | `gates.blocked_reasons` | `[]` | `['missing_fact:pc_name']` |
| 139194 | `scenario_key` | `consultation` | `peripheral_diagnostics` |
| 139194 | `outcome.kind` | `resolution` | `clarification` |
| 139194 | `outcome.outcome_key` | `in_work_standard` | `peripheral_clarify` |
| 139194 | `outcome.missing_fields` | `None` | `['pc_name']` |
| 139194 | `outcome.invalid_fields` | `None` | `[]` |
| 139194 | `policy.status_id` | `27` | `35` |
| 139194 | `policy.status_name` | `В работе` | `Требует уточнения` |
| 139194 | `gates.blocked_reasons` | `[]` | `['missing_fact:pc_name']` |
| 139241 | `outcome.kind` | `action` | `clarification` |
| 139241 | `outcome.outcome_key` | `install_printer_proposed` | `printer_ip_clarify` |
| 139241 | `outcome.action` | `install_printer` | `None` |
| 139241 | `outcome.parameters` | `{'pc_name': 'TNT1004', 'printer_ip': '192.0.2.1', 'printer_name': 'P LaserJet Pro MFP M435nw (), Canon ImageRunner C3025i (), Samsung Xpress M2070 (usb', 'printer_targets': [{'connection_type': 'network', 'printer_address': '192.0.2.1', 'printer_name': 'HP LaserJet Pro MFP M435nw'}, {'connection_type': 'network', 'printer_address': '192.0.2.2', 'printer_name': 'Canon ImageRunner C3025i'}, {'connection_type': 'usb', 'printer_address': None, 'printer_name': 'Samsung Xpress M2070'}]}` | `None` |
| 139241 | `outcome.missing_fields` | `None` | `['pc_name']` |
| 139241 | `outcome.invalid_fields` | `None` | `[]` |
| 139241 | `policy.status_id` | `None` | `35` |
| 139241 | `policy.status_name` | `None` | `Требует уточнения` |
| 139241 | `gates.can_execute_action` | `True` | `False` |
| 139241 | `gates.blocked_reasons` | `[]` | `['missing_fact:pc_name']` |
| 139241 | `facts.pc_name` | `{'source': 'structured_field', 'source_ref': 'field:1112', 'state': 'valid', 'value': 'TNT1004'}` | `None` |
| 139286 | `scenario_key` | `consultation` | `pc_performance` |
| 139317 | `outcome.kind` | `resolution` | `clarification` |
| 139317 | `outcome.outcome_key` | `in_work_standard` | `scan_connection_clarify` |
| 139317 | `outcome.missing_fields` | `None` | `['connection_type']` |
| 139317 | `outcome.invalid_fields` | `None` | `[]` |
| 139317 | `policy.status_id` | `27` | `35` |
| 139317 | `policy.status_name` | `В работе` | `Ожидание ответа` |
| 139317 | `policy.requires_approval` | `True` | `False` |
| 139317 | `gates.requires_approval` | `True` | `False` |
| 139317 | `gates.blocked_reasons` | `[]` | `['missing_fact:connection_type']` |
| 139561 | `scenario_key` | `consultation` | `pc_performance` |
| 139576 | `scenario_key` | `redirect` | `pc_performance` |
| 139576 | `outcome.outcome_key` | `wrong_service` | `in_work_standard` |
| 139576 | `policy.status_id` | `30` | `27` |
| 139576 | `policy.status_name` | `Отменена` | `В работе` |
| 139645 | `scenario_key` | `consultation` | `peripheral_setup` |
| 139645 | `outcome.kind` | `resolution` | `clarification` |
| 139645 | `outcome.outcome_key` | `in_work_standard` | `peripheral_clarify` |
| 139645 | `outcome.missing_fields` | `None` | `['pc_name']` |
| 139645 | `outcome.invalid_fields` | `None` | `[]` |
| 139645 | `policy.status_id` | `27` | `35` |
| 139645 | `policy.status_name` | `В работе` | `Требует уточнения` |
| 139645 | `gates.blocked_reasons` | `[]` | `['missing_fact:pc_name']` |
| 139689 | `facts.pc_name` | `{'source': 'structured_field', 'source_ref': 'field:1112', 'state': 'valid', 'value': 'KZM0028'}` | `{'source': 'comment', 'source_ref': 'comment:sha256:859b50f439cf687bcab6ea450baaaa1a:pc_name', 'state': 'valid', 'value': 'NTEMW1005'}` |
| 139791 | `scenario_key` | `consultation` | `peripheral_diagnostics` |
| 139791 | `outcome.kind` | `resolution` | `clarification` |
| 139791 | `outcome.outcome_key` | `in_work_standard` | `peripheral_clarify` |
| 139791 | `outcome.missing_fields` | `None` | `['pc_name']` |
| 139791 | `outcome.invalid_fields` | `None` | `[]` |
| 139791 | `policy.status_id` | `27` | `35` |
| 139791 | `policy.status_name` | `В работе` | `Требует уточнения` |
| 139791 | `gates.blocked_reasons` | `[]` | `['missing_fact:pc_name']` |
| 139971 | `scenario_key` | `consultation` | `redirect` |
| 139971 | `outcome.kind` | `resolution` | `manual_review` |
| 139971 | `outcome.outcome_key` | `in_work_standard` | `None` |
| 139971 | `policy.status_id` | `27` | `None` |
| 139971 | `policy.status_name` | `В работе` | `None` |
| 139971 | `policy.requires_approval` | `True` | `None` |
| 139971 | `gates.can_send_response` | `True` | `False` |
| 139971 | `gates.requires_approval` | `True` | `False` |
| 139971 | `gates.blocked_reasons` | `[]` | `['response_invalid', 'manual_review']` |
| 139971 | `facts.pc_name` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:pc_name', 'state': 'valid', 'value': '<private>'}` | `None` |
| 140063 | `scenario_key` | `consultation` | `pc_performance` |
| 140099 | `scenario_key` | `redirect` | `pc_performance` |
| 140099 | `outcome.outcome_key` | `wrong_service` | `in_work_standard` |
| 140099 | `policy.status_id` | `30` | `27` |
| 140099 | `policy.status_name` | `Отменена` | `В работе` |
| 140116 | `facts.pc_name` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:pc_name', 'state': 'valid', 'value': 'NTEMW1006'}` | `{'state': 'ambiguous'}` |
| 140124 | `facts.printer_address` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:printer_address', 'state': 'valid', 'value': 'kmkp1008'}` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:printer_address', 'state': 'valid', 'value': 'kmkp1007'}` |
| 140191 | `scenario_key` | `consultation` | `pc_performance` |
| 140233 | `outcome.kind` | `resolution` | `clarification` |
| 140233 | `outcome.outcome_key` | `in_work_standard` | `scan_connection_clarify` |
| 140233 | `outcome.missing_fields` | `None` | `['connection_type']` |
| 140233 | `outcome.invalid_fields` | `None` | `[]` |
| 140233 | `policy.status_id` | `27` | `35` |
| 140233 | `policy.status_name` | `В работе` | `Ожидание ответа` |
| 140233 | `policy.requires_approval` | `True` | `False` |
| 140233 | `gates.requires_approval` | `True` | `False` |
| 140233 | `gates.blocked_reasons` | `[]` | `['missing_fact:connection_type']` |
| 140237 | `scenario_key` | `consultation` | `peripheral_setup` |
| 140237 | `outcome.kind` | `resolution` | `clarification` |
| 140237 | `outcome.outcome_key` | `in_work_standard` | `peripheral_clarify` |
| 140237 | `outcome.missing_fields` | `None` | `['pc_name']` |
| 140237 | `outcome.invalid_fields` | `None` | `[]` |
| 140237 | `policy.status_id` | `27` | `35` |
| 140237 | `policy.status_name` | `В работе` | `Требует уточнения` |
| 140237 | `gates.blocked_reasons` | `[]` | `['missing_fact:pc_name']` |
| 140244 | `outcome.kind` | `action` | `clarification` |
| 140244 | `outcome.outcome_key` | `install_printer_proposed` | `printer_ip_clarify` |
| 140244 | `outcome.action` | `install_printer` | `None` |
| 140244 | `outcome.parameters` | `{'pc_name': 'TKT1009', 'printer_ip': 'tktp1011', 'printer_name': 'принтер Canon MF3010 и принтер этикеток COMPA2', 'printer_targets': [{'connection_type': 'usb', 'printer_address': None, 'printer_name': 'Canon MF3010'}, {'connection_type': 'network', 'printer_address': 'tktp1011', 'printer_name': 'COMPA2'}]}` | `None` |
| 140244 | `outcome.missing_fields` | `None` | `[]` |
| 140244 | `outcome.invalid_fields` | `None` | `['pc_name']` |
| 140244 | `policy.status_id` | `None` | `35` |
| 140244 | `policy.status_name` | `None` | `Требует уточнения` |
| 140244 | `gates.can_execute_action` | `True` | `False` |
| 140244 | `gates.blocked_reasons` | `[]` | `['invalid_fact:pc_name']` |
| 140244 | `facts.pc_name` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:pc_name', 'state': 'valid', 'value': 'TKT1009'}` | `{'state': 'ambiguous'}` |
| 140244 | `facts.printer_address` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:printer_address', 'state': 'valid', 'value': 'tktp1011'}` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:printer_address', 'state': 'valid', 'value': 'tktp1009'}` |
| 140244 | `facts.printer_targets` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:printer_targets', 'state': 'valid', 'value': [{'connection_type': 'usb', 'printer_address': None, 'printer_name': 'Canon MF3010'}, {'connection_type': 'network', 'printer_address': 'tktp1011', 'printer_name': 'COMPA2'}]}` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:printer_targets', 'state': 'valid', 'value': [{'connection_type': 'usb', 'printer_address': None, 'printer_name': 'Canon MF3010'}, {'connection_type': 'network', 'printer_address': 'tktp1009', 'printer_name': 'COMPA2'}]}` |
| 140263 | `facts.printer_name` | `{'source': 'structured_field', 'source_ref': 'field:1111', 'state': 'valid', 'value': 'Клавиатура'}` | `None` |
| 140294 | `outcome.kind` | `action` | `clarification` |
| 140294 | `outcome.outcome_key` | `install_printer_proposed` | `printer_ip_clarify` |
| 140294 | `outcome.action` | `install_printer` | `None` |
| 140294 | `outcome.parameters` | `{'pc_name': 'TKT1012', 'printer_ip': '192.0.2.3', 'printer_name': 'МФУ ECOSYS M5526cdw', 'printer_targets': [{'connection_type': 'network', 'printer_address': '192.0.2.3', 'printer_name': 'ECOSYS M5526cdw'}]}` | `None` |
| 140294 | `outcome.missing_fields` | `None` | `['pc_name']` |
| 140294 | `outcome.invalid_fields` | `None` | `[]` |
| 140294 | `policy.status_id` | `None` | `35` |
| 140294 | `policy.status_name` | `None` | `Требует уточнения` |
| 140294 | `gates.can_execute_action` | `True` | `False` |
| 140294 | `gates.blocked_reasons` | `[]` | `['missing_fact:pc_name']` |
| 140294 | `facts.pc_name` | `{'source': 'structured_field', 'source_ref': 'field:1112', 'state': 'valid', 'value': 'TKT1012'}` | `None` |
| 140300 | `facts.printer_address` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:printer_address', 'state': 'valid', 'value': 'kmkp1014'}` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:printer_address', 'state': 'valid', 'value': 'kmkp1013'}` |
| 140339 | `facts.pc_name` | `{'source': 'comment', 'source_ref': 'comment:sha256:b34aaf6679da27015c351466df1959c9:pc_name', 'state': 'valid', 'value': '192.0.2.4'}` | `{'state': 'ambiguous'}` |
| 140339 | `facts.printer_address` | `{'source': 'comment', 'source_ref': 'comment:sha256:b34aaf6679da27015c351466df1959c9:printer_address', 'state': 'valid', 'value': '192.0.2.4'}` | `{'source': 'comment', 'source_ref': 'comment:sha256:bef9409a25367af0f43cdc2095ebc3f3:printer_address', 'state': 'valid', 'value': '192.0.2.4'}` |
| 140420 | `facts.pc_name` | `{'source': 'structured_field', 'source_ref': 'field:1112', 'state': 'valid', 'value': 'NTEMW1019'}` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:pc_name', 'state': 'valid', 'value': 'NTEMW1019'}` |
| 140421 | `facts.printer_address` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:printer_address', 'state': 'valid', 'value': 'tktp1021'}` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:printer_address', 'state': 'valid', 'value': 'tktp1020'}` |
| 140467 | `scenario_key` | `consultation` | `os_reinstallation` |
| 140496 | `outcome.missing_fields` | `['printer_address']` | `['pc_name', 'printer_address']` |
| 140496 | `gates.blocked_reasons` | `['missing_fact:printer_address', 'invalid_fact:printer_targets']` | `['missing_fact:pc_name', 'missing_fact:printer_address', 'invalid_fact:printer_targets']` |
| 140496 | `facts.pc_name` | `{'source': 'structured_field', 'source_ref': 'field:1112', 'state': 'valid', 'value': 'KMK1023'}` | `None` |
| 140517 | `facts.pc_name` | `{'source': 'structured_field', 'source_ref': 'field:1112', 'state': 'valid', 'value': 'NTEMW1025'}` | `None` |
| 140593 | `facts.pc_name` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:pc_name', 'state': 'valid', 'value': 'KMK1007'}` | `None` |
| 140593 | `facts.printer_address` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:printer_address', 'state': 'valid', 'value': 'kmkp1008'}` | `None` |
| 140644 | `facts.pc_name` | `{'source': 'structured_field', 'source_ref': 'field:1112', 'state': 'valid', 'value': '<private>'}` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:pc_name', 'state': 'valid', 'value': 'NTEMW1027'}` |
| 140653 | `scenario_key` | `consultation` | `peripheral_setup` |
| 140653 | `outcome.kind` | `resolution` | `clarification` |
| 140653 | `outcome.outcome_key` | `in_work_standard` | `peripheral_clarify` |
| 140653 | `outcome.missing_fields` | `None` | `['pc_name']` |
| 140653 | `outcome.invalid_fields` | `None` | `[]` |
| 140653 | `policy.status_id` | `27` | `35` |
| 140653 | `policy.status_name` | `В работе` | `Требует уточнения` |
| 140653 | `gates.blocked_reasons` | `[]` | `['missing_fact:pc_name']` |
| 140660 | `scenario_key` | `consultation` | `pc_performance` |
| 140669 | `facts.pc_name` | `{'source': 'structured_field', 'source_ref': 'field:1112', 'state': 'valid', 'value': 'KMK1031'}` | `{'state': 'ambiguous'}` |
| 140669 | `facts.printer_address` | `{'source': 'comment', 'source_ref': 'comment:sha256:d64c294a1b20660e615fa82196583281:printer_address', 'state': 'valid', 'value': 'kmkp1032'}` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:printer_address', 'state': 'valid', 'value': 'kmkp1031'}` |
| 140669 | `facts.printer_targets` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:printer_targets', 'state': 'valid', 'value': [{'connection_type': 'network', 'printer_address': 'kmkp1032', 'printer_name': 'KYOCERA TASKalfa 1801 GX: ntemw1028'}]}` | `{'source': 'parser', 'source_ref': 'parser:ticket_text:printer_targets', 'state': 'valid', 'value': [{'connection_type': 'network', 'printer_address': 'kmkp1031', 'printer_name': 'KYOCERA TASKalfa 1801 GX: ntemw1028'}]}` |
| 140707 | `outcome.kind` | `resolution` | `clarification` |
| 140707 | `outcome.outcome_key` | `in_work_standard` | `printer_queue_clarify` |
| 140707 | `outcome.missing_fields` | `None` | `['pc_name']` |
| 140707 | `outcome.invalid_fields` | `None` | `[]` |
| 140707 | `policy.status_id` | `27` | `35` |
| 140707 | `policy.status_name` | `В работе` | `Ожидание ответа` |
| 140707 | `policy.requires_approval` | `True` | `False` |
| 140707 | `gates.requires_approval` | `True` | `False` |
| 140707 | `gates.blocked_reasons` | `[]` | `['missing_fact:pc_name']` |
