import asyncio
import json
import logging
import re
import secrets
import string
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

TARGET_WLAN_GROUP = "WLAN-WORKNET"

# Таблица транслитерации ГОСТ/Active Directory (русский -> латиница)
RU_TO_LAT_MAP = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

# Справочник типовых аббревиатур подразделений холдинга «ТЭМ-ПО»
DEPARTMENT_ABBREVIATIONS: dict[str, str] = {
    "ОГТ": "Отдел главного технолога",
    "ОГМ": "Отдел главного механика",
    "ОГК": "Отдел главного конструктора",
    "ОГЭ": "Отдел главного энергетика",
    "ОТК": "Отдел технического контроля",
    "ОМТС": "Отдел снабжения",
    "ОСНАБ": "Отдел снабжения",
    "ОТИЗ": "Отдел труда и заработной платы",
    "ОТЗ": "Отдел труда и заработной платы",
    "ПДО": "Планово диспетчерский отдел",
    "ПЭО": "Планово экономический отдел",
    "СЭБ": "Служба по экономической безопасности",
    "ОК": "Отдел кадров",
    "ЮО": "Юридический отдел",
    "ЮРОТДЕЛ": "Юридический отдел",
    "АСУ": "Отдел АСУ",
    "ОАСУ": "Отдел АСУ",
    "ИТР": "ИТР",
    "СБ": "СБ",
    "ОСМК": "Отдел СМК",
    "СМК": "Отдел СМК",
    "ОГС": "Отдел главного сварщика",
    "СПК": "Служба производственного контроля",
    "ЦФИ": "Цех фасонных изделий",
    "ОТРП": "Отдел технического развития продукции",
    "ОТОП": "Отдел технического обеспечения производства",
    "ОСР": "Отдел сбыта и реализации",
    "ОУДМ": "Отдел учета и движения материалов",
    "ПО": "Проектный отдел",
    "ФО": "Финансовый отдел",
    "ОПР": "Отдел по претензионной работе",
    "БУХГАЛТЕРИЯ": "Бухгалтерия",
    "ОП": "Отдел продаж",
}


def transliterate_text(text: str) -> str:
    """Транслитерирует русский текст в латиницу по корпоративному стандарту AD."""
    res = []
    for ch in text.lower():
        res.append(RU_TO_LAT_MAP.get(ch, ch))
    return "".join(res)


def generate_sam_account_name(surname: str, name: str, patronymic: Optional[str] = None) -> str:
    """
    Генерирует базовый логин sAMAccountName в формате «фамилия.и.о» (например, «spirin.a.i»).
    Если отчество отсутствует, генерирует «фамилия.и» («spirin.a»).
    """
    sur = transliterate_text(surname.strip())
    # Убираем любые спецсимволы и пробелы из фамилии
    sur = re.sub(r"[^a-z0-9]", "", sur)
    n = transliterate_text(name.strip()[:1])
    n = re.sub(r"[^a-z0-9]", "", n) or "a"

    if patronymic and patronymic.strip():
        p = transliterate_text(patronymic.strip()[:1])
        p = re.sub(r"[^a-z0-9]", "", p)
        if p:
            return f"{sur}.{n}.{p}"
    return f"{sur}.{n}"


def generate_secure_password(length: int = 10) -> str:
    """
    Генерирует надежный и легко вводимый временный пароль:
    - 1 спецсимвол (!, @, #, $, %)
    - Цифры
    - Заглавные и строчные буквы (без неоднозначных символов вроде l, 1, O, 0)
    """
    special_chars = "!@#$%"
    upper_chars = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # без I, O для читаемости
    lower_chars = "abcdefghijkmnopqrstuvwxyz"  # без l для читаемости
    digits = "23456789"  # без 0, 1 для читаемости

    spec = secrets.choice(special_chars)
    up = secrets.choice(upper_chars)
    dig = secrets.choice(digits)
    low1 = secrets.choice(lower_chars)
    low2 = secrets.choice(lower_chars)

    all_chars = upper_chars + lower_chars + digits
    remaining = [secrets.choice(all_chars) for _ in range(max(length - 5, 5))]

    pwd_list = [up, spec, dig, low1, low2] + remaining
    secrets.SystemRandom().shuffle(pwd_list)
    return "".join(pwd_list)


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


@dataclass
class ADUserCreationResult:
    success: bool
    sam_account_name: str
    display_name: str
    password: str = ""
    user_principal_name: str = ""
    distinguished_name: str = ""
    ou: str = ""
    groups: list[str] = field(default_factory=list)
    message: str = ""
    error: Optional[str] = None


class ActiveDirectoryExecutor:
    """
    Отказоустойчивый исполнитель операций в Active Directory.
    Обеспечивает Single-DC Affinity, проверку активности учетных записей,
    создание пользователей (New-ADUser), разблокировку (Unlock-ADAccount),
    поиск карточек сотрудников, идемпотентность и Read-after-Write верификацию.
    """

    def __init__(self, target_wlan_group: str = TARGET_WLAN_GROUP):
        self.target_wlan_group = target_wlan_group

    @staticmethod
    def _run_ps_command_sync(script: str, timeout: int = 25) -> dict[str, Any]:
        """
        Синхронно выполняет PowerShell-скрипт и возвращает распарсенный JSON результат.
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

    @classmethod
    def _run_ps_command(cls, script: str, timeout: int = 25) -> dict[str, Any]:
        """Синхронный метод для обратной совместимости."""
        return cls._run_ps_command_sync(script, timeout)

    @classmethod
    async def _run_ps_command_async(
        cls, script: str, timeout: int = 25
    ) -> dict[str, Any]:
        """
        Асинхронная неблокирующая обертка выполнения PowerShell через asyncio.to_thread.
        Предотвращает микрозадержки event loop при масштабировании.
        """
        return await asyncio.to_thread(cls._run_ps_command_sync, script, timeout)

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

    @staticmethod
    def extract_user_creation_details_from_task(task: dict[str, Any]) -> dict[str, Any]:
        """
        Интеллектуальное извлечение реквизитов для создания нового пользователя в AD:
        - ФИО (поля 1069, 1070, 1071 или текст)
        - Должность (поле 1073)
        - Телефон (поле 1075)
        - Подразделение (поле 1078)
        - Кабинет (поле 1079)
        - Имя ПК (поле 1120)
        - Организация (Categories, CategoryIds или компания заявителя)
        """
        meta = task.get("_field_meta", {}).get("raw", {}) if isinstance(task.get("_field_meta"), dict) else {}
        surname = (task.get("Field1057") or meta.get("1057") or task.get("Field1069") or meta.get("1069") or "").strip()
        name = (task.get("Field1058") or meta.get("1058") or task.get("Field1070") or meta.get("1070") or "").strip()
        patronymic = (task.get("Field1059") or meta.get("1059") or task.get("Field1071") or meta.get("1071") or "").strip()
        title = (task.get("Field1065") or meta.get("1065") or task.get("Field1073") or meta.get("1073") or "").strip()
        phone = (task.get("Field1066") or meta.get("1066") or task.get("Field1075") or meta.get("1075") or "").strip()
        dept = (task.get("Field1064") or meta.get("1064") or task.get("Field1078") or meta.get("1078") or "").strip()
        room = (task.get("Field1079") or meta.get("1079") or "").strip()
        pc_name = (task.get("Field1068") or meta.get("1068") or task.get("Field1120") or meta.get("1120") or "").strip()

        # Fallback: парсинг из текста и темы задачи
        if not (surname and name):
            full_text = f"{task.get('Name', '')} {task.get('Description', '')}"
            fio_m = re.search(
                r"(?:фио|сотрудник|пользователь|создать|учетная запись)\s*[:\-]?\s*([А-ЯЁ][а-яё]+)\s+([А-ЯЁ][а-яё]+)(?:\s+([А-ЯЁ][а-яё]+))?",
                full_text,
                re.IGNORECASE,
            )
            if fio_m:
                if not surname:
                    surname = fio_m.group(1).strip()
                if not name:
                    name = fio_m.group(2).strip()
                if not patronymic and fio_m.group(3):
                    patronymic = fio_m.group(3).strip()

        company = (
            task.get("Categories")
            or task.get("CreatorCompanyName")
            or task.get("CreatorCompany")
            or ""
        )
        clean_company = re.sub(r'["«»]', "", company).strip()

        creator_comp = (
            task.get("CreatorCompanyName")
            or task.get("CreatorCompany")
            or ""
        )
        clean_creator_comp = re.sub(r'["«»]', "", creator_comp).strip()
        creator_dept = (task.get("CreatorDepartment") or "").strip()

        return {
            "surname": surname,
            "name": name,
            "patronymic": patronymic,
            "title": title,
            "phone": phone,
            "department": dept,
            "room": room,
            "pc_name": pc_name,
            "company": clean_company,
            "creator_company": clean_creator_comp,
            "creator_department": creator_dept,
        }

    def create_user_account(
        self,
        surname: str,
        name: str,
        patronymic: Optional[str] = None,
        company: Optional[str] = None,
        department: Optional[str] = None,
        phone: Optional[str] = None,
        pc_name: Optional[str] = None,
        title: Optional[str] = None,
        password: Optional[str] = None,
        creator_company: Optional[str] = None,
        creator_dept: Optional[str] = None,
    ) -> ADUserCreationResult:
        """
        Создает нового пользователя в Active Directory с Single-DC Affinity,
        двухслойным поиском OU (Организация + Подразделение с учетом аббревиатур),
        добавлением в группу HLP_<Организация>, автогенерацией стойкого пароля
        и Read-after-Write верификацией.
        """
        if not surname or not name:
            return ADUserCreationResult(
                success=False,
                sam_account_name="",
                display_name="",
                error="Не указаны обязательные поля: Фамилия и Имя сотрудника",
            )

        clean_surname = surname.strip()
        clean_name = name.strip()
        clean_patr = (patronymic or "").strip()
        display_name = f"{clean_surname} {clean_name}" + (f" {clean_patr}" if clean_patr else "")
        base_sam = generate_sam_account_name(clean_surname, clean_name, clean_patr)
        gen_password = password or generate_secure_password(10)

        # Раскрываем аббревиатуру подразделения
        clean_dept = (department or "").strip()
        expanded_dept = DEPARTMENT_ABBREVIATIONS.get(clean_dept.upper(), clean_dept)

        clean_comp = (company or "").strip()
        clean_creator_comp = (creator_company or "").strip()
        clean_creator_dept = (creator_dept or "").strip()

        # Экранирование для PowerShell
        safe_surname = clean_surname.replace('"', '`"').replace("$", "`$")
        safe_name = clean_name.replace('"', '`"').replace("$", "`$")
        safe_display = display_name.replace('"', '`"').replace("$", "`$")
        safe_base_sam = base_sam.replace('"', '`"').replace("$", "`$")
        safe_pass = gen_password.replace('"', '`"').replace("$", "`$")
        safe_phone = (phone or "").strip().replace('"', '`"').replace("$", "`$")
        safe_pc = (pc_name or "").strip().replace('"', '`"').replace("$", "`$")
        safe_title = (title or "").strip().replace('"', '`"').replace("$", "`$")

        safe_comp = clean_comp.replace('"', '`"').replace("$", "`$")
        safe_dept = expanded_dept.replace('"', '`"').replace("$", "`$")
        safe_raw_dept = clean_dept.replace('"', '`"').replace("$", "`$")
        safe_c_comp = clean_creator_comp.replace('"', '`"').replace("$", "`$")
        safe_c_dept = clean_creator_dept.replace('"', '`"').replace("$", "`$")

        script = """
        Import-Module ActiveDirectory -ErrorAction Stop
        $dc = (Get-ADDomainController -Discover).HostName
        $usersRoot = "OU=CORPORATE_USERS,DC=corporate,DC=loc"

        # 1. Поиск OU организации (Слой 1)
        $compOU = $null
        $compQuery = "__SAFE_COMP__"
        if ($compQuery) {
            $ous = Get-ADOrganizationalUnit -Server $dc -Filter * -SearchBase $usersRoot -SearchScope OneLevel -ErrorAction SilentlyContinue
            $compOU = $ous | Where-Object { $_.Name -like "*$compQuery*" } | Select-Object -First 1
        }
        if (-not $compOU -and "__SAFE_C_COMP__") {
            $cComp = "__SAFE_C_COMP__"
            $ous = Get-ADOrganizationalUnit -Server $dc -Filter * -SearchBase $usersRoot -SearchScope OneLevel -ErrorAction SilentlyContinue
            $compOU = $ous | Where-Object { $_.Name -like "*$cComp*" } | Select-Object -First 1
        }
        if (-not $compOU) {
            $targetCompOUPath = $usersRoot
            $companyExactName = "__SAFE_COMP__"
        } else {
            $targetCompOUPath = $compOU.DistinguishedName
            $companyExactName = $compOU.Name
        }

        # 2. Поиск OU подразделения внутри организации (Слой 2)
        $targetOU = $targetCompOUPath
        $deptQueries = @("__SAFE_DEPT__", "__SAFE_RAW_DEPT__", "__SAFE_C_DEPT__") | Where-Object { $_ -ne "" }
        if ($compOU -and $deptQueries.Count -gt 0) {
            $subOUs = Get-ADOrganizationalUnit -Server $dc -Filter * -SearchBase $compOU.DistinguishedName -ErrorAction SilentlyContinue
            foreach ($dq in $deptQueries) {
                $foundDept = $subOUs | Where-Object { $_.Name -like "*$dq*" } | Select-Object -First 1
                if ($foundDept) {
                    $targetOU = $foundDept.DistinguishedName
                    break
                }
            }
        }

        # 3. Определение сервисной группы ServiceDesk (HLP_<Организация>)
        $hlpGroup = $null
        if ($companyExactName) {
            $hlpQuery = "HLP_$companyExactName"
            $hlpGroup = Get-ADGroup -Server $dc -Filter "Name -like '*$hlpQuery*' -or SamAccountName -like '*$hlpQuery*'" -SearchBase "OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc" -ErrorAction SilentlyContinue | Select-Object -First 1
            if (-not $hlpGroup) {
                $shortComp = $companyExactName -replace '^(АО|ООО|ПАО|ИП)\\s*', ''
                $hlpGroup = Get-ADGroup -Server $dc -Filter "Name -like '*$shortComp*' -or SamAccountName -like '*$shortComp*'" -SearchBase "OU=ServiceDesk,OU=CORPORATE_SERVICES,DC=corporate,DC=loc" -ErrorAction SilentlyContinue | Select-Object -First 1
            }
        }

        # 4. Проверка существования пользователя с таким DisplayName
        $existing = Get-ADUser -Server $dc -Filter "Name -eq '__SAFE_DISPLAY__' -or DisplayName -eq '__SAFE_DISPLAY__'" -ErrorAction SilentlyContinue
        if ($existing) {
            $exSam = $existing.SamAccountName
            Write-Output (ConvertTo-Json @{
                success = $false
                error = "Пользователь '__SAFE_DISPLAY__' уже существует в Active Directory (логин: $exSam)"
            })
            exit 0
        }

        # 5. Разрешение коллизий sAMAccountName
        $sam = "__SAFE_BASE_SAM__"
        $idx = 2
        while (Get-ADUser -Server $dc -Filter "SamAccountName -eq '$sam'" -ErrorAction SilentlyContinue) {
            $sam = "__SAFE_BASE_SAM__$idx"
            $idx++
        }

        # 6. Создание учетной записи New-ADUser
        try {
            $secPass = ConvertTo-SecureString "__SAFE_PASS__" -AsPlainText -Force
            $otherAttrs = @{}
            if ("__SAFE_PHONE__") { $otherAttrs["telephoneNumber"] = "__SAFE_PHONE__" }
            if ("__SAFE_TITLE__") { $otherAttrs["title"] = "__SAFE_TITLE__" }

            New-ADUser -Server $dc -Name "__SAFE_DISPLAY__" -DisplayName "__SAFE_DISPLAY__" -GivenName "__SAFE_NAME__" -Surname "__SAFE_SURNAME__" -SamAccountName $sam -UserPrincipalName "$sam@corporate.loc" -Path $targetOU -AccountPassword $secPass -ChangePasswordAtLogon $true -Enabled $true -Description "__SAFE_PC__" -OtherAttributes $otherAttrs -ErrorAction Stop

            # 7. Добавление в группу HLP_<Организация>
            $addedGroups = @()
            if ($hlpGroup) {
                try {
                    Add-ADGroupMember -Server $dc -Identity $hlpGroup.SamAccountName -Members $sam -ErrorAction Stop
                    $addedGroups += [string]$hlpGroup.Name
                } catch {
                    # Игнорируем ошибку группы при успешном создании
                }
            }

            # 8. Read-after-Write верификация на том же контроллере домена
            $verify = Get-ADUser -Server $dc -Identity $sam -Properties MemberOf, Description, telephoneNumber, Title, UserPrincipalName, DistinguishedName, Enabled -ErrorAction Stop
            $grps = @()
            if ($verify.MemberOf) {
                $grps = @($verify.MemberOf | ForEach-Object { ($_ -split ',')[0] -replace '^CN=', '' })
            }

            Write-Output (ConvertTo-Json @{
                success = [bool]$verify.Enabled
                sam_account_name = [string]$verify.SamAccountName
                user_principal_name = [string]$verify.UserPrincipalName
                display_name = [string]$verify.Name
                distinguished_name = [string]$verify.DistinguishedName
                ou = [string]$targetOU
                groups = [string[]]$grps
                message = "Учетная запись успешно создана в Active Directory"
            })
        } catch {
            Write-Output (ConvertTo-Json @{
                success = $false
                error = $_.Exception.Message
            })
        }
        """

        replacements = {
            "__SAFE_COMP__": safe_comp,
            "__SAFE_C_COMP__": safe_c_comp,
            "__SAFE_DEPT__": safe_dept,
            "__SAFE_RAW_DEPT__": safe_raw_dept,
            "__SAFE_C_DEPT__": safe_c_dept,
            "__SAFE_DISPLAY__": safe_display,
            "__SAFE_NAME__": safe_name,
            "__SAFE_SURNAME__": safe_surname,
            "__SAFE_BASE_SAM__": safe_base_sam,
            "__SAFE_PASS__": safe_pass,
            "__SAFE_PHONE__": safe_phone,
            "__SAFE_PC__": safe_pc,
            "__SAFE_TITLE__": safe_title,
        }
        for k, v in replacements.items():
            script = script.replace(k, v)

        data = self._run_ps_command(script, timeout=30)
        if data.get("success"):
            return ADUserCreationResult(
                success=True,
                sam_account_name=data.get("sam_account_name") or base_sam,
                display_name=data.get("display_name") or display_name,
                password=gen_password,
                user_principal_name=data.get("user_principal_name") or f"{base_sam}@corporate.loc",
                distinguished_name=data.get("distinguished_name") or "",
                ou=data.get("ou") or "",
                groups=data.get("groups") or [],
                message=data.get("message") or "Учетная запись успешно создана",
            )

        err_msg = data.get("error") or "Не удалось создать учетную запись в Active Directory"
        logger.error("Ошибка при создании пользователя %s: %s", display_name, err_msg)
        return ADUserCreationResult(
            success=False,
            sam_account_name=base_sam,
            display_name=display_name,
            password=gen_password,
            error=err_msg,
        )

    async def search_user_profiles_async(
        self, identity: str, company: Optional[str] = None
    ) -> list[ADUserProfile]:
        """Асинхронный поиск пользователей в AD без блокировки event loop."""
        return await asyncio.to_thread(self.search_user_profiles, identity, company)

    async def get_user_status_async(
        self, identity: str, company: Optional[str] = None
    ) -> ADUserStatus:
        """Асинхронная проверка статуса пользователя в AD."""
        return await asyncio.to_thread(self.get_user_status, identity, company)

    async def unlock_user_account_async(
        self, identity: str
    ) -> tuple[bool, str, Optional[ADUserProfile]]:
        """Асинхронная разблокировка учетной записи в AD."""
        return await asyncio.to_thread(self.unlock_user_account, identity)

    async def add_user_to_group_async(
        self, identity: str, group_name: str
    ) -> ADExecutionResult:
        """Асинхронное добавление пользователя в группу AD."""
        return await asyncio.to_thread(self.add_user_to_group, identity, group_name)

    async def grant_wlan_access_async(self, identity: str) -> ADExecutionResult:
        """Асинхронная выдача доступа Wi-Fi (WLAN-WORKNET) в AD."""
        return await asyncio.to_thread(self.grant_wlan_access, identity)

    async def create_user_account_async(
        self,
        surname: str,
        name: str,
        patronymic: Optional[str] = None,
        company: Optional[str] = None,
        department: Optional[str] = None,
        phone: Optional[str] = None,
        pc_name: Optional[str] = None,
        title: Optional[str] = None,
        password: Optional[str] = None,
        creator_company: Optional[str] = None,
        creator_dept: Optional[str] = None,
    ) -> ADUserCreationResult:
        """Асинхронное создание учетной записи в AD."""
        return await asyncio.to_thread(
            self.create_user_account,
            surname,
            name,
            patronymic,
            company,
            department,
            phone,
            pc_name,
            title,
            password,
            creator_company,
            creator_dept,
        )

