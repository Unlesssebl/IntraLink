import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

TARGET_WLAN_GROUP = "WLAN-WORKNET"


@dataclass
class ADUserProfile:
    found: bool
    sam_account_name: Optional[str] = None
    display_name: Optional[str] = None
    enabled: bool = False
    locked_out: bool = False
    password_expired: bool = False
    title: Optional[str] = None
    department: Optional[str] = None
    company: Optional[str] = None
    phone: Optional[str] = None
    room: Optional[str] = None
    mail: Optional[str] = None
    manager: Optional[str] = None
    groups: list[str] = field(default_factory=list)
    last_logon: Optional[str] = None
    account_expiration_date: Optional[str] = None
    is_wlan_member: bool = False
    error: Optional[str] = None


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
    разблокировку (Unlock-ADAccount), поиск карточек сотрудников,
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
            # Проверяем массив JSON
            arr_start = out.find("[")
            arr_end = out.rfind("]")
            if arr_start != -1 and arr_end != -1 and (json_start == -1 or arr_start < json_start):
                return {"items": json.loads(out[arr_start : arr_end + 1])}
            return json.loads(out)
        except subprocess.TimeoutExpired:
            return {"error": f"Таймаут выполнения запроса к Active Directory ({timeout}s)"}
        except json.JSONDecodeError as e:
            return {"error": f"Ошибка парсинга JSON ответа PowerShell: {e}", "raw": res.stdout}
        except Exception as e:
            return {"error": f"Непредвиденная ошибка выполнения PowerShell: {e}"}

    def search_user_profiles(self, identity: str, company: Optional[str] = None) -> list[ADUserProfile]:
        """
        Полнотекстовый поиск пользователей в Active Directory с извлечением полной карточки
        (должность, отдел, телефон, кабинет, руководитель, группы, статус блокировки).
        """
        if not identity or not identity.strip():
            return [ADUserProfile(found=False, error="Идентификатор пользователя пуст")]

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

        safe_comp = (company or "").strip().replace('"', '`"').replace("$", "`$")

        script = f"""
        Import-Module ActiveDirectory -ErrorAction Stop
        $users = Get-ADUser -Filter "{filter_expr}" -Properties Title, Department, Company, telephoneNumber, physicalDeliveryOfficeName, Manager, LockedOut, PasswordExpired, AccountExpirationDate, LastLogonDate, MemberOf, Enabled, Mail -ErrorAction SilentlyContinue
        
        if (-not $users) {{
            Write-Output (ConvertTo-Json @{{ found = $false; error = "Пользователь '$([string]'{clean_identity}')' не найден в Active Directory" }})
            exit 0
        }}

        $userList = @()
        if ($users -is [array]) {{
            $userList = $users
        }} else {{
            $userList = @($users)
        }}

        $result = @()
        foreach ($u in $userList) {{
            $grps = @()
            if ($u.MemberOf) {{
                $grps = @($u.MemberOf | ForEach-Object {{ ($_ -split ',')[0] -replace '^CN=', '' }})
            }}
            $isWlan = ($u.MemberOf -like "*{self.target_wlan_group}*")
            
            $item = [ordered]@{{
                found = $true
                sam_account_name = [string]$u.SamAccountName
                display_name = [string]$u.Name
                enabled = [bool]$u.Enabled
                locked_out = [bool]$u.LockedOut
                password_expired = [bool]$u.PasswordExpired
                title = [string]$u.Title
                department = [string]$u.Department
                company = [string]$u.Company
                phone = [string]$u.telephoneNumber
                room = [string]$u.physicalDeliveryOfficeName
                mail = [string]$u.Mail
                manager = [string]$u.Manager
                last_logon = [string]$u.LastLogonDate
                account_expiration_date = [string]$u.AccountExpirationDate
                is_wlan_member = [bool]$isWlan
                groups = [string[]]$grps
            }}
            $result += $item
        }}

        if ($result.Count -eq 1) {{
            Write-Output (ConvertTo-Json $result[0])
        }} else {{
            Write-Output (ConvertTo-Json $result)
        }}
        """

        data = self._run_ps_command(script)
        if "error" in data and not data.get("found") and "items" not in data:
            return [ADUserProfile(found=False, error=data["error"])]

        items = []
        if "items" in data:
            items = data["items"]
        elif isinstance(data, list):
            items = data
        elif isinstance(data, dict) and data.get("found"):
            items = [data]
        elif isinstance(data, dict) and not data.get("found"):
            return [ADUserProfile(found=False, error=data.get("error", "UserNotFound"))]

        profiles = []
        for raw in items:
            profiles.append(
                ADUserProfile(
                    found=raw.get("found", True),
                    sam_account_name=raw.get("sam_account_name"),
                    display_name=raw.get("display_name"),
                    enabled=raw.get("enabled", False),
                    locked_out=raw.get("locked_out", False),
                    password_expired=raw.get("password_expired", False),
                    title=raw.get("title") or None,
                    department=raw.get("department") or None,
                    company=raw.get("company") or None,
                    phone=raw.get("phone") or None,
                    room=raw.get("room") or None,
                    mail=raw.get("mail") or None,
                    manager=raw.get("manager") or None,
                    groups=raw.get("groups") or [],
                    last_logon=raw.get("last_logon") or None,
                    account_expiration_date=raw.get("account_expiration_date") or None,
                    is_wlan_member=raw.get("is_wlan_member", False),
                    error=raw.get("error"),
                )
            )
        return profiles

    def get_user_status(self, identity: str, company: Optional[str] = None) -> ADUserStatus:
        """
        Быстрая проверка статуса для обратной совместимости.
        """
        profiles = self.search_user_profiles(identity, company=company)
        if not profiles or not profiles[0].found:
            err = profiles[0].error if profiles else "UserNotFound"
            return ADUserStatus(found=False, error=err)
        
        # Если найдено несколько, берем активного или точное совпадение по компании
        p = profiles[0]
        if len(profiles) > 1:
            active = [x for x in profiles if x.enabled]
            p = active[0] if active else profiles[0]

        return ADUserStatus(
            found=True,
            sam_account_name=p.sam_account_name,
            display_name=p.display_name,
            enabled=p.enabled,
            is_wlan_member=p.is_wlan_member,
            department=p.department,
            mail=p.mail,
            error=p.error,
        )

    def unlock_user_account(self, identity: str) -> tuple[bool, str, Optional[ADUserProfile]]:
        """
        Разблокировка заблокированной учетной записи в Active Directory с Single-DC Affinity
        и предпроверкой статуса LockedOut.
        """
        profiles = self.search_user_profiles(identity)
        if not profiles or not profiles[0].found:
            return False, f"Пользователь '{identity}' не найден в Active Directory", None
        
        if len(profiles) > 1:
            names = ", ".join(f"{p.sam_account_name} ({p.display_name} - {p.department})" for p in profiles)
            return False, f"Найдено несколько учетных записей ({len(profiles)}): {names}. Уточните логин sAMAccountName.", None

        user = profiles[0]
        if not user.enabled:
            return False, f"Учетная запись '{user.sam_account_name}' ({user.display_name}) отключена администратором домена (Enabled=False)", user

        if not user.locked_out:
            diag_info = []
            if user.password_expired:
                diag_info.append("истек срок действия пароля")
            if user.account_expiration_date:
                diag_info.append(f"срок действия аккаунта: {user.account_expiration_date}")
            diag_str = f" ({', '.join(diag_info)})" if diag_info else ""
            return False, f"Учетная запись '{user.sam_account_name}' ({user.display_name}) НЕ заблокирована (LockedOut=False){diag_str}", user

        safe_sam = (user.sam_account_name or "").replace('"', '`"').replace("$", "`$")

        script = f"""
        Import-Module ActiveDirectory -ErrorAction Stop
        $dc = (Get-ADDomainController -Discover).HostName
        try {{
            Unlock-ADAccount -Server $dc -Identity "{safe_sam}" -ErrorAction Stop
            
            # Read-after-Write верификация на том же контроллере домена
            $verify = Get-ADUser -Server $dc -Identity "{safe_sam}" -Properties LockedOut -ErrorAction Stop
            $isUnlocked = (-not $verify.LockedOut)
            
            Write-Output (ConvertTo-Json @{{
                success = [bool]$isUnlocked
                message = if ($isUnlocked) {{ "Разблокирована и верифицирована" }} else {{ "Остается заблокированной после попытки разблокировки" }}
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
            user.locked_out = False
            logger.info("Учетная запись %s успешно разблокирована в Active Directory", user.sam_account_name)
            return True, f"Учетная запись '{user.sam_account_name}' ({user.display_name}) успешно разблокирована в домене", user

        err_msg = data.get("error") or data.get("message") or "Сбой разблокировки"
        logger.error("Ошибка при разблокировке учетной записи %s: %s", user.sam_account_name, err_msg)
        return False, f"Не удалось разблокировать '{user.sam_account_name}': {err_msg}", user

    def add_user_to_group(self, identity: str, group_name: str) -> ADExecutionResult:
        """
        Добавляет пользователя в произвольную доменную группу безопасности с pre-flight проверкой группы,
        Single-DC Affinity и Read-after-Write верификацией.
        """
        clean_group = group_name.strip()
        status = self.get_user_status(identity)
        if not status.found:
            return ADExecutionResult(
                success=False,
                already_member=False,
                sam_account_name=identity,
                message=f"Пользователь '{identity}' не найден в Active Directory",
                target_group=clean_group,
                error=status.error or "UserNotFound",
            )

        if not status.enabled:
            return ADExecutionResult(
                success=False,
                already_member=False,
                sam_account_name=status.sam_account_name or identity,
                display_name=status.display_name,
                message=f"Учетная запись '{status.sam_account_name}' ({status.display_name}) отключена в домене",
                target_group=clean_group,
                error="AccountDisabled",
            )

        safe_sam = (status.sam_account_name or "").replace('"', '`"').replace("$", "`$")
        safe_group = clean_group.replace('"', '`"').replace("$", "`$")

        script = f"""
        Import-Module ActiveDirectory -ErrorAction Stop
        $dc = (Get-ADDomainController -Discover).HostName
        
        # 1. Pre-flight проверка существования группы
        $grp = Get-ADGroup -Server $dc -Filter "Name -eq '{safe_group}' -or SamAccountName -eq '{safe_group}'" -ErrorAction SilentlyContinue
        if (-not $grp) {{
            Write-Output (ConvertTo-Json @{{
                success = $false
                error = "Группа '$([string]'{clean_group}')' не найдена в Active Directory"
            }})
            exit 0
        }}

        $targetGroupExactName = $grp.SamAccountName

        # 2. Проверка текущего членства
        $u = Get-ADUser -Server $dc -Identity "{safe_sam}" -Properties MemberOf -ErrorAction Stop
        if ($u.MemberOf -like "*$targetGroupExactName*") {{
            Write-Output (ConvertTo-Json @{{
                success = $true
                already_member = $true
                target_group = $targetGroupExactName
            }})
            exit 0
        }}

        # 3. Добавление в группу
        try {{
            Add-ADGroupMember -Server $dc -Identity $targetGroupExactName -Members "{safe_sam}" -ErrorAction Stop
            
            # Read-after-Write верификация на том же контроллере домена
            $verify = Get-ADUser -Server $dc -Identity "{safe_sam}" -Properties MemberOf -ErrorAction Stop
            $ok = ($verify.MemberOf -like "*$targetGroupExactName*")
            
            Write-Output (ConvertTo-Json @{{
                success = [bool]$ok
                already_member = $false
                target_group = $targetGroupExactName
                message = if ($ok) {{ "Добавлен и верифицирован" }} else {{ "Не найден в группе после добавления" }}
            }})
        }} catch {{
            Write-Output (ConvertTo-Json @{{
                success = $false
                already_member = $false
                error = $_.Exception.Message
            }})
        }}
        """

        data = self._run_ps_command(script)
        if data.get("success"):
            already = data.get("already_member", False)
            msg = (
                f"Пользователь '{status.sam_account_name}' ({status.display_name}) уже состоит в группе {clean_group}"
                if already
                else f"Пользователь '{status.sam_account_name}' ({status.display_name}) успешно добавлен в группу {clean_group}"
            )
            return ADExecutionResult(
                success=True,
                already_member=already,
                sam_account_name=status.sam_account_name or identity,
                display_name=status.display_name,
                message=msg,
                target_group=clean_group,
            )

        err_msg = data.get("error") or data.get("message") or "Сбой добавления в группу"
        logger.error(
            "Не удалось добавить пользователя %s в группу %s: %s",
            status.sam_account_name,
            clean_group,
            err_msg,
        )
        return ADExecutionResult(
            success=False,
            already_member=False,
            sam_account_name=status.sam_account_name or identity,
            display_name=status.display_name,
            message=f"Ошибка добавления в группу {clean_group}: {err_msg}",
            target_group=clean_group,
            error=err_msg,
        )

    def grant_wlan_access(self, identity: str) -> ADExecutionResult:
        """
        Добавляет пользователя в целевую группу WLAN-WORKNET.
        """
        return self.add_user_to_group(identity, self.target_wlan_group)

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
            if len(candidate.split()) >= 2:
                return candidate

        # 2. Приоритет: явный CreatorLogin
        creator_login = (task.get("CreatorLogin") or "").strip()
        if creator_login and not re.match(r"^[A-Z]{3,5}\d{3,5}$", creator_login, re.IGNORECASE):
            return creator_login

        # 3. ФИО создателя
        creator_name = (task.get("Creator") or "").strip()
        if creator_name:
            return creator_name

        # 4. Fallback: если логин был именем ПК, возвращаем его
        return creator_login or ""
