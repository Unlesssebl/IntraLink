#!/usr/bin/env python3
"""
scripts/verify_docs.py

Скрипт автоматизированной проверки целостности документации IntraLink:
1. Whitelist-проверка файлов в корне docs/ (не допускает захламления корня).
2. Валидация внутренних относительных ссылок в markdown-файлах (проверка битых ссылок).
3. Проверка наличия обязательных разделов в паспортах docs/services/*/README.md.
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"

ALLOWED_ROOT_FILES = {
    "README.md",
    "architecture.md",
    "developer_guide.md",
    "brandbook.md",
    "roadmap.md",
}

# Регулярное выражение для поиска markdown-ссылок [текст](путь)
LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def check_root_docs_cleanliness() -> list[str]:
    errors = []
    for item in DOCS_DIR.iterdir():
        if item.is_file():
            if item.name not in ALLOWED_ROOT_FILES:
                errors.append(
                    f"[Root docs violation] Неразрешенный файл в корне docs/: {item.name}. "
                    f"Разрешены только: {', '.join(sorted(ALLOWED_ROOT_FILES))}"
                )
    return errors


def check_markdown_links() -> tuple[list[str], int]:
    errors = []
    checked_links_count = 0

    # Проверяем все .md файлы в docs/
    for md_file in DOCS_DIR.rglob("*.md"):
        # Пропускаем внешние спецификации IntraService API (49 файлов) и архивные документы
        if "external" in md_file.parts or "archive" in md_file.parts:
            continue

        try:
            content = md_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            errors.append(f"[Encoding Error] Не удалось прочитать {md_file.relative_to(REPO_ROOT)} в UTF-8")
            continue

        for match in LINK_PATTERN.finditer(content):
            _, raw_link = match.groups()
            raw_link = raw_link.strip()

            # Игнорируем внешние ссылки, mailto, якоря и deep links
            if any(raw_link.startswith(prefix) for prefix in ("http://", "https://", "mailto:", "#", "intralink://", "file://")):
                continue

            # Отсекаем якорь внутри ссылки (e.g., path/to/file.md#section)
            link_path_part = raw_link.split("#")[0].strip()
            if not link_path_part:
                continue

            checked_links_count += 1

            # Резолвим путь относительно текущего файла
            target_path = (md_file.parent / link_path_part).resolve()

            if not target_path.exists():
                rel_source = md_file.relative_to(REPO_ROOT)
                errors.append(
                    f"[Broken Link] В {rel_source}: ссылка '{raw_link}' указывает на несуществующий путь '{target_path.relative_to(REPO_ROOT, walk_up=True)}'"
                )

    return errors, checked_links_count


def check_service_passports() -> list[str]:
    errors = []
    services_dir = DOCS_DIR / "services"
    if not services_dir.exists():
        errors.append("[Missing Dir] Директория docs/services не найдена")
        return errors

    for service_dir in services_dir.iterdir():
        if service_dir.is_dir():
            readme = service_dir / "README.md"
            if not readme.exists():
                errors.append(f"[Missing Passport] Отсутствует README.md в {service_dir.relative_to(REPO_ROOT)}")
                continue

            content = readme.read_text(encoding="utf-8")
            has_responsibility = any(s in content for s in ("Зона ответственности", "Назначение"))
            if not has_responsibility:
                errors.append(
                    f"[Incomplete Passport] В {readme.relative_to(REPO_ROOT)} отсутствует раздел 'Зона ответственности' или 'Назначение'"
                )

    return errors


def main() -> int:
    print("🔍 Запуск верификации документации IntraLink...")

    all_errors = []

    # 1. Проверка чистоты корня docs
    root_errors = check_root_docs_cleanliness()
    if root_errors:
        all_errors.extend(root_errors)
        print(f"❌ Нарушений в корне docs/: {len(root_errors)}")
    else:
        print("✅ Корень docs/ чист (только утвержденные базовые файлы)")

    # 2. Проверка паспортов сервисов
    passport_errors = check_service_passports()
    if passport_errors:
        all_errors.extend(passport_errors)
        print(f"❌ Ошибок в паспортах сервисов: {len(passport_errors)}")
    else:
        print("✅ Паспорта сервисов присутствуют и оформлены по стандарту")

    # 3. Проверка относительных ссылок
    link_errors, checked_count = check_markdown_links()
    if link_errors:
        all_errors.extend(link_errors)
        print(f"❌ Найдено битых ссылок: {len(link_errors)} из {checked_count} проверенных")
    else:
        print(f"✅ Проверено ссылок: {checked_count}. Битых ссылок не обнаружено")

    if all_errors:
        print("\nОбнаруженные проблемы:")
        for err in all_errors:
            print(f"  • {err}")
        return 1

    print("\n🎉 Документация в идеальном порядке! Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
