**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

|Lifetime|bool|Жизненныйцикл. Видимость блока|
|---|---|---|
|Comment|Int|Комментарий|
|ChangeService|Boolean|Можно менять сервис и тип заявки?|
|ChangeCreator|Boolean|Можно изменять заявителя заявки?|
|Status|int|Статус|
|SystemFields|Int|Системные поля|
|ViewHistory|Boolean|Можно просматривать историю<br>изменений?|
|CanBeExecutor|Boolean|Может быть исполнителем заявки?|
|AssignItselfExecutor|Boolean|Можно назначить себя<br>исполнителем?|
|AssignExecutors|Boolean|Можно назначить исполнителей?|
|RemoveExecutors|IDictionary<int,bool>|Можно удалять исполнителей?<br>Словарь <Идентификатор<br>пользователя,можноудалять?>|
|AssignExecutorGroup|Boolean|Можно назначать группу<br>исполнителей?|
|RemoveExecutorGroup|Boolean|Можно удалять группу<br>исполнителей?|
|HasExecutorGroup|Boolean|На сервисе есть группы<br>исполнителей. Если true то<br>показываем блок группы<br>исполнителей, в противном случае не<br>показываем.|
|AssignItselfObserver|Boolean|Можно назначать себя<br>наблюдателем?|
|AssignObservers|Boolean|Можно назначать наблюдателей?|
|RemoveObservers|Boolean|Можноудалять наблюдателей?|
|AssignCoordinators|Boolean|Можно назначать согласующих?|
|RemoveCoordinators|Boolean|Можноудалять согласующих?|
||||
|TaskTypeFieldsData|Object|Полномочия для дополнительных<br>полей|
|MassIncident|TaskFieldRights|Массовый инцидент|
|Hours|Int|Трудозатраты|
|Rate|Int|Стоимость трудозатрат|
|EditOthersExpenses|Boolean|Можно редактировать чужие<br>трудозатраты(часы и стоимость)?|
|RemoveExpenses|IDictionary<int,bool>|Можно удалить трудозатраты?<br>Словарь<идентификатор трудозатрат,<br>можноудалять?>|
|ToStatuses|List<TaskStatusSimpleVi<br>ew>|Список объектов<br>TaskStatusSimpleView. Содержит<br>статусы, в которые можно перевести<br>заявку|
|ToPriorities|List<TaskPrioritySimple<br>View>|Список объектов<br>TaskPrioritySimpleView. Содержит|



**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 22 из 82 

