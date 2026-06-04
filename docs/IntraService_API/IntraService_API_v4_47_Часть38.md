**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

**Метод GET. Получение списка статусов** 

## Поля статуса для получения 

|**Название**|**Тип**|**Описание**|
|---|---|---|
|Id|Int|Идентификаторстатуса|
|Name|String|Название|
|Description|String|Описание|
|Image16Url|String|Url маленькой иконки|
|Image24Url|String|Url большой иконки|
|IsInitial|Bool|Признак «Начальный»|
|IsCommentRequired|Bool|Признак «Комментарии обязательны»|
|IsFinal|Bool|Признак «Конечный»|
|IsFixed|Bool|Признак «Заявка выполнена»|
|IsDeadlineAccountingPaused|Bool|Признак «Учет времени выполнения<br>приостановлен»|
|IsConcurrence|Bool|Признак «Согласование»|
|IsExternal|Bool|Признак «Во внешней организации»|
|Changed|DateTime|Дата последнего изменения статуса|



## Получение списка статусов 

_Вызов:_ 

GET: /api/taskstatus?{filterFields} 

Список возвращается в отсортированном виде. 

_Возможные параметры:_ 

**{filterFields}** - поля для фильтрации 

Все условия фильтрации склеиваются по условию И. Подробнее в разделе о фильтрации статусов 

## Пример вывода в xml 

GET: /api/taskstatus 

_<ArrayOfTaskStatusView>_ 

_<TaskStatusView> <Changed>2015-10-29T13:51:14.023</Changed> <Description/> <Id>31</Id> <Image16Url>http://localhost/IntraService4.40p2/img/statuses/actual16.png </Image16Url> <Image24Url>http://localhost/IntraService4.40p2/img/statuses/actual24.png </Image24Url> <IsCommentRequired>false</IsCommentRequired> <IsConcurrence>false</IsConcurrence> <IsDeadlineAccountingPaused>false</IsDeadlineAccountingPaused> <IsExternal>false</IsExternal> <IsFinal>false</IsFinal> <IsFixed>false</IsFixed> <IsInitial>true</IsInitial> <Name>Открыта</Name>_ 

**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 38 из 82 

