1. Есть тег Rule Engine, но нету смарт тега - типа заявки, к примеру заявка №140210
2. У заявки есть исполнитель, но в web Ui он не отображается в столбце "исполнитель", к примеру заявка №140146
3. У заявки №140146, не корректно определился ответ, потому что не правильно была обработана заявка, просмотри и дай ответ
4. При нажатии на "диагностика сети" Ошибка при запросе к /admin/api/diag/NTEMW1123: Error: Недостаточно прав: учетная запись 'belikov' не входит в список администраторов.
    tt http://localhost:8000/operator-panel:55
    Jm http://localhost:8000/operator-panel:59
    p http://localhost:8000/operator-panel:59
    onClick http://localhost:8000/operator-panel:59
    n0 http://localhost:8000/operator-panel:54
    Cu http://localhost:8000/operator-panel:54
    cc http://localhost:8000/operator-panel:54
    Cu http://localhost:8000/operator-panel:54
    Vu http://localhost:8000/operator-panel:55
    xm http://localhost:8000/operator-panel:55
operator-panel:55:38252
5. Почему то у всех заявок сервис "Общие вопросы", при перемещении по сервисам в sidebar заявок вообще нет, а так же не работает раскрытие подсервисов, даже кнопки нет.
intralink/core-api	2026-09-03 13:18:10,205 - core_api.services.triage_service - WARNING - Ошибка получения справочника сервисов IntraService: 'str' object has no attribute 'get'
6. Реализовать запоминание состояния развернутости боковой панели (инспектора)
8. 