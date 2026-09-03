**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

|||приоритеты, которые можно<br>установитьдля заявки|
|---|---|---|
|ToServiceStages|List<ServiceStageSimple<br>View>|Список объектов<br>ServiceStageSimpleView. Содержит<br>этапы, которые можно установить<br>для заявки|
|CategoriesWithAutoAssignUsers|List<AutoAssignsTaskCr<br>eateView>|Список объектов<br>AutoAssignsTaskCreateView. Содержит<br>категории, по которым возможны<br>автоназначения при создании заявки,<br>и правила автоназначения. Поле не<br>возвращается при получении<br>полномочий по существующей<br>заявке|
|StatusesWithAutoAssignUsers|List<AutoAssignsStatusC<br>hangeView>|Список объектов<br>AutoAssignsStatusChangeView.<br>Содержит статусы, по которым<br>возможны авто назначения при<br>изменении статуса заявки, и правила<br>авто назначения.|
|ViewExpenses|Boolean|Если true, то показываем блок<br>трудозатрат.|



## **Полномочия по работе с полями заявки рассчитываются как сумма следующих чисел:** 

- 0 – нет прав 

- 1 – разрешено редактировать 

- 2 – разрешено только чтение 

- 4 – поле является обязательным 

_Поля объекта AutoAssignsStatusChangeView ресурса UserTaskRights._ 

Объект содержит возможные авто назначения пользователей на заявку при переводе в данный статус 

|Название|Тип|Описание|
|---|---|---|
|Id|Int|Id статуса|
|Name|String|Название статуса|
|DeleteExecutors|Boolean|Снимать текущих исполнителей по заявке|
|DeleteObservers|Boolean|Снимать текущих наблюдателей на заявке|
|ExecutorIds|String|Идентификаторы исполнителей через запятую|
|Executors|String|Имена исполнителей через запятую|
|ExecutorGroupId|Int|Id группы исполнителей|
|ExecutorGroup|String|Название группы исполнителей|
|ObserverIds|String|Идентификаторы наблюдателей через запятую|
|Observers|String|Имена наблюдателей через запятую|



**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 23 из 82 

