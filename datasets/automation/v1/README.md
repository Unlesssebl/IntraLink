# Automation acceptance dataset v1

Это закрытый offline-набор приёмки ADR 0006. Он не является shadow-контуром и не вызывает IntraService, LDAP, WinRM или LLM.

- `intake.jsonl`: доступный на входе снимок заявки → эталонный `CaseDecision`.
- `action_selection.jsonl`: подтверждённые факты → эталонные workflow, disposition и capabilities.

В набор запрещено помещать пароли, токены, закрытые комментарии и сведения, появившиеся после принятия соответствующего решения. Новая версия набора создаётся в новом каталоге и после закрытой приёмки атомарно заменяет активную версию движка.

Запуск:

```powershell
.venv\Scripts\python.exe -m core.automation.replay --dataset-root datasets/automation/v1 --min-accuracy 1.0
```
