**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

_…….. …….. </Task> <Statuses> <Status> <Id>31</Id> <Name>Открыта</Name> ……. </Status> </Statuses> <Priorities> <Priority> <Id>9</Id> <Name>Средний</Name> …… </Priority> </Priorities> </TaskForm>_ 

**Получение шаблона заявки для создания (значения по умолчанию)** 

_Вызов_ 

GET /api/newtask?serviceid={serviced}&tasktypeid={tasktypeid}&include={includeList} _Возможные параметры_ 

_**serviceid** – обязательный параметр. Идентификатор сервиса, в котором создается заявка._ _**tasktypeid** – обязательный параметр. Идентификатор типа заявки_ 

_**include** – необязательный, позволяет получить связанные с заявкой ресурсы в одном запросе._ 

## _**Возможные значения:**_ 

## Аналогично, как и для получения конкретной заявки 

**Полномочия пользователя по работе с заявкой** 

_Поля ресурса UserTaskRights_ 

|**Название**|**Тип**|**Описание**|
|---|---|---|
|Name|Int|Название|
|Description|Int|Описание|
|Priority|Int|Приоритет|
|Deadline|Int|Срок исполнения|
|Categories|Int|Категории|
|CompletionStatus|Int|Выполнено|
|ServiceStage|Int|Этап|
|Files|Int|Файлы|
|Assets|Int|Активы|
|ExecutorGroup|bool|Группа исполнителей. Видимость<br>блока|
|Observers|bool|Наблюдатели. Видимость блока|
|Executors|bool|Исполнители. Видимость блока|
|Creator|bool|Заявитель. Видимость блока|



**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 21 из 82 

