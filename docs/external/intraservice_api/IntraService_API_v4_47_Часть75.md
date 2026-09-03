**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

## Получение определенного актива 

_Вызов_ 

GET: /api/asset/{assetid} Где {assetid} – идентификатор актива 

_Пример вывода в xml_ GET: /api/asset/49 

_<AssetView> <Id>49</Id> <InventoryNumber>К123-45</InventoryNumber> <Name> К123-45: тестовое значение</Name> <TypeId>15</TypeId> <ParentId/> <Path>49|</Path> <TypeName>test 1</TypeName> <FullParentName/> <OwnerId>70</OwnerId> <Changed>28.09.2015 14:16:16</Changed> <Data><field id="54" >тестовое значение</field></Data> </AssetView>_ 

**Метод POST.  Добавление новых активов.** 

## Поля актива для создания 

|**Название**|**Тип значения**|**Обязательность**|**Комментарий**|
|---|---|---|---|
|TypeId|int|Да|Идентификатор типа актива|
|InventoryNumber|string|Да|Инвентарный номер|
|OwnerId|int|Нет|Идентификатор владельца<br>актива. Значение учитывается<br>только если у текущего<br>пользователя есть право<br>просматривать пользователей.<br>Иначе возьмется текущий<br>пользователь.|
|ParentId|int|Нет|Идентификатор родительского<br>актива|
|IsArchive|bool|Нет|Признак архивный|
|Field{id}|string|Нет|Значение дополнительного поля<br>с идентификатором, например,<br>Field8|
|Comment|string|Нет|Примечание|



**ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 75 из 82 

