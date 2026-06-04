**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

_Поля объекта TaskStatusSimpleView ресурса UserTaskRights._ 

|Название|Тип|Описание|
|---|---|---|
|Id|Int|Id статуса|
|Name|String|Название статуса|
|IsExternal|Boolean|Статус является внешним.|
|IsConcurrence|Boolean|Согласование|



_Поля объектов TaskPrioritySimpleView ресурса UserTaskRights._ 

|Название|Тип|Описание|
|---|---|---|
|Id|Int|Id приоритета|
|Name|String|Название приоритета|



_Поля объекта ServiceStageSimpleView ресурса UserTaskRights._ 

|Название|Тип|Описание|
|---|---|---|
|Id|Int|Id этапа|
|Name|String|Название этапа|
|EndDate|Date|Дата окончания этапа|



_Поля объекта AutoAssignsTaskCreateView ресурса UserTaskRights._ Объект содержит возможные автоназначения пользователей на заявку при выборе данной категории. Правила действуют только при создании заявки. 

|Название|Тип|Описание|
|---|---|---|
|Id|Int|Id категории|
|Name|String|Название категории|
|ExecutorIds|String|Идентификаторы исполнителей через запятую|
|Executors|String|Имена исполнителей через запятую|
|ExecutorGroupId|Int|Id группы исполнителей|
|ExecutorGroup|String|Название группы исполнителей|
|ObserverIds|String|Идентификаторы наблюдателей через запятую|
|Observers|String|Имена наблюдателей через запятую|



**Получение атрибутов заявки при изменении других атрибутов.** 

_Вызов_ 

## GET 

= = = = /api/task/field {field}&oldworkflowid {oldworkflowid}&serviced {serviceid}&priorityid {priorityid}&sta tusid={statusid}&taskid={taskid}&creatorid={creatorid}&tasktypeid={tasktypeid}&assetids={assetids}&ca tegoryids={categoryids} 

Примеры использования: 

- 1) При изменении атрибутов заявки возможно изменение бизнес процесса и/или класса обслуживания, что в свою очередь влияет на изменение атрибутов заявки. 

**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 24 из 82 

