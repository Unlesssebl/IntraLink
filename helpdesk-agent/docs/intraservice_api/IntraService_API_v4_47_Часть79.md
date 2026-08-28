**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

_<FieldTypeId>1</FieldTypeId> <Hint i:nil="true"/> <Id>56</Id> <IsRequired>false</IsRequired> <Name>Тет строка</Name> <Unit i:nil="true"/> </AssetTypeFieldView> <AssetTypeFieldView> <FieldSelectValues i:nil="true"/> <FieldTypeId>4</FieldTypeId> <Hint i:nil="true"/> <Id>57</Id> <IsRequired>false</IsRequired> <Name>Тест чек</Name> <Unit i:nil="true"/> </AssetTypeFieldView> </AssetTypeFields> <ExternalId i:nil="true"/> <Id>16</Id> <Image16Url>http://localhost/IntraService4.40/img/assets/ava1.jpg</Image16Url> <InventoryNumberInName>true</InventoryNumberInName> <IsArchive>false</IsArchive> <Name>test type</Name> </AssetTypeView>_ 

## **Общие настройки системы** 

/api/settings 

**Метод GET. Получение списка общих настроек системы** 

Поля для получения общих настроек 

|**Название**|**Тип**|**Описание**|
|---|---|---|
|Key|String|Ключ-идентификаторпараметра|
|Value|String|Значение параметра|
|FieldTypeId|Int|Тип значения параметра (1-строка, 2-число, 3-<br>булевый,4-список через запятую)|
|Name|String|Название параметра|
|Description|String|Описание параметра|



## Получение списка общих настроек 

## _Вызов_ 

## GET /api/settings?keys={keys_list} 

## _Возможные параметры_ 

## _**keys** – необязательный параметр_ 

Позволяет получить настройки по списку ключей-идентификаторов настроек (ключи должны быть перечислены через запятую). 

Пример: 

> **ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 79 из 82 

