**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

|PriorityId|Int||Идентификатор приоритета|
|---|---|---|---|
|StatusId|Int||Идентификатор статуса|
|ServiceId|Int||Идентификатор сервиса|
|ServiceStageId|Int||Идентификатор этапа|
|ServiceStage|String||Название этапа|
|ParentId|Int||Идентификатор родительской<br>заявки|
|Name|String|+|Название|
|Description|String|+|Описание|
|Deadline|DateTime||Срок|
|Created|DateTime||Дата создания|
|Changed|DateTime||Дата изменения|
|CreatorId|Int||Идентификатор заявителя|
|Creator|String||Заявитель|
|EditorId|Int||Идентификатор пользователя,<br>последним изменившего<br>заявку|
|ExecutorIds|String||Идентификаторы<br>исполнителей через запятую|
|Executors|String|+|Список исполнителей|
|Files|String|+|Список имен файлов.<br>Внимание! Устарело. Будет<br>удалено. Используйте поле<br>FileNames|
|FileNames|String||Список имен файлов.<br>Разделитель –|||
|FileIds|String||Идентификаторы файлов|
|CategoryIds|String||Идентификаторы категорий<br>через запятую.<br>В списке только при указании<br>в параметре**include**.|
|Categories|String|+|Список категорий (через || на<br>карточнойформе)|
|Hours|Decimal||Трудозатраты в часах|
|Price|Decimal||Трудозатраты в деньгах|
|Assets|String|+|Список наименований<br>активов (через || на<br>карточнойформе)|
|AssetIds|String||Идентификаторы активов<br>через запятую.<br>В списке только при указании<br>в параметре**include.**|



**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 11 из 82 

