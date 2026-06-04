**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

## **Приоритет** 

## /api/taskpriority 

**Метод GET. Получение списка приоритетов** 

Поля приоритета для получения 

|**Название**|**Тип**|**Описание**|
|---|---|---|
|Id|Int|Идентификаторприоритета|
|Name|String|Название|
|Description|String|Описание|
|Image16Url|String|Url иконки|



## Получение списка приоритетов 

_Вызов:_ 

GET: 

## /api/taskpriority 

Список возвращается в отсортированном виде. 

## Пример вывода в xml 

## GET /api/taskpriority 

_<ArrayOfTaskPriorityView> <TaskPriorityView>_ 

_<Description/> <Id>11</Id>_ 

_<Image16Url>http://localhost/IntraService4.40p2/img/priorities/priority2.gif </Image16Url> <Name>Низкий</Name>_ 

_</TaskPriorityView>_ 

_<TaskPriorityView>_ 

_….._ 

_<TaskPriorityView> </ArrayOfTaskPriorityView>_ 

## **Компания** 

## /api/company 

**Метод GET.  Получение списка компаний и определенной компании.** 

Поля компании для получения 

|**Название**|**Тип**|**Используется**<br>**в search**|**Описание**|
|---|---|---|---|
|Id|Int||Идентификаторкомпании|
|FullName|String||Полное наименование (с учетом<br>иерархии)|
|Name|String|+|Наименование|
|Domain|String|+|Домен(ы)|
|ParentId|Int||Идентификатор родительского<br>подразделения|



**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 40 из 82 

