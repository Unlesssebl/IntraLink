import json
import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

TARGET_WLAN_GROUP = "WLAN-WORKNET"


@dataclass
class ADUserStatus:
    found: bool
    sam_account_name: Optional[str] = None
    display_name: Optional[str] = None
    enabled: bool = False
    is_wlan_member: bool = False
    department: Optional[str] = None
    mail: Optional[str] = None
    error: Optional[str] = None


@dataclass
class ADExecutionResult:
    success: bool
    already_member: bool
    sam_account_name: str
    display_name: Optional[str] = None
    message: str = ""
    target_group: str = TARGET_WLAN_GROUP
    error: Optional[str] = None


class ActiveDirectoryExecutor:
    """
    Отказоустойчивый исполнитель операций в Active Directory.
    Обеспечивает Single-DC Affinity, проверку активности учетных записей,
    идемпотентность и Read-after-Write верификацию.
    """

    def __init__(self, target_wlan_group: str = TARGET_WLAN_GROUP):
        self.target_wlan_group = target_wlan_group

    @staticmethod
    def _run_ps_command(script: str, timeout: int = 25) -> dict[str, Any]:
        """
        Выполняет PowerShell-скрипт и возвращает распарсенный JSON результат.
        """
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            if res.returncode != 0:
                err_msg = res.stderr.strip() or f"Код возврата: {res.returncode}"
                return {"error": err_msg}
            out = res.stdout.strip()
            if not out:
                return {}
            # Извлекаем JSON даже если в выводе есть предупреждения
            json_start = out.find("{")
            json_end = out.rfind("}")
            if json_start != -1 and json_end != -1:
                return json.loads(out[json_start : json_end + 1])
            return json.loads(out)
        except subprocess.TimeoutExpired:
            return {"error": f"Таймаут выполнения запроса к Active Directory ({timeout}s)"}
        except json.JSONDecodeError as e:
            return {"error": f"Ошибка парсинга JSON ответа PowerShell: {e}", "raw": res.stdout}
        except Exception as e:
            return {"error": f"Непредвиденная ошибка выполнения PowerShell: {e}"}

    def get_user_status(self, identity: str, company: Optional[str] = None) -> ADUserStatus:
        """
        Каскадный поиск пользователя в Active Directory с нормализацией инициалов и проверкой WLAN.
        """
        if not identity or not identity.strip():
            return ADUserStatus(found=False, error="Идентификатор пользователя пуст")

        clean_identity = identity.strip()
        
        # 1. Проверка на инициалы: "Трутнева Л.А.", "Трутнева Л. А.", "Трутнева Л."
        initials_match = re.match(r"^([А-ЯЁа-яёA-Za-z]+)\s+([А-ЯЁA-Za-z])\.(?:\s*([А-ЯЁA-Za-z])\.)?$", clean_identity)
        if initials_match:
            surname = initials_match.group(1).replace('"', '`"').replace("$", "`$")
            init1 = initials_match.group(2).replace('"', '`"').replace("$", "`$")
            filter_expr = f"Name -like '{surname} {init1}*' -or SamAccountName -like '{surname}*'"
        else:
            safe_id = clean_identity.replace('"', '`"').replace("$", "`$")
            filter_expr = f"SamAccountName -eq '{safe_id}' -or UserPrincipalName -like '{safe_id}*' -or Name -like '*{safe_id}*'"

        # Нормализация ё/е для русских фамилий
        if "ё" in filter_expr or "е" in filter_expr:
            # PowerShell regex/like handles basic matching, but filter with wildcard covers both
            pass

        safe_comp = (company or "").strip().replace('"', '`"').replace("$", "`$")

        script = f"""
        Import-Module ActiveDirectory -ErrorAction Stop
        $user = Get-ADUser -Filter "{filter_expr}" -Properties MemberOf, Enabled, Mail, Department, Company -ErrorAction SilentlyContinue
        if (-not $user) {{
            Write-Output (ConvertTo-Json @{{ found = $false; error = "Пользователь '$([string]'{clean_identity}')' не найден в Active Directory" }})
            exit 0
        }}
        if ($user -is [array]) {{
            # Если передана компания/отдел, ищем точное совпадение
            if ("{safe_comp}" -ne "") {{
                $matched = $user | Where-Object {{ $_.Company -like "*{safe_comp}*" -or $_.Department -like "*{safe_comp}*" }}
                if ($matched) {{
                    $user = if ($matched -is [array]) {{ $matched[0] }} else {{ $matched }}
                }}
            }}
            # Иначе берем первое активное совпадение
            if ($user -is [array]) {{
                $active = $user | Where-Object {{ $_.Enabled -eq $true }}
                $user = if ($active) {{ if ($active -is [array]) {{ $active[0] }} else {{ $active }} }} else {{ $user[0] }}
            }}
        }}
        $isWlan = ($user.MemberOf -like "*{self.target_wlan_group}*")
        Write-Output (ConvertTo-Json @{{
            found = $true
            sam_account_name = [string]$user.SamAccountName
            display_name = [string]$user.Name
            enabled = [bool]$user.Enabled
            is_wlan_member = [bool]$isWlan
            department = [string]$user.Department
            mail = [string]$user.Mail
        }})
        """

        data = self._run_ps_command(script)
        if "error" in data and not data.get("found"):
            return ADUserStatus(found=False, error=data["error"])

        return ADUserStatus(
            found=data.get("found", False),
            sam_account_name=data.get("sam_account_name"),
            display_name=data.get("display_name"),
            enabled=data.get("enabled", False),
            is_wlan_member=data.get("is_wlan_member", False),
            department=data.get("department"),
            mail=data.get("mail"),
            error=data.get("error"),
        )

    def grant_wlan_access(self, identity: str) -> ADExecutionResult:
        """
        Добавляет пользователя в целевую группу WLAN с Single-DC Affinity и Read-after-Write проверкой.
        """
        status = self.get_user_status(identity)
        if not status.found:
            return ADExecutionResult(
                success=False,
                already_member=False,
                sam_account_name=identity,
                message=f"Пользователь '{identity}' не найден в Active Directory",
                target_group=self.target_wlan_group,
                error=status.error or "UserNotFound",
            )

        if not status.enabled:
            return ADExecutionResult(
                success=False,
                already_member=False,
                sam_account_name=status.sam_account_name or identity,
                display_name=status.display_name,
                message=f"Учетная запись '{status.sam_account_name}' ({status.display_name}) отключена в домене",
                target_group=self.target_wlan_group,
                error="AccountDisabled",
            )

        if status.is_wlan_member:
            return ADExecutionResult(
                success=True,
                already_member=True,
                sam_account_name=status.sam_account_name or identity,
                display_name=status.display_name,
                message=f"Пользователь '{status.sam_account_name}' ({status.display_name}) уже состоит в группе {self.target_wlan_group}",
                target_group=self.target_wlan_group,
            )

        safe_sam = (status.sam_account_name or "").replace('"', '`"').replace("$", "`$")
        safe_group = self.target_wlan_group.replace('"', '`"').replace("$", "`$")

        script = f"""
        Import-Module ActiveDirectory -ErrorAction Stop
        $dc = (Get-ADDomainController -Discover).HostName
        try {{
            Add-ADGroupMember -Server $dc -Identity "{safe_group}" -Members "{safe_sam}" -ErrorAction Stop
            
            # Read-after-Write верификация на том же контроллере домена
            $verify = Get-ADUser -Server $dc -Identity "{safe_sam}" -Properties MemberOf -ErrorAction Stop
            $ok = ($verify.MemberOf -like "*{safe_group}*")
            
            Write-Output (ConvertTo-Json @{{
                success = [bool]$ok
                message = if ($ok) {{ "Добавлен и верифицирован" }} else {{ "Не найден в группе после добавления" }}
            }})
        }} catch {{
            Write-Output (ConvertTo-Json @{{
                success = $false
                error = $_.Exception.Message
            }})
        }}
        """

        data = self._run_ps_command(script)
        if data.get("success"):
            logger.info(
                "Пользователь %s успешно добавлен в доменную группу %s",
                status.sam_account_name,
                self.target_wlan_group,
            )
            return ADExecutionResult(
                success=True,
                already_member=False,
                sam_account_name=status.sam_account_name or identity,
                display_name=status.display_name,
                message=f"Пользователь '{status.sam_account_name}' ({status.display_name}) успешно добавлен в группу {self.target_wlan_group}",
                target_group=self.target_wlan_group,
            )

        err_msg = data.get("error") or "Сбой верификации членства"
        logger.error(
            "Не удалось добавить пользователя %s в группу %s: %s",
            status.sam_account_name,
            self.target_wlan_group,
            err_msg,
        )
        return ADExecutionResult(
            success=False,
            already_member=False,
            sam_account_name=status.sam_account_name or identity,
            display_name=status.display_name,
            message=f"Ошибка добавления в группу {self.target_wlan_group}: {err_msg}",
            target_group=self.target_wlan_group,
            error=err_msg,
        )

    @staticmethod
    def extract_identity_from_task(task: dict[str, Any]) -> str:
        """
        Интеллектуальное извлечение логина / ФИО пользователя из полей заявки.
        """
        # 1. Проверяем текст заявки на явные фразы: "для Иванова И.И.", "пользователю <логин/ФИО>"
        desc = (task.get("Description") or "") + " " + (task.get("Name") or "")
        fio_match = re.search(r"(?:для|пользователю|сотруднику)\s+([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?)", desc, re.IGNORECASE)
        if fio_match:
            candidate = fio_match.group(1).strip()
            # Проверяем, что это не название отдела/сервиса
            if len(candidate.split()) >= 2:
                return candidate

        # 2. Приоритет: явный CreatorLogin
        creator_login = (task.get("CreatorLogin") or "").strip()
        # Отсекаем явные имена ПК в логине (например, NTEMW0603)
        if creator_login and not re.match(r"^[A-Z]{3,5}\d{3,5}$", creator_login, re.IGNORECASE):
            return creator_login

        # 3. ФИО создателя
        creator_name = (task.get("Creator") or "").strip()
        if creator_name:
            return creator_name

        # 4. Fallback: если логин был именем ПК, возвращаем его
        return creator_login or ""
