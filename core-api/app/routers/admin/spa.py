import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Путь к файлу шаблона админ-панели (static/admin/index.html)
HTML_PATH = Path(__file__).resolve().parent.parent.parent / "static" / "admin" / "index.html"


@router.get("/admin", response_class=HTMLResponse)
async def get_admin_ui():
    """
    Отдает HTML-страницу админ-панели.
    """
    if not HTML_PATH.exists():
        # Если static/admin не существует, отдаем страницу заглушки
        return HTMLResponse(
            content=(
                "<h1>Панель администратора не найдена на сервере.</h1>"
                "<p>Пожалуйста, убедитесь, что static/admin/index.html существует.</p>"
            ),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    try:
        with HTML_PATH.open(encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except Exception as e:
        logger.exception("Ошибка при чтении файла шаблона админ-панели: %s", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка сервера при загрузке UI: {e}",
        ) from e
