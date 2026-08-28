**==> picture [114 x 37] intentionally omitted <==**

**==> picture [202 x 58] intentionally omitted <==**

## **Тип актива** 

## /api/assettype 

**Метод GET. Получение списка типов актива и конкретного типа актива.** 

## Поля типа актива для получения 

|Название|Тип<br>Описание|Тип<br>Описание|
|---|---|---|
|Id|Int|Идентификатортипа актива|
|Name|String|Наименование|
|ExternalId|Int|Идентификатор типа актива в других<br>системах(при синхронизации активов)|
|IsArchive|Int|Признак архивный|
|Image16Url|String|Путь до изображения|
|InventoryNumberInName|Bool|Использовать ли инвентарный номер в<br>формировании активовданного типа|
|AssetTypeFields|-|Список дополнительных полей типа актива<br>(**только при получении определенного**<br>**актива**)|



## Получение списка типов актива 

## _Вызов_ 

GET: /api/assettype?fields={fieldList}&archive={value}&pagesize={value}&page={value} 

## _Возможные параметры_ 

_**fields** - перечень полей ресурса «Тип актива» для вывода (из таблицы)_ 

## **Например:** 

Выведем список названий и ссылок на изображение для всех типов актива GET: /api/assettype?fields=Name,Image16Url 

_**archive –** позволяет вывести заархивированные типы актива_ 

_Возможные значения:_ 

true/false 

_**page** ,_ _**pagesize** – параметры отображения таблицы_ 

## _Пример вывода в xml_ 

GET: /api/assettype?fields=Id,Name,InventoryNumber&pagesize=2 

_<AssetTypeList>_ 

_<AssetTypes> <AssetType> <Id>5</Id> <Name>Room</Name> </AssetType> <AssetType> <Id>6</Id> <Name>Workstation</Name> </AssetType> </AssetTypes>_ 

> **ООО «Интравижн»** , Москва, ул. Б. Новодмитровская, д. 36/4 стр.1, тел. +7 (495) 795-23-44 

Стр. 77 из 82 

